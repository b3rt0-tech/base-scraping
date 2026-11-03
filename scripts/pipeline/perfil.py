"""Perfil do cliente ideal (`icp.yaml`): o que faz um lead ser bom **para o produto do usuário**.

O dicionário de nichos (`references/cnae.md`) diz *quem* buscar; o perfil diz *quem vale mais* entre os
encontrados. Sem perfil, o score é o padrão de completude (pesos 30/30/20/20). Com perfil, a dimensão
`fit` (0 a `pesos.fit`) mede o encaixe com critérios contínuos — capital, tempo de mercado, unidades —
que também desempatam leads iguais no resto.

Quem escreve o `icp.yaml` é o usuário ou o consultor de vendas (o Claude, a partir do produto); este
módulo só lê, valida e aplica — nada de LLM aqui.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scripts.basecnpj.config import SKILL_ROOT

if TYPE_CHECKING:
    from scripts.basecnpj.models import CNPJDetalhe
    from scripts.pipeline.validate import ValidacaoResult
    from scripts.scraping.enrich import EnrichmentResult

PERFIL_PADRAO = SKILL_ROOT / "icp.yaml"

_PORTE_CODIGO = {"ME": "01", "EPP": "03", "demais": "05"}


class PerfilInvalido(ValueError):
    """`icp.yaml` ausente, mal formado ou fora do formato — vira erro JSON `entrada_invalida` no CLI."""


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Faixa(_Modelo):
    """Abaixo de `minimo` vale 0; em `ideal` ou acima vale 1; no meio, proporcional."""

    minimo: float = 0
    ideal: float

    @model_validator(mode="after")
    def _ideal_acima_do_minimo(self) -> Faixa:
        if self.ideal <= self.minimo:
            raise ValueError("`ideal` precisa ser maior que `minimo`")
        return self


class Pesos(_Modelo):
    aderencia_icp: float = 30
    qualidade_contato: float = 30
    presenca_digital: float = 20
    saude_cadastral: float = 20
    fit: float = 0

    @model_validator(mode="after")
    def _somam_100(self) -> Pesos:
        total = self.aderencia_icp + self.qualidade_contato + self.presenca_digital + self.saude_cadastral + self.fit
        if abs(total - 100) > 0.01:
            raise ValueError(f"os pesos precisam somar 100 (somam {total:g})")
        return self


class Excluir(_Modelo):
    """Critérios eliminatórios: o lead vira descartado na validação (e a descoberta repõe outro)."""

    mei: bool = False
    empresa_nova: bool = False  # aberta há menos de 6 meses


class Prioridade(_Modelo):
    """Cortes de score para a prioridade A/B/C. O padrão foi calibrado em dados reais: sem site, o máximo é
    64 (nicho principal + WhatsApp + cadastro saudável), então A ≥ 62 é "dá para abordar hoje".
    """

    corte_a: float = 62
    corte_b: float = 50

    @model_validator(mode="after")
    def _a_acima_de_b(self) -> Prioridade:
        if self.corte_a <= self.corte_b:
            raise ValueError("`corte_a` precisa ser maior que `corte_b`")
        return self


class PerfilICP(_Modelo):
    descricao: str | None = None
    pesos: Pesos = Pesos()
    capital_social: Faixa | None = None  # em R$, escala logarítmica (R$ 10 mil → 100 mil pesa como 100 mil → 1 mi)
    anos_de_mercado: Faixa | None = None
    unidades: Faixa | None = None
    portes_preferidos: list[Literal["ME", "EPP", "demais"]] = []
    municipios_prioritarios: list[str] = []
    cnaes_prioritarios: list[str] = []  # prefixos, como no dicionário (secundário vale metade)
    tecnologias: list[str] = []  # detectadas no site (ex. wordpress, vtex) — só conta com enriquecimento
    whatsapp: bool = False  # celular cadastral próprio (`whatsapp_provavel`) conta como fit
    excluir: Excluir = Excluir()
    prioridade: Prioridade = Prioridade()

    @model_validator(mode="after")
    def _fit_tem_criterio(self) -> PerfilICP:
        if self.pesos.fit > 0 and not self.criterios_ativos():
            raise ValueError("`pesos.fit` > 0 exige ao menos um critério de fit (capital_social, anos_de_mercado…)")
        return self

    def criterios_ativos(self) -> list[str]:
        return [
            nome
            for nome, ativo in (
                ("capital_social", self.capital_social is not None),
                ("anos_de_mercado", self.anos_de_mercado is not None),
                ("unidades", self.unidades is not None),
                ("portes_preferidos", bool(self.portes_preferidos)),
                ("municipios_prioritarios", bool(self.municipios_prioritarios)),
                ("cnaes_prioritarios", bool(self.cnaes_prioritarios)),
                ("tecnologias", bool(self.tecnologias)),
                ("whatsapp", self.whatsapp),
            )
            if ativo
        ]

    def exclusoes_ativas(self) -> dict[str, bool]:
        """Entra no `run_id`: mudar o que é eliminatório muda quais leads a consulta entrega."""
        return {nome: True for nome, ativo in self.excluir.model_dump().items() if ativo}


def carregar_perfil(caminho: str | Path | None) -> tuple[PerfilICP | None, Path | None]:
    """Lê o perfil de `caminho`; sem caminho, usa `icp.yaml` da raiz da skill se existir. Devolve o perfil e
    o arquivo usado (None, None quando não há perfil — score padrão).
    """
    if caminho is None:
        if not PERFIL_PADRAO.is_file():
            return None, None
        caminho = PERFIL_PADRAO
    arquivo = Path(caminho)
    try:
        dados = yaml.safe_load(arquivo.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise PerfilInvalido(f"não foi possível ler o perfil '{arquivo}': {exc}") from exc
    except UnicodeDecodeError as exc:
        raise PerfilInvalido(f"o perfil '{arquivo}' não está em UTF-8.") from exc
    except yaml.YAMLError as exc:
        raise PerfilInvalido(f"o perfil '{arquivo}' não é um YAML válido: {exc}") from exc
    try:
        return PerfilICP.model_validate(dados or {}), arquivo
    except ValidationError as exc:
        erros = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'raiz'}: {e['msg']}" for e in exc.errors())
        raise PerfilInvalido(f"perfil '{arquivo}' fora do formato (ver icp.example.yaml): {erros}") from exc


def _sem_acento(texto: str | None) -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in normalizado if not unicodedata.combining(c)).strip().upper()


def _linear(valor: float | None, faixa: Faixa) -> float:
    if valor is None:
        return 0.0
    return min(1.0, max(0.0, (valor - faixa.minimo) / (faixa.ideal - faixa.minimo)))


def _logaritmica(valor: float | None, faixa: Faixa) -> float:
    if valor is None or valor <= 0:
        return 0.0
    minimo = math.log1p(max(faixa.minimo, 0))
    return min(1.0, max(0.0, (math.log1p(valor) - minimo) / (math.log1p(faixa.ideal) - minimo)))


def motivo_exclusao(detalhe: CNPJDetalhe, flags: list[str], perfil: PerfilICP | None) -> str | None:
    if perfil is None:
        return None
    if perfil.excluir.mei and detalhe.eh_mei():
        return "perfil: MEI fora do perfil (excluir.mei)"
    if perfil.excluir.empresa_nova and "empresa_nova" in flags:
        return "perfil: empresa com menos de 6 meses (excluir.empresa_nova)"
    return None


def criterios_fit(
    validacao: ValidacaoResult, enrichment: EnrichmentResult | None, perfil: PerfilICP
) -> dict[str, float]:
    """Encaixe (0 a 1) em cada critério ativo do perfil. Base também para os motivos em linguagem de vendas."""
    from scripts.pipeline.sinais import idade_em_anos, unidades_ativas  # import tardio: evita ciclo

    detalhe = validacao.detalhe
    if detalhe is None:
        return {nome: 0.0 for nome in perfil.criterios_ativos()}

    criterios: dict[str, float] = {}
    if perfil.capital_social is not None:
        capital = float(detalhe.capital_social) if detalhe.capital_social is not None else None
        criterios["capital_social"] = _logaritmica(capital, perfil.capital_social)
    if perfil.anos_de_mercado is not None:
        criterios["anos_de_mercado"] = _linear(idade_em_anos(detalhe), perfil.anos_de_mercado)
    if perfil.unidades is not None:
        criterios["unidades"] = _linear(float(unidades_ativas(detalhe)), perfil.unidades)
    if perfil.portes_preferidos:
        codigos = {_PORTE_CODIGO[p] for p in perfil.portes_preferidos}
        criterios["portes_preferidos"] = 1.0 if detalhe.porte_codigo in codigos else 0.0
    if perfil.municipios_prioritarios:
        matriz = detalhe.estabelecimento_matriz
        municipio = _sem_acento(matriz.municipio if matriz else None)
        alvos = {_sem_acento(m) for m in perfil.municipios_prioritarios}
        criterios["municipios_prioritarios"] = 1.0 if municipio in alvos else 0.0
    if perfil.cnaes_prioritarios:
        principal = detalhe.cnae_principal() or ""
        if any(principal.startswith(p) for p in perfil.cnaes_prioritarios):
            criterios["cnaes_prioritarios"] = 1.0
        elif any(c.startswith(p) for c in detalhe.cnaes_secundarios() for p in perfil.cnaes_prioritarios):
            criterios["cnaes_prioritarios"] = 0.5
        else:
            criterios["cnaes_prioritarios"] = 0.0
    if perfil.tecnologias:
        detectadas = {t.lower() for t in (enrichment.context.tecnologias if enrichment and enrichment.context else [])}
        criterios["tecnologias"] = 1.0 if detectadas & {t.lower() for t in perfil.tecnologias} else 0.0
    if perfil.whatsapp:
        criterios["whatsapp"] = 1.0 if "whatsapp_provavel" in validacao.flags else 0.0
    return criterios


def fit(validacao: ValidacaoResult, enrichment: EnrichmentResult | None, perfil: PerfilICP) -> float:
    """Média dos critérios ativos (0 a 1)."""
    criterios = criterios_fit(validacao, enrichment, perfil)
    return sum(criterios.values()) / len(criterios) if criterios else 0.0


def _reais(valor: float) -> str:
    if valor >= 1_000_000:
        return f"R$ {valor / 1_000_000:.1f} mi".replace(".", ",").replace(",0 mi", " mi")
    if valor >= 1_000:
        return f"R$ {valor / 1_000:.0f} mil"
    return f"R$ {valor:.0f}"


def descrever_criterios(
    validacao: ValidacaoResult, enrichment: EnrichmentResult | None, perfil: PerfilICP
) -> list[tuple[str, float]]:
    """(texto em linguagem de vendas, encaixe 0–1) para cada critério ativo, na ordem de `criterios_fit`."""
    from scripts.pipeline.sinais import idade_em_anos, unidades_ativas  # import tardio: evita ciclo

    detalhe = validacao.detalhe
    textos: list[tuple[str, float]] = []
    for nome, encaixe in criterios_fit(validacao, enrichment, perfil).items():
        texto = nome
        if nome == "capital_social" and perfil.capital_social is not None:
            capital = float(detalhe.capital_social) if detalhe and detalhe.capital_social is not None else 0.0
            texto = f"capital social {_reais(capital)} (ideal a partir de {_reais(perfil.capital_social.ideal)})"
        elif nome == "anos_de_mercado" and perfil.anos_de_mercado is not None:
            idade = idade_em_anos(detalhe) if detalhe else None
            anos = f"{int(idade)} anos de mercado" if idade is not None else "tempo de mercado desconhecido"
            texto = f"{anos} (ideal a partir de {perfil.anos_de_mercado.ideal:g})"
        elif nome == "unidades" and perfil.unidades is not None:
            n = unidades_ativas(detalhe) if detalhe else 1
            unidades = f"{n} unidades ativas" if n > 1 else "1 unidade ativa"
            texto = f"{unidades} (ideal a partir de {perfil.unidades.ideal:g})"
        elif nome == "portes_preferidos":
            texto = "porte preferido" if encaixe else "porte fora do preferido"
        elif nome == "municipios_prioritarios":
            texto = "município prioritário" if encaixe else "fora dos municípios prioritários"
        elif nome == "cnaes_prioritarios":
            texto = {1.0: "CNAE prioritário", 0.5: "CNAE prioritário só como secundário"}.get(
                encaixe, "CNAE fora dos prioritários"
            )
        elif nome == "tecnologias":
            texto = "usa tecnologia do perfil no site" if encaixe else "sem tecnologia do perfil detectada no site"
        elif nome == "whatsapp":
            texto = "tem celular/WhatsApp cadastral" if encaixe else "sem celular/WhatsApp cadastral"
        textos.append((texto, encaixe))
    return textos
