from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.basecnpj import routes
from scripts.basecnpj.config import SKILL_ROOT, Settings

DEFAULT_CNAE_DICT_PATH = SKILL_ROOT / "references" / "cnae.md"  # não depende do diretório de onde a skill roda

# Códigos oficiais de situação cadastral e porte da Receita Federal — só `02` (Ativa),
# `04` (Inapta) e `08` (Baixada) foram confirmados batendo com dado real no Passo 4;
# os demais seguem o layout público da RFB e devem ser tratados como não-confirmados
# até aparecerem numa resposta real.
SITUACAO_CODIGOS = {
    "nula": "01",
    "ativa": "02",
    "suspensa": "03",
    "inapta": "04",
    "baixada": "08",
}

PORTE_CODIGOS = {
    "nao_informado": "00",
    "me": "01",
    "micro": "01",
    "microempresa": "01",
    "epp": "03",
    "pequeno_porte": "03",
    "demais": "05",
}

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|(.+)\|(.+)\|$")
_BACKTICK_CODE_RE = re.compile(r"`(\d+)`")
_CNAE_SUBCLASSE_RE = re.compile(r"(\d{4})-(\d)/(\d{2})")


@dataclass
class NichoEntry:
    aliases: list[str]
    cnaes: list[str]


@dataclass
class ICP:
    nicho: str
    cnaes: list[str]
    uf: str | None = None
    municipio: str | None = None
    porte: str | None = None
    situacao: str | None = "02"
    needs_confirmation: bool = False
    filtros_extra: dict[str, Any] = field(default_factory=dict)
    entrada_dicionario: str | None = None  # aliases da linha de references/cnae.md que casou
    candidatos: list[str] = field(default_factory=list)  # preenchido quando o nicho é ambíguo

    def filtros_por_cnae(self, cnae: str) -> dict[str, Any]:
        filtros: dict[str, Any] = {"cnae": cnae}
        if self.uf:
            filtros["uf"] = self.uf
        if self.municipio:
            filtros["municipio"] = self.municipio
        if self.porte:
            filtros["porte"] = self.porte
        if self.situacao:
            filtros["situacao"] = self.situacao
        filtros.update(self.filtros_extra)
        return filtros


def _cnaes_from_row(cnae_col: str, prefixo_col: str) -> list[str]:
    backticked = _BACKTICK_CODE_RE.findall(prefixo_col)
    if backticked:
        return backticked
    codes = []
    for grupo, seq, sub in _CNAE_SUBCLASSE_RE.findall(cnae_col):
        codes.append(f"{grupo}{seq}{sub}")
    return codes


def load_cnae_dictionary(path: Path | str = DEFAULT_CNAE_DICT_PATH) -> list[NichoEntry]:
    """Faz o parsing da tabela Markdown de `references/cnae.md` (nicho → CNAE)."""
    path = Path(path)
    entries: list[NichoEntry] = []
    for line in path.read_text().splitlines():
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        nicho_col, cnae_col, prefixo_col = (c.strip() for c in match.groups())
        if nicho_col.lower().startswith("nicho") or set(nicho_col) <= {"-", " "}:
            continue  # cabeçalho / linha separadora
        aliases = [a.strip().lower() for a in nicho_col.split(",") if a.strip()]
        cnaes = _cnaes_from_row(cnae_col, prefixo_col)
        if not aliases or not cnaes:
            continue
        entries.append(NichoEntry(aliases=aliases, cnaes=cnaes))
    return entries


_STOPWORDS = frozenset({"de", "da", "do", "das", "dos", "e", "em", "a", "o", "as", "os", "para", "com"})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(texto: str) -> frozenset[str]:
    """Palavras do texto sem acento, em minúsculas e sem conectivos ("de", "em"...)."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return frozenset(t for t in _TOKEN_RE.findall(sem_acento) if t not in _STOPWORDS)


def buscar_nichos(nicho: str, dictionary: list[NichoEntry]) -> list[NichoEntry]:
    """Todas as entradas do dicionário que casam com o nicho pedido (linguagem natural).

    Casa por **palavra inteira**, sem acento/caixa e ignorando conectivos: "ótica" casa com
    "otica", e "pet" não casa mais com "carpete". Alias exato vence; senão vale o alias estar
    contido no pedido ("dentista em campinas") ou o pedido ser parte do alias ("clínica
    odontológica" em "dentista, clínica odontológica"). Mais de uma entrada = nicho ambíguo.
    """
    alvo = _tokens(nicho)
    if not alvo:
        return []
    exatas = [e for e in dictionary if any(_tokens(alias) == alvo for alias in e.aliases)]
    if exatas:
        return exatas
    return [
        e
        for e in dictionary
        if any(t and (t <= alvo or alvo <= t) for t in (_tokens(alias) for alias in e.aliases))
    ]


def resolve_nicho(nicho: str, dictionary: list[NichoEntry]) -> NichoEntry | None:
    """A entrada do nicho, ou None se não houver nenhuma **ou se for ambíguo** (nunca escolhe a primeira)."""
    candidatas = buscar_nichos(nicho, dictionary)
    return candidatas[0] if len(candidatas) == 1 else None


def build_icp(
    *,
    nicho: str,
    uf: str | None = None,
    municipio: str | None = None,
    porte: str | None = None,
    situacao: str = "ativa",
    cep: str | None = None,
    matriz: bool = False,
    cnae_dict_path: Path | str = DEFAULT_CNAE_DICT_PATH,
) -> ICP:
    """Monta o ICP estruturado (S0). Se o nicho não bater no dicionário, devolve
    needs_confirmation=True e cnaes=[] — quem chama (o skill) deve confirmar com o
    usuário antes de qualquer paginação, nunca inventar um CNAE aqui. Se o nicho casar
    com mais de uma entrada, também é needs_confirmation e `candidatos` lista as opções.

    `cep` aceita prefixo (ex. "0507" cobre vários CEPs de um bairro/região — a API
    faz match por prefixo, confirmado empiricamente; ver references/basecnpj.md).
    É o jeito de filtrar por bairro/região dentro de um município, já que a busca
    avançada não tem um filtro de bairro nomeado.

    `matriz=True` restringe a descoberta a matrizes (`matriz_filial="1"`).
    """
    dictionary = load_cnae_dictionary(cnae_dict_path)
    candidatas = buscar_nichos(nicho, dictionary)
    entry = candidatas[0] if len(candidatas) == 1 else None

    situacao_codigo = SITUACAO_CODIGOS.get(situacao.strip().lower(), situacao) if situacao else None
    porte_codigo = PORTE_CODIGOS.get(porte.strip().lower(), porte) if porte else None
    filtros_extra: dict[str, Any] = {"cep": cep} if cep else {}
    if matriz:
        filtros_extra["matriz_filial"] = "1"

    if entry is None:
        return ICP(
            nicho=nicho,
            cnaes=[],
            uf=uf,
            municipio=municipio,
            porte=porte_codigo,
            situacao=situacao_codigo,
            needs_confirmation=True,
            filtros_extra=filtros_extra,
            candidatos=[", ".join(c.aliases) for c in candidatas] if len(candidatas) > 1 else [],
        )

    return ICP(
        nicho=nicho,
        cnaes=entry.cnaes,
        uf=uf,
        municipio=municipio,
        porte=porte_codigo,
        situacao=situacao_codigo,
        needs_confirmation=False,
        filtros_extra=filtros_extra,
        entrada_dicionario=", ".join(entry.aliases),
    )


async def preflight(*, settings: Settings | None = None) -> dict[str, Any]:
    """route=status — confere token e conectividade antes de qualquer descoberta."""
    return await routes.status(settings=settings)
