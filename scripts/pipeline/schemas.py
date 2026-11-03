"""Contrato de saída do CLI (`descobrir`, `prospectar`, `validar-lista`) e do erro padrão.

É a fonte da verdade do que o skill/IA recebe no stdout: toda saída é validada contra estes modelos
antes de ser impressa, e `schemas/lead.schema.json` é gerado a partir deles
(`uv run python -m scripts.pipeline.schemas > schemas/lead.schema.json`).

Campo sem dado **não aparece** na saída (serialização com `exclude_unset`), por isso quase tudo é opcional.
`extra="forbid"`: um campo novo no `export.py` sem declarar aqui falha alto em vez de vazar sem contrato.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import models_json_schema


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proveniencia(_Modelo):
    fonte: Literal["basecnpj", "scraping", "pipeline"]
    rota_ou_url: str
    timestamp: float


class Endereco(_Modelo):
    logradouro: str | None = None
    municipio: str | None = None
    uf: str | None = None
    cep: str | None = None


class Email(_Modelo):
    valor: str
    nivel: int  # 0 (inválido) a 5 — escada da seção 4.3
    confianca: Literal["invalido", "baixa", "media", "boa", "alta", "alta_nominal", "maxima"]


class Decisor(_Modelo):
    """Quem provavelmente decide a compra. Só nome público e qualificação — sem CPF nem faixa etária."""

    nome: str
    qualificacao: str
    desde: str | None = None
    origem: Literal["quadro_societario", "razao_social_titular"]


class Enriquecimento(_Modelo):
    status: Literal["ok", "requer_navegador", "match_fraco", "falha"]
    motivo: str | None = None


class ScoreBreakdown(_Modelo):
    aderencia_icp: float
    qualidade_contato: float
    presenca_digital: float
    saude_cadastral: float
    total: float


class Lead(_Modelo):
    cnpj: str
    # cadastral (BaseCNPJ, route=cnpj)
    razao_social: str | None = None
    nome_fantasia: str | None = None
    capital_social: float | int | None = None
    porte: str | None = None
    natureza_juridica: str | None = None
    cnae_principal: str | None = None
    cnaes_secundarios: list[str] | None = None
    situacao_cadastral: str | None = None
    data_abertura: str | None = None
    matriz_filial: Literal["matriz", "filial"] | None = None
    endereco: Endereco | None = None
    telefone_cadastral: str | None = None
    email_cadastral: str | None = None
    decisores: list[Decisor] | None = None
    # scraping (S4) — só quando há site candidato confirmado
    site: str | None = None
    emails: list[Email] | None = None
    telefones: list[str] | None = None
    whatsapp: list[str] | None = None
    redes_sociais: dict[str, str] | None = None
    enriquecimento: Enriquecimento | None = None
    # pipeline
    score: float
    score_breakdown: ScoreBreakdown
    flags: list[str] | None = None
    proveniencia: dict[str, Proveniencia]


class Icp(_Modelo):
    nicho: str
    entrada_dicionario: str | None = None
    cnaes: list[str]


class LeadResumo(_Modelo):
    cnpj: str
    razao_social: str | None = None


class Descartado(_Modelo):
    cnpj: str
    motivo: str | None = None


class NaoResolvido(_Modelo):
    cnpj: str | None = None
    nome: str | None = None
    motivo: str


class SaidaDescobrir(_Modelo):
    icp: Icp
    total: int
    leads: list[LeadResumo]
    descartados: list[Descartado]


class SaidaProspectar(_Modelo):
    icp: Icp
    total: int
    leads: list[Lead]
    descartados: list[Descartado]


class SaidaValidarLista(_Modelo):
    total: int
    leads: list[Lead]
    descartados: list[Descartado]
    nao_resolvidos: list[NaoResolvido]
    aviso: str | None = None


class Erro(_Modelo):
    erro: str
    mensagem: str


MODELOS_DE_SAIDA = (SaidaDescobrir, SaidaProspectar, SaidaValidarLista, Erro)


def gerar_json_schema() -> dict[str, Any]:
    """JSON Schema das saídas do CLI (cada comando devolve um dos modelos de `MODELOS_DE_SAIDA`)."""
    _, defs = models_json_schema([(modelo, "validation") for modelo in MODELOS_DE_SAIDA])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "base-scraping — saídas do CLI",
        "description": "Gerado por scripts/pipeline/schemas.py — não editar à mão.",
        "oneOf": [{"$ref": f"#/$defs/{modelo.__name__}"} for modelo in MODELOS_DE_SAIDA],
        **defs,
    }


if __name__ == "__main__":
    print(json.dumps(gerar_json_schema(), indent=2, ensure_ascii=False))
