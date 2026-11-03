from __future__ import annotations

import re
from collections import defaultdict

from scripts.scraping.email_verify import GENERIC_PROVIDER_DOMAINS

from .validate import ValidacaoResult

FLAG_CONTADOR = "contato_de_contador"

# `consult`/`escritorio` ficam de fora: "consultorio" e "consultoria" são comuns em saúde e serviços
# (falso positivo medido em amostra real de dentistas). `.cnt.br` é o TLD reservado a contabilidade.
_PALAVRAS_CONTABEIS = re.compile(r"contab|contador|assessoria")
_TLD_CONTABIL = ".cnt.br"


def _digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor)


def sinal_email_contabil(email: str | None) -> bool:
    """Palavra contábil na parte local ou no domínio do e-mail (`silvacontabil.com.br`, `fulano.contabil@gmail.com`)."""
    if not email or "@" not in email:
        return False
    email = email.strip().lower()
    _, dominio = email.rsplit("@", 1)
    return bool(_PALAVRAS_CONTABEIS.search(email)) or dominio.endswith(_TLD_CONTABIL)


def _contatos(validacao: ValidacaoResult) -> tuple[str | None, set[str]]:
    detalhe = validacao.detalhe
    if detalhe is None:
        return None, set()
    matriz = detalhe.estabelecimento_matriz
    email = (matriz.email or "").strip().lower() if matriz else ""
    telefones = {f"{t.ddd or ''}{_digitos(t.numero)}" for t in detalhe.telefones if t.numero}
    return email or None, telefones


def _indices(
    resultados: list[ValidacaoResult],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Raízes de CNPJ por e-mail genérico, por telefone e por domínio próprio."""
    por_email: dict[str, set[str]] = defaultdict(set)
    por_telefone: dict[str, set[str]] = defaultdict(set)
    por_dominio_proprio: dict[str, set[str]] = defaultdict(set)
    for r in resultados:
        email, telefones = _contatos(r)
        raiz = r.cnpj[:8]
        if email:
            dominio = email.rsplit("@", 1)[-1]
            if dominio in GENERIC_PROVIDER_DOMAINS:
                por_email[email].add(raiz)
            else:
                por_dominio_proprio[dominio].add(raiz)
        for telefone in telefones:
            por_telefone[telefone].add(raiz)
    return por_email, por_telefone, por_dominio_proprio


def telefones_compartilhados(resultados: list[ValidacaoResult]) -> set[str]:
    """Telefones (DDD+número, só dígitos) que aparecem em ≥2 empresas de raízes diferentes."""
    _, por_telefone, _ = _indices(resultados)
    return {telefone for telefone, raizes in por_telefone.items() if len(raizes) >= 2}


def marcar_contadores(resultados: list[ValidacaoResult]) -> int:
    """O e-mail/telefone cadastral de uma empresa pequena costuma ser o do escritório de contabilidade que a
    abriu — contato que não leva ao decisor. Acrescenta o flag `contato_de_contador` quando:

    - o e-mail tem palavra contábil (sinal individual); ou
    - o mesmo e-mail em provedor genérico, ou o mesmo telefone, aparece em ≥2 empresas de raízes de CNPJ
      diferentes na execução (filiais da mesma empresa compartilham contato legitimamente; e-mail em domínio
      próprio repetido costuma ser uma rede/franquia, e isenta o telefone repetido, então só conta se
      também tiver palavra contábil).

    Devolve quantos leads foram marcados.
    """
    por_email, por_telefone, por_dominio_proprio = _indices(resultados)

    marcados = 0
    for r in resultados:
        email, telefones = _contatos(r)
        # mesmo domínio próprio em ≥2 empresas = rede/franquia: o telefone repetido também é do grupo
        rede = email is not None and len(por_dominio_proprio.get(email.rsplit("@", 1)[-1], ())) >= 2
        repetido = (email in por_email and len(por_email[email]) >= 2) or (
            not rede and any(len(por_telefone[t]) >= 2 for t in telefones)
        )
        if (sinal_email_contabil(email) or repetido) and FLAG_CONTADOR not in r.flags:
            r.flags.append(FLAG_CONTADOR)
            marcados += 1
    return marcados
