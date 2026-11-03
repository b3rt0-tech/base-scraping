from __future__ import annotations

import re
from collections.abc import Collection
from datetime import date

from scripts.basecnpj.models import CNPJDetalhe

from .contador import telefones_compartilhados
from .validate import ValidacaoResult, _parse_data

FLAG_WHATSAPP = "whatsapp_provavel"
FLAG_MEI = "mei"
FLAG_MULTIUNIDADE = "multiunidade"

_SITUACAO_ATIVA = "02"


def _hoje() -> date:
    return date.today()  # isolado para o teste de snapshot congelar a data


def _digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def celular(ddd: str | None, numero: str | None) -> str | None:
    """Número em formato internacional se for celular. A Receita guarda muito celular no formato antigo, com
    8 dígitos começando em 6–9 (antes do nono dígito): esses ganham o 9 na frente. Fixo (2–5) devolve None.
    """
    ddd, numero = _digitos(ddd), _digitos(numero)
    if len(ddd) != 2:
        return None
    if len(numero) == 8 and numero[0] in "6789":
        numero = "9" + numero
    if len(numero) == 9 and numero[0] == "9":
        return f"+55{ddd}{numero}"
    return None


def celular_cadastral(detalhe: CNPJDetalhe, *, ignorar: Collection[str] = ()) -> str | None:
    """Primeiro telefone cadastral que é celular (provável WhatsApp), pulando fax e os de `ignorar`
    (telefones repetidos entre empresas diferentes, que costumam ser do contador).
    """
    for telefone in detalhe.telefones:
        if (telefone.tipo or "").lower() == "fax":
            continue
        if f"{telefone.ddd or ''}{_digitos(telefone.numero)}" in ignorar:
            continue
        numero = celular(telefone.ddd, telefone.numero)
        if numero:
            return numero
    return None


def eh_mei(detalhe: CNPJDetalhe) -> bool:
    simples = getattr(detalhe, "simples", None)
    return isinstance(simples, dict) and simples.get("opcao_mei") is True


def unidades_ativas(detalhe: CNPJDetalhe) -> int:
    """Estabelecimentos ativos (matriz + filiais). Sem a lista na resposta, conta 1 (a própria empresa)."""
    ativos = [e for e in detalhe.estabelecimentos if e.situacao_cadastral in (None, _SITUACAO_ATIVA)]
    return max(1, len(ativos))


def anos_de_mercado(detalhe: CNPJDetalhe, *, hoje: date | None = None) -> int | None:
    matriz = detalhe.estabelecimento_matriz
    abertura = _parse_data(matriz.data_inicio_atividade if matriz else None)
    if abertura is None:
        return None
    referencia = hoje or _hoje()
    anos = referencia.year - abertura.year - ((referencia.month, referencia.day) < (abertura.month, abertura.day))
    return max(0, anos)


def aplicar_sinais(resultados: list[ValidacaoResult]) -> None:
    """Acrescenta os flags de venda: `whatsapp_provavel` (celular cadastral que não é repetido entre empresas),
    `mei` e `multiunidade`. Roda depois de `marcar_contadores`, sobre o lote inteiro (precisa ver repetição).
    """
    compartilhados = telefones_compartilhados(resultados)
    for r in resultados:
        if r.detalhe is None:
            continue
        novos = []
        r.celular_cadastral = celular_cadastral(r.detalhe, ignorar=compartilhados)
        if r.celular_cadastral:
            novos.append(FLAG_WHATSAPP)
        if eh_mei(r.detalhe):
            novos.append(FLAG_MEI)
        if unidades_ativas(r.detalhe) > 1:
            novos.append(FLAG_MULTIUNIDADE)
        r.flags.extend(f for f in novos if f not in r.flags)
