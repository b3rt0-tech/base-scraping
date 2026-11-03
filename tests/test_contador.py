from __future__ import annotations

import pytest

from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline.contador import FLAG_CONTADOR, marcar_contadores, sinal_email_contabil
from scripts.pipeline.score import compute_score
from scripts.pipeline.validate import ValidacaoResult
from tests.conftest import make_cnpj_raw


def _lead(cnpj: str, email: str | None, telefone: tuple[str, str] | None = None) -> ValidacaoResult:
    raw = make_cnpj_raw(cnpj_completo=cnpj, matriz={"email": email})
    raw["telefones"] = [{"ddd": telefone[0], "numero": telefone[1]}] if telefone else []
    flags = ["contato_cadastral_disponivel"] if (email or telefone) else []
    return ValidacaoResult(
        cnpj=cnpj, status="validado", detalhe=CNPJDetalhe.model_validate(raw), flags=flags, cnae_match="principal"
    )


@pytest.mark.parametrize(
    "email",
    [
        "contato@silvacontabil.com.br",
        "fulano.contabil@gmail.com",
        "meucnpj@contabilonline.com.br",
        "beltranocontabilidade@gmail.com",
        "joao@escritorioexemplo.cnt.br",
        "societario@exemploassessoria.com.br",
        "JOAO.CONTADOR@HOTMAIL.COM",
    ],
)
def test_email_com_palavra_contabil(email):
    assert sinal_email_contabil(email)


@pytest.mark.parametrize(
    "email",
    [
        "contato@redeodonto.com.br",
        "odontologiaconsultorio123@gmail.com",  # "consultorio" não é contabilidade
        "maria@exemploconsultoria.com.br",
        "maria@gmail.com",
        "",
        None,
        "sem-arroba",
    ],
)
def test_email_normal_nao_e_contador(email):
    assert not sinal_email_contabil(email)


def test_marca_dominio_contabil_sozinho():
    leads = [_lead("11111111000101", "x@fulanocontabil.com.br"), _lead("22222222000102", "dr@clinica.com.br")]
    assert marcar_contadores(leads) == 1
    assert FLAG_CONTADOR in leads[0].flags
    assert FLAG_CONTADOR not in leads[1].flags


def test_email_generico_repetido_entre_empresas_diferentes():
    leads = [_lead("11111111000101", "fulana@terra.com.br"), _lead("22222222000102", "fulana@terra.com.br")]
    assert marcar_contadores(leads) == 2


def test_telefone_repetido_entre_empresas_diferentes():
    leads = [
        _lead("11111111000101", "a@clinica1.com.br", ("19", "33330000")),
        _lead("22222222000102", "b@clinica2.com.br", ("19", "33330000")),
        _lead("33333333000103", "c@clinica3.com.br", ("19", "99999999")),
    ]
    assert marcar_contadores(leads) == 2
    assert FLAG_CONTADOR not in leads[2].flags


def test_filiais_da_mesma_raiz_nao_contam_como_repeticao():
    leads = [_lead("11111111000101", "dono@gmail.com"), _lead("11111111000284", "dono@gmail.com")]
    assert marcar_contadores(leads) == 0


def test_dominio_proprio_repetido_e_rede_nao_contador():
    leads = [
        _lead("11111111000101", "loja@redeodonto.com.br", ("19", "33330000")),
        _lead("22222222000102", "loja@redeodonto.com.br", ("19", "33330000")),
    ]
    assert marcar_contadores(leads) == 0


def test_idempotente():
    leads = [_lead("11111111000101", "x@fulanocontabil.com.br")]
    marcar_contadores(leads)
    assert marcar_contadores(leads) == 0
    assert leads[0].flags.count(FLAG_CONTADOR) == 1


def test_score_nao_da_bonus_cadastral_para_contador():
    normal = _lead("11111111000101", "dr@clinica.com.br")
    contador = _lead("22222222000102", "x@fulanocontabil.com.br")
    marcar_contadores([normal, contador])
    assert compute_score(normal).qualidade_contato == 8.0
    assert compute_score(contador).qualidade_contato == 0.0
