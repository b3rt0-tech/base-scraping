from __future__ import annotations

import json
import logging

import httpx
from typer.testing import CliRunner

from scripts.basecnpj import client
from scripts.pipeline_cli import app
from scripts.scraping import http as scraping_http

from .conftest import CNPJ_PADRAO, OUTRO_CNPJ, cnpj_handler, erro, make_cnpj_raw, ok

runner = CliRunner()
SITE = "https://sorriso.com.br"


def _rodar(*args: str):
    return runner.invoke(app, list(args))


def test_stdout_continua_sendo_um_unico_json_com_logs_ligados(api_dentista, tmp_path):
    resultado = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"))
    assert resultado.exit_code == 0
    saida = json.loads(resultado.stdout)  # falharia se qualquer log vazasse para o stdout
    assert saida["total"] == 1
    for marco in ("S0 preflight ok", "S1 CNAE 8630504: página 1/1", "S3 pronto: 1 validado", "pronto: 1 lead"):
        assert marco in resultado.stderr, marco
    assert resultado.stderr.count("\n") >= 4


def test_progresso_da_validacao_vai_para_o_stderr(api_dentista, tmp_path):
    resultado = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"))
    assert "S3 validação: 2/2" in resultado.stderr


def test_verbose_mostra_detalhe_por_lead_e_o_padrao_nao(api_dentista, tmp_path):
    padrao = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "a.sqlite3"))
    detalhado = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "b.sqlite3"), "-v")
    assert f"S3 1/2 {CNPJ_PADRAO} → validado" not in padrao.stderr
    assert f"S3 1/2 {CNPJ_PADRAO} → validado" in detalhado.stderr
    assert f"S3 2/2 {OUTRO_CNPJ} → descartado situacao_cadastral=08" in detalhado.stderr
    json.loads(detalhado.stdout)


def test_quiet_silencia_o_progresso_mas_nao_o_stdout(api_dentista, tmp_path):
    resultado = _rodar("descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), "--quiet")
    assert resultado.stderr == ""
    assert json.loads(resultado.stdout)["total"] == 1


def test_verbose_e_quiet_juntos_sao_recusados(api):
    chamadas = api({})
    resultado = _rodar("descobrir", "dentista", "--verbose", "--quiet")
    assert resultado.exit_code == 1
    assert json.loads(resultado.stdout)["erro"] == "opcoes_invalidas"
    assert chamadas == []


def test_erro_de_negocio_continua_json_no_stdout_com_logs_ligados(api):
    api({"status": {"status": "ok"}, "avancada": erro(401, "unauthorized")})
    resultado = _rodar("descobrir", "dentista", "--uf", "SP")
    assert resultado.exit_code == 1
    assert json.loads(resultado.stdout)["erro"] == "token_invalido"


def test_enriquecimento_loga_progresso_e_avisa_falha_por_lead(api_dentista, respx_mock, tmp_path):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(404))
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({CNPJ_PADRAO: f"{SITE}/"}))

    resultado = _rodar(
        "prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), "--sites-json", str(sites)
    )

    assert "S4 enriquecimento: 1 lead(s) com site candidato" in resultado.stderr
    assert f"WARNING S4 1/1 {CNPJ_PADRAO} → falha (http_404)" in resultado.stderr
    assert json.loads(resultado.stdout)["leads"][0]["enriquecimento"]["status"] == "falha"


def test_sem_sites_json_avisa_que_s4_foi_pulado(api_dentista, tmp_path):
    resultado = _rodar("prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"))
    assert "S4 pulado" in resultado.stderr


def test_validar_lista_loga_cada_item(api, tmp_path):
    api(
        {
            "status": {"status": "ok"},
            "cnpj": cnpj_handler({CNPJ_PADRAO: make_cnpj_raw()}),
        }
    )
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": CNPJ_PADRAO}, {"cnpj": "123"}]))
    resultado = _rodar("validar-lista", "--arquivo", str(lista))
    assert f"S3 1/2 {CNPJ_PADRAO} → validado" in resultado.stderr
    assert "WARNING S2 2/2 123 → CNPJ inválido" in resultado.stderr
    assert json.loads(resultado.stdout)["total"] == 1


async def test_retry_do_scraping_avisa_no_log(respx_mock, caplog):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    with caplog.at_level(logging.WARNING, logger="base_scraping"):
        await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
    assert any("scraping: tentativa 1 falhou" in r.message and "http_503" in r.message for r in caplog.records)


async def test_retry_do_basecnpj_avisa_no_log(api, caplog):
    respostas = iter([erro(500, "server_error"), ok({"fim": True})])
    api({"cnpj": lambda req: next(respostas)})
    with caplog.at_level(logging.WARNING, logger="base_scraping"):
        assert await client.request("cnpj", {"cnpj": "1"}) == {"fim": True}
    assert any("BaseCNPJ: tentativa 1 falhou" in r.message for r in caplog.records)
