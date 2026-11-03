"""[14] Aprender com resultados: registrar o desfecho, não repetir trabalhados, relatório de funil."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scripts import pipeline_cli
from scripts.pipeline import db, resultados
from scripts.pipeline.resultados import ResultadoInvalido
from scripts.pipeline_cli import app

from .conftest import cnpj_handler, make_cnpj_raw, ok

runner = CliRunner()


def _cnpj(i: int) -> str:
    return f"{i:08d}000100"


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "p.sqlite3")


# --- registrar ----------------------------------------------------------------------


def test_registrar_csv_do_crm_com_status_em_portugues(conn, tmp_path):
    arquivo = tmp_path / "resultados.csv"
    arquivo.write_text(
        "CNPJ;Status;Motivo\n"
        "00.000.001/0001-00;Reunião marcada;\n"
        "00000002000100;GANHO;\n"
        "00000003000100;sem interesse;preço\n"
        "00000004000100;talvez;\n"
        "123;perdido;\n",
        encoding="utf-8-sig",
    )
    saida = resultados.registrar(conn, arquivo)
    assert saida["registrados"] == 3
    assert saida["por_status"] == {"reuniao": 1, "ganho": 1, "perdido": 1}
    assert [i["linha"] for i in saida["ignorados"]] == [4, 5]
    assert resultados.trabalhados(conn) == {_cnpj(1), _cnpj(2), _cnpj(3)}


@pytest.mark.parametrize(
    "conteudo",
    [
        [{"cnpj": "00000001000100", "status": "nao_e_fit", "motivo": "MEI"}],
        {"00000001000100": "não é fit"},
        {"00000001000100": {"status": "Não é fit", "motivo": "MEI"}},
    ],
)
def test_registrar_json(conn, tmp_path, conteudo):
    arquivo = tmp_path / "r.json"
    arquivo.write_text(json.dumps(conteudo, ensure_ascii=False))
    assert resultados.registrar(conn, arquivo)["por_status"] == {"nao_e_fit": 1}


def test_ultimo_registro_vale(conn, tmp_path):
    arquivo = tmp_path / "r.json"
    arquivo.write_text(json.dumps({_cnpj(1): "contatado"}))
    resultados.registrar(conn, arquivo)
    arquivo.write_text(json.dumps({_cnpj(1): "ganho"}))
    resultados.registrar(conn, arquivo)
    assert resultados.relatorio(conn)["geral"]["ganho"] == 1


@pytest.mark.parametrize("nome, conteudo", [("r.txt", "x"), ("r.json", "{"), ("r.csv", "cnpj,status\n1,ganho\n")])
def test_arquivo_invalido(conn, tmp_path, nome, conteudo):
    arquivo = tmp_path / nome
    arquivo.write_text(conteudo)
    with pytest.raises(ResultadoInvalido):
        resultados.registrar(conn, arquivo)


# --- relatório ----------------------------------------------------------------------


def _entregue(cnpj, prioridade="A", porte="Micro empresa", municipio="CAMPINAS", flags=()):
    return {
        "cnpj": cnpj,
        "prioridade": prioridade,
        "score": 60,
        "cnae_principal": "8630504",
        "porte": porte,
        "endereco": {"municipio": municipio, "uf": "SP"},
        "flags": list(flags),
    }


def test_relatorio_agrega_funil_por_dimensao_e_sinal(conn, tmp_path):
    resultados.registrar_entregues(
        conn,
        [
            _entregue(_cnpj(1), "A", flags=["whatsapp_provavel"]),
            _entregue(_cnpj(2), "A", flags=["whatsapp_provavel"]),
            _entregue(_cnpj(3), "B", flags=["mei"]),
            _entregue(_cnpj(4), "C", flags=["mei"], municipio="VALINHOS"),
        ],
    )
    arquivo = tmp_path / "r.json"
    arquivo.write_text(
        json.dumps({_cnpj(1): "ganho", _cnpj(2): "reuniao", _cnpj(3): "perdido", _cnpj(4): "contatado",
                    _cnpj(9): {"status": "perdido", "motivo": "preço"}})
    )
    resultados.registrar(conn, arquivo)

    rel = resultados.relatorio(conn)

    assert rel["geral"] == {
        "total": 5, "respondeu": 2, "reuniao": 2, "ganho": 1, "perdido": 2, "nao_e_fit": 0,
        "taxa_resposta": 0.4, "taxa_reuniao": 0.4, "taxa_ganho": 0.2,
    }
    assert rel["sem_dados_de_entrega"] == 1  # _cnpj(9) não saiu desta skill
    prioridade = {g["valor"]: g for g in rel["por_dimensao"]["prioridade"]}
    assert prioridade["A"]["reuniao"] == 2 and prioridade["A"]["taxa_ganho"] == 0.5
    assert prioridade["B"]["perdido"] == 1
    whatsapp = next(s for s in rel["por_sinal"] if s["sinal"] == "whatsapp_provavel")
    assert whatsapp["com"]["taxa_reuniao"] == 1.0 and whatsapp["sem"]["taxa_reuniao"] == 0.0
    assert rel["motivos_de_perda"] == [{"motivo": "preço", "total": 1}]
    assert rel["por_dimensao"]["municipio"][0]["valor"] == "CAMPINAS"  # maior amostra primeiro


def test_relatorio_vazio(conn):
    rel = resultados.relatorio(conn)
    assert rel["geral"]["total"] == 0 and rel["geral"]["taxa_ganho"] == 0.0


# --- pipeline -------------------------------------------------------------------------


def _api(api, cnpjs):
    def avancada(request):
        return ok({"items": [{"cnpj_completo": c} for c in cnpjs], "total_pages": 1})

    return api(
        {"status": {"status": "ok"}, "avancada": avancada, "cnpj": cnpj_handler({c: make_cnpj_raw() for c in cnpjs})}
    )


def _invocar(*args):
    resultado = runner.invoke(app, list(args))
    assert resultado.exit_code == 0, resultado.stdout
    return json.loads(resultado.stdout)


def test_trabalhado_nao_volta_e_a_descoberta_repoe(api, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_cli, "TARGET_LEADS", 2)
    _api(api, [_cnpj(1), _cnpj(2), _cnpj(3)])
    base = tmp_path / "p.sqlite3"
    primeiro = _invocar("prospectar", "dentista", "--uf", "SP", "--db-path", str(base))
    assert [lead["cnpj"] for lead in primeiro["leads"]] == [_cnpj(1), _cnpj(2)]

    arquivo = tmp_path / "r.json"
    arquivo.write_text(json.dumps({_cnpj(1): "perdido"}))
    assert _invocar("registrar-resultado", "--arquivo", str(arquivo), "--db-path", str(base))["registrados"] == 1

    # o mesmo lote 1 não devolve mais o perdido
    de_novo = _invocar("prospectar", "dentista", "--uf", "SP", "--db-path", str(base))
    assert _cnpj(1) not in [lead["cnpj"] for lead in de_novo["leads"]]
    assert de_novo["ja_trabalhados"] == 1

    # e numa consulta nova ele nem é validado; a descoberta repõe com o próximo
    nova = _invocar("prospectar", "dentista", "--uf", "SP", "--matriz", "--db-path", str(base))
    assert [lead["cnpj"] for lead in nova["leads"]] == [_cnpj(2), _cnpj(3)]
    assert nova["ja_trabalhados"] == 1

    rel = _invocar("relatorio-resultados", "--db-path", str(base))
    assert rel["geral"]["perdido"] == 1 and rel["sem_dados_de_entrega"] == 0


def test_registrar_resultado_invalido_e_erro_json(tmp_path):
    arquivo = tmp_path / "r.csv"
    arquivo.write_text("cnpj,status\n1,ganho\n")
    resultado = runner.invoke(app, ["registrar-resultado", "--arquivo", str(arquivo), "--db-path", str(tmp_path / "p")])
    assert resultado.exit_code == 1
    assert json.loads(resultado.stdout)["erro"] == "entrada_invalida"
