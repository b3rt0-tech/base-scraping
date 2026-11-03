from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline import perfil as perfil_module
from scripts.pipeline.perfil import PerfilICP, PerfilInvalido, carregar_perfil, criterios_fit
from scripts.pipeline.score import compute_score
from scripts.pipeline.sinais import aplicar_sinais
from scripts.pipeline.validate import ValidacaoResult
from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, cnpj_handler, make_cnpj_raw, ok

runner = CliRunner()
EXEMPLO = Path(__file__).resolve().parents[1] / "icp.example.yaml"


def _lead(cnpj: str = "11111111000101", **raw) -> ValidacaoResult:
    r = ValidacaoResult(
        cnpj=cnpj,
        status="validado",
        detalhe=CNPJDetalhe.model_validate(make_cnpj_raw(cnpj_completo=cnpj, **raw)),
        flags=["contato_cadastral_disponivel"],
        cnae_match="principal",
    )
    aplicar_sinais([r])
    return r


def _perfil(**dados) -> PerfilICP:
    return PerfilICP.model_validate(dados)


# --- carregar / validar ---------------------------------------------------------


def test_exemplo_versionado_e_valido():
    perfil, arquivo = carregar_perfil(EXEMPLO)
    assert arquivo == EXEMPLO
    assert perfil is not None and perfil.pesos.fit == 35
    assert perfil.excluir.mei is True


def test_sem_arquivo_e_sem_padrao_nao_ha_perfil():
    assert carregar_perfil(None) == (None, None)


def test_icp_yaml_padrao_na_raiz_e_usado(tmp_path, monkeypatch):
    padrao = tmp_path / "icp.yaml"
    padrao.write_text("capital_social: {minimo: 0, ideal: 1000}\n")
    monkeypatch.setattr(perfil_module, "PERFIL_PADRAO", padrao)
    perfil, arquivo = carregar_perfil(None)
    assert arquivo == padrao and perfil is not None and perfil.capital_social is not None


@pytest.mark.parametrize(
    "conteudo, trecho",
    [
        ("pesos: {fit: 50}\n", "somar 100"),
        ("pesos: {aderencia_icp: 20, qualidade_contato: 20, presenca_digital: 10, saude_cadastral: 10, fit: 40}\n", "critério de fit"),
        ("capital_social: {minimo: 100, ideal: 10}\n", "ideal"),
        ("prioridade: {corte_a: 40, corte_b: 60}\n", "corte_a"),
        ("portes_preferidos: [GRANDE]\n", "portes_preferidos"),
        ("campo_inventado: 1\n", "campo_inventado"),
        ("pesos: [1, 2\n", "YAML válido"),
    ],
)
def test_perfil_invalido_explica_o_erro(tmp_path, conteudo, trecho):
    arquivo = tmp_path / "icp.yaml"
    arquivo.write_text(conteudo)
    with pytest.raises(PerfilInvalido, match=trecho):
        carregar_perfil(arquivo)


def test_perfil_inexistente(tmp_path):
    with pytest.raises(PerfilInvalido, match="não foi possível ler"):
        carregar_perfil(tmp_path / "nao-existe.yaml")


# --- score ------------------------------------------------------------------------


def test_perfil_com_pesos_padrao_reproduz_o_score_atual():
    lead = _lead()
    sem = compute_score(lead)
    com = compute_score(lead, perfil=_perfil())
    assert com.as_dict() == sem.as_dict()
    assert "fit" not in com.as_dict()


def test_fit_desempata_leads_iguais_no_resto():
    """O empate real (todos 58/64) some quando o perfil olha capital e tempo de mercado."""
    perfil = _perfil(
        pesos={"aderencia_icp": 20, "qualidade_contato": 25, "presenca_digital": 10, "saude_cadastral": 10, "fit": 35},
        capital_social={"minimo": 10000, "ideal": 300000},
        anos_de_mercado={"minimo": 1, "ideal": 8},
    )
    pequeno = _lead("11111111000101", capital_social=15000, matriz={"data_inicio_atividade": "2024-01-10"})
    medio = _lead("22222222000102", capital_social=80000, matriz={"data_inicio_atividade": "2020-01-10"})
    grande = _lead("33333333000103", capital_social=500000, matriz={"data_inicio_atividade": "2010-01-10"})

    sem = [compute_score(x).total for x in (pequeno, medio, grande)]
    com = [compute_score(x, perfil=perfil).total for x in (pequeno, medio, grande)]

    assert len(set(sem)) == 1  # sem perfil: empate
    assert com[0] < com[1] < com[2]  # com perfil: ordem previsível
    assert compute_score(grande, perfil=perfil).fit == 35.0


def test_criterios_de_fit():
    perfil = _perfil(
        portes_preferidos=["ME"],
        municipios_prioritarios=["campinas"],
        cnaes_prioritarios=["86305"],
        whatsapp=True,
        unidades={"minimo": 1, "ideal": 3},
    )
    lead = _lead(
        porte_codigo="01",
        matriz={"municipio": "CAMPINAS"},
        telefones=[{"tipo": "comercial", "ddd": "19", "numero": "99876543"}],
        estabelecimentos=[{"situacao_cadastral": "02"}, {"situacao_cadastral": "02"}],
    )
    assert criterios_fit(lead, None, perfil) == {
        "unidades": 0.5,
        "portes_preferidos": 1.0,
        "municipios_prioritarios": 1.0,
        "cnaes_prioritarios": 1.0,
        "whatsapp": 1.0,
    }


def test_municipio_ignora_acento_e_caixa():
    perfil = _perfil(municipios_prioritarios=["São Paulo"])
    assert criterios_fit(_lead(matriz={"municipio": "SAO PAULO"}), None, perfil)["municipios_prioritarios"] == 1.0


def test_capital_abaixo_do_minimo_e_zero_e_acima_do_ideal_e_um():
    perfil = _perfil(capital_social={"minimo": 10000, "ideal": 100000})
    assert criterios_fit(_lead(capital_social=5000), None, perfil)["capital_social"] == 0.0
    assert criterios_fit(_lead(capital_social=1_000_000), None, perfil)["capital_social"] == 1.0
    assert 0 < criterios_fit(_lead(capital_social=30000), None, perfil)["capital_social"] < 1


def test_pesos_reescalam_as_dimensoes():
    perfil = _perfil(
        pesos={"aderencia_icp": 15, "qualidade_contato": 30, "presenca_digital": 20, "saude_cadastral": 20, "fit": 15},
        whatsapp=True,
    )
    score = compute_score(_lead(), perfil=perfil)
    assert score.aderencia_icp == 15.0  # 30 de 30 → 15 de 15


# --- exclusões e CLI ----------------------------------------------------------------


def _api_mei(api):
    mei = make_cnpj_raw(cnpj_completo="22222222000102", simples={"opcao_simples": True, "opcao_mei": True})
    return api(
        {
            "status": {"status": "ok"},
            "avancada": ok(
                {"items": [{"cnpj_completo": CNPJ_PADRAO}, {"cnpj_completo": "22222222000102"}], "total_pages": 1}
            ),
            "cnpj": cnpj_handler({CNPJ_PADRAO: make_cnpj_raw(), "22222222000102": mei}),
        }
    )


def _prospectar(tmp_path, *extra):
    resultado = runner.invoke(
        app, ["prospectar", "dentista", "--uf", "SP", "--db-path", str(tmp_path / "p.sqlite3"), *extra]
    )
    return resultado.exit_code, json.loads(resultado.stdout)


def test_perfil_exclui_mei_na_validacao(api, tmp_path):
    _api_mei(api)
    perfil = tmp_path / "icp.yaml"
    perfil.write_text("excluir: {mei: true}\n")

    codigo, saida = _prospectar(tmp_path, "--perfil", str(perfil))

    assert codigo == 0
    assert [lead["cnpj"] for lead in saida["leads"]] == [CNPJ_PADRAO]
    assert saida["descartados"] == [{"cnpj": "22222222000102", "motivo": "perfil: MEI fora do perfil (excluir.mei)"}]
    assert saida["icp"]["perfil"] == str(perfil)


def test_sem_exclusao_o_mei_fica(api, tmp_path):
    _api_mei(api)
    codigo, saida = _prospectar(tmp_path)
    assert codigo == 0 and saida["total"] == 2
    assert "perfil" not in saida["icp"]


def test_mudar_exclusao_e_outra_consulta(api, tmp_path):
    """Mesmo banco: rodar sem exclusão e depois com `excluir.mei` não reaproveita o lote antigo."""
    _api_mei(api)
    _prospectar(tmp_path)
    perfil = tmp_path / "icp.yaml"
    perfil.write_text("excluir: {mei: true}\n")
    _, saida = _prospectar(tmp_path, "--perfil", str(perfil))
    assert saida["total"] == 1


def test_perfil_invalido_e_erro_json(api, tmp_path):
    perfil = tmp_path / "icp.yaml"
    perfil.write_text("pesos: {fit: 50}\n")
    codigo, saida = _prospectar(tmp_path, "--perfil", str(perfil))
    assert codigo == 1
    assert saida["erro"] == "entrada_invalida" and "somar 100" in saida["mensagem"]


def test_score_breakdown_traz_fit_com_perfil(api, tmp_path):
    _api_mei(api)
    perfil = tmp_path / "icp.yaml"
    perfil.write_text(EXEMPLO.read_text())
    _, saida = _prospectar(tmp_path, "--perfil", str(perfil))
    breakdown = saida["leads"][0]["score_breakdown"]
    assert "fit" in breakdown
    assert breakdown["total"] == pytest.approx(sum(v for k, v in breakdown.items() if k != "total"), abs=0.02)


def test_empate_de_score_desempata_por_fit_capital_e_tempo():
    from scripts.pipeline.export import export_leads

    def lead(cnpj, fit, capital, anos):
        return {"cnpj": cnpj, "score": 60.0, "score_breakdown": {"fit": fit}, "capital_social": capital, "anos_de_mercado": anos}

    saida = export_leads([lead("a", 10, 5000, 3), lead("b", 12, 1000, 1), lead("c", 10, 5000, 9), lead("d", 10, 90000, 1)])
    assert [x["cnpj"] for x in saida["leads"]] == ["b", "d", "c", "a"]
