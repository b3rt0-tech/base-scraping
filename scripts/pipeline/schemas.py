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
    fit: float | None = None  # só com perfil (`icp.yaml`) que dá peso ao fit
    total: float


class Canal(_Modelo):
    tipo: Literal["whatsapp", "email", "telefone", "site"]
    valor: str


class Resumo(_Modelo):
    """Boletim do lote: quanto dele dá para trabalhar já."""

    total: int
    por_prioridade: dict[Literal["A", "B", "C"], int]
    com_decisor: int
    com_canal_direto: int
    com_whatsapp: int
    com_site: int
    com_contato_de_contador: int
    mei: int


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
    telefone_cadastral_celular: str | None = None  # +55DDD9XXXXXXXX — só com o flag `whatsapp_provavel`
    unidades: int | None = None  # estabelecimentos ativos (matriz + filiais)
    anos_de_mercado: int | None = None
    decisores: list[Decisor] | None = None
    # scraping (S4) — só quando há site candidato confirmado
    site: str | None = None
    site_origem: Literal["sites_json", "email_cadastral"] | None = None  # quem indicou o site candidato
    emails: list[Email] | None = None
    telefones: list[str] | None = None
    whatsapp: list[str] | None = None
    redes_sociais: dict[str, str] | None = None
    enriquecimento: Enriquecimento | None = None
    # pipeline
    score: float
    score_breakdown: ScoreBreakdown
    # explicação em linguagem de vendas (S6)
    prioridade: Literal["A", "B", "C"]
    motivos: list[str] | None = None  # um por item que pesou no score, com os pontos
    alertas: list[str] | None = None  # o que cuidar, já com a ação
    canal_direto: bool
    melhor_canal: Canal | None = None
    flags: list[str] | None = None
    proveniencia: dict[str, Proveniencia]


class Icp(_Modelo):
    nicho: str
    entrada_dicionario: str | None = None
    cnaes: list[str]
    perfil: str | None = None  # arquivo `icp.yaml` aplicado (fit, pesos, exclusões)


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
    lote: int
    descobertos: int
    validados: int
    esgotou_busca: bool
    excluidos: int = 0  # já no CRM (`--excluir`): não entram nem em `descartados`
    leads: list[LeadResumo]
    descartados: list[Descartado]


class SaidaProspectar(_Modelo):
    icp: Icp
    total: int
    lote: int
    descobertos: int
    validados: int
    esgotou_busca: bool
    excluidos: int = 0  # já no CRM (`--excluir`): não entram nem em `descartados`
    leads: list[Lead]
    resumo: Resumo
    descartados: list[Descartado]


class SaidaValidarLista(_Modelo):
    total: int
    leads: list[Lead]
    resumo: Resumo
    descartados: list[Descartado]
    nao_resolvidos: list[NaoResolvido]
    excluidos: int = 0  # já no CRM (`--excluir`)
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
