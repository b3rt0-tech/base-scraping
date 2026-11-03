from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, erro, make_cnpj_raw, ok

runner = CliRunner()

OUTRO_CNPJ = "98765432000110"


def _cnpj_handler(por_cnpj: dict[str, dict | httpx.Response]):
    def handler(request):
        resposta = por_cnpj[request.url.params["cnpj"]]
        return resposta if isinstance(resposta, httpx.Response) else ok(resposta)

    return handler


def _rodar(*args: str) -> tuple[int, dict]:
    resultado = runner.invoke(app, list(args))
    return resultado.exit_code, json.loads(resultado.stdout)


@pytest.fixture
def api_dentista(api):
    return api(
        {
            "status": {"status": "ok"},
            "avancada": {"items": [{"cnpj_completo": CNPJ_PADRAO}, {"cnpj_completo": OUTRO_CNPJ}], "total_pages": 1},
            "cnpj": _cnpj_handler(
                {CNPJ_PADRAO: make_cnpj_raw(), OUTRO_CNPJ: make_cnpj_raw(matriz={"situacao_cadastral": "08"})}
            ),
        }
    )


def test_descobrir(api_dentista, tmp_path):
    codigo, saida = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"))
    assert codigo == 0
    assert saida["total"] == 1
    assert saida["icp"] == {
        "nicho": "dentista",
        "entrada_dicionario": "dentista, clínica odontológica",
        "cnaes": ["8630504"],
    }
    assert saida["leads"] == [{"cnpj": CNPJ_PADRAO, "razao_social": "CLINICA SORRISO LTDA"}]
    assert saida["descartados"] == [{"cnpj": OUTRO_CNPJ, "motivo": "situacao_cadastral=08 (esperado 02)"}]


def test_descobrir_retomada_nao_refaz_chamadas(api_dentista, tmp_path):
    db_path = str(tmp_path / "p.sqlite3")
    _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", db_path)
    chamadas_primeira = len(api_dentista)
    codigo, saida = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", db_path)
    assert codigo == 0 and saida["total"] == 1
    assert len(api_dentista) - chamadas_primeira == 1  # só o preflight (status nunca é cacheado)


def test_prospectar_com_sites(api_dentista, respx_mock, tmp_path):
    site = "https://sorriso.com.br"
    respx_mock.get(f"{site}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{site}/").mock(
        return_value=httpx.Response(200, text="<p>CNPJ 12.345.678/0001-95 ana@sorriso.com.br</p>" + "texto " * 50)
    )
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({CNPJ_PADRAO: f"{site}/"}))

    codigo, saida = _rodar(
        "prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), "--sites-json", str(sites)
    )
    assert codigo == 0
    lead = saida["leads"][0]
    assert lead["site"] == f"{site}/"
    assert lead["enriquecimento"]["status"] == "ok"
    assert lead["emails"][0]["valor"] == "ana@sorriso.com.br"
    assert lead["score"] == lead["score_breakdown"]["total"]
    assert len(saida["descartados"]) == 1


def test_nicho_desconhecido_nao_chama_api(api):
    chamadas = api({})
    codigo, saida = _rodar("descobrir", "fábrica de foguetes")
    assert codigo == 1
    assert saida["erro"] == "nicho_nao_encontrado"
    assert chamadas == []


def test_nicho_ambiguo_nao_chama_api(api):
    chamadas = api({})
    codigo, saida = _rodar("descobrir", "loja")
    assert codigo == 1
    assert saida["erro"] == "nicho_ambiguo"
    assert "loja de roupas" in saida["mensagem"] and "ótica" in saida["mensagem"]
    assert chamadas == []


def test_prospectar_tambem_informa_o_icp(api_dentista, tmp_path):
    codigo, saida = _rodar("prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"))
    assert codigo == 0
    assert saida["icp"]["cnaes"] == ["8630504"]


def test_token_invalido(api):
    api({"status": {"status": "ok"}, "avancada": erro(401, "unauthorized")})
    codigo, saida = _rodar("descobrir", "dentista", "--uf", "SP")
    assert codigo == 1
    assert saida["erro"] == "token_invalido"


def test_preflight_falhou(api):
    api({"status": {"status": "degradado"}})
    codigo, saida = _rodar("descobrir", "dentista")
    assert codigo == 1
    assert saida["erro"] == "preflight_falhou"


def test_validar_lista(api, tmp_path):
    api(
        {
            "status": {"status": "ok"},
            "sugestao": {"items": [{"cnpj_completo": OUTRO_CNPJ}]},
            "cnpj": _cnpj_handler({CNPJ_PADRAO: make_cnpj_raw(), OUTRO_CNPJ: erro(404, "not_found")}),
        }
    )
    lista = tmp_path / "lista.json"
    lista.write_text(
        json.dumps(
            [
                {"cnpj": "12.345.678/0001-95"},
                {"cnpj": CNPJ_PADRAO},  # duplicado
                {"cnpj": "123"},
                {"nome": "Empresa Sumida"},
            ]
        )
    )
    codigo, saida = _rodar("validar-lista", "--arquivo", str(lista))
    assert codigo == 0
    assert [lead["cnpj"] for lead in saida["leads"]] == [CNPJ_PADRAO]
    assert saida["descartados"] == [{"cnpj": OUTRO_CNPJ, "motivo": "cnpj nao encontrado no BaseCNPJ (404)"}]
    assert saida["nao_resolvidos"] == [{"cnpj": "123", "motivo": "cnpj inválido (não tem 14 dígitos)"}]
    assert "aviso" not in saida


def test_validar_lista_acima_do_teto_avisa(api, tmp_path):
    api({"status": {"status": "ok"}})
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": "1"}] * 105))
    codigo, saida = _rodar("validar-lista", "--arquivo", str(lista))
    assert codigo == 0
    assert saida["aviso"].startswith("5 item(ns)")


@pytest.mark.parametrize("conteudo", ["{nao json", json.dumps({"cnpj": "1"}), json.dumps([{"x": 1}])])
def test_validar_lista_entrada_invalida(tmp_path, conteudo):
    lista = tmp_path / "lista.json"
    lista.write_text(conteudo)
    codigo, saida = _rodar("validar-lista", "--arquivo", str(lista))
    assert codigo == 1
    assert saida["erro"] == "entrada_invalida"
