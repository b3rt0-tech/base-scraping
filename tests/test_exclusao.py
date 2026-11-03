"""[11] `--excluir`: quem já está no CRM não volta como lead novo."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scripts import pipeline_cli
from scripts.pipeline.exclusao import ExclusaoInvalida, carregar_exclusoes
from scripts.pipeline_cli import app

from .conftest import cnpj_handler, make_cnpj_raw, ok

runner = CliRunner()


def _cnpj(i: int, filial: int = 1) -> str:
    return f"{i:08d}{filial:04d}00"


# --- leitura dos arquivos ---------------------------------------------------------


def test_csv_exportado_do_crm_reconhece_cnpj_e_dominio(tmp_path):
    arquivo = tmp_path / "crm.csv"
    arquivo.write_text(
        "Empresa;CNPJ;E-mail;Site\n"
        "Clínica A;12.345.678/0001-95;ana@clinicaa.com.br;\n"
        "Clínica B;;;https://www.clinicab.com.br/contato\n"
        "Clínica C;98765432000110;dono@gmail.com;\n",
        encoding="utf-8-sig",
    )
    exclusoes = carregar_exclusoes(arquivo)
    assert exclusoes.raizes == {"12345678", "98765432"}
    assert exclusoes.dominios == {"clinicaa.com.br", "clinicab.com.br"}
    assert exclusoes.ignorados == 1  # gmail.com excluiria todo mundo


def test_csv_em_latin1_do_excel(tmp_path):
    arquivo = tmp_path / "crm.csv"
    arquivo.write_bytes("Razão,CNPJ\nClínica,12345678000195\n".encode("latin-1"))
    assert carregar_exclusoes(arquivo).raizes == {"12345678"}


def test_txt_um_por_linha(tmp_path):
    arquivo = tmp_path / "crm.txt"
    arquivo.write_text("12345678000195\n\nclinicaa.com.br\nlixo qualquer\n")
    exclusoes = carregar_exclusoes(arquivo)
    assert exclusoes.raizes == {"12345678"} and exclusoes.dominios == {"clinicaa.com.br"}


@pytest.mark.parametrize(
    "conteudo",
    [
        ["12345678000195", "clinicaa.com.br"],
        [{"cnpj": "12.345.678/0001-95", "email": "x@clinicaa.com.br"}],
        {"cnpjs": [12345678000195], "dominios": ["https://clinicaa.com.br"]},
    ],
)
def test_json_em_tres_formatos(tmp_path, conteudo):
    arquivo = tmp_path / "crm.json"
    arquivo.write_text(json.dumps(conteudo))
    exclusoes = carregar_exclusoes(arquivo)
    assert exclusoes.raizes == {"12345678"} and exclusoes.dominios == {"clinicaa.com.br"}


@pytest.mark.parametrize(
    "nome, conteudo, trecho",
    [
        ("crm.xlsx", "x", "aceita"),
        ("crm.txt", "nada aqui\n", "nenhum CNPJ"),
        ("crm.json", "{", "JSON válido"),
        ("crm.json", '"texto"', "lista"),
    ],
)
def test_arquivo_invalido(tmp_path, nome, conteudo, trecho):
    arquivo = tmp_path / nome
    arquivo.write_text(conteudo)
    with pytest.raises(ExclusaoInvalida, match=trecho):
        carregar_exclusoes(arquivo)


def test_assinatura_muda_com_a_lista(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("12345678000195\n")
    b.write_text("12345678000195\n98765432000110\n")
    assert carregar_exclusoes(a).assinatura() != carregar_exclusoes(b).assinatura()


# --- pipeline ---------------------------------------------------------------------


@pytest.fixture
def teto_pequeno(monkeypatch):
    monkeypatch.setattr(pipeline_cli, "TARGET_LEADS", 2)


def _api(api, paginas: list[list[tuple[str, dict]]]):
    def avancada(request):
        page = int(request.url.params["page"])
        itens = paginas[page - 1] if page <= len(paginas) else []
        return ok({"items": [{"cnpj_completo": c} for c, _ in itens], "total_pages": len(paginas)})

    payloads = {c: raw for pagina in paginas for c, raw in pagina}
    return api({"status": {"status": "ok"}, "avancada": avancada, "cnpj": cnpj_handler(payloads)})


def _rodar(tmp_path, comando: str, *extra) -> dict:
    args = [comando, "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    resultado = runner.invoke(app, args)
    assert resultado.exit_code == 0, resultado.stdout
    return json.loads(resultado.stdout)


def test_cliente_do_crm_sai_e_a_descoberta_repoe(api, tmp_path, teto_pequeno):
    chamadas = _api(
        api,
        [
            [(_cnpj(1), make_cnpj_raw()), (_cnpj(2), make_cnpj_raw(matriz={"email": "x@clienteantigo.com.br"}))],
            [(_cnpj(3), make_cnpj_raw()), (_cnpj(4), make_cnpj_raw())],
        ],
    )
    crm = tmp_path / "crm.txt"
    crm.write_text(f"{_cnpj(1, filial=2)}\nclienteantigo.com.br\n")  # filial do cliente 1 exclui a empresa

    saida = _rodar(tmp_path, "prospectar", "--excluir", str(crm))

    assert sorted(lead["cnpj"] for lead in saida["leads"]) == [_cnpj(3), _cnpj(4)]
    assert saida["excluidos"] == 2
    assert saida["descartados"] == []  # cliente não é descarte
    # o CNPJ excluído pela raiz nem chega a ser consultado no detalhe
    assert _cnpj(1) not in [c.url.params.get("cnpj") for c in chamadas]


def test_sem_excluir_nada_muda(api, tmp_path, teto_pequeno):
    _api(api, [[(_cnpj(1), make_cnpj_raw()), (_cnpj(2), make_cnpj_raw())]])
    saida = _rodar(tmp_path, "descobrir")
    assert saida["excluidos"] == 0 and saida["total"] == 2


def test_excluir_invalido_e_erro_json(api, tmp_path):
    crm = tmp_path / "crm.txt"
    crm.write_text("nada\n")
    resultado = runner.invoke(app, ["prospectar", "dentista", "--uf", "SP", "--excluir", str(crm)])
    assert resultado.exit_code == 1
    assert json.loads(resultado.stdout)["erro"] == "entrada_invalida"


def test_validar_lista_tambem_exclui(api, tmp_path):
    api({"status": {"status": "ok"}, "cnpj": cnpj_handler({_cnpj(1): make_cnpj_raw(), _cnpj(2): make_cnpj_raw()})})
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": _cnpj(1)}, {"cnpj": _cnpj(2)}]))
    crm = tmp_path / "crm.txt"
    crm.write_text(f"{_cnpj(2)}\n")

    resultado = runner.invoke(app, ["validar-lista", "--arquivo", str(lista), "--excluir", str(crm)])

    saida = json.loads(resultado.stdout)
    assert [lead["cnpj"] for lead in saida["leads"]] == [_cnpj(1)]
    assert saida["excluidos"] == 1
