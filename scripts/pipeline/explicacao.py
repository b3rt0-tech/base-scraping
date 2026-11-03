"""Por que este lead, o que cuidar e por onde falar — em linguagem de vendas, a partir do que o pipeline já
calculou (itens do score, flags, sinais, enriquecimento). Determinístico: nada aqui interpreta texto livre.
"""

from __future__ import annotations

from typing import Any, Literal

from scripts.pipeline.perfil import PerfilICP, Prioridade
from scripts.pipeline.score import ScoreBreakdown
from scripts.pipeline.validate import ValidacaoResult
from scripts.scraping.enrich import EnrichmentResult

Nivel = Literal["A", "B", "C"]

_ORDEM_DIMENSOES = ("aderencia_icp", "fit", "qualidade_contato", "presenca_digital", "saude_cadastral")
_EMAIL_DIRETO = ("maxima", "alta_nominal", "alta")

ALERTA_CONTADOR = (
    "E-mail/telefone do cadastro parece ser do contador: não use para chegar ao decisor; prefira WhatsApp ou site."
)
ALERTA_EMPRESA_NOVA = "Empresa aberta há menos de 6 meses: confirme se já está operando antes de oferecer."
ALERTA_CNAE_SECUNDARIO = "O nicho é só atividade secundária: confirme no primeiro contato se é o foco do negócio."
ALERTA_MEI = "MEI: estrutura e ticket mínimos; ofereça o plano mais simples ou descarte se o ticket for alto."
ALERTA_SEM_CANAL = "Sem canal direto (WhatsApp, e-mail próprio ou site): ache o site ou o Instagram antes de abordar."
ALERTA_SEM_DECISOR = "Sem decisor no cadastro: no primeiro contato, pergunte quem é o responsável pela compra."
_ALERTA_ENRIQUECIMENTO = {
    "match_fraco": "O site informado não confirmou ser desta empresa: não use os contatos dele.",
    "requer_navegador": "O site só carrega com navegador (JavaScript): confira os contatos manualmente.",
    "falha": "Não foi possível ler o site: confira os contatos manualmente se for abordar.",
}


def _pontos(valor: float) -> str:
    return f"{valor:+g}".replace(".", ",")


def motivos(score: ScoreBreakdown) -> list[str]:
    """Um motivo por item que pesou no score, com os pontos — a soma bate com `score_breakdown.total`."""
    linhas = []
    for dimensao in _ORDEM_DIMENSOES:
        for texto, pontos in score.itens.get(dimensao, []):
            if pontos:
                linhas.append(f"{texto} ({_pontos(pontos)})")
    return linhas


def _internacional(numero: str) -> str:
    """WhatsApp do site vem como dígitos do link wa.me (5519…): mesmo formato do celular cadastral (+5519…)."""
    digitos = "".join(c for c in numero if c.isdigit())
    return f"+{digitos}" if digitos.startswith("55") else numero


def melhor_canal(validacao: ValidacaoResult, enrichment: EnrichmentResult | None) -> dict[str, str] | None:
    """Por onde falar primeiro. WhatsApp responde mais em PME; depois e-mail confirmado no site, e-mail do
    cadastro (se não for do contador), telefone do site e, por último, o próprio site.
    """
    flags = validacao.flags
    contador = "contato_de_contador" in flags
    detalhe = validacao.detalhe
    matriz = detalhe.estabelecimento_matriz if detalhe else None
    site_ok = enrichment is not None and enrichment.site_matched

    if site_ok and enrichment and enrichment.whatsapp:
        return {"tipo": "whatsapp", "valor": _internacional(enrichment.whatsapp[0])}
    if validacao.celular_cadastral:
        return {"tipo": "whatsapp", "valor": validacao.celular_cadastral}
    if site_ok and enrichment:
        diretos = [e for e in enrichment.emails if e.confianca in _EMAIL_DIRETO]
        if diretos:
            return {"tipo": "email", "valor": diretos[0].email}
    if matriz and matriz.email and not contador:
        return {"tipo": "email", "valor": matriz.email.strip().lower()}
    if site_ok and enrichment and enrichment.telefones:
        return {"tipo": "telefone", "valor": enrichment.telefones[0]}
    if detalhe and detalhe.telefones and not contador:
        telefone = detalhe.telefone_principal()
        if telefone:
            return {"tipo": "telefone", "valor": telefone}
    if site_ok and enrichment and enrichment.site:
        return {"tipo": "site", "valor": enrichment.site}
    return None


def alertas(
    validacao: ValidacaoResult,
    enrichment: EnrichmentResult | None,
    *,
    canal: dict[str, str] | None,
    status_enriquecimento: str | None,
) -> list[str]:
    flags = validacao.flags
    lista = []
    if "contato_de_contador" in flags:
        lista.append(ALERTA_CONTADOR)
    if canal is None:
        lista.append(ALERTA_SEM_CANAL)
    if validacao.detalhe is not None and not validacao.detalhe.decisores():
        lista.append(ALERTA_SEM_DECISOR)
    if "empresa_nova" in flags:
        lista.append(ALERTA_EMPRESA_NOVA)
    if "cnae_secundario" in flags:
        lista.append(ALERTA_CNAE_SECUNDARIO)
    if "mei" in flags:
        lista.append(ALERTA_MEI)
    if status_enriquecimento in _ALERTA_ENRIQUECIMENTO:
        lista.append(_ALERTA_ENRIQUECIMENTO[status_enriquecimento])
    return lista


def prioridade(score_total: float, *, canal: dict[str, str] | None, tem_decisor: bool, cortes: Prioridade) -> Nivel:
    """A: score alto **e** dá para falar com quem decide (canal direto + decisor). C: score baixo ou sem
    canal — não vale a cadência do SDR antes de achar um contato. B: o resto.
    """
    if canal is None or score_total < cortes.corte_b:
        return "C"
    if score_total >= cortes.corte_a and tem_decisor:
        return "A"
    return "B"


def explicar(
    validacao: ValidacaoResult,
    enrichment: EnrichmentResult | None,
    score: ScoreBreakdown,
    *,
    perfil: PerfilICP | None = None,
    status_enriquecimento: str | None = None,
) -> dict[str, Any]:
    canal = melhor_canal(validacao, enrichment)
    tem_decisor = validacao.detalhe is not None and bool(validacao.detalhe.decisores())
    cortes = perfil.prioridade if perfil is not None else Prioridade()
    resultado: dict[str, Any] = {
        "prioridade": prioridade(score.total, canal=canal, tem_decisor=tem_decisor, cortes=cortes),
        "motivos": motivos(score),
        "alertas": alertas(validacao, enrichment, canal=canal, status_enriquecimento=status_enriquecimento),
        "canal_direto": canal is not None,
    }
    if canal is not None:
        resultado["melhor_canal"] = canal
    return resultado


def resumo(leads: list[dict[str, Any]]) -> dict[str, Any]:
    """Boletim do lote: quanto dele dá para trabalhar já."""
    flags = [set(lead.get("flags", [])) for lead in leads]
    return {
        "total": len(leads),
        "por_prioridade": {nivel: sum(lead.get("prioridade") == nivel for lead in leads) for nivel in "ABC"},
        "com_decisor": sum(bool(lead.get("decisores")) for lead in leads),
        "com_canal_direto": sum(bool(lead.get("canal_direto")) for lead in leads),
        "com_whatsapp": sum(
            (lead.get("melhor_canal") or {}).get("tipo") == "whatsapp" for lead in leads
        ),
        "com_site": sum(bool(lead.get("site")) for lead in leads),
        "com_contato_de_contador": sum("contato_de_contador" in f for f in flags),
        "mei": sum("mei" in f for f in flags),
        **_por_veredito(leads),
    }


def _por_veredito(leads: list[dict[str, Any]]) -> dict[str, Any]:
    vereditos = [(lead.get("qualificacao") or {}).get("veredito") for lead in leads]
    if not any(vereditos):
        return {}
    return {"por_veredito": {v: vereditos.count(v) for v in ("valido", "talvez", "descartar")}}
