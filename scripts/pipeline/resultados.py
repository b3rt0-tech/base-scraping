"""Aprender com resultados: o que aconteceu com cada lead depois de entregue.

- `entregues`: foto dos atributos de negócio de cada lead entregue (CNAE, porte, cidade, prioridade, sinais) —
  é o que o relatório cruza com o desfecho.
- `resultados`: o desfecho que o vendedor registrou (`registrar-resultado`).

Só CNPJ e atributos da empresa — nenhum nome, e-mail ou telefone (LGPD). Quem lê o relatório e propõe
ajustar o `icp.yaml` é o consultor (o Claude); aqui é só contagem.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any

from scripts.utils import only_digits

STATUS = ("contatado", "respondeu", "reuniao", "ganho", "perdido", "nao_e_fit")
# avanço no funil: quem chegou a "reuniao" também "respondeu" e foi "contatado"
_ETAPA = {"contatado": 1, "respondeu": 2, "reuniao": 3, "ganho": 4, "perdido": 1, "nao_e_fit": 1}
_SINONIMOS = {
    "contactado": "contatado",
    "abordado": "contatado",
    "respondido": "respondeu",
    "reuniao marcada": "reuniao",
    "reuniao agendada": "reuniao",
    "demo": "reuniao",
    "venda": "ganho",
    "fechado": "ganho",
    "cliente": "ganho",
    "ganhou": "ganho",
    "perda": "perdido",
    "perdeu": "perdido",
    "sem interesse": "perdido",
    "nao e fit": "nao_e_fit",
    "fora do perfil": "nao_e_fit",
}
SINAIS = ("mei", "whatsapp_provavel", "multiunidade", "contato_de_contador")
DIMENSOES = ("cnae_principal", "porte", "municipio", "prioridade")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entregues (
    cnpj TEXT PRIMARY KEY,
    prioridade TEXT,
    score REAL,
    cnae_principal TEXT,
    porte TEXT,
    municipio TEXT,
    uf TEXT,
    flags_json TEXT,
    entregue_em REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resultados (
    cnpj TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    motivo TEXT,
    atualizado_em REAL NOT NULL
);
"""


class ResultadoInvalido(ValueError):
    """Arquivo do `registrar-resultado` ilegível — vira erro JSON `entrada_invalida` no CLI."""


def preparar(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def registrar_entregues(conn: sqlite3.Connection, leads: list[dict[str, Any]], *, agora: float | None = None) -> None:
    """Guarda a foto de negócio de cada lead entregue (a última entrega vale)."""
    preparar(conn)
    momento = agora if agora is not None else time.time()
    conn.executemany(
        """
        INSERT INTO entregues (cnpj, prioridade, score, cnae_principal, porte, municipio, uf, flags_json, entregue_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cnpj) DO UPDATE SET prioridade=excluded.prioridade, score=excluded.score,
            cnae_principal=excluded.cnae_principal, porte=excluded.porte, municipio=excluded.municipio,
            uf=excluded.uf, flags_json=excluded.flags_json, entregue_em=excluded.entregue_em
        """,
        [
            (
                lead["cnpj"],
                lead.get("prioridade"),
                lead.get("score"),
                lead.get("cnae_principal"),
                lead.get("porte"),
                (lead.get("endereco") or {}).get("municipio"),
                (lead.get("endereco") or {}).get("uf"),
                json.dumps(lead.get("flags") or []),
                momento,
            )
            for lead in leads
        ],
    )
    conn.commit()


def trabalhados(conn: sqlite3.Connection) -> set[str]:
    """CNPJs que já têm resultado registrado — não voltam como lead novo."""
    preparar(conn)
    return {row[0] for row in conn.execute("SELECT cnpj FROM resultados")}


def _normalizar_status(valor: Any) -> str | None:
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().strip().lower()
    texto = texto.replace("_", " ").replace("-", " ")
    texto = " ".join(texto.split())
    if texto.replace(" ", "_") in STATUS:
        return texto.replace(" ", "_")
    return _SINONIMOS.get(texto)


def _ler_registros(caminho: Path) -> list[dict[str, Any]]:
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        texto = caminho.read_text(encoding="latin-1")
    except OSError as exc:
        raise ResultadoInvalido(f"não foi possível ler '{caminho}': {exc}") from exc

    if caminho.suffix.lower() == ".json":
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ResultadoInvalido(f"'{caminho}' não é um JSON válido: {exc}") from exc
        if isinstance(dados, dict):  # {"<cnpj>": "<status>"} ou {"<cnpj>": {"status": ..., "motivo": ...}}
            return [
                {"cnpj": cnpj, **(valor if isinstance(valor, dict) else {"status": valor})}
                for cnpj, valor in dados.items()
            ]
        if isinstance(dados, list) and all(isinstance(item, dict) for item in dados):
            return dados
        raise ResultadoInvalido('JSON deve ser uma lista de {"cnpj", "status", "motivo"?} ou {"<cnpj>": "<status>"}')

    if caminho.suffix.lower() == ".csv":
        try:
            dialeto: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
        return [{(chave or "").strip().lower(): valor for chave, valor in linha.items()} for linha in leitor]

    raise ResultadoInvalido(f"registrar-resultado aceita .csv ou .json; veio '{caminho.name}'.")


def registrar(
    conn: sqlite3.Connection, caminho: str | Path, *, agora: float | None = None
) -> dict[str, Any]:
    """Grava o desfecho de cada CNPJ do arquivo (o último registro vale). Linhas inválidas são devolvidas
    com o motivo, sem derrubar as válidas.
    """
    preparar(conn)
    registros = _ler_registros(Path(caminho))
    momento = agora if agora is not None else time.time()
    validos: list[tuple[str, str, str | None, float]] = []
    ignorados: list[dict[str, Any]] = []
    for linha, registro in enumerate(registros, start=1):
        cnpj = only_digits(str(registro.get("cnpj") or ""))
        if len(cnpj) != 14:
            ignorados.append({"linha": linha, "motivo": f"CNPJ inválido: {registro.get('cnpj')!r}"})
            continue
        status = _normalizar_status(registro.get("status"))
        if status is None:
            ignorados.append(
                {"linha": linha, "motivo": f"status {registro.get('status')!r} fora de: {', '.join(STATUS)}"}
            )
            continue
        motivo = str(registro.get("motivo") or "").strip() or None
        validos.append((cnpj, status, motivo, momento))
    if not validos and ignorados:
        raise ResultadoInvalido(f"nenhuma linha válida em '{caminho}': {ignorados[0]['motivo']}")
    conn.executemany(
        """
        INSERT INTO resultados (cnpj, status, motivo, atualizado_em) VALUES (?, ?, ?, ?)
        ON CONFLICT(cnpj) DO UPDATE SET status=excluded.status, motivo=excluded.motivo,
            atualizado_em=excluded.atualizado_em
        """,
        validos,
    )
    conn.commit()
    por_status: dict[str, int] = {}
    for _, status, _, _ in validos:
        por_status[status] = por_status.get(status, 0) + 1
    return {"registrados": len(validos), "por_status": por_status, "ignorados": ignorados}


def _taxas(grupo: list[str]) -> dict[str, Any]:
    total = len(grupo)
    etapas = [_ETAPA[s] for s in grupo]
    respondeu = sum(e >= 2 for e in etapas)
    reuniao = sum(e >= 3 for e in etapas)
    ganho = grupo.count("ganho")
    return {
        "total": total,
        "respondeu": respondeu,
        "reuniao": reuniao,
        "ganho": ganho,
        "perdido": grupo.count("perdido"),
        "nao_e_fit": grupo.count("nao_e_fit"),
        "taxa_resposta": round(respondeu / total, 3) if total else 0.0,
        "taxa_reuniao": round(reuniao / total, 3) if total else 0.0,
        "taxa_ganho": round(ganho / total, 3) if total else 0.0,
    }


def relatorio(conn: sqlite3.Connection) -> dict[str, Any]:
    """Funil por atributo do lead entregue: onde a conversão é boa (e o `icp.yaml` deve priorizar) e onde é
    ruim (candidato a `excluir` ou peso menor). Grupos ordenados por total, para o consultor ver primeiro o
    que tem amostra.
    """
    preparar(conn)
    linhas = conn.execute(
        """
        SELECT r.cnpj, r.status, r.motivo, e.prioridade, e.cnae_principal, e.porte, e.municipio, e.flags_json
        FROM resultados r LEFT JOIN entregues e USING (cnpj)
        """
    ).fetchall()
    geral = [row[1] for row in linhas]
    por_dimensao: dict[str, list[dict[str, Any]]] = {}
    indices = {"prioridade": 3, "cnae_principal": 4, "porte": 5, "municipio": 6}
    for dimensao in DIMENSOES:
        grupos: dict[str, list[str]] = {}
        for row in linhas:
            valor = row[indices[dimensao]]
            if valor:
                grupos.setdefault(str(valor), []).append(row[1])
        por_dimensao[dimensao] = sorted(
            ({"valor": valor, **_taxas(status)} for valor, status in grupos.items()), key=lambda g: -g["total"]
        )
    sinais = []
    for sinal in SINAIS:
        com = [row[1] for row in linhas if row[7] and sinal in json.loads(row[7])]
        sem = [row[1] for row in linhas if row[7] and sinal not in json.loads(row[7])]
        sinais.append({"sinal": sinal, "com": _taxas(com), "sem": _taxas(sem)})
    motivos_perda: dict[str, int] = {}
    for row in linhas:
        if row[1] in ("perdido", "nao_e_fit") and row[2]:
            motivos_perda[row[2]] = motivos_perda.get(row[2], 0) + 1
    return {
        "geral": _taxas(geral),
        "sem_dados_de_entrega": sum(1 for row in linhas if row[4] is None and row[3] is None),
        "por_dimensao": por_dimensao,
        "por_sinal": sinais,
        "motivos_de_perda": sorted(
            ({"motivo": m, "total": n} for m, n in motivos_perda.items()), key=lambda m: -m["total"]
        ),
    }
