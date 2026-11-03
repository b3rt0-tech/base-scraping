"""[10] Site candidato automático: o domínio próprio do e-mail cadastral vira URL candidata."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from scripts import pipeline_cli
from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline.sinais import site_do_email_cadastral
from scripts.pipeline.validate import ValidacaoResult
from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, cnpj_handler, make_cnpj_raw

runner = CliRunner()
SITE = "https://sorriso.com.br"
PAGINA = "<html><body><p>Clínica Sorriso — CNPJ 12.345.678/0001-95 — ana@sorriso.com.br</p>" + "<p>texto</p>" * 30


def _validacao(email: str | None, flags: list[str] | None = None) -> ValidacaoResult:
    raw = make_cnpj_raw(matriz={"email": email})
    return ValidacaoResult(
        cnpj=CNPJ_PADRAO, status="validado", detalhe=CNPJDetalhe.model_validate(raw), flags=flags or []
    )


@pytest.mark.parametrize(
    "email, esperado",
    [
        ("contato@sorriso.com.br", "https://sorriso.com.br/"),
        ("  Ana@Sorriso.COM.br ", "https://sorriso.com.br/"),
        ("dono@gmail.com", None),  # provedor genérico
        ("dono@gmail.com.br", None),  # erro de digitação comum no cadastro
        ("x@mailinator.com", None),  # descartável
        ("x@silvacontabil.com.br", None),  # escritório de contabilidade
        ("x@escritorioexemplo.cnt.br", None),
        (None, None),
        ("sem-arroba", None),
    ],
)
def test_site_do_email_cadastral(email, esperado):
    assert site_do_email_cadastral(_validacao(email)) == esperado


def test_lead_marcado_como_contador_nao_gera_site():
    assert site_do_email_cadastral(_validacao("contato@sorriso.com.br", ["contato_de_contador"])) is None


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(pipeline_cli, "SITE_AUTOMATICO", True)


def _api(api, raw=None):
    return api(
        {
            "status": {"status": "ok"},
            "avancada": {"items": [{"cnpj_completo": CNPJ_PADRAO}], "total_pages": 1},
            "cnpj": cnpj_handler({CNPJ_PADRAO: raw or make_cnpj_raw()}),
        }
    )


def _prospectar(tmp_path, *extra) -> dict:
    resultado = runner.invoke(
        app, ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    )
    assert resultado.exit_code == 0, resultado.stdout
    return json.loads(resultado.stdout)["leads"][0]


def test_dominio_do_email_e_raspado_e_marca_a_origem(api, respx_mock, tmp_path, ligado):
    _api(api)
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(200, text=PAGINA))

    lead = _prospectar(tmp_path)

    assert lead["site"].startswith(SITE)
    assert lead["site_origem"] == "email_cadastral"
    assert lead["enriquecimento"]["status"] == "ok"
    assert any("site oficial confirmado" in m for m in lead["motivos"])


def test_site_automatico_que_nao_responde_some_sem_alerta(api, respx_mock, tmp_path, ligado):
    _api(api)
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(500))

    lead = _prospectar(tmp_path)

    assert "site" not in lead and "enriquecimento" not in lead
    assert not any("site" in a for a in lead.get("alertas", []))


def test_site_automatico_de_outra_empresa_some_sem_alerta(api, respx_mock, tmp_path, ligado):
    _api(api)
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(200, text="<p>Outra empresa qualquer</p>" * 30))

    lead = _prospectar(tmp_path)

    assert "site" not in lead and "enriquecimento" not in lead


def test_sites_json_tem_prioridade(api, respx_mock, tmp_path, ligado):
    _api(api)
    outro = "https://clinicasorriso.com.br"
    respx_mock.get(f"{outro}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{outro}/").mock(return_value=httpx.Response(200, text=PAGINA))
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({CNPJ_PADRAO: f"{outro}/"}))

    lead = _prospectar(tmp_path, "--sites-json", str(sites))

    assert lead["site"].startswith(outro)
    assert lead["site_origem"] == "sites_json"


def test_email_generico_nao_dispara_scraping(api, respx_mock, tmp_path, ligado):
    _api(api, make_cnpj_raw(matriz={"email": "dono@gmail.com"}))
    lead = _prospectar(tmp_path)
    assert "site" not in lead and "enriquecimento" not in lead
