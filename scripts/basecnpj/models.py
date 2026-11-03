from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Modelos deliberadamente parciais: só os campos que o pipeline (Passo 5+) vai
# precisar de fato. `extra="allow"` porque a resposta real do BaseCNPJ tem mais
# campos do que os documentados no ARCTETURE.md, e não queremos quebrar o parsing
# por causa de um campo novo/desconhecido.


class EstabelecimentoMatriz(BaseModel):
    model_config = ConfigDict(extra="allow")

    nome_fantasia: str | None = None
    situacao_cadastral: str | None = None
    situacao_descricao: str | None = None
    data_inicio_atividade: str | None = None
    cnae_principal_codigo: str | None = None
    email: str | None = None
    uf: str | None = None
    municipio: str | None = None
    cep: str | None = None
    logradouro: str | None = None


class CnaeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    tipo: str  # "Principal" | "Secundária"
    cnae: str
    desc: str | None = None


class Telefone(BaseModel):
    model_config = ConfigDict(extra="allow")

    tipo: str | None = None
    ddd: str | None = None
    numero: str | None = None


class Estabelecimento(BaseModel):
    model_config = ConfigDict(extra="allow")

    cnpj_completo_formatado: str | None = None
    matriz_filial_codigo: str | None = None  # "1"=Matriz, "2"=Filial
    matriz_filial: str | None = None
    situacao_cadastral: str | None = None
    uf: str | None = None
    municipio: str | None = None


MAX_DECISORES = 3  # empresa grande tem dezenas de sócios/diretores: o vendedor precisa de poucos nomes


def _sem_acento(texto: str | None) -> str:
    base = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in base if not unicodedata.combining(c)).strip()


# Marcas de pessoa jurídica no nome do sócio. A API às vezes mascara o CNPJ de um sócio-empresa como se fosse
# CPF ("***.***.*27-**") e marca tipo_socio "1" (visto em holdings "... PARTICIPACOES LTDA"), então o nome é a
# última linha de defesa. "SA" solto fica de fora: é sobrenome (Sá); só a forma pontuada S.A./S/A conta.
_MARCA_PJ = re.compile(
    r"\b(ltda|limitada|eireli|holding|participacoes|empreendimentos|investimentos|administradora|"
    r"associacao|fundacao|fundo|cooperativa|condominio)\b|\bs\s*[./]\s*a\b\.?|\s(me|epp)$"
)


def parece_pessoa_juridica(nome: str | None) -> bool:
    return bool(_MARCA_PJ.search(_sem_acento(nome)))


class Socio(BaseModel):
    """Sócio/administrador de `socios[]` (raiz de route=cnpj), só com o que o pipeline usa.

    O payload traz também `documento_mascarado` (CPF parcial) e `faixa_etaria`: são dados pessoais
    que não servem à prospecção, então são lidos aqui só para decidir se é pessoa física e **descartados** —
    nunca chegam ao modelo, ao SQLite de checkpoint nem à saída (minimização, LGPD).
    """

    model_config = ConfigDict(extra="ignore")

    nome: str
    qualificacao: str | None = None
    entrada: str | None = None
    pessoa_fisica: bool = False

    @model_validator(mode="before")
    @classmethod
    def _do_payload_da_api(cls, dados: Any) -> Any:
        if not isinstance(dados, dict) or "nome_razao" not in dados:
            return dados  # já no formato do modelo (ex. recarregado do checkpoint)
        documento = dados.get("documento_mascarado") or ""
        # CPF mascarado ("***.***.*37-**") é o sinal confiável de pessoa física; tipo_socio "1" confere nos dados
        # reais (177/177), mas "3" é estrangeiro e o código não é documentado, então o documento vem primeiro.
        pessoa_fisica = documento.startswith("***.") or (not documento and dados.get("tipo_socio") == "1")
        pessoa_fisica = pessoa_fisica and not parece_pessoa_juridica(dados["nome_razao"])
        return {
            "nome": (dados["nome_razao"] or "").strip(),  # a API devolve nomes com espaço no fim
            "qualificacao": (dados.get("qualificacao_descricao") or "").strip() or None,
            "entrada": dados.get("data_entrada"),
            "pessoa_fisica": pessoa_fisica,
        }


@dataclass(frozen=True)
class Decisor:
    nome: str
    qualificacao: str
    desde: str | None
    origem: Literal["quadro_societario", "razao_social_titular"]


# Quem costuma decidir a compra, do mais para o menos provável. Casado por palavra na qualificação
# (sem acento), porque os códigos da Receita são muitos e a descrição é o que a API entrega de forma estável.
_PRIORIDADE_QUALIFICACAO = (
    ("administrador", 0),  # Sócio-Administrador, Administrador
    ("presidente", 1),
    ("diretor", 2),
    ("gerente", 3),  # Sócio-Gerente
    ("titular", 4),
    ("socio", 5),
)
_PRIORIDADE_OUTRAS = 9  # conselheiro, procurador...


def _prioridade(qualificacao: str | None) -> int:
    q = _sem_acento(qualificacao)
    if "conselheiro" in q:
        return _PRIORIDADE_OUTRAS
    return next((peso for palavra, peso in _PRIORIDADE_QUALIFICACAO if palavra in q), _PRIORIDADE_OUTRAS)


class CNPJDetalhe(BaseModel):
    """Resposta de route=cnpj. Ver references/basecnpj.md para o formato real observado."""

    model_config = ConfigDict(extra="allow")

    cnpj_basico: str
    cnpj_completo_formatado: str
    razao_social: str
    capital_social: float | int | None = None
    porte_codigo: str | None = None
    porte_descricao: str | None = None
    natureza_juridica_descricao: str | None = None
    estabelecimento_matriz: EstabelecimentoMatriz | None = None
    estabelecimentos: list[Estabelecimento] = []
    cnaes: list[CnaeItem] = []
    telefones: list[Telefone] = []
    socios: list[Socio] = []

    def telefone_principal(self) -> str | None:
        if not self.telefones:
            return None
        primeiro = self.telefones[0]
        return f"({primeiro.ddd}) {primeiro.numero}" if primeiro.ddd else primeiro.numero

    def eh_mei(self) -> bool:
        simples = getattr(self, "simples", None)  # `simples{opcao_simples, opcao_mei}` vem como campo extra
        return isinstance(simples, dict) and simples.get("opcao_mei") is True

    def cnae_principal(self) -> str | None:
        return next((c.cnae for c in self.cnaes if c.tipo == "Principal"), None)

    def cnaes_secundarios(self) -> list[str]:
        return [c.cnae for c in self.cnaes if c.tipo != "Principal"]

    def matriz_filial_codigo(self) -> str | None:
        for est in self.estabelecimentos:
            if est.cnpj_completo_formatado == self.cnpj_completo_formatado:
                return est.matriz_filial_codigo
        return None

    def decisores(self, limite: int = MAX_DECISORES) -> list[Decisor]:
        """Quem provavelmente decide a compra: pessoas físicas do quadro, sem repetição, ordenadas por
        qualificação (administrador > presidente > diretor > gerente > titular > sócio) e depois por data de entrada.

        Empresário individual/MEI não tem `socios[]` — o titular **é** a empresa e o nome dele é a razão social —,
        então vira o único decisor, marcado com a origem `razao_social_titular`. Sócio pessoa jurídica ou
        estrangeiro não é decisor.
        """
        pessoas = sorted(
            # o nome é conferido de novo aqui: checkpoints antigos guardaram pessoa_fisica sem essa checagem
            (s for s in self.socios if s.pessoa_fisica and not parece_pessoa_juridica(s.nome)),
            key=lambda s: (_prioridade(s.qualificacao), s.entrada or "9999", _sem_acento(s.nome)),
        )
        vistos: set[str] = set()
        decisores: list[Decisor] = []
        for socio in pessoas:
            chave = _sem_acento(socio.nome)
            if chave in vistos:
                continue  # a mesma pessoa aparece em mais de uma qualificação (ex. presidente e diretor)
            vistos.add(chave)
            decisores.append(
                Decisor(socio.nome, socio.qualificacao or "Sócio", socio.entrada, "quadro_societario")
            )

        if not decisores and "individual" in _sem_acento(self.natureza_juridica_descricao):
            matriz = self.estabelecimento_matriz
            decisores.append(
                Decisor(
                    self.razao_social,
                    "Titular (empresário individual)",
                    matriz.data_inicio_atividade if matriz else None,
                    "razao_social_titular",
                )
            )
        return decisores[:limite]
