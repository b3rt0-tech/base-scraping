from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, OUTRO_CNPJ, cnpj_handler, erro, make_cnpj_raw

runner = CliRunner()

def _rodar(*args: str) -> tuple[int, dict]:
    resultado = runner.invoke(app, list(args))
    return resultado.exit_code, json.loads(resultado.stdout)


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


SITES_INVALIDOS = [
    ("nao-existe.json", None, "não foi possível ler"),
    ("quebrado.json", "{nao é json", "não foi possível ler"),
    ("lista.json", json.dumps([CNPJ_PADRAO]), "deve ser um objeto"),
    ("chave.json", json.dumps({"abc": "https://x.com.br"}), "não é um CNPJ"),
    ("curto.json", json.dumps({"123": "https://x.com.br"}), "não é um CNPJ"),
    ("valor.json", json.dumps({CNPJ_PADRAO: 42}), "precisa começar com http"),
    ("semesquema.json", json.dumps({CNPJ_PADRAO: "sorriso.com.br"}), "precisa começar com http"),
]


@pytest.mark.parametrize("comando", ["prospectar", "validar-lista"])
@pytest.mark.parametrize("nome, conteudo, trecho", SITES_INVALIDOS)
def test_sites_json_invalido_vira_erro_json(api, tmp_path, comando, nome, conteudo, trecho):
    chamadas = api({})
    sites = tmp_path / nome
    if conteudo is not None:
        sites.write_text(conteudo)
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": CNPJ_PADRAO}]))
    args = ["prospectar", "dentista"] if comando == "prospectar" else ["validar-lista", "--arquivo", str(lista)]
    codigo, saida = _rodar(*args, "--sites-json", str(sites))
    assert codigo == 1
    assert saida["erro"] == "entrada_invalida"
    assert trecho in saida["mensagem"]
    assert chamadas == []  # falha antes de qualquer chamada à API


def test_sites_json_aceita_cnpj_formatado(api_dentista, respx_mock, tmp_path):
    site = "https://sorriso.com.br"
    respx_mock.get(f"{site}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{site}/").mock(
        return_value=httpx.Response(200, text="<p>CNPJ 12.345.678/0001-95 ana@sorriso.com.br</p>" + "texto " * 50)
    )
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"12.345.678/0001-95": f" {site}/ "}))

    codigo, saida = _rodar(
        "prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), "--sites-json", str(sites)
    )
    assert codigo == 0
    assert saida["leads"][0]["site"] == f"{site}/"
    assert saida["leads"][0]["enriquecimento"]["status"] == "ok"


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


@pytest.mark.parametrize(
    "args",
    [
        ["descobrir", "dentista"],
        ["prospectar", "dentista"],
        ["validar-lista", "--arquivo", "{lista}"],
    ],
)
def test_token_ausente_e_diferente_de_token_invalido(api, monkeypatch, tmp_path, args):
    chamadas = api({"status": {"status": "ok"}})
    monkeypatch.delenv("BASECNPJ_TOKEN")
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": CNPJ_PADRAO}]))
    codigo, saida = _rodar(*[a.format(lista=lista) for a in args])
    assert codigo == 1
    assert saida["erro"] == "token_ausente"
    assert ".env" in saida["mensagem"]
    assert chamadas == []  # nem o preflight público é chamado


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
            "cnpj": cnpj_handler({CNPJ_PADRAO: make_cnpj_raw(), OUTRO_CNPJ: erro(404, "not_found")}),
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
