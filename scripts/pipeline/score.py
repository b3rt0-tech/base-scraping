from __future__ import annotations

from dataclasses import dataclass, field

from scripts.pipeline.perfil import PerfilICP, descrever_criterios
from scripts.pipeline.validate import ValidacaoResult
from scripts.scraping.enrich import EnrichmentResult

# Pesos da seção 8 do ARCTETURE.md — somam 100.
PESO_ADERENCIA_ICP = 30
PESO_QUALIDADE_CONTATO = 30
PESO_PRESENCA_DIGITAL = 20
PESO_SAUDE_CADASTRAL = 20

_EMAIL_NIVEL_PONTOS = {
    "maxima": 22,
    "alta_nominal": 20,
    "alta": 16,
    "boa": 10,
    "media": 5,
    "baixa": 2,
    "invalido": 0,
}

_EMAIL_DESCRICAO = {
    "maxima": "e-mail do site confere com o cadastral",
    "alta_nominal": "e-mail nominal publicado no site oficial",
    "alta": "e-mail publicado no site oficial",
    "boa": "e-mail de domínio próprio com MX válido",
    "media": "e-mail de domínio próprio",
    "baixa": "e-mail de provedor genérico",
}

Item = tuple[str, float]  # (motivo em linguagem de vendas, pontos)


@dataclass
class ScoreBreakdown:
    aderencia_icp: float
    qualidade_contato: float
    presenca_digital: float
    saude_cadastral: float
    fit: float | None = None  # só com perfil (`icp.yaml`) que dá peso ao fit
    # de onde veio cada ponto, por dimensão — a soma de cada lista é o valor da dimensão
    itens: dict[str, list[Item]] = field(default_factory=dict, compare=False)

    @property
    def total(self) -> float:
        soma = self.aderencia_icp + self.qualidade_contato + self.presenca_digital + self.saude_cadastral
        return round(soma + (self.fit or 0.0), 2)

    def as_dict(self) -> dict[str, float]:
        dimensoes = {
            "aderencia_icp": self.aderencia_icp,
            "qualidade_contato": self.qualidade_contato,
            "presenca_digital": self.presenca_digital,
            "saude_cadastral": self.saude_cadastral,
        }
        if self.fit is not None:
            dimensoes["fit"] = self.fit
        return {**dimensoes, "total": self.total}


def _itens_aderencia_icp(validacao: ValidacaoResult) -> list[Item]:
    """CNAE principal vale o peso cheio; só como secundário rebaixa (seção 3.3 e
    S3 do ARCTETURE.md dizem "mantém, rebaixa score" — nunca zera)."""
    if validacao.cnae_match == "secundario":
        return [("o nicho é atividade secundária no CNPJ", round(PESO_ADERENCIA_ICP * 0.6, 2))]
    if validacao.cnae_match == "principal":
        return [("o nicho é a atividade principal (CNAE principal)", float(PESO_ADERENCIA_ICP))]
    return [("lista própria, sem filtro de nicho", float(PESO_ADERENCIA_ICP))]


def _itens_qualidade_contato(validacao: ValidacaoResult, enrichment: EnrichmentResult | None) -> list[Item]:
    itens: list[Item] = []
    pontos_email = 0.0
    if enrichment and enrichment.emails:
        melhor = max(enrichment.emails, key=lambda e: _EMAIL_NIVEL_PONTOS.get(e.confianca, 0))
        pontos_email = float(_EMAIL_NIVEL_PONTOS.get(melhor.confianca, 0))
        if pontos_email:
            itens.append((_EMAIL_DESCRICAO.get(melhor.confianca, "e-mail no site"), pontos_email))

    if enrichment:
        if enrichment.telefones:
            itens.append(("telefone publicado no site", 4.0))
        if enrichment.whatsapp:
            itens.append(("WhatsApp publicado no site", 4.0))

    # sem nenhum e-mail vindo do scraping, o contato cadastral (S3) ainda conta —
    # "ganho direto: contato antes de qualquer scraping" (seção 5 do ARCTETURE.md)
    # (exceto se for de contador: contato que não chega ao decisor não merece o bônus)
    cadastral_util = "contato_cadastral_disponivel" in validacao.flags and "contato_de_contador" not in validacao.flags
    if pontos_email == 0 and cadastral_util:
        itens.append(("e-mail/telefone no cadastro da Receita", 8.0))

    # celular cadastral (provável WhatsApp, não repetido entre empresas): canal de maior resposta em PME
    if "whatsapp_provavel" in validacao.flags and not (enrichment and enrichment.whatsapp):
        itens.append(("celular no cadastro (provável WhatsApp)", 6.0))
    return itens


def _itens_presenca_digital(enrichment: EnrichmentResult | None) -> list[Item]:
    if enrichment is None or not enrichment.site_matched:
        return []
    itens: list[Item] = [("site oficial confirmado (CNPJ/nome/telefone na página)", 12.0)]
    if enrichment.redes_sociais:
        redes = sorted(enrichment.redes_sociais)
        itens.append((f"redes sociais: {', '.join(redes)}", min(6.0, 2.0 * len(redes))))
    if enrichment.context and (enrichment.context.ecommerce or enrichment.context.tecnologias):
        sinais = (["e-commerce"] if enrichment.context.ecommerce else []) + enrichment.context.tecnologias
        itens.append((f"site com {', '.join(sinais)}", 2.0))
    return itens


def _itens_saude_cadastral(validacao: ValidacaoResult) -> list[Item]:
    # "situação ativa" já é pré-requisito pra um lead chegar como validado (S3) —
    # o pontos-base aqui reflete isso; o resto diferencia maturidade/capital.
    itens: list[Item] = [("CNPJ ativo na Receita", 10.0)]
    if "empresa_nova" not in validacao.flags:
        itens.append(("empresa com mais de 6 meses", 5.0))
    detalhe = validacao.detalhe
    capital = getattr(detalhe, "capital_social", None) if detalhe else None
    if capital and capital > 0:
        itens.append(("capital social declarado", 5.0))
    return itens


def _limitar(itens: list[Item], teto: float) -> list[Item]:
    """Soma dos itens não passa do peso da dimensão; o excedente vira um item negativo explícito."""
    soma = sum(p for _, p in itens)
    if soma > teto:
        return [*itens, (f"limitado ao teto da dimensão ({teto:g})", teto - soma)]
    return itens


def _escalar(itens: list[Item], fator: float) -> list[Item]:
    return [(motivo, round(pontos * fator, 2)) for motivo, pontos in itens]


def _soma(itens: list[Item]) -> float:
    return round(sum(p for _, p in itens), 2)


def compute_score(
    validacao: ValidacaoResult,
    enrichment: EnrichmentResult | None = None,
    perfil: PerfilICP | None = None,
) -> ScoreBreakdown:
    """S6 — pesos da seção 8 do ARCTETURE.md: 30 aderência ao ICP, 30 qualidade de
    contato, 20 presença digital, 20 saúde cadastral. `enrichment` pode ser None
    (lead sem scraping bem-sucedido continua pontuável, só com menos campos —
    premissa central do ARCTETURE.md).

    Com perfil (`icp.yaml`), cada dimensão é reescalada para o peso dele (os pesos padrão dão fator 1) e
    o `fit` entra como quinta dimensão. Toda dimensão guarda em `itens` de onde veio cada ponto.
    """
    itens = {
        "aderencia_icp": _limitar(_itens_aderencia_icp(validacao), PESO_ADERENCIA_ICP),
        "qualidade_contato": _limitar(_itens_qualidade_contato(validacao, enrichment), PESO_QUALIDADE_CONTATO),
        "presenca_digital": _limitar(_itens_presenca_digital(enrichment), PESO_PRESENCA_DIGITAL),
        "saude_cadastral": _limitar(_itens_saude_cadastral(validacao), PESO_SAUDE_CADASTRAL),
    }
    fit: float | None = None
    if perfil is not None:
        pesos = perfil.pesos
        itens["aderencia_icp"] = _escalar(itens["aderencia_icp"], pesos.aderencia_icp / PESO_ADERENCIA_ICP)
        itens["qualidade_contato"] = _escalar(
            itens["qualidade_contato"], pesos.qualidade_contato / PESO_QUALIDADE_CONTATO
        )
        itens["presenca_digital"] = _escalar(itens["presenca_digital"], pesos.presenca_digital / PESO_PRESENCA_DIGITAL)
        itens["saude_cadastral"] = _escalar(itens["saude_cadastral"], pesos.saude_cadastral / PESO_SAUDE_CADASTRAL)
        if pesos.fit > 0:
            criterios = descrever_criterios(validacao, enrichment, perfil)
            por_criterio = pesos.fit / len(criterios)
            itens["fit"] = [(texto, round(encaixe * por_criterio, 2)) for texto, encaixe in criterios]
            fit = _soma(itens["fit"])

    return ScoreBreakdown(
        aderencia_icp=_soma(itens["aderencia_icp"]),
        qualidade_contato=_soma(itens["qualidade_contato"]),
        presenca_digital=_soma(itens["presenca_digital"]),
        saude_cadastral=_soma(itens["saude_cadastral"]),
        fit=fit,
        itens=itens,
    )
