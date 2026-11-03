"""[05] O teto de 100 vale para leads validados: descartes não encolhem a entrega."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scripts import pipeline_cli
from scripts.pipeline import discovery
from scripts.pipeline_cli import app

from .conftest import cnpj_handler, make_cnpj_raw, ok

runner = CliRunner()

ATIVO = make_cnpj_raw()
BAIXADO = make_cnpj_raw(matriz={"situacao_cadastral": "08"})


def _cnpj(i: int) -> str:
    return f"{i:08d}000100"


def _api_paginada(api, paginas: list[list[tuple[int, dict]]]):
    """Páginas de `avancada` com (id, payload) — o payload decide se o lead valida ou é descartado."""

    def avancada(request):
        page = int(request.url.params["page"])
        itens = paginas[page - 1] if page <= len(paginas) else []
        return ok({"items": [{"cnpj_completo": _cnpj(i)} for i, _ in itens], "total_pages": len(paginas)})

    payloads = {_cnpj(i): raw for pagina in paginas for i, raw in pagina}
    return api({"status": {"status": "ok"}, "avancada": avancada, "cnpj": cnpj_handler(payloads)})


def _paginas_avancada(chamadas) -> list[int]:
    return [int(c.url.params["page"]) for c in chamadas if c.url.params.get("route") == "avancada"]


def _descobrir(tmp_path, *extra: str) -> dict:
    args = ["descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    resultado = runner.invoke(app, args)
    assert resultado.exit_code == 0, resultado.stdout
    return json.loads(resultado.stdout)


@pytest.fixture
def teto_pequeno(monkeypatch):
    monkeypatch.setattr(pipeline_cli, "TARGET_LEADS", 3)
    monkeypatch.setattr(discovery, "MAX_DESCOBERTOS", 50)


def test_descartes_puxam_mais_paginas_ate_o_teto_de_validados(api, tmp_path, teto_pequeno):
    chamadas = _api_paginada(
        api,
        [
            [(1, ATIVO), (2, BAIXADO), (3, BAIXADO)],
            [(4, ATIVO), (5, ATIVO), (6, ATIVO)],
            [(7, ATIVO)],
        ],
    )
    saida = _descobrir(tmp_path)

    assert saida["total"] == saida["validados"] == 3
    assert saida["descobertos"] == 5  # só busca o que falta: 2 da página 2, não a página inteira
    assert saida["esgotou_busca"] is False
    assert len(saida["descartados"]) == 2
    assert _paginas_avancada(chamadas) == [1, 2]  # a página 3 nunca é pedida


def test_busca_esgotada_entrega_o_que_houver_e_avisa(api, tmp_path, teto_pequeno):
    _api_paginada(api, [[(1, ATIVO), (2, BAIXADO), (3, BAIXADO)], [(4, BAIXADO)]])
    saida = _descobrir(tmp_path)

    assert saida["validados"] == 1
    assert saida["descobertos"] == 4
    assert saida["esgotou_busca"] is True


def test_teto_de_seguranca_de_descobertos(api, tmp_path, teto_pequeno, monkeypatch):
    monkeypatch.setattr(discovery, "MAX_DESCOBERTOS", 4)
    paginas = [[(p * 3 + k, BAIXADO) for k in range(3)] for p in range(10)]
    chamadas = _api_paginada(api, paginas)
    saida = _descobrir(tmp_path)

    assert saida["validados"] == 0
    assert saida["descobertos"] == 4
    assert saida["esgotou_busca"] is False
    assert _paginas_avancada(chamadas) == [1, 2]


def test_retomada_com_teto_atingido_nao_chama_a_busca(api, tmp_path, teto_pequeno):
    chamadas = _api_paginada(api, [[(1, ATIVO), (2, ATIVO), (3, ATIVO)], [(4, ATIVO)]])
    _descobrir(tmp_path)
    antes = len(_paginas_avancada(chamadas))
    saida = _descobrir(tmp_path)

    assert saida["validados"] == 3
    assert len(_paginas_avancada(chamadas)) == antes


def test_retomada_nao_rebaixa_lead_ja_validado(api, tmp_path, teto_pequeno):
    """Ao reler a página do checkpoint, um CNPJ já validado não pode voltar a "descoberto"."""
    _api_paginada(api, [[(1, ATIVO), (2, ATIVO), (3, ATIVO)], [(4, ATIVO), (5, ATIVO)]])
    _descobrir(tmp_path)
    saida = _descobrir(tmp_path, "--continuar")

    assert saida["lote"] == 2
    assert [lead["cnpj"] for lead in saida["leads"]] == [_cnpj(4), _cnpj(5)]
    assert saida["descobertos"] == 5


# --- [06] --continuar --------------------------------------------------------


def _cnpjs(saida: dict) -> list[str]:
    return [lead["cnpj"] for lead in saida["leads"]]


def test_continuar_entrega_lotes_sem_repetir_cnpj(api, tmp_path, teto_pequeno):
    _api_paginada(api, [[(i, ATIVO) for i in range(p * 3, p * 3 + 3)] for p in range(3)])

    lote1 = _descobrir(tmp_path)
    lote2 = _descobrir(tmp_path, "--continuar")
    lote3 = _descobrir(tmp_path, "--continuar")

    assert [lote1["lote"], lote2["lote"], lote3["lote"]] == [1, 2, 3]
    todos = _cnpjs(lote1) + _cnpjs(lote2) + _cnpjs(lote3)
    assert len(todos) == 9 and len(set(todos)) == 9
    assert lote3["esgotou_busca"] is True


def test_sem_continuar_repete_o_lote_1(api, tmp_path, teto_pequeno):
    chamadas = _api_paginada(api, [[(1, ATIVO), (2, ATIVO), (3, ATIVO)], [(4, ATIVO)]])
    lote1 = _descobrir(tmp_path)
    _descobrir(tmp_path, "--continuar")
    antes = len(_paginas_avancada(chamadas))

    de_novo = _descobrir(tmp_path)

    assert de_novo["lote"] == 1
    assert _cnpjs(de_novo) == _cnpjs(lote1)
    assert len(_paginas_avancada(chamadas)) == antes


def test_continuar_com_busca_esgotada_devolve_lote_vazio(api, tmp_path, teto_pequeno):
    _api_paginada(api, [[(1, ATIVO), (2, ATIVO)]])
    _descobrir(tmp_path)
    saida = _descobrir(tmp_path, "--continuar")

    assert saida["lote"] == 2
    assert saida["total"] == 0
    assert saida["esgotou_busca"] is True


def test_banco_antigo_sem_coluna_lote_e_migrado(tmp_path):
    import sqlite3

    from scripts.pipeline import db

    caminho = tmp_path / "antigo.sqlite3"
    antigo = sqlite3.connect(caminho)
    antigo.executescript(
        """
        CREATE TABLE leads (run_id TEXT NOT NULL, cnpj TEXT NOT NULL, status TEXT NOT NULL, discovery_json TEXT,
            detalhe_json TEXT, discard_reason TEXT, updated_at REAL NOT NULL, PRIMARY KEY (run_id, cnpj));
        INSERT INTO leads VALUES ('r', '1', 'validado', NULL, NULL, NULL, 0);
        """
    )
    antigo.commit()
    antigo.close()

    conn = db.connect(caminho)
    assert db.lote_atual(conn, "r") == 0
    db.atribuir_lote(conn, "r", 1, 100)
    assert [lead["cnpj"] for lead in db.list_lote(conn, "r", 1)] == ["1"]
