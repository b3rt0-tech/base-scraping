"""[15] Consultor de vendas: o veredito do Claude (`--qualificacoes`) entra no lead, sem remover nada."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts import pipeline_cli
from scripts.pipeline.qualificacao import QualificacaoInvalida, carregar_qualificacoes
from scripts.pipeline_cli import app

from .conftest import cnpj_handler, make_cnpj_raw, ok

runner = CliRunner()
RAIZ = Path(__file__).resolve().parents[1]


def _cnpj(i: int) -> str:
    return f"{i:08d}000100"


def _escrever(tmp_path, conteudo) -> Path:
    arquivo = tmp_path / "qualificacoes.json"
    arquivo.write_text(json.dumps(conteudo, ensure_ascii=False))
    return arquivo


def test_carrega_e_normaliza_cnpj(tmp_path):
    arquivo = _escrever(tmp_path, {"00.000.001/0001-00": {"veredito": "valido", "motivo": " tem 3 unidades "}})
    assert carregar_qualificacoes(arquivo) == {_cnpj(1): {"veredito": "valido", "motivo": "tem 3 unidades"}}


@pytest.mark.parametrize(
    "conteudo, trecho",
    [
        ({_cnpj(1): {"veredito": "otimo", "motivo": "x"}}, "veredito"),
        ({_cnpj(1): {"veredito": "valido", "motivo": "  "}}, "motivo"),
        ({_cnpj(1): {"veredito": "valido"}}, "motivo"),
        ({_cnpj(1): {"veredito": "valido", "motivo": "x", "nota": 10}}, "nota"),
        ({"123": {"veredito": "valido", "motivo": "x"}}, "não é um CNPJ"),
        ([{"veredito": "valido"}], "list"),
    ],
)
def test_qualificacao_invalida(tmp_path, conteudo, trecho):
    with pytest.raises(QualificacaoInvalida, match=trecho):
        carregar_qualificacoes(_escrever(tmp_path, conteudo))


def _api(api, n=3):
    cnpjs = [_cnpj(i) for i in range(1, n + 1)]
    return api(
        {
            "status": {"status": "ok"},
            "avancada": lambda request: ok({"items": [{"cnpj_completo": c} for c in cnpjs], "total_pages": 1}),
            "cnpj": cnpj_handler({c: make_cnpj_raw() for c in cnpjs}),
        }
    )


def _prospectar(tmp_path, *extra):
    resultado = runner.invoke(
        app, ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    )
    return resultado.exit_code, json.loads(resultado.stdout)


def test_veredito_ordena_e_descartado_continua_visivel(api, tmp_path):
    _api(api)
    arquivo = _escrever(
        tmp_path,
        {
            _cnpj(1): {"veredito": "descartar", "motivo": "MEI para ticket alto"},
            _cnpj(2): {"veredito": "valido", "motivo": "3 unidades", "abordagem": "WhatsApp", "mensagem": "Oi, Ana!"},
        },
    )
    codigo, saida = _prospectar(tmp_path, "--qualificacoes", str(arquivo))

    assert codigo == 0
    ordem = [(lead["cnpj"], (lead.get("qualificacao") or {}).get("veredito")) for lead in saida["leads"]]
    assert ordem == [(_cnpj(2), "valido"), (_cnpj(3), None), (_cnpj(1), "descartar")]  # sem veredito = talvez
    assert saida["total"] == 3  # recomenda, nunca remove
    assert saida["resumo"]["por_veredito"] == {"valido": 1, "talvez": 0, "descartar": 1}
    valido = saida["leads"][0]
    assert valido["qualificacao"]["mensagem"] == "Oi, Ana!"
    assert valido["proveniencia"]["qualificacao"]["fonte"] == "consultor"


def test_cnpj_de_outra_consulta_gera_aviso(api, tmp_path):
    _api(api, n=1)
    arquivo = _escrever(tmp_path, {_cnpj(9): {"veredito": "valido", "motivo": "x"}})
    _, saida = _prospectar(tmp_path, "--qualificacoes", str(arquivo))
    assert "1 CNPJ(s) do --qualificacoes não estão entre os leads" in saida["aviso"]


def test_sem_qualificacoes_nao_ha_por_veredito(api, tmp_path):
    _api(api, n=1)
    _, saida = _prospectar(tmp_path)
    assert "por_veredito" not in saida["resumo"] and "qualificacao" not in saida["leads"][0]


def test_qualificacao_invalida_e_erro_json(api, tmp_path):
    arquivo = _escrever(tmp_path, {_cnpj(1): {"veredito": "otimo", "motivo": "x"}})
    codigo, saida = _prospectar(tmp_path, "--qualificacoes", str(arquivo))
    assert codigo == 1 and saida["erro"] == "entrada_invalida"


def test_veredito_e_mensagem_vao_para_a_planilha(api, tmp_path):
    _api(api, n=1)
    arquivo = _escrever(tmp_path, {_cnpj(1): {"veredito": "valido", "motivo": "m", "mensagem": "Olá, tudo bem?"}})
    destino = tmp_path / "leads.csv"
    _prospectar(tmp_path, "--qualificacoes", str(arquivo), "--formato", "csv", "--saida", str(destino))
    cabecalho, linha = list(csv.reader(destino.open(encoding="utf-8-sig")))[:2]
    valores = dict(zip(cabecalho, linha, strict=True))
    assert valores["Veredito"] == "valido" and valores["1ª mensagem"] == "Olá, tudo bem?"


def test_validar_lista_aceita_qualificacoes(api, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_cli, "SITE_AUTOMATICO", False)
    api({"status": {"status": "ok"}, "cnpj": cnpj_handler({_cnpj(1): make_cnpj_raw()})})
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": _cnpj(1)}]))
    arquivo = _escrever(tmp_path, {_cnpj(1): {"veredito": "talvez", "motivo": "CNAE secundário"}})
    resultado = runner.invoke(app, ["validar-lista", "--arquivo", str(lista), "--qualificacoes", str(arquivo)])
    assert json.loads(resultado.stdout)["leads"][0]["qualificacao"]["veredito"] == "talvez"


def test_consultoria_documenta_os_tres_vereditos_e_a_regra_de_nao_remover():
    texto = (RAIZ / "references" / "consultoria.md").read_text(encoding="utf-8")
    for termo in ("`valido`", "`talvez`", "`descartar`", "Recomende, nunca remova", "--qualificacoes", "1ª mensagem"):
        assert termo in texto, termo
    skill = (RAIZ / "SKILL.md").read_text(encoding="utf-8")
    assert "references/consultoria.md" in skill and "consultor de vendas" in skill
