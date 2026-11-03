"""[12] Exportação CSV/XLSX pronta para o CRM."""

from __future__ import annotations

import csv
import json

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from scripts.pipeline import planilha
from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO

runner = CliRunner()

LEAD = {
    "cnpj": "12345678000195",
    "razao_social": "CLÍNICA AÇAÍ LTDA",
    "nome_fantasia": "AÇAÍ",
    "endereco": {"municipio": "SÃO PAULO", "uf": "SP"},
    "prioridade": "A",
    "score": 72.5,
    "decisores": [{"nome": "ANA SOUZA LIMA", "qualificacao": "Sócio-Administrador"}],
    "melhor_canal": {"tipo": "whatsapp", "valor": "+5511999998888"},
    "email_cadastral": "Contato@Acai.com.br",
    "telefone_cadastral": "(11) 32345678",
    "motivos": ["a (+30)", "b (+6)"],
    "alertas": ["x: y"],
    "flags": ["whatsapp_provavel"],
    "qualificacao": {"veredito": "valido", "motivo": "tem estrutura", "abordagem": "WhatsApp", "mensagem": "Olá"},
}


def test_colunas_na_ordem_e_valores():
    cabecalho, linha = planilha.linhas([LEAD])
    valores = dict(zip(cabecalho, linha, strict=True))
    assert cabecalho[:3] == ["Prioridade", "Veredito", "Score"]
    assert valores["Decisor"] == "ANA SOUZA LIMA"
    assert valores["WhatsApp"] == "+5511999998888"
    assert valores["E-mail"] == "contato@acai.com.br"
    assert valores["Motivos"] == "a (+30) | b (+6)"
    assert valores["1ª mensagem"] == "Olá"


def test_contato_do_contador_nao_vai_para_a_planilha():
    lead = {**LEAD, "flags": ["contato_de_contador"], "melhor_canal": None}
    valores = dict(zip(*planilha.linhas([lead]), strict=True))
    assert valores["E-mail"] == "" and valores["Telefone"] == ""


def test_email_confirmado_no_site_tem_preferencia():
    lead = {**LEAD, "emails": [{"valor": "ana@acai.com.br", "nivel": 5, "confianca": "alta_nominal"}]}
    assert dict(zip(*planilha.linhas([lead]), strict=True))["E-mail"] == "ana@acai.com.br"


def test_lead_minimo_nao_quebra():
    _, linha = planilha.linhas([{"cnpj": "1", "score": 1, "prioridade": "C"}])
    assert len(linha) == len(planilha.COLUNAS)


@pytest.mark.parametrize("separador", [",", ";"])
def test_csv_com_bom_e_acentos(tmp_path, separador):
    caminho = tmp_path / "leads.csv"
    planilha.gravar_csv(caminho, [LEAD], separador=separador)
    bruto = caminho.read_bytes()
    assert bruto.startswith(b"\xef\xbb\xbf")  # BOM: o Excel reconhece UTF-8
    linhas = list(csv.reader(caminho.open(encoding="utf-8-sig"), delimiter=separador))
    assert linhas[1][linhas[0].index("Empresa")] == "CLÍNICA AÇAÍ LTDA"


def test_xlsx_abre_com_cabecalho_e_filtro(tmp_path):
    caminho = tmp_path / "leads.xlsx"
    planilha.gravar_xlsx(caminho, [LEAD])
    aba = load_workbook(caminho).active
    assert aba["A1"].value == "Prioridade" and aba["A1"].font.bold
    assert aba.freeze_panes == "A2" and aba.auto_filter.ref
    assert aba.cell(row=2, column=4).value == "CLÍNICA AÇAÍ LTDA"


def test_nome_padrao_sem_acento():
    assert str(planilha.nome_padrao("Clínica Odontológica-São Paulo-lote2", "csv")) == (
        "leads-clinica-odontologica-sao-paulo-lote2.csv"
    )


# --- CLI ------------------------------------------------------------------------------


def _prospectar(tmp_path, *extra):
    args = ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    resultado = runner.invoke(app, args)
    return resultado.exit_code, json.loads(resultado.stdout)


@pytest.mark.parametrize("formato", ["csv", "xlsx"])
def test_prospectar_grava_planilha_e_stdout_continua_json(api_dentista, tmp_path, formato):
    destino = tmp_path / f"saida.{formato}"
    codigo, saida = _prospectar(tmp_path, "--formato", formato, "--saida", str(destino))

    assert codigo == 0
    assert saida["arquivo"] == str(destino.resolve()) and saida["formato"] == formato
    assert saida["total"] == 1 and saida["lote"] == 1
    assert saida["resumo"]["total"] == 1 and saida["descartados"] == 1
    assert destino.exists()
    if formato == "csv":
        linhas = list(csv.reader(destino.open(encoding="utf-8-sig")))
        assert linhas[1][linhas[0].index("CNPJ")] == CNPJ_PADRAO


def test_nome_padrao_usa_nicho_cidade_e_lote(api_dentista, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resultado = runner.invoke(app, ["prospectar", "dentista", "--uf", "SP", "--municipio", "Campinas", "--formato", "csv"])
    assert json.loads(resultado.stdout)["arquivo"].endswith("leads-dentista-campinas-lote1.csv")


def test_roda_de_outro_diretorio_sem_espalhar_cache(api_dentista, tmp_path, monkeypatch):
    """A skill instalada roda do diretório do usuário: o dicionário de nichos tem de ser achado e nenhum
    `.cache/` pode ser criado ali (cache e checkpoint ficam na raiz da skill).
    """
    pasta = tmp_path / "projeto-do-usuario"
    pasta.mkdir()
    monkeypatch.chdir(pasta)
    resultado = runner.invoke(app, ["prospectar", "dentista", "--uf", "SP"])
    assert resultado.exit_code == 0, resultado.stdout
    assert json.loads(resultado.stdout)["total"] == 1
    assert not (pasta / ".cache").exists()


@pytest.mark.parametrize("args", [["--formato", "pdf"], ["--formato", "csv", "--separador", "|"]])
def test_opcoes_invalidas(api_dentista, tmp_path, args):
    codigo, saida = _prospectar(tmp_path, *args)
    assert codigo == 1 and saida["erro"] == "opcoes_invalidas"


def test_saida_em_pasta_inexistente_e_erro_json(api_dentista, tmp_path):
    codigo, saida = _prospectar(tmp_path, "--formato", "csv", "--saida", str(tmp_path / "nao" / "existe.csv"))
    assert codigo == 1 and saida["erro"] == "saida_invalida"
