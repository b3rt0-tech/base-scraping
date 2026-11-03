from __future__ import annotations

import re

import pytest

from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline.contador import marcar_contadores
from scripts.pipeline.explicacao import (
    ALERTA_CNAE_SECUNDARIO,
    ALERTA_CONTADOR,
    ALERTA_EMPRESA_NOVA,
    ALERTA_MEI,
    ALERTA_SEM_CANAL,
    ALERTA_SEM_DECISOR,
    explicar,
    melhor_canal,
    prioridade,
    resumo,
)
from scripts.pipeline.export import build_lead_json, export_leads
from scripts.pipeline.perfil import PerfilICP, Prioridade
from scripts.pipeline.score import compute_score
from scripts.pipeline.sinais import aplicar_sinais
from scripts.pipeline.validate import ValidacaoResult
from scripts.scraping.email_verify import EmailVerification
from scripts.scraping.enrich import EnrichmentResult

from .conftest import make_cnpj_raw

_PONTOS = re.compile(r"\(([+-][\d,]+)\)$")


def _lead(cnpj="11111111000101", flags=("contato_cadastral_disponivel",), cnae_match="principal", **raw):
    r = ValidacaoResult(
        cnpj=cnpj,
        status="validado",
        detalhe=CNPJDetalhe.model_validate(make_cnpj_raw(cnpj_completo=cnpj, **raw)),
        flags=list(flags),
        cnae_match=cnae_match,
    )
    aplicar_sinais([r])
    return r


def _enriquecido(**kw) -> EnrichmentResult:
    base = {"site": "https://x.com.br", "site_matched": True}
    return EnrichmentResult(**{**base, **kw})


def _soma_motivos(motivos: list[str]) -> float:
    return round(sum(float(_PONTOS.search(m).group(1).replace(",", ".")) for m in motivos), 2)


PERFIL = PerfilICP.model_validate(
    {
        "pesos": {"aderencia_icp": 20, "qualidade_contato": 25, "presenca_digital": 10, "saude_cadastral": 10, "fit": 35},
        "capital_social": {"minimo": 10000, "ideal": 300000},
        "anos_de_mercado": {"minimo": 1, "ideal": 8},
        "whatsapp": True,
    }
)


@pytest.mark.parametrize(
    "lead, enrichment, perfil",
    [
        (_lead(), None, None),
        (_lead(), None, PERFIL),
        (_lead(cnae_match="secundario", flags=["cnae_secundario"]), None, None),
        (_lead(telefones=[{"tipo": "comercial", "ddd": "19", "numero": "99876543"}]), None, PERFIL),
        (
            _lead(),
            _enriquecido(
                emails=[EmailVerification(email="ana@x.com.br", nivel=5)],
                telefones=["1932345678"],
                whatsapp=["5519999999999"],
                redes_sociais={"instagram": "i", "facebook": "f", "linkedin": "l", "youtube": "y"},
            ),
            None,
        ),
        (
            _lead(),
            _enriquecido(emails=[EmailVerification(email="ana@x.com.br", nivel=5)], whatsapp=["5519999999999"]),
            PERFIL,
        ),
    ],
)
def test_todo_ponto_do_score_tem_motivo(lead, enrichment, perfil):
    score = compute_score(lead, enrichment, perfil)
    motivos = explicar(lead, enrichment, score, perfil=perfil)["motivos"]
    assert motivos and all(_PONTOS.search(m) for m in motivos)
    assert _soma_motivos(motivos) == pytest.approx(score.total, abs=0.05)


def test_teto_da_dimensao_vira_item_negativo_explicito():
    from scripts.pipeline.score import _limitar

    itens = _limitar([("a", 20.0), ("b", 15.0)], 30)
    assert itens[-1] == ("limitado ao teto da dimensão (30)", -5.0)
    assert sum(p for _, p in itens) == 30


def test_alertas_sao_acionaveis():
    lead = _lead(
        flags=["contato_cadastral_disponivel", "empresa_nova", "cnae_secundario"],
        cnae_match="secundario",
        socios=[],
        simples={"opcao_mei": True},
        natureza_juridica_descricao="Sociedade Empresária Limitada",
        matriz={"email": "x@fulanocontabil.com.br"},
    )
    marcar_contadores([lead])
    explicacao = explicar(lead, None, compute_score(lead))
    for alerta in (ALERTA_CONTADOR, ALERTA_EMPRESA_NOVA, ALERTA_CNAE_SECUNDARIO, ALERTA_MEI, ALERTA_SEM_DECISOR):
        assert alerta in explicacao["alertas"]
    assert all(":" in a for a in explicacao["alertas"])  # problema: ação


def test_sem_canal_direto_vira_prioridade_c_e_alerta():
    lead = _lead(flags=[], matriz={"email": None}, telefones=[])
    explicacao = explicar(lead, None, compute_score(lead))
    assert explicacao["canal_direto"] is False and "melhor_canal" not in explicacao
    assert explicacao["prioridade"] == "C"
    assert ALERTA_SEM_CANAL in explicacao["alertas"]


def test_melhor_canal_prefere_whatsapp_depois_email_confirmado():
    cel = [{"tipo": "comercial", "ddd": "19", "numero": "99876543"}]
    assert melhor_canal(_lead(telefones=cel), None) == {"tipo": "whatsapp", "valor": "+5519999876543"}
    enrichment = _enriquecido(whatsapp=["5519911112222"])
    assert melhor_canal(_lead(telefones=cel), enrichment) == {"tipo": "whatsapp", "valor": "+5519911112222"}
    enrichment = _enriquecido(emails=[EmailVerification(email="ana@x.com.br", nivel=5)])
    assert melhor_canal(_lead(), enrichment) == {"tipo": "email", "valor": "ana@x.com.br"}


def test_melhor_canal_nao_usa_contato_do_contador():
    lead = _lead(matriz={"email": "x@fulanocontabil.com.br"})
    marcar_contadores([lead])
    assert melhor_canal(lead, None) is None


def test_site_que_nao_confirmou_nao_vira_canal():
    lead = _lead(flags=[], matriz={"email": None}, telefones=[])
    enrichment = EnrichmentResult(site="https://x.com.br", site_matched=False, whatsapp=["5519911112222"])
    assert melhor_canal(lead, enrichment) is None


@pytest.mark.parametrize(
    "score, canal, decisor, esperado",
    [
        (75, True, True, "A"),
        (75, True, False, "B"),  # sem decisor não é A
        (75, False, True, "C"),  # sem canal é C
        (55, True, True, "B"),
        (40, True, True, "C"),
    ],
)
def test_prioridade(score, canal, decisor, esperado):
    c = {"tipo": "email", "valor": "a@b.com"} if canal else None
    assert prioridade(score, canal=c, tem_decisor=decisor, cortes=Prioridade(corte_a=70, corte_b=50)) == esperado


def test_export_ordena_por_prioridade_e_traz_resumo():
    a = {"cnpj": "a", "score": 50, "prioridade": "B", "canal_direto": True, "melhor_canal": {"tipo": "email"}}
    b = {"cnpj": "b", "score": 90, "prioridade": "C", "canal_direto": False}
    c = {"cnpj": "c", "score": 70, "prioridade": "A", "canal_direto": True, "melhor_canal": {"tipo": "whatsapp"},
         "decisores": [{}], "flags": ["mei"]}
    saida = export_leads([a, b, c])
    assert [lead["cnpj"] for lead in saida["leads"]] == ["c", "a", "b"]
    assert saida["resumo"] == {
        "total": 3,
        "por_prioridade": {"A": 1, "B": 1, "C": 1},
        "com_decisor": 1,
        "com_canal_direto": 2,
        "com_whatsapp": 1,
        "com_site": 0,
        "com_contato_de_contador": 0,
        "mei": 1,
    }


def test_resumo_vazio():
    assert resumo([])["por_prioridade"] == {"A": 0, "B": 0, "C": 0}


def test_build_lead_json_traz_explicacao_e_cortes_do_perfil():
    lead = _lead(telefones=[{"tipo": "comercial", "ddd": "19", "numero": "99876543"}])
    score = compute_score(lead)
    exigente = PerfilICP.model_validate({"prioridade": {"corte_a": 99, "corte_b": 98}})
    assert build_lead_json(lead, score)["prioridade"] in ("A", "B")
    assert build_lead_json(lead, score, perfil=exigente)["prioridade"] == "C"
