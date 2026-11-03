from __future__ import annotations

from datetime import date

import pytest

from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline.contador import marcar_contadores
from scripts.pipeline.export import build_lead_json
from scripts.pipeline.score import compute_score
from scripts.pipeline.sinais import (
    FLAG_MEI,
    FLAG_MULTIUNIDADE,
    FLAG_WHATSAPP,
    anos_de_mercado,
    aplicar_sinais,
    celular,
    unidades_ativas,
)
from scripts.pipeline.validate import ValidacaoResult

from .conftest import make_cnpj_raw


@pytest.mark.parametrize(
    "ddd, numero, esperado",
    [
        ("19", "99876543", "+5519999876543"),  # celular no formato antigo (8 dígitos): ganha o 9
        ("19", "81234567", "+5519981234567"),
        ("11", "6123-4567", "+5511961234567"),
        ("19", "987654321", "+5519987654321"),  # já com o nono dígito
        ("19", "32345678", None),  # fixo
        ("19", "23456789", None),
        ("1", "99876543", None),  # DDD inválido
        (None, "99876543", None),
        ("19", None, None),
        ("19", "876543210", None),  # 9 dígitos sem começar com 9
    ],
)
def test_celular(ddd, numero, esperado):
    assert celular(ddd, numero) == esperado


def _lead(cnpj: str = "11111111000101", **raw_overrides) -> ValidacaoResult:
    raw = make_cnpj_raw(cnpj_completo=cnpj, **raw_overrides)
    return ValidacaoResult(
        cnpj=cnpj,
        status="validado",
        detalhe=CNPJDetalhe.model_validate(raw),
        flags=["contato_cadastral_disponivel"],
        cnae_match="principal",
    )


def _tel(numero: str, tipo: str = "comercial", ddd: str = "19") -> dict:
    return {"tipo": tipo, "ddd": ddd, "numero": numero}


def test_whatsapp_provavel_pelo_celular_cadastral():
    lead = _lead(telefones=[_tel("32345678"), _tel("99876543")])
    aplicar_sinais([lead])
    assert FLAG_WHATSAPP in lead.flags
    assert lead.celular_cadastral == "+5519999876543"


def test_fax_e_fixo_nao_viram_whatsapp():
    lead = _lead(telefones=[_tel("99876543", tipo="fax"), _tel("32345678")])
    aplicar_sinais([lead])
    assert FLAG_WHATSAPP not in lead.flags
    assert lead.celular_cadastral is None


def test_celular_repetido_entre_empresas_nao_e_whatsapp_do_lead():
    """Mesmo celular em empresas sem relação = telefone do contador, não do decisor."""
    a = _lead("11111111000101", telefones=[_tel("99876543")])
    b = _lead("22222222000102", telefones=[_tel("99876543")])
    marcar_contadores([a, b])
    aplicar_sinais([a, b])
    assert FLAG_WHATSAPP not in a.flags and FLAG_WHATSAPP not in b.flags


def test_pula_celular_compartilhado_e_usa_o_proprio():
    a = _lead("11111111000101", telefones=[_tel("99876543"), _tel("98888777")])
    b = _lead("22222222000102", telefones=[_tel("99876543")])
    aplicar_sinais([a, b])
    assert a.celular_cadastral == "+5519998888777"


def test_mei():
    lead = _lead(simples={"opcao_simples": True, "opcao_mei": True})
    outro = _lead("22222222000102", simples={"opcao_simples": True, "opcao_mei": False})
    sem_dado = _lead("33333333000103")
    aplicar_sinais([lead, outro, sem_dado])
    assert FLAG_MEI in lead.flags
    assert FLAG_MEI not in outro.flags and FLAG_MEI not in sem_dado.flags


def test_unidades_contam_so_ativas():
    estabelecimentos = [
        {"matriz_filial_codigo": "1", "situacao_cadastral": "02"},
        {"matriz_filial_codigo": "2", "situacao_cadastral": "02"},
        {"matriz_filial_codigo": "2", "situacao_cadastral": "08"},  # filial baixada
    ]
    lead = _lead(estabelecimentos=estabelecimentos)
    assert unidades_ativas(lead.detalhe) == 2
    aplicar_sinais([lead])
    assert FLAG_MULTIUNIDADE in lead.flags


def test_uma_unidade_nao_e_multiunidade():
    lead = _lead(estabelecimentos=[])
    assert unidades_ativas(lead.detalhe) == 1
    aplicar_sinais([lead])
    assert FLAG_MULTIUNIDADE not in lead.flags


@pytest.mark.parametrize(
    "abertura, hoje, anos",
    [
        ("2015-03-10", date(2026, 3, 9), 10),  # véspera do aniversário
        ("2015-03-10", date(2026, 3, 10), 11),
        ("2026-09-01", date(2026, 11, 3), 0),
        (None, date(2026, 11, 3), None),
    ],
)
def test_anos_de_mercado(abertura, hoje, anos):
    lead = _lead(matriz={"data_inicio_atividade": abertura})
    assert anos_de_mercado(lead.detalhe, hoje=hoje) == anos


def test_aplicar_sinais_e_idempotente():
    lead = _lead(telefones=[_tel("99876543")], simples={"opcao_mei": True})
    aplicar_sinais([lead])
    aplicar_sinais([lead])
    assert lead.flags.count(FLAG_WHATSAPP) == 1 and lead.flags.count(FLAG_MEI) == 1


def test_whatsapp_cadastral_soma_no_score_de_contato():
    com = _lead(telefones=[_tel("99876543")])
    sem = _lead("22222222000102", telefones=[_tel("32345678")])
    aplicar_sinais([com, sem])
    assert compute_score(com).qualidade_contato - compute_score(sem).qualidade_contato == 6.0


def test_export_traz_celular_unidades_e_anos():
    lead = _lead(telefones=[_tel("99876543")])
    aplicar_sinais([lead])
    saida = build_lead_json(lead, compute_score(lead))
    assert saida["telefone_cadastral_celular"] == "+5519999876543"
    assert saida["unidades"] == 1
    assert isinstance(saida["anos_de_mercado"], int)
    assert saida["proveniencia"]["telefone_cadastral_celular"]["fonte"] == "basecnpj"


def test_export_sem_celular_nao_traz_o_campo():
    lead = _lead()
    aplicar_sinais([lead])
    assert "telefone_cadastral_celular" not in build_lead_json(lead, compute_score(lead))
