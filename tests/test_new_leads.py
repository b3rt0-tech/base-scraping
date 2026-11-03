"""[16] `/new-leads`: as duas perguntas, literais e nesta ordem, no comando e na skill."""

from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PERGUNTA_1 = "1-) Qual o ICP/Nicho?"
PERGUNTA_2 = "2-) Qual o produto?"


@pytest.mark.parametrize("arquivo", ["commands/new-leads.md", "SKILL.md"])
def test_as_duas_perguntas_literais_e_em_ordem(arquivo):
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    assert PERGUNTA_1 in texto and PERGUNTA_2 in texto, arquivo
    assert texto.index(PERGUNTA_1) < texto.index(PERGUNTA_2), arquivo


def test_comando_tem_frontmatter_com_descricao():
    texto = (RAIZ / "commands" / "new-leads.md").read_text(encoding="utf-8")
    assert texto.startswith("---\ndescription: ")
    assert texto.count("---") >= 2


def test_skill_ativa_por_gatilho_new_leads():
    """Fallback: mesmo sem o arquivo de comando instalado, `/new-leads` ativa a skill pelo description."""
    cabecalho = (RAIZ / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert "/new-leads" in cabecalho
    assert "name: base-scraping" in cabecalho  # a skill continua com o mesmo nome


def test_comando_so_para_nos_bloqueios_e_usa_o_consultor():
    texto = (RAIZ / "commands" / "new-leads.md").read_text(encoding="utf-8")
    for trecho in ("consultoria.md", "--perfil", "--qualificacoes", "nicho_ambiguo", "token_ausente", "--continuar"):
        assert trecho in texto, trecho


def test_regra_antiga_de_confirmacao_obrigatoria_saiu_da_skill():
    texto = (RAIZ / "SKILL.md").read_text(encoding="utf-8")
    assert "Confirmação do ICP é obrigatória" not in texto
    assert "Mostre a consulta interpretada e siga" in texto
