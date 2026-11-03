"""Snapshots da saída dos 3 comandos: qualquer mudança no contrato aparece como diff em `tests/snapshots/`.

Para regenerar de propósito: `SNAPSHOT_DIR=tests/snapshots uv run pytest tests/test_snapshots.py` (e revisar o diff).
"""

from __future__ import annotations

import json
import os
import types
from datetime import date
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from scripts.pipeline import export as export_module
from scripts.pipeline import sinais as sinais_module
from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, OUTRO_CNPJ, cnpj_handler, erro, make_cnpj_raw

runner = CliRunner()
SNAPSHOTS = Path(__file__).parent / "snapshots"
SITE = "https://sorriso.com.br"

PAGINA = f"""<html><body>
<p>Clínica Sorriso — CNPJ 12.345.678/0001-95</p>
<p>{"Atendimento odontológico completo para toda a família. " * 4}</p>
<p>Fale com ana@sorriso.com.br ou contato@sorriso.com.br, telefone (19) 3234-5678.</p>
<a href="https://wa.me/5519999999999">WhatsApp</a> <a href="https://www.instagram.com/sorriso">Instagram</a>
<script>var x = "logo@2x.png";</script>
</body></html>"""


@pytest.fixture(autouse=True)
def relogio_fixo(monkeypatch):
    monkeypatch.setattr(export_module, "time", types.SimpleNamespace(time=lambda: 1_700_000_000.0))
    monkeypatch.setattr(sinais_module, "_hoje", lambda: date(2023, 11, 14))  # mesmo instante do relógio acima


def _conferir(nome: str, stdout: str) -> None:
    json.loads(stdout)  # sempre um único JSON válido
    regenerar = os.environ.get("SNAPSHOT_DIR")
    if regenerar:
        Path(regenerar, nome).write_text(stdout)
        return
    assert stdout == (SNAPSHOTS / nome).read_text(), f"saída mudou em relação a tests/snapshots/{nome}"


def test_snapshot_descobrir(api_dentista, tmp_path):
    resultado = runner.invoke(app, ["descobrir", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3")])
    assert resultado.exit_code == 0
    _conferir("descobrir.json", resultado.stdout)


def test_snapshot_prospectar_com_enriquecimento(api_dentista, respx_mock, tmp_path):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(200, text=PAGINA))
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({CNPJ_PADRAO: f"{SITE}/"}))

    resultado = runner.invoke(
        app,
        ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), "--sites-json", str(sites)],
    )

    assert resultado.exit_code == 0
    _conferir("prospectar.json", resultado.stdout)


def test_snapshot_validar_lista(api, tmp_path):
    api(
        {
            "status": {"status": "ok"},
            "sugestao": {"items": []},
            "busca": {"items": []},
            "cnpj": cnpj_handler({CNPJ_PADRAO: make_cnpj_raw(), OUTRO_CNPJ: erro(404, "not_found")}),
        }
    )
    lista = tmp_path / "lista.json"
    lista.write_text(
        json.dumps(
            [
                {"cnpj": "12.345.678/0001-95"},
                {"cnpj": CNPJ_PADRAO},
                {"cnpj": OUTRO_CNPJ},
                {"cnpj": "123"},
                {"nome": "Empresa Sumida"},
            ]
        )
    )

    resultado = runner.invoke(app, ["validar-lista", "--arquivo", str(lista)])

    assert resultado.exit_code == 0
    _conferir("validar_lista.json", resultado.stdout)
