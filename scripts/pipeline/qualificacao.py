"""`--qualificacoes`: o veredito do consultor de vendas (o Claude) entra no lead.

Quem decide é o Claude, lendo o JSON da própria execução e o produto do usuário (regra de ouro: scripts
não chamam LLM). Este módulo só valida o arquivo e mescla `qualificacao` em cada lead — o veredito
recomenda, nunca remove: um lead `descartar` continua na saída, no fim da lista, para o usuário reverter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from scripts.utils import only_digits

Veredito = Literal["valido", "talvez", "descartar"]
ORDEM_VEREDITO = {"valido": 2, "talvez": 1, "descartar": 0}


class QualificacaoInvalida(ValueError):
    """Arquivo do `--qualificacoes` fora do formato — vira erro JSON `entrada_invalida` no CLI."""


class Qualificacao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    veredito: Veredito
    motivo: str
    abordagem: str | None = None
    mensagem: str | None = None  # 1ª mensagem (WhatsApp ou e-mail), escrita para os leads A

    @field_validator("motivo")
    @classmethod
    def _motivo_preenchido(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("o motivo do veredito não pode ficar vazio")
        return valor.strip()


def carregar_qualificacoes(caminho: str | Path | None) -> dict[str, dict[str, Any]]:
    """`{"<cnpj>": {"veredito": "valido|talvez|descartar", "motivo": "...", "abordagem"?, "mensagem"?}}`."""
    if caminho is None:
        return {}
    arquivo = Path(caminho)
    formato = 'O --qualificacoes deve ser {"<cnpj>": {"veredito": "valido|talvez|descartar", "motivo": "..."}}'
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificacaoInvalida(f"não foi possível ler '{arquivo}' como JSON: {exc}") from exc
    if not isinstance(dados, dict):
        raise QualificacaoInvalida(f"{formato}; veio {type(dados).__name__}.")
    qualificacoes: dict[str, dict[str, Any]] = {}
    for chave, valor in dados.items():
        cnpj = only_digits(str(chave))
        if len(cnpj) != 14:
            raise QualificacaoInvalida(f"{formato}; a chave '{chave}' não é um CNPJ.")
        try:
            qualificacoes[cnpj] = Qualificacao.model_validate(valor).model_dump(exclude_none=True)
        except ValidationError as exc:
            erros = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'valor'}: {e['msg']}" for e in exc.errors())
            raise QualificacaoInvalida(f"qualificação de '{chave}' fora do formato: {erros}") from exc
    return qualificacoes


def aplicar(leads: list[dict[str, Any]], qualificacoes: dict[str, dict[str, Any]]) -> int:
    """Mescla `qualificacao` nos leads (e a proveniência dela). Devolve quantos CNPJs do arquivo não estavam
    entre os leads — o skill avisa, porque provavelmente é de outra consulta.
    """
    if not qualificacoes:
        return 0
    por_cnpj = {lead["cnpj"]: lead for lead in leads}
    for cnpj, qualificacao in qualificacoes.items():
        lead = por_cnpj.get(cnpj)
        if lead is None:
            continue
        lead["qualificacao"] = qualificacao
        proveniencia = lead.get("proveniencia")
        if proveniencia is not None and "score" in proveniencia:
            origem = {"fonte": "consultor", "rota_ou_url": "--qualificacoes"}
            proveniencia["qualificacao"] = {**proveniencia["score"], **origem}
    return sum(1 for cnpj in qualificacoes if cnpj not in por_cnpj)
