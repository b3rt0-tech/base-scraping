from __future__ import annotations

from datetime import date

import pytest

from scripts.pipeline import db, discovery, icp
from scripts.pipeline.export import build_lead_json, export_leads
from scripts.pipeline.resolve import Candidato, _pick_unique_match, resolve_candidato
from scripts.pipeline.score import compute_score
from scripts.pipeline.validate import validate_lead
from scripts.scraping.context_signals import ContextSignals
from scripts.scraping.email_verify import EmailVerification
from scripts.scraping.enrich import EnrichmentResult

from .conftest import CNAE_DICT_PATH, CNPJ_PADRAO, make_cnpj_raw


def _icp(**kwargs) -> icp.ICP:
    return icp.build_icp(cnae_dict_path=CNAE_DICT_PATH, **kwargs)


# --- icp (S0) -------------------------------------------------------------


def test_dicionario_cnae_carrega_todos_os_nichos():
    entradas = icp.load_cnae_dictionary(CNAE_DICT_PATH)
    assert len(entradas) == 11
    assert all(e.aliases and e.cnaes for e in entradas)


@pytest.mark.parametrize(
    "nicho, cnaes",
    [
        ("dentista", ["8630504"]),
        ("Clínica Odontológica", ["8630504"]),
        ("oficina mecânica", ["4520"]),
        ("pet shop", ["4789004", "9609208"]),
        ("ótica", ["4774100"]),
    ],
)
def test_resolve_nicho(nicho, cnaes):
    entrada = icp.resolve_nicho(nicho, icp.load_cnae_dictionary(CNAE_DICT_PATH))
    assert entrada is not None
    assert entrada.cnaes == cnaes


def test_build_icp_traduz_codigos_e_filtros_extra():
    resultado = _icp(nicho="dentista", uf="SP", municipio="Campinas", porte="ME", cep="0507", matriz=True)
    assert resultado.situacao == "02"
    assert resultado.porte == "01"
    assert not resultado.needs_confirmation
    assert resultado.filtros_por_cnae("8630504") == {
        "cnae": "8630504",
        "uf": "SP",
        "municipio": "Campinas",
        "porte": "01",
        "situacao": "02",
        "cep": "0507",
        "matriz_filial": "1",
    }


def test_build_icp_nicho_desconhecido_pede_confirmacao():
    resultado = _icp(nicho="fábrica de foguetes")
    assert resultado.needs_confirmation
    assert resultado.cnaes == []


# --- db -------------------------------------------------------------------


def test_db_upsert_preserva_json_anterior_e_checkpoint(tmp_path):
    conn = db.connect(tmp_path / "p.sqlite3")
    run_id = db.make_run_id({"b": 2, "a": 1})
    assert run_id == db.make_run_id({"a": 1, "b": 2})

    db.upsert_lead(conn, run_id, "1", status="descoberto", discovery={"x": 1})
    db.upsert_lead(conn, run_id, "1", status="validado", detalhe={"y": 2})
    lead = db.get_lead(conn, run_id, "1")
    assert lead["status"] == "validado"
    assert lead["discovery"] == {"x": 1}
    assert lead["detalhe"] == {"y": 2}
    assert db.count_leads(conn, run_id, status="validado") == 1

    assert db.get_checkpoint(conn, run_id, "8630504") == {"next_page": 1, "exhausted": False}
    db.save_checkpoint(conn, run_id, "8630504", 3, exhausted=True)
    assert db.get_checkpoint(conn, run_id, "8630504") == {"next_page": 3, "exhausted": True}


# --- discovery (S1) -------------------------------------------------------


def _paginas(por_cnae: dict[str, list[list[str]]]):
    """Handler de `avancada`: por CNAE, uma lista de páginas com CNPJs."""

    def handler(request):
        from .conftest import ok

        cnae = request.url.params["cnae"]
        page = int(request.url.params["page"])
        paginas = por_cnae[cnae]
        items = [{"cnpj_completo": c} for c in paginas[page - 1]] if page <= len(paginas) else []
        return ok({"items": items, "total_pages": len(paginas)})

    return handler


async def test_discover_pagina_deduplica_entre_cnaes(api, tmp_path):
    chamadas = api({"avancada": _paginas({"4789004": [["1", "2"], ["3"]], "9609208": [["3", "4"]]})})
    conn = db.connect(tmp_path / "p.sqlite3")
    leads = await discovery.discover(_icp(nicho="pet shop"), conn=conn, run_id="r")
    assert sorted(lead["cnpj"] for lead in leads) == ["1", "2", "3", "4"]
    assert len(chamadas) == 3
    assert db.get_checkpoint(conn, "r", "4789004")["exhausted"]


async def test_discover_respeita_teto_e_retoma(api, tmp_path):
    chamadas = api({"avancada": _paginas({"8630504": [["1", "2"], ["3", "4"], ["5"]]})})
    conn = db.connect(tmp_path / "p.sqlite3")
    alvo = _icp(nicho="dentista")

    leads = await discovery.discover(alvo, conn=conn, run_id="r", target_leads=3)
    assert len(leads) == 3
    assert db.get_checkpoint(conn, "r", "8630504") == {"next_page": 2, "exhausted": False}

    await discovery.discover(alvo, conn=conn, run_id="r", target_leads=10)
    assert db.count_leads(conn, "r") == 5
    # retoma na página 2 (checkpoint), mas ela vem do cache — a API só vê a página 3 nova
    assert [int(c.url.params["page"]) for c in chamadas] == [1, 2, 3]


async def test_discover_recusa_icp_sem_cnae(tmp_path):
    conn = db.connect(tmp_path / "p.sqlite3")
    with pytest.raises(ValueError):
        await discovery.discover(_icp(nicho="xyz inexistente"), conn=conn, run_id="r")


# --- resolve (S2) ---------------------------------------------------------


def test_pick_unique_match():
    a, b = {"cnpj_formatado": "1", "uf": "SP"}, {"cnpj_formatado": "2", "uf": "RJ"}
    assert _pick_unique_match([], "SP", None) is None
    assert _pick_unique_match([a], None, None) is a
    assert _pick_unique_match([a, b], None, None) is None  # ambíguo, nunca chuta
    assert _pick_unique_match([a, b], "RJ", None) is b
    assert _pick_unique_match([a, {"cnpj_formatado": "3", "uf": "SP"}], "SP", None) is None


async def test_resolve_cai_para_busca_quando_sugestao_ambigua(api):
    api(
        {
            "sugestao": {"items": [{"cnpj_completo": "1"}, {"cnpj_completo": "2"}]},
            "busca": {"items": [{"cnpj_formatado": "12.345.678/0001-95", "uf": "SP"}, {"cnpj_formatado": "9", "uf": "RJ"}]},
        }
    )
    resultado = await resolve_candidato(Candidato(nome="Sorriso", uf="SP"))
    assert resultado.status == "resolvido"
    assert resultado.cnpj == CNPJ_PADRAO
    assert resultado.fonte == "busca"


async def test_resolve_sem_resultado_vira_nao_resolvido(api):
    api({"sugestao": {"items": []}, "busca": {"items": []}})
    resultado = await resolve_candidato(Candidato(nome="Nada"))
    assert resultado.status == "nao_resolvido"
    assert resultado.cnpj is None


# --- validate (S3) --------------------------------------------------------

HOJE = date(2026, 11, 3)


async def _validar(raw, **icp_kwargs):
    kwargs = {"nicho": "dentista", **icp_kwargs}
    return await validate_lead(CNPJ_PADRAO, _icp(**kwargs), raw=raw, hoje=HOJE)


async def test_validate_lead_ok_com_flags():
    resultado = await _validar(make_cnpj_raw())
    assert resultado.status == "validado"
    assert resultado.cnae_match == "principal"
    assert resultado.flags == ["contato_cadastral_disponivel"]


@pytest.mark.parametrize(
    "raw, icp_kwargs, motivo",
    [
        (make_cnpj_raw(matriz={"situacao_cadastral": "08"}), {}, "situacao_cadastral=08"),
        (make_cnpj_raw(cnaes=[{"tipo": "Principal", "cnae": "4781400"}]), {}, "cnae fora do ICP"),
        (make_cnpj_raw(porte_codigo="05"), {"porte": "ME"}, "porte=05"),
        (
            make_cnpj_raw(estabelecimentos=[{"cnpj_completo_formatado": "12.345.678/0001-95", "matriz_filial_codigo": "2"}]),
            {"matriz": True},
            "filial",
        ),
    ],
)
async def test_validate_lead_descartes(raw, icp_kwargs, motivo):
    resultado = await _validar(raw, **icp_kwargs)
    assert resultado.status == "descartado"
    assert motivo in resultado.discard_reason


async def test_validate_lead_cnae_secundario_e_empresa_nova():
    raw = make_cnpj_raw(
        cnaes=[{"tipo": "Principal", "cnae": "4781400"}, {"tipo": "Secundária", "cnae": "8630504"}],
        matriz={"data_inicio_atividade": "2026-09-01", "email": None},
        telefones=[],
    )
    resultado = await _validar(raw)
    assert resultado.status == "validado"
    assert resultado.cnae_match == "secundario"
    assert resultado.flags == ["empresa_nova", "cnae_secundario"]


async def test_validate_lead_busca_na_api_sem_raw(api):
    chamadas = api({"cnpj": make_cnpj_raw()})
    resultado = await validate_lead(CNPJ_PADRAO, _icp(nicho="dentista"), hoje=HOJE)
    assert resultado.status == "validado"
    assert len(chamadas) == 1


# --- score + export (S6) --------------------------------------------------


def _enrichment_completo() -> EnrichmentResult:
    return EnrichmentResult(
        site="https://sorriso.com.br/",
        site_matched=True,
        emails=[EmailVerification(email="ana@sorriso.com.br", nivel=5)],
        telefones=["1932345678"],
        whatsapp=["5519999999999"],
        redes_sociais={"instagram": "https://instagram.com/sorriso"},
        context=ContextSignals(tecnologias=["wordpress"]),
    )


async def test_score_sem_enriquecimento_usa_contato_cadastral():
    score = compute_score(await _validar(make_cnpj_raw()))
    assert score.as_dict() == {
        "aderencia_icp": 30.0,
        "qualidade_contato": 8.0,
        "presenca_digital": 0.0,
        "saude_cadastral": 20.0,
        "total": 58.0,
    }


async def test_score_com_enriquecimento_e_teto_por_dimensao():
    score = compute_score(await _validar(make_cnpj_raw()), _enrichment_completo())
    assert score.qualidade_contato == 28.0  # 20 (alta_nominal) + 4 telefone + 4 whatsapp
    assert score.presenca_digital == 16.0  # 12 site + 2 rede + 2 tecnologia
    assert score.total <= 100


async def test_score_cnae_secundario_rebaixa():
    raw = make_cnpj_raw(cnaes=[{"tipo": "Principal", "cnae": "4781400"}, {"tipo": "Secundária", "cnae": "8630504"}])
    assert compute_score(await _validar(raw)).aderencia_icp == 18.0


async def test_build_lead_json_com_proveniencia():
    validacao = await _validar(make_cnpj_raw())
    enrichment = _enrichment_completo()
    lead = build_lead_json(validacao, compute_score(validacao, enrichment), enrichment, validado_em=1.0, enriquecido_em=2.0)

    assert lead["razao_social"] == "CLINICA SORRISO LTDA"
    assert lead["matriz_filial"] == "matriz"
    assert lead["telefone_cadastral"] == "(19) 32345678"
    assert lead["emails"] == [{"valor": "ana@sorriso.com.br", "nivel": 5, "confianca": "alta_nominal"}]
    assert lead["enriquecimento"] == {"status": "ok", "motivo": None}
    assert lead["proveniencia"]["razao_social"] == {"fonte": "basecnpj", "rota_ou_url": "route=cnpj", "timestamp": 1.0}
    assert lead["proveniencia"]["emails"]["fonte"] == "scraping"
    campos_sem_proveniencia = set(lead) - set(lead["proveniencia"]) - {"score_breakdown", "proveniencia", "enriquecimento", "flags"}
    assert campos_sem_proveniencia == set()


@pytest.mark.parametrize(
    "enrichment, status",
    [
        (EnrichmentResult(site="x", site_matched=True, requer_navegador=True), "requer_navegador"),
        (EnrichmentResult(site="x", erro="match_fraco"), "match_fraco"),
        (EnrichmentResult(erro="ConnectError: boom"), "falha"),
    ],
)
async def test_build_lead_json_status_enriquecimento(enrichment, status):
    validacao = await _validar(make_cnpj_raw())
    lead = build_lead_json(validacao, compute_score(validacao, enrichment), enrichment)
    assert lead["enriquecimento"]["status"] == status
    assert "site" not in lead or status == "requer_navegador"


def test_export_ordena_por_score_e_aplica_teto():
    leads = [{"cnpj": str(i), "score": i} for i in range(5)]
    saida = export_leads(leads, max_leads=3)
    assert saida["total"] == 3
    assert [lead["score"] for lead in saida["leads"]] == [4, 3, 2]
