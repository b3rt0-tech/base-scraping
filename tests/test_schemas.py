from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from scripts.pipeline import export as export_module
from scripts.pipeline.explicacao import resumo
from scripts.pipeline.schemas import Erro, Lead, SaidaValidarLista, gerar_json_schema
from scripts.pipeline_cli import app

SCHEMA_VERSIONADO = Path(__file__).resolve().parents[1] / "schemas" / "lead.schema.json"

LEAD_MINIMO = {
    "cnpj": "12345678000195",
    "score": 10.0,
    "prioridade": "C",
    "canal_direto": False,
    "score_breakdown": {
        "aderencia_icp": 1.0,
        "qualidade_contato": 2.0,
        "presenca_digital": 3.0,
        "saude_cadastral": 4.0,
        "total": 10.0,
    },
    "proveniencia": {"cnpj": {"fonte": "basecnpj", "rota_ou_url": "route=cnpj", "timestamp": 1.0}},
}


def test_json_schema_versionado_esta_em_sincronia_com_os_modelos():
    assert json.loads(SCHEMA_VERSIONADO.read_text()) == gerar_json_schema(), (
        "schemas/lead.schema.json desatualizado — regenere com: "
        "uv run python -m scripts.pipeline.schemas > schemas/lead.schema.json"
    )


def test_lead_so_exige_cnpj_score_prioridade_canal_e_proveniencia():
    assert set(gerar_json_schema()["$defs"]["Lead"]["required"]) == {
        "cnpj",
        "score",
        "score_breakdown",
        "prioridade",
        "canal_direto",
        "proveniencia",
    }
    Lead.model_validate(LEAD_MINIMO)


def test_campo_sem_dado_nao_aparece_na_saida():
    saida = Lead.model_validate(LEAD_MINIMO).model_dump(mode="json", exclude_unset=True)
    assert set(saida) == set(LEAD_MINIMO)


@pytest.mark.parametrize(
    "alteracao",
    [
        {"campo_novo_sem_contrato": 1},
        {"matriz_filial": "talvez"},
        {"emails": [{"valor": "a@b.com", "nivel": 3, "confianca": "otima"}]},
        {"enriquecimento": {"status": "quase"}},
        {"score": "alto"},
    ],
)
def test_lead_fora_do_contrato_e_recusado(alteracao):
    with pytest.raises(ValidationError):
        Lead.model_validate({**LEAD_MINIMO, **alteracao})


def test_nao_resolvido_aceita_cnpj_ou_nome():
    saida = SaidaValidarLista.model_validate(
        {
            "total": 0,
            "leads": [],
            "descartados": [],
            "nao_resolvidos": [{"cnpj": "123", "motivo": "x"}, {"nome": "Fulano", "motivo": "y"}],
            "resumo": resumo([]),
        }
    )
    assert saida.nao_resolvidos[1].cnpj is None


def test_erro_padrao_tem_so_erro_e_mensagem():
    assert Erro(erro="x", mensagem="y").model_dump() == {"erro": "x", "mensagem": "y"}


def test_saida_fora_do_contrato_vira_erro_json_e_nao_e_impressa(api_dentista, tmp_path, monkeypatch):
    original = export_module.build_lead_json

    def com_campo_extra(*args, **kwargs):
        return {**original(*args, **kwargs), "campo_novo_sem_contrato": 1}

    monkeypatch.setattr("scripts.pipeline_cli.build_lead_json", com_campo_extra)

    resultado = CliRunner().invoke(app, ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3")])

    assert resultado.exit_code == 1
    saida = json.loads(resultado.stdout)
    assert saida["erro"] == "saida_invalida"
    assert "campo_novo_sem_contrato" in saida["mensagem"]
