"""Exportação em planilha (CSV/XLSX) para importar no CRM ou abrir no Excel: uma linha por lead, colunas
planas na ordem em que o vendedor lê. O JSON continua sendo o contrato completo; a planilha é a vista
de trabalho.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

_EMAIL_CONFIRMADO = ("maxima", "alta_nominal", "alta")


def _decisor(lead: dict[str, Any], campo: str) -> str:
    decisores = lead.get("decisores") or []
    return decisores[0].get(campo) or "" if decisores else ""


def _contador(lead: dict[str, Any]) -> bool:
    return "contato_de_contador" in (lead.get("flags") or [])


def _melhor_email(lead: dict[str, Any]) -> str:
    """E-mail confirmado no site; senão o cadastral, se não for do contador; senão qualquer um do site."""
    emails = lead.get("emails") or []
    confirmados = [e["valor"] for e in emails if e.get("confianca") in _EMAIL_CONFIRMADO]
    if confirmados:
        return confirmados[0]
    if lead.get("email_cadastral") and not _contador(lead):
        return lead["email_cadastral"].strip().lower()
    return emails[0]["valor"] if emails else ""


def _melhor_telefone(lead: dict[str, Any]) -> str:
    if lead.get("telefones"):
        return lead["telefones"][0]
    if lead.get("telefone_cadastral") and not _contador(lead):
        return lead["telefone_cadastral"]
    return ""


def _whatsapp(lead: dict[str, Any]) -> str:
    canal = lead.get("melhor_canal") or {}
    if canal.get("tipo") == "whatsapp":
        return canal["valor"]
    return lead.get("telefone_cadastral_celular") or ""


def _qualificacao(campo: str) -> Callable[[dict[str, Any]], str]:
    return lambda lead: (lead.get("qualificacao") or {}).get(campo) or ""


def _endereco(campo: str) -> Callable[[dict[str, Any]], str]:
    return lambda lead: (lead.get("endereco") or {}).get(campo) or ""


def _juntar(campo: str) -> Callable[[dict[str, Any]], str]:
    return lambda lead: " | ".join(lead.get(campo) or [])


def _texto(campo: str) -> Callable[[dict[str, Any]], Any]:
    return lambda lead: lead.get(campo) if lead.get(campo) is not None else ""


# (cabeçalho, como extrair) — a ordem é a de leitura do vendedor: prioridade, quem, como falar, por quê
COLUNAS: list[tuple[str, Callable[[dict[str, Any]], Any]]] = [
    ("Prioridade", _texto("prioridade")),
    ("Veredito", _qualificacao("veredito")),
    ("Score", _texto("score")),
    ("Empresa", _texto("razao_social")),
    ("Nome fantasia", _texto("nome_fantasia")),
    ("CNPJ", _texto("cnpj")),
    ("Cidade", _endereco("municipio")),
    ("UF", _endereco("uf")),
    ("Decisor", lambda lead: _decisor(lead, "nome")),
    ("Cargo do decisor", lambda lead: _decisor(lead, "qualificacao")),
    ("Melhor canal", lambda lead: (lead.get("melhor_canal") or {}).get("tipo", "")),
    ("WhatsApp", _whatsapp),
    ("E-mail", _melhor_email),
    ("Telefone", _melhor_telefone),
    ("Site", _texto("site")),
    ("Motivos", _juntar("motivos")),
    ("Alertas", _juntar("alertas")),
    ("Motivo do veredito", _qualificacao("motivo")),
    ("Abordagem", _qualificacao("abordagem")),
    ("1ª mensagem", _qualificacao("mensagem")),
    ("Anos de mercado", _texto("anos_de_mercado")),
    ("Unidades", _texto("unidades")),
    ("Capital social", _texto("capital_social")),
    ("Sinais", _juntar("flags")),
]


def linhas(leads: list[dict[str, Any]]) -> list[list[Any]]:
    """Cabeçalho + uma linha por lead. Colunas sempre presentes (vazias quando não há dado), para o
    mapeamento no CRM ser feito uma vez só.
    """
    return [[nome for nome, _ in COLUNAS]] + [[extrair(lead) for _, extrair in COLUNAS] for lead in leads]


def gravar_csv(caminho: Path, leads: list[dict[str, Any]], *, separador: str = ",") -> None:
    """UTF-8 com BOM: o Excel reconhece os acentos; HubSpot/Pipedrive importam normalmente."""
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        csv.writer(arquivo, delimiter=separador).writerows(linhas(leads))


def gravar_xlsx(caminho: Path, leads: list[dict[str, Any]]) -> None:
    from openpyxl import Workbook  # type: ignore[import-untyped]
    from openpyxl.styles import Font  # type: ignore[import-untyped]
    from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]

    planilha = Workbook()
    aba = planilha.active
    aba.title = "Leads"
    for linha in linhas(leads):
        aba.append(linha)
    for celula in aba[1]:
        celula.font = Font(bold=True)
    aba.freeze_panes = "A2"
    aba.auto_filter.ref = aba.dimensions
    for indice, (nome, _) in enumerate(COLUNAS, start=1):
        largura = max([len(nome)] + [len(str(c.value or "")) for c in aba[get_column_letter(indice)][1:50]])
        aba.column_dimensions[get_column_letter(indice)].width = min(60, max(10, largura + 2))
    planilha.save(caminho)


def nome_padrao(base: str, formato: str) -> Path:
    """`leads-dentista-lote1.csv` — sem acento/espaço, para não sobrescrever a exportação de outra consulta."""
    sem_acento = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-") or "leads"
    return Path(f"leads-{slug}.{formato}")
