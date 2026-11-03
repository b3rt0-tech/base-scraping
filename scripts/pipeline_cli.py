from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer
from pydantic import BaseModel, ValidationError

from scripts import log as log_module
from scripts.basecnpj.config import SKILL_ROOT, get_settings
from scripts.basecnpj.errors import AuthError, NotFoundError
from scripts.pipeline import db as db_module
from scripts.pipeline import discovery, planilha
from scripts.pipeline import icp as icp_module
from scripts.pipeline import qualificacao as qualificacao_module
from scripts.pipeline import resultados as resultados_module
from scripts.pipeline.contador import marcar_contadores
from scripts.pipeline.exclusao import ExclusaoInvalida, Exclusoes, carregar_exclusoes
from scripts.pipeline.export import build_lead_json, export_leads
from scripts.pipeline.perfil import PerfilICP, PerfilInvalido, carregar_perfil
from scripts.pipeline.resolve import Candidato, resolve_candidato
from scripts.pipeline.schemas import (
    Erro,
    SaidaArquivo,
    SaidaDescobrir,
    SaidaProspectar,
    SaidaRegistrarResultado,
    SaidaRelatorioResultados,
    SaidaValidarLista,
)
from scripts.pipeline.score import compute_score
from scripts.pipeline.sinais import aplicar_sinais, site_do_email_cadastral
from scripts.pipeline.validate import ValidacaoResult, validate_lead
from scripts.scraping.enrich import enrich
from scripts.scraping.http import USER_AGENT, ScrapeSession
from scripts.utils import only_digits

log = log_module.get_logger("cli")

app = typer.Typer(help="Pipeline do base-scraping — S0→S3 (descobrir) e S0→S4→S6 (prospectar, sem S5).")

TARGET_LEADS = 100  # decisão fechada, seção 11 do ARCTETURE.md — sem override de CLI
SITE_AUTOMATICO = True  # sem --sites-json, tenta o domínio próprio do e-mail cadastral (desligado nos testes)
MAX_ITENS_LISTA = 100  # teto de itens do `validar-lista`, mesmo valor do teto de leads
ENRIQUECIMENTO_CONCORRENCIA = 8  # leads enriquecidos ao mesmo tempo (domínios distintos; o mesmo domínio é serializado)
MOTIVO_404 = "cnpj nao encontrado no BaseCNPJ (404)"

VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Detalhe de cada lead no stderr.")]
ContinuarOpt = Annotated[
    bool, typer.Option("--continuar", help="Entrega os próximos 100 validados da mesma consulta (lote seguinte).")
]
PerfilOpt = Annotated[
    str | None,
    typer.Option(
        "--perfil", help="icp.yaml do perfil do cliente ideal (padrão: icp.yaml na raiz da skill, se houver)."
    ),
]
ExcluirOpt = Annotated[
    str | None,
    typer.Option("--excluir", help="CSV/TXT/JSON com CNPJs e/ou domínios já no CRM (não voltam como lead novo)."),
]
FormatoOpt = Annotated[
    str,
    typer.Option("--formato", help="json (padrão, stdout), csv ou xlsx (grava o arquivo; stdout traz o resumo)."),
]
SaidaOpt = Annotated[str | None, typer.Option("--saida", help="Arquivo do csv/xlsx (padrão: leads-<nicho>-loteN).")]
SeparadorOpt = Annotated[str, typer.Option("--separador", help='Separador do CSV: "," (CRMs) ou ";" (Excel BR).')]
QualificacoesOpt = Annotated[
    str | None,
    typer.Option("--qualificacoes", help='JSON {"<cnpj>": {"veredito", "motivo", ...}} do consultor de vendas.'),
]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="Só avisos e erros no stderr.")]


class PreflightError(RuntimeError):
    """O `route=status` não respondeu `ok` — a descoberta não deve começar."""


class TokenAusenteError(RuntimeError):
    """`BASECNPJ_TOKEN` não está configurado — diferente de token inválido (`AuthError`)."""


def _exit_with_error(codigo: str, mensagem: str) -> NoReturn:
    typer.echo(json.dumps(Erro(erro=codigo, mensagem=mensagem).model_dump(), ensure_ascii=False))
    raise typer.Exit(code=1)


def _emitir(modelo: type[BaseModel], dados: dict[str, Any]) -> None:
    """Valida a saída contra o contrato (`scripts/pipeline/schemas.py`) e imprime o JSON.

    Campo sem dado não aparece (`exclude_unset`). Se o pipeline produzir algo fora do contrato,
    é bug nosso: responde erro JSON em vez de imprimir uma saída que o skill não saberia ler.
    """
    try:
        saida = modelo.model_validate(dados)
    except ValidationError as exc:
        _exit_with_error("saida_invalida", f"a saída não respeita o contrato {modelo.__name__}: {exc}")
    typer.echo(json.dumps(saida.model_dump(mode="json", exclude_unset=True), indent=2, ensure_ascii=False))


def _configurar_logs(verbose: bool, quiet: bool) -> None:
    if verbose and quiet:
        _exit_with_error("opcoes_invalidas", "--verbose e --quiet não podem ser usados juntos.")
    log_module.configure(verbose=verbose, quiet=quiet)  # só stderr: o stdout é o JSON final


def _run_or_exit(coro):
    try:
        return asyncio.run(coro)
    except AuthError:
        _exit_with_error(
            "token_invalido",
            "Token inválido, expirado ou sem permissão para a rota; conferir a variável BASECNPJ_TOKEN.",
        )
    except TokenAusenteError as exc:
        _exit_with_error("token_ausente", str(exc))
    except PreflightError as exc:
        _exit_with_error("preflight_falhou", str(exc))


def _resolve_icp_or_exit(
    nicho: str,
    uf: str | None,
    municipio: str | None,
    porte: str | None,
    situacao: str,
    cep: str | None,
    matriz: bool = False,
) -> icp_module.ICP:
    icp = icp_module.build_icp(
        nicho=nicho,
        uf=uf,
        municipio=municipio,
        porte=porte,
        situacao=situacao,
        cep=cep,
        matriz=matriz,
    )
    if icp.needs_confirmation and icp.candidatos:
        _exit_with_error(
            "nicho_ambiguo",
            (
                f"Nicho '{nicho}' casa com mais de uma entrada de references/cnae.md: "
                + " | ".join(icp.candidatos)
                + ". Pergunte ao usuário qual delas ele quer e rode de novo com o nome dela."
            ),
        )
    if icp.needs_confirmation:
        _exit_with_error(
            "nicho_nao_encontrado",
            (
                f"Nicho '{nicho}' não está em references/cnae.md. "
                "Confirme o(s) CNAE(s) certo(s) com o usuário antes de rodar a descoberta."
            ),
        )
    return icp


def _icp_resumo(icp: icp_module.ICP, arquivo_perfil: Path | None = None) -> dict[str, Any]:
    """Qual entrada do dicionário foi usada, quais CNAEs vão para a busca e qual perfil foi aplicado."""
    resumo: dict[str, Any] = {"nicho": icp.nicho, "entrada_dicionario": icp.entrada_dicionario, "cnaes": icp.cnaes}
    if arquivo_perfil is not None:
        resumo["perfil"] = str(arquivo_perfil)
    return resumo


def _validar_formato_ou_exit(formato: str, separador: str) -> str:
    formato = formato.strip().lower()
    if formato not in ("json", "csv", "xlsx"):
        _exit_with_error("opcoes_invalidas", f"--formato deve ser json, csv ou xlsx; veio '{formato}'.")
    if separador not in (",", ";", "\t"):
        _exit_with_error("opcoes_invalidas", '--separador deve ser "," ou ";".')
    return formato


def _emitir_leads(
    modelo: type[BaseModel], dados: dict[str, Any], *, formato: str, saida: str | None, separador: str, base: str
) -> None:
    """JSON no stdout (padrão) ou planilha em arquivo + resumo JSON no stdout. A saída é validada contra o
    contrato antes de virar planilha também: o CSV nunca carrega um lead que o JSON recusaria.
    """
    if formato == "json":
        _emitir(modelo, dados)
        return
    try:
        modelo.model_validate(dados)
    except ValidationError as exc:
        _exit_with_error("saida_invalida", f"a saída não respeita o contrato {modelo.__name__}: {exc}")
    caminho = Path(saida) if saida else planilha.nome_padrao(base, formato)
    try:
        if formato == "csv":
            planilha.gravar_csv(caminho, dados["leads"], separador=separador)
        else:
            planilha.gravar_xlsx(caminho, dados["leads"])
    except OSError as exc:
        _exit_with_error("saida_invalida", f"não foi possível gravar '{caminho}': {exc}")
    log.info("planilha gravada: %s (%d lead(s))", caminho, dados["total"])
    resumo_arquivo = {
        "arquivo": str(caminho.resolve()),
        "formato": formato,
        "total": dados["total"],
        "resumo": dados["resumo"],
        "excluidos": dados.get("excluidos", 0),
        "descartados": len(dados.get("descartados", [])),
    }
    for campo in ("lote", "esgotou_busca"):
        if campo in dados:
            resumo_arquivo[campo] = dados[campo]
    _emitir(SaidaArquivo, resumo_arquivo)


def _carregar_qualificacoes_ou_exit(caminho: str | None) -> dict[str, dict[str, Any]]:
    try:
        return qualificacao_module.carregar_qualificacoes(caminho)
    except qualificacao_module.QualificacaoInvalida as exc:
        _exit_with_error("entrada_invalida", str(exc))


def _aplicar_qualificacoes(leads: list[dict[str, Any]], qualificacoes: dict[str, dict[str, Any]]) -> str | None:
    fora = qualificacao_module.aplicar(leads, qualificacoes)
    if fora:
        log.warning("--qualificacoes: %d CNPJ(s) não estão entre os leads desta execução", fora)
        return f"{fora} CNPJ(s) do --qualificacoes não estão entre os leads desta execução (outra consulta/lote?)"
    return None


def _carregar_exclusoes_ou_exit(caminho: str | None) -> Exclusoes:
    try:
        exclusoes = carregar_exclusoes(caminho)
    except ExclusaoInvalida as exc:
        _exit_with_error("entrada_invalida", str(exc))
    if exclusoes:
        log.info(
            "excluir: %d empresa(s) por CNPJ e %d domínio(s) do CRM%s",
            len(exclusoes.raizes),
            len(exclusoes.dominios),
            f" ({exclusoes.ignorados} de provedor genérico ignorado(s))" if exclusoes.ignorados else "",
        )
    return exclusoes


def _carregar_perfil_ou_exit(caminho: str | None) -> tuple[PerfilICP | None, Path | None]:
    try:
        perfil, arquivo = carregar_perfil(caminho)
    except PerfilInvalido as exc:
        _exit_with_error("entrada_invalida", str(exc))
    if arquivo is not None:
        log.info("perfil aplicado: %s", arquivo)
    return perfil, arquivo


async def _exigir_preflight() -> None:
    # `route=status` é público e não acusaria a falta do token — checar antes
    if not get_settings().token:
        raise TokenAusenteError(
            f"BASECNPJ_TOKEN não está configurado. Crie {SKILL_ROOT / '.env'} a partir de .env.example "
            "(BASECNPJ_TOKEN=<seu-token>) ou exporte a variável de ambiente."
        )
    status = await icp_module.preflight()
    if status.get("status") != "ok":
        raise PreflightError(f"preflight falhou: {status}")


async def _descobrir_ate_validar(
    icp: icp_module.ICP,
    *,
    nicho: str,
    uf: str | None,
    municipio: str | None,
    porte: str | None,
    situacao: str,
    cep: str | None,
    db_path: str | None,
    matriz: bool = False,
    continuar: bool = False,
    perfil: PerfilICP | None = None,
    exclusoes: Exclusoes | None = None,
) -> tuple[Any, str, list[dict[str, Any]], dict[str, Any]]:
    """S0→S3: roda preflight, descoberta (com checkpoint) e validação dos leads ainda não processados.
    Devolve (conn, run_id, validados do lote, `lote`/`descobertos`/`validados`/`esgotou_busca`).
    Sem `continuar`, sempre o lote 1; com `continuar`, os próximos até 100 validados nunca entregues.
    """
    await _exigir_preflight()
    log.info("S0 preflight ok — nicho '%s', CNAEs %s", icp.nicho, ", ".join(icp.cnaes))

    conn = db_module.connect(Path(db_path) if db_path else None)
    params: dict[str, Any] = {
        "nicho": nicho,
        "uf": uf,
        "municipio": municipio,
        "porte": porte,
        "situacao": situacao,
        "cep": cep,
        # o checkpoint guarda "próxima página", que só vale para o mesmo tamanho de página
        "per_page": discovery.PER_PAGE,
    }
    if matriz:
        params["matriz"] = True  # só quando usada, para não mudar o run_id de execuções antigas
    if perfil is not None and perfil.exclusoes_ativas():
        params["perfil_excluir"] = perfil.exclusoes_ativas()  # outro corte eliminatório = outra consulta
    if exclusoes:
        params["excluir"] = exclusoes.assinatura()  # outra lista do CRM = outra consulta
    run_id = db_module.make_run_id(params)
    db_module.ensure_run(conn, run_id, params, TARGET_LEADS)
    # quem já tem resultado registrado (registrar-resultado) não volta como lead novo
    trabalhados = resultados_module.trabalhados(conn)
    db_module.marcar_trabalhados(conn, run_id, trabalhados)

    # retomada de execução interrompida no meio da validação
    await _validar_pendentes(conn, run_id, icp, perfil, exclusoes, trabalhados)

    lote_entregue = db_module.lote_atual(conn, run_id)
    if lote_entregue and not continuar:
        # repetir a mesma consulta devolve o mesmo lote 1 (idempotente, sem chamada nova à busca)
        lote = 1
    else:
        lote = lote_entregue + 1
        # o teto de 100 vale para leads **validados** ainda não entregues: descobre aos poucos (só o que
        # falta) e valida a cada rodada, até 100, busca esgotada ou o teto de segurança de descobertos
        teto_descobertos = db_module.count_leads(conn, run_id) + discovery.MAX_DESCOBERTOS
        while True:
            faltam = TARGET_LEADS - db_module.count_validados_sem_lote(conn, run_id)
            descobertos_ate_agora = db_module.count_leads(conn, run_id)
            if faltam <= 0 or descobertos_ate_agora >= teto_descobertos:
                break
            if discovery.busca_esgotada(icp, conn=conn, run_id=run_id):
                break
            alvo = min(teto_descobertos, descobertos_ate_agora + faltam)
            await discovery.discover(icp, conn=conn, run_id=run_id, target_leads=alvo)
            await _validar_pendentes(conn, run_id, icp, perfil, exclusoes, trabalhados)
        db_module.atribuir_lote(conn, run_id, lote, TARGET_LEADS)

    entregues_do_lote = db_module.list_lote(conn, run_id, lote)
    # lote já entregue antes (repetição do lote 1): tira quem ganhou resultado desde então
    validados = [lead for lead in entregues_do_lote if lead["cnpj"] not in trabalhados]
    info = {
        "lote": lote,
        "descobertos": db_module.count_leads(conn, run_id),
        "validados": len(validados),
        "esgotou_busca": discovery.busca_esgotada(icp, conn=conn, run_id=run_id),
        "excluidos": db_module.count_leads(conn, run_id, status="excluido"),
        "ja_trabalhados": db_module.count_leads(conn, run_id, status="ja_trabalhado")
        + len(entregues_do_lote)
        - len(validados),
    }
    log.info(
        "S3 pronto: lote %d com %d validado(s); %d descoberto(s) na consulta%s",
        lote,
        info["validados"],
        info["descobertos"],
        " (busca esgotada)" if info["esgotou_busca"] else "",
    )
    return conn, run_id, validados, info


async def _validar_pendentes(
    conn: Any,
    run_id: str,
    icp: icp_module.ICP,
    perfil: PerfilICP | None,
    exclusoes: Exclusoes | None = None,
    trabalhados: set[str] | None = None,
) -> None:
    """S3 dos leads ainda em "descoberto"; os já validados/descartados (retomada) são pulados. Quem já está no
    CRM (`--excluir`) vira "excluido": por CNPJ antes da chamada de validação, por domínio logo depois.
    """
    descobertos = db_module.list_leads(conn, run_id, status="descoberto")
    total = len(descobertos)
    if total:
        log.info("S1 descoberta: %d lead(s) para validar", total)
    for posicao, lead in enumerate(descobertos, start=1):
        cnpj = lead["cnpj"]
        if exclusoes and (motivo := exclusoes.por_cnpj(cnpj)):
            db_module.upsert_lead(conn, run_id, cnpj, status="excluido", discard_reason=motivo)
            continue
        if trabalhados and cnpj in trabalhados:
            db_module.upsert_lead(conn, run_id, cnpj, status="ja_trabalhado", discard_reason="resultado registrado")
            continue
        try:
            resultado = await validate_lead(cnpj, icp, perfil=perfil)
        except NotFoundError:
            log.warning("S3 %d/%d %s: CNPJ não encontrado (404)", posicao, total, cnpj)
            db_module.upsert_lead(
                conn,
                run_id,
                cnpj,
                status="nao_resolvido",
                discard_reason=MOTIVO_404,
            )
            continue
        status, motivo = resultado.status, resultado.discard_reason
        matriz = resultado.detalhe.estabelecimento_matriz if resultado.detalhe else None
        por_dominio = exclusoes.por_email(matriz.email if matriz else None) if exclusoes else None
        if status == "validado" and por_dominio:
            status, motivo = "excluido", por_dominio
        db_module.upsert_lead(
            conn,
            run_id,
            cnpj,
            status=status,
            detalhe=resultado.detalhe.model_dump() if resultado.detalhe else None,
            discard_reason=motivo,
        )
        log.debug("S3 %d/%d %s → %s %s", posicao, total, cnpj, resultado.status, resultado.discard_reason or "")
        if posicao % 10 == 0 or posicao == total:
            log.info("S3 validação: %d/%d", posicao, total)


def _ler_sites_json_ou_exit(arquivo: str | None) -> dict[str, str]:
    """Lê o `--sites-json` (`{"<cnpj>": "<url candidata>"}`). A chave pode vir com ou sem pontuação —
    é normalizada para 14 dígitos, que é como o pipeline busca a URL de cada lead.
    """
    if not arquivo:
        return {}
    formato = 'O --sites-json deve ser um objeto {"<cnpj de 14 dígitos>": "<url http(s)>"}'
    dados = _ler_json_ou_exit(arquivo)
    if not isinstance(dados, dict):
        _exit_with_error("entrada_invalida", f"{formato}; veio {type(dados).__name__}.")

    sites: dict[str, str] = {}
    for chave, url in dados.items():
        cnpj = only_digits(chave)
        if len(cnpj) != 14:
            _exit_with_error("entrada_invalida", f"{formato}; chave '{chave}' não é um CNPJ.")
        if not isinstance(url, str) or not url.strip().lower().startswith(("http://", "https://")):
            _exit_with_error("entrada_invalida", f"{formato}; a URL de '{chave}' precisa começar com http:// ou https://.")
        sites[cnpj] = url.strip()
    return sites


def _listar_descartados(conn: Any, run_id: str) -> list[dict[str, Any]]:
    """Leads que não chegaram à saída (descartados na validação ou não resolvidos)."""
    descartados: list[dict[str, Any]] = []
    for status in ("descartado", "nao_resolvido"):
        for lead in db_module.list_leads(conn, run_id, status=status):
            descartados.append({"cnpj": lead["cnpj"], "motivo": lead["discard_reason"]})
    return descartados


@dataclass
class _Progresso:
    total: int
    feitos: int = 0


async def _enriquecer_um(
    resultado: ValidacaoResult,
    candidate_url: str | None,
    session: ScrapeSession,
    client: httpx.AsyncClient,
    semaforo: asyncio.Semaphore,
    progresso: _Progresso,
    perfil: PerfilICP | None = None,
    origem_site: str | None = None,
) -> dict[str, Any]:
    enrichment = None
    if candidate_url:
        detalhe = resultado.detalhe
        estab = detalhe.estabelecimento_matriz if detalhe else None
        async with semaforo:
            enrichment = await enrich(
                candidate_url,
                session,
                cnpj=resultado.cnpj,
                razao_social=detalhe.razao_social if detalhe else None,
                nome_fantasia=estab.nome_fantasia if estab else None,
                telefone_cadastral=detalhe.telefone_principal() if detalhe else None,
                email_cadastral=estab.email if estab else None,
                client=client,
            )

    if origem_site == "email_cadastral" and enrichment is not None and not enrichment.site_matched:
        # site deduzido por nós que não se confirmou (fora do ar, domínio de terceiro): o usuário nunca o
        # indicou, então some sem virar alerta — o lead sai só com o dado cadastral
        enrichment = None

    score = compute_score(resultado, enrichment, perfil)
    lead = build_lead_json(resultado, score, enrichment, perfil=perfil, origem_site=origem_site)
    if candidate_url:
        progresso.feitos += 1
        status = lead["enriquecimento"]["status"] if "enriquecimento" in lead else "sem site próprio"
        motivo = lead["enriquecimento"]["motivo"] if "enriquecimento" in lead else None
        # site deduzido do e-mail que não confirmou é o caso comum (domínio só de e-mail): não é aviso
        registrar = log.info if status == "ok" else log.debug if "enriquecimento" not in lead else log.warning
        sufixo = f" ({motivo})" if motivo else ""
        registrar("S4 %d/%d %s → %s%s", progresso.feitos, progresso.total, resultado.cnpj, status, sufixo)
    return lead


async def _enriquecer_e_pontuar(
    resultados: list[ValidacaoResult],
    sites: dict[str, str],
    *,
    concorrencia: int = ENRIQUECIMENTO_CONCORRENCIA,
    perfil: PerfilICP | None = None,
) -> list[dict[str, Any]]:
    """S4+S6: para cada resultado validado, enriquece (quando há site candidato em
    `sites`) e pontua. Devolve os JSONs de lead, na mesma ordem de `resultados`, ainda
    sem ordenar/limitar.

    Até `concorrencia` leads rodam ao mesmo tempo, com um único `httpx.AsyncClient`
    compartilhado. O rate limit continua valendo: o `ScrapeSession` serializa as
    requisições de cada domínio (lock + delay de 2-5s) e o teto de páginas por domínio.
    """
    # `--sites-json` tem prioridade; sem ele, o domínio próprio do e-mail cadastral vira candidato
    candidatos: dict[str, tuple[str, str]] = {}
    for r in resultados:
        if sites.get(r.cnpj):
            candidatos[r.cnpj] = (sites[r.cnpj], "sites_json")
        elif SITE_AUTOMATICO and (site := site_do_email_cadastral(r)):
            candidatos[r.cnpj] = (site, "email_cadastral")

    session = ScrapeSession()
    semaforo = asyncio.Semaphore(concorrencia)
    progresso = _Progresso(total=len(candidatos))
    if progresso.total:
        automaticos = sum(1 for _, origem in candidatos.values() if origem == "email_cadastral")
        log.info(
            "S4 enriquecimento: %d lead(s) com site candidato (%d pelo e-mail cadastral), até %d em paralelo",
            progresso.total,
            automaticos,
            concorrencia,
        )
    else:
        log.info("S4 pulado: nenhum site candidato — leads saem só com dado cadastral")
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        return list(
            await asyncio.gather(
                *(
                    _enriquecer_um(
                        resultado,
                        candidatos.get(resultado.cnpj, (None, None))[0],
                        session,
                        client,
                        semaforo,
                        progresso,
                        perfil,
                        origem_site=candidatos.get(resultado.cnpj, (None, None))[1],
                    )
                    for resultado in resultados
                )
            )
        )


@app.command()
def descobrir(
    nicho: str,
    uf: str | None = None,
    municipio: str | None = None,
    porte: str | None = None,
    situacao: str = "ativa",
    cep: str | None = None,
    matriz: bool = False,
    db_path: str | None = None,
    continuar: ContinuarOpt = False,
    perfil_arquivo: PerfilOpt = None,
    excluir: ExcluirOpt = None,
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """S0→S3: traduz o nicho em CNAE (`references/cnae.md`), descobre via busca
    avançada (teto fixo de 100 leads), valida cada lead via `route=cnpj`, e
    imprime o resultado em JSON — sempre JSON, decisão fechada da seção 11.

    `--cep` filtra por prefixo de CEP (ex. "0507") — é o jeito de restringir a
    um bairro/região dentro de um município, já que não há filtro de bairro
    nomeado na API (ver references/basecnpj.md). `--matriz` restringe a
    descoberta a estabelecimentos matriz (exclui filiais).
    """
    _configurar_logs(verbose, quiet)
    icp = _resolve_icp_or_exit(nicho, uf, municipio, porte, situacao, cep, matriz)
    perfil, arquivo_perfil = _carregar_perfil_ou_exit(perfil_arquivo)
    exclusoes = _carregar_exclusoes_ou_exit(excluir)

    async def _pipeline() -> dict[str, Any]:
        conn, run_id, validados, info = await _descobrir_ate_validar(
            icp,
            nicho=nicho,
            uf=uf,
            municipio=municipio,
            porte=porte,
            situacao=situacao,
            cep=cep,
            db_path=db_path,
            matriz=matriz,
            continuar=continuar,
            perfil=perfil,
            exclusoes=exclusoes,
        )
        leads = [{"cnpj": v["cnpj"], "razao_social": (v["detalhe"] or {}).get("razao_social")} for v in validados]
        return {
            "icp": _icp_resumo(icp, arquivo_perfil),
            "total": len(leads),
            "leads": leads,
            "descartados": _listar_descartados(conn, run_id),
            **info,
        }

    resultado_final = _run_or_exit(_pipeline())
    log.info("pronto: %d lead(s), %d descartado(s)", resultado_final["total"], len(resultado_final["descartados"]))
    _emitir(SaidaDescobrir, resultado_final)


@app.command()
def prospectar(
    nicho: str,
    uf: str | None = None,
    municipio: str | None = None,
    porte: str | None = None,
    situacao: str = "ativa",
    cep: str | None = None,
    matriz: bool = False,
    db_path: str | None = None,
    continuar: ContinuarOpt = False,
    perfil_arquivo: PerfilOpt = None,
    excluir: ExcluirOpt = None,
    sites_json: str | None = None,
    qualificacoes_arquivo: QualificacoesOpt = None,
    formato: FormatoOpt = "json",
    saida: SaidaOpt = None,
    separador: SeparadorOpt = ",",
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """S0→S4→S6 (sem S5 — decisão fechada, seção 11): descobre, valida,
    enriquece (quando há site candidato) e pontua. Sempre JSON, teto fixo de
    100 leads. `--cep` filtra por bairro/região (prefixo de CEP). `--matriz`
    descobre só matrizes. `--sites-json`
    aponta pra um arquivo `{"cnpj": "url candidata"}` — achar a URL em si é
    responsabilidade do skill via busca web, não deste comando (regra de ouro
    da seção 6 do ARCTETURE.md).
    """
    _configurar_logs(verbose, quiet)
    formato = _validar_formato_ou_exit(formato, separador)
    icp = _resolve_icp_or_exit(nicho, uf, municipio, porte, situacao, cep, matriz)
    perfil, arquivo_perfil = _carregar_perfil_ou_exit(perfil_arquivo)
    exclusoes = _carregar_exclusoes_ou_exit(excluir)

    sites = _ler_sites_json_ou_exit(sites_json)
    qualificacoes = _carregar_qualificacoes_ou_exit(qualificacoes_arquivo)

    async def _pipeline() -> dict[str, Any]:
        conn, run_id, validados, info = await _descobrir_ate_validar(
            icp,
            nicho=nicho,
            uf=uf,
            municipio=municipio,
            porte=porte,
            situacao=situacao,
            cep=cep,
            db_path=db_path,
            matriz=matriz,
            continuar=continuar,
            perfil=perfil,
            exclusoes=exclusoes,
        )

        resultados: list[ValidacaoResult] = []
        for v in validados:
            # reconstrói o ValidacaoResult completo (flags/cnae_match) a partir do
            # detalhe já persistido — sem nova chamada de rede (raw= evita o fetch)
            resultado = await validate_lead(v["cnpj"], icp, raw=v["detalhe"], perfil=perfil)
            if resultado.status != "validado":
                continue  # ICP mudou entre execuções — não deveria acontecer, mas não quebra
            resultados.append(resultado)

        marcar_contadores(resultados)
        aplicar_sinais(resultados)
        leads_json = await _enriquecer_e_pontuar(resultados, sites, perfil=perfil)
        aviso = _aplicar_qualificacoes(leads_json, qualificacoes)
        saida = export_leads(leads_json, max_leads=TARGET_LEADS)
        if aviso:
            saida["aviso"] = aviso
        saida["icp"] = _icp_resumo(icp, arquivo_perfil)
        saida["descartados"] = _listar_descartados(conn, run_id)
        saida.update(info)
        resultados_module.registrar_entregues(conn, saida["leads"])
        return saida

    resultado_final = _run_or_exit(_pipeline())
    log.info("S6 pronto: %d lead(s), %d descartado(s)", resultado_final["total"], len(resultado_final["descartados"]))
    _emitir_leads(
        SaidaProspectar,
        resultado_final,
        formato=formato,
        saida=saida,
        separador=separador,
        base=f"{nicho}-{municipio or uf or ''}-lote{resultado_final['lote']}",
    )


def _ler_json_ou_exit(arquivo: str) -> Any:
    """Lê um JSON de entrada do usuário. Aceita BOM (JSON salvo no Windows/Excel) e devolve erro JSON,
    nunca traceback, para arquivo ausente, fora de UTF-8 ou malformado.
    """
    try:
        return json.loads(Path(arquivo).read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        _exit_with_error(
            "entrada_invalida",
            f"'{arquivo}' não está em UTF-8 (comum em arquivo salvo como latin-1/Windows-1252): salve como UTF-8.",
        )
    except (OSError, ValueError) as exc:  # ValueError cobre JSON malformado
        _exit_with_error("entrada_invalida", f"não foi possível ler '{arquivo}' como JSON: {exc}")


def _validar_item_da_lista(posicao: int, item: Any) -> dict[str, Any]:
    """Valida um item de `--arquivo` e o devolve normalizado; o erro cita a posição (1 = primeiro item)."""
    formato = 'cada item deve ser {"cnpj": ...} ou {"nome": ..., "uf"?: ..., "municipio"?: ...}'

    def recusar(motivo: str) -> NoReturn:
        _exit_with_error("entrada_invalida", f"item {posicao} da lista: {motivo}; {formato}.")

    if not isinstance(item, dict):
        recusar(f"veio {type(item).__name__}, esperado um objeto")

    cnpj, nome = item.get("cnpj"), item.get("nome")
    if isinstance(cnpj, bool) or not isinstance(cnpj, (str, int, type(None))):
        recusar(f"'cnpj' deve ser texto ou número, veio {type(cnpj).__name__}")
    if nome is not None and not isinstance(nome, str):
        recusar(f"'nome' deve ser texto, veio {type(nome).__name__}")
    for campo in ("uf", "municipio"):
        if item.get(campo) is not None and not isinstance(item[campo], str):
            recusar(f"'{campo}' deve ser texto, veio {type(item[campo]).__name__}")

    if isinstance(cnpj, int):
        cnpj = str(cnpj).zfill(14)  # planilha/JSON como número perde os zeros à esquerda
    cnpj = cnpj.strip() if isinstance(cnpj, str) else None
    nome = nome.strip() if isinstance(nome, str) else None
    if not cnpj and not nome:
        recusar("sem 'cnpj' nem 'nome'")
    return {**item, "cnpj": cnpj or None, "nome": nome or None}


def _ler_lista_ou_exit(arquivo: str) -> list[dict[str, Any]]:
    itens = _ler_json_ou_exit(arquivo)
    if not isinstance(itens, list):
        _exit_with_error(
            "entrada_invalida", f"O arquivo deve ser uma lista (JSON array); veio {type(itens).__name__}."
        )
    return [_validar_item_da_lista(posicao, item) for posicao, item in enumerate(itens, start=1)]


@app.command("validar-lista")
def validar_lista(
    arquivo: str = typer.Option(..., help="JSON com a lista de CNPJs/nomes"),
    situacao: str = "ativa",
    sites_json: str | None = None,
    qualificacoes_arquivo: QualificacoesOpt = None,
    perfil_arquivo: PerfilOpt = None,
    excluir: ExcluirOpt = None,
    formato: FormatoOpt = "json",
    saida: SaidaOpt = None,
    separador: SeparadorOpt = ",",
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """S2→S3→S4→S6 para uma lista própria (sem descoberta): `--arquivo` é um JSON
    com itens `{"cnpj": ...}` ou `{"nome": ..., "uf"?, "municipio"?}`, no máximo
    100 itens (o excedente é ignorado e informado na saída). Nome é resolvido via
    `route=sugestao`/`route=busca`; ambíguo ou sem resultado vai para
    `nao_resolvidos`. A validação não checa CNAE (a lista não tem nicho).
    `--sites-json` funciona como em `prospectar`.
    """
    _configurar_logs(verbose, quiet)
    formato = _validar_formato_ou_exit(formato, separador)
    itens = _ler_lista_ou_exit(arquivo)
    ignorados = max(0, len(itens) - MAX_ITENS_LISTA)
    itens = itens[:MAX_ITENS_LISTA]

    sites = _ler_sites_json_ou_exit(sites_json)
    qualificacoes = _carregar_qualificacoes_ou_exit(qualificacoes_arquivo)
    perfil, _ = _carregar_perfil_ou_exit(perfil_arquivo)
    exclusoes = _carregar_exclusoes_ou_exit(excluir)

    codigo_situacao = icp_module.SITUACAO_CODIGOS.get(situacao.strip().lower(), situacao)
    icp = icp_module.ICP(nicho="lista", cnaes=[], situacao=codigo_situacao)

    async def _pipeline() -> dict[str, Any]:
        await _exigir_preflight()

        descartados: list[dict[str, Any]] = []
        excluidos = 0
        nao_resolvidos: list[dict[str, Any]] = []
        validados: list[ValidacaoResult] = []
        vistos: set[str] = set()

        total_itens = len(itens)
        for posicao, item in enumerate(itens, start=1):
            if item.get("cnpj"):
                cnpj = only_digits(str(item["cnpj"]))
                if len(cnpj) != 14:
                    nao_resolvidos.append({"cnpj": item["cnpj"], "motivo": "cnpj inválido (não tem 14 dígitos)"})
                    log.warning("S2 %d/%d %s → CNPJ inválido", posicao, total_itens, item["cnpj"])
                    continue
            else:
                resolucao = await resolve_candidato(
                    Candidato(nome=item["nome"], uf=item.get("uf"), municipio=item.get("municipio"))
                )
                if resolucao.status != "resolvido" or resolucao.cnpj is None:
                    nao_resolvidos.append({"nome": item["nome"], "motivo": "sem match único na sugestão/busca"})
                    log.warning("S2 %d/%d '%s' → não resolvido (sem match único)", posicao, total_itens, item["nome"])
                    continue
                cnpj = resolucao.cnpj

            if cnpj in vistos:
                log.debug("S2 %d/%d %s → repetido, ignorado", posicao, total_itens, cnpj)
                continue
            vistos.add(cnpj)
            if exclusoes.por_cnpj(cnpj):
                excluidos += 1
                log.info("S3 %d/%d %s → já está no CRM, excluído", posicao, total_itens, cnpj)
                continue

            try:
                resultado = await validate_lead(cnpj, icp, perfil=perfil)
            except NotFoundError:
                descartados.append({"cnpj": cnpj, "motivo": MOTIVO_404})
                log.warning("S3 %d/%d %s → CNPJ não encontrado (404)", posicao, total_itens, cnpj)
                continue
            matriz = resultado.detalhe.estabelecimento_matriz if resultado.detalhe else None
            if resultado.status == "validado" and exclusoes.por_email(matriz.email if matriz else None):
                excluidos += 1
                log.info("S3 %d/%d %s → domínio já está no CRM, excluído", posicao, total_itens, cnpj)
                continue
            if resultado.status == "validado":
                validados.append(resultado)
            else:
                descartados.append({"cnpj": cnpj, "motivo": resultado.discard_reason})
            log.info("S3 %d/%d %s → %s", posicao, total_itens, cnpj, resultado.status)

        marcar_contadores(validados)
        aplicar_sinais(validados)
        leads_json = await _enriquecer_e_pontuar(validados, sites, perfil=perfil)
        aviso = _aplicar_qualificacoes(leads_json, qualificacoes)
        saida = export_leads(leads_json, max_leads=TARGET_LEADS)
        if aviso:
            saida["aviso"] = aviso
        saida["descartados"] = descartados
        saida["nao_resolvidos"] = nao_resolvidos
        saida["excluidos"] = excluidos
        resultados_module.registrar_entregues(db_module.connect(), saida["leads"])
        if ignorados:
            saida["aviso"] = f"{ignorados} item(ns) além do teto de {MAX_ITENS_LISTA} foram ignorados"
        return saida

    resultado_final = _run_or_exit(_pipeline())
    log.info(
        "S6 pronto: %d lead(s), %d descartado(s), %d não resolvido(s)",
        resultado_final["total"],
        len(resultado_final["descartados"]),
        len(resultado_final["nao_resolvidos"]),
    )
    _emitir_leads(
        SaidaValidarLista, resultado_final, formato=formato, saida=saida, separador=separador, base="lista"
    )



@app.command("registrar-resultado")
def registrar_resultado(
    arquivo: str = typer.Option(..., help="CSV ou JSON com cnpj, status e motivo (opcional)"),
    db_path: str | None = None,
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """Registra o desfecho de leads já trabalhados (contatado, respondeu, reuniao, ganho, perdido, nao_e_fit).
    CNPJ com resultado não volta em `descobrir`/`prospectar`, e entra no `relatorio-resultados`.
    """
    _configurar_logs(verbose, quiet)
    conn = db_module.connect(Path(db_path) if db_path else None)
    try:
        saida = resultados_module.registrar(conn, arquivo)
    except resultados_module.ResultadoInvalido as exc:
        _exit_with_error("entrada_invalida", str(exc))
    log.info("resultados: %d registrado(s), %d linha(s) ignorada(s)", saida["registrados"], len(saida["ignorados"]))
    _emitir(SaidaRegistrarResultado, saida)


@app.command("relatorio-resultados")
def relatorio_resultados(
    db_path: str | None = None,
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """Funil dos leads entregues por CNAE, porte, cidade, prioridade e sinal — base para ajustar o icp.yaml."""
    _configurar_logs(verbose, quiet)
    conn = db_module.connect(Path(db_path) if db_path else None)
    _emitir(SaidaRelatorioResultados, resultados_module.relatorio(conn))


if __name__ == "__main__":
    app()
