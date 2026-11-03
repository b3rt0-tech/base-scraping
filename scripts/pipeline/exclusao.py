"""`--excluir`: quem já está no CRM (cliente, lead em andamento) não volta como lead novo.

Aceita o export do próprio CRM sem preparo: `.csv` (qualquer coluna), `.txt` (um por linha) ou `.json`
(lista de textos/objetos, ou `{"cnpjs": [...], "dominios": [...]}`). Cada valor é reconhecido sozinho:
CNPJ (14 dígitos, com ou sem pontuação) exclui a **empresa inteira** pela raiz (8 primeiros dígitos, então
filiais também saem); e-mail, URL ou domínio exclui quem tem o mesmo domínio no e-mail cadastral.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.scraping.email_verify import GENERIC_PROVIDER_DOMAINS
from scripts.utils import only_digits

_DOMINIO_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
_EXTENSOES = (".csv", ".txt", ".json")


class ExclusaoInvalida(ValueError):
    """Arquivo do `--excluir` ilegível — vira erro JSON `entrada_invalida` no CLI."""


@dataclass
class Exclusoes:
    raizes: set[str] = field(default_factory=set)  # 8 primeiros dígitos do CNPJ
    dominios: set[str] = field(default_factory=set)
    ignorados: int = 0  # domínios de provedor genérico (gmail…) — excluiriam todo mundo

    def __bool__(self) -> bool:
        return bool(self.raizes or self.dominios)

    def assinatura(self) -> str:
        """Entra no `run_id`: outra lista de exclusão é outra consulta (a descoberta repõe os excluídos)."""
        conteudo = json.dumps([sorted(self.raizes), sorted(self.dominios)])
        return hashlib.sha256(conteudo.encode()).hexdigest()[:12]

    def por_cnpj(self, cnpj: str) -> str | None:
        if only_digits(cnpj)[:8] in self.raizes:
            return "já está no CRM (CNPJ)"
        return None

    def por_email(self, email: str | None) -> str | None:
        dominio = _dominio(email or "")
        if dominio and dominio in self.dominios:
            return f"já está no CRM (domínio {dominio})"
        return None


def _dominio(valor: str) -> str | None:
    """Domínio de um e-mail, URL ou domínio solto; None se não parecer um."""
    texto = valor.strip().lower()
    if not texto:
        return None
    if "@" in texto:
        texto = texto.rsplit("@", 1)[1]
    elif "://" in texto:
        texto = urlparse(texto).netloc
    texto = texto.split("/", 1)[0].split(":", 1)[0].strip(".")
    if texto.startswith("www."):
        texto = texto[4:]
    return texto if _DOMINIO_RE.match(texto) else None


def _adicionar(exclusoes: Exclusoes, valor: Any) -> None:
    if isinstance(valor, int):
        valor = str(valor).zfill(14)
    if not isinstance(valor, str):
        return
    digitos = only_digits(valor)
    if len(digitos) == 14 and not re.search(r"[a-zA-Z@]", valor):
        exclusoes.raizes.add(digitos[:8])
        return
    dominio = _dominio(valor)
    if dominio is None:
        return
    if dominio in GENERIC_PROVIDER_DOMAINS:
        exclusoes.ignorados += 1
        return
    exclusoes.dominios.add(dominio)


def _valores_json(dados: Any) -> list[Any]:
    if isinstance(dados, dict):
        return [v for chave in ("cnpjs", "dominios") for v in dados.get(chave, [])]
    if isinstance(dados, list):
        valores: list[Any] = []
        for item in dados:
            valores.extend(item.values() if isinstance(item, dict) else [item])
        return valores
    raise ExclusaoInvalida('JSON do --excluir deve ser uma lista ou {"cnpjs": [...], "dominios": [...]}')


def carregar_exclusoes(caminho: str | Path | None) -> Exclusoes:
    exclusoes = Exclusoes()
    if caminho is None:
        return exclusoes
    arquivo = Path(caminho)
    if arquivo.suffix.lower() not in _EXTENSOES:
        raise ExclusaoInvalida(f"--excluir aceita {', '.join(_EXTENSOES)}; veio '{arquivo.name}'.")
    try:
        texto = arquivo.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            texto = arquivo.read_text(encoding="latin-1")  # CSV exportado pelo Excel no Windows
        except OSError as exc:
            raise ExclusaoInvalida(f"não foi possível ler '{arquivo}': {exc}") from exc
    except OSError as exc:
        raise ExclusaoInvalida(f"não foi possível ler '{arquivo}': {exc}") from exc

    if arquivo.suffix.lower() == ".json":
        try:
            valores = _valores_json(json.loads(texto))
        except json.JSONDecodeError as exc:
            raise ExclusaoInvalida(f"'{arquivo}' não é um JSON válido: {exc}") from exc
    elif arquivo.suffix.lower() == ".csv":
        amostra = texto[:4096]
        try:
            dialeto: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(amostra, delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        valores = [celula for linha in csv.reader(io.StringIO(texto), dialeto) for celula in linha]
    else:
        valores = texto.splitlines()

    for valor in valores:
        _adicionar(exclusoes, valor)
    if not exclusoes:
        raise ExclusaoInvalida(f"'{arquivo}' não tem nenhum CNPJ (14 dígitos) nem domínio/e-mail reconhecível.")
    return exclusoes
