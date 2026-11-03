from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.basecnpj import cache as basecnpj_cache
from scripts.basecnpj import client as basecnpj_client
from scripts.pipeline import perfil as perfil_module
from scripts.scraping import email_verify
from scripts.scraping import http as scraping_http

REPO_ROOT = Path(__file__).resolve().parents[1]
CNAE_DICT_PATH = REPO_ROOT / "references" / "cnae.md"
API_URL = "https://basecnpj.app/api/v1/"

CNPJ_PADRAO = "12345678000195"

_CNPJ_RAW: dict[str, Any] = {
    "cnpj_basico": "12345678",
    "cnpj_completo_formatado": "12.345.678/0001-95",
    "razao_social": "CLINICA SORRISO LTDA",
    "capital_social": 50000,
    "porte_codigo": "01",
    "porte_descricao": "Micro empresa",
    "natureza_juridica_descricao": "Sociedade Empresária Limitada",
    "estabelecimento_matriz": {
        "nome_fantasia": "SORRISO",
        "situacao_cadastral": "02",
        "situacao_descricao": "Ativa",
        "data_inicio_atividade": "2015-03-10",
        "cnae_principal_codigo": "8630504",
        "email": "contato@sorriso.com.br",
        "uf": "SP",
        "municipio": "CAMPINAS",
        "cep": "13010-000",
        "logradouro": "RUA DAS FLORES",
    },
    "estabelecimentos": [{"cnpj_completo_formatado": "12.345.678/0001-95", "matriz_filial_codigo": "1"}],
    "cnaes": [
        {"tipo": "Principal", "cnae": "8630504", "desc": "Atividade odontológica"},
        {"tipo": "Secundária", "cnae": "8630506", "desc": "Vacinação"},
    ],
    "telefones": [{"tipo": "Fixo", "ddd": "19", "numero": "32345678"}],
    # formato real de route=cnpj (documento_mascarado e faixa_etaria NÃO podem chegar à saída)
    "socios": [
        {
            "nome_razao": "ANA SOUZA LIMA",
            "tipo_socio": "1",
            "documento_mascarado": "***.123.456-**",
            "data_entrada": "2015-03-10",
            "qualificacao_codigo": "49",
            "qualificacao_descricao": "Sócio-Administrador",
            "faixa_etaria_codigo": "4",
            "faixa_etaria": "41 a 50 anos",
            "pais_codigo": None,
            "pais": None,
            "representante_nome": None,
            "representante_qualificacao": "Não informada",
        },
        {
            "nome_razao": "CARLOS PEREIRA",
            "tipo_socio": "1",
            "documento_mascarado": "***.654.321-**",
            "data_entrada": "2018-06-01",
            "qualificacao_codigo": "22",
            "qualificacao_descricao": "Sócio",
            "faixa_etaria_codigo": "5",
            "faixa_etaria": "51 a 60 anos",
            "pais_codigo": None,
            "pais": None,
            "representante_nome": None,
            "representante_qualificacao": "Não informada",
        },
    ],
}


def make_cnpj_raw(**overrides: Any) -> dict[str, Any]:
    """Payload de `route=cnpj` no formato real observado; `matriz={...}` mescla no estabelecimento_matriz."""
    raw = copy.deepcopy(_CNPJ_RAW)
    matriz = overrides.pop("matriz", None)
    if matriz:
        raw["estabelecimento_matriz"].update(matriz)
    raw.update(overrides)
    return raw


def ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "data": data})


def erro(status: int, code: str, message: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "error": {"code": code, "message": message}}, headers=headers)


@pytest.fixture(autouse=True)
def isolar_logger():
    """O CLI aponta o logger para o stderr do CliRunner (que fecha ao fim da chamada): desfaz depois do teste."""
    yield
    logger = logging.getLogger("base_scraping")
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


@pytest.fixture(autouse=True)
def isolar_ambiente(request, monkeypatch, tmp_path):
    """Cache em tmp, sem espera de retry/delay e sem DNS real. Testes offline usam
    token falso — o token real do `.env` só é usado nos testes `integration`.
    """
    monkeypatch.setattr(basecnpj_cache, "DEFAULT_CACHE_DIR", tmp_path / "cache-basecnpj")
    monkeypatch.setattr(scraping_http, "SCRAPING_CACHE_DIR", tmp_path / "cache-scraping")
    monkeypatch.delenv("BASECNPJ_BASE_URL", raising=False)
    monkeypatch.setattr(perfil_module, "PERFIL_PADRAO", tmp_path / "sem-icp.yaml")  # um icp.yaml local não vaza

    if request.node.get_closest_marker("integration"):
        return

    monkeypatch.setenv("BASECNPJ_TOKEN", "token-de-teste")
    monkeypatch.setattr(basecnpj_client, "_DEFAULT_BACKOFF", lambda retry_state: 0)
    monkeypatch.setattr(scraping_http, "_DEFAULT_BACKOFF", lambda retry_state: 0)

    async def _sem_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(scraping_http, "_respect_domain_delay", _sem_delay)
    monkeypatch.setattr(email_verify, "_resolver_mx_real", lambda dominio: ["mx.fake"])


Handler = Callable[[httpx.Request], httpx.Response] | httpx.Response | dict[str, Any]


@pytest.fixture
def api(respx_mock):
    """Simula a API do BaseCNPJ despachando por `?route=`. Uso:
    `api({"status": {...}, "cnpj": lambda req: ...})`. Devolve a lista de requests recebidos.
    """
    chamadas: list[httpx.Request] = []

    def configurar(handlers: dict[str, Handler]) -> list[httpx.Request]:
        def despachar(request: httpx.Request) -> httpx.Response:
            chamadas.append(request)
            route = request.url.params.get("route")
            if route not in handlers:
                return erro(404, "unknown_route", f"rota não simulada: {route}")
            handler = handlers[route]
            if isinstance(handler, httpx.Response):
                return handler
            if callable(handler):
                return handler(request)
            return ok(handler)

        respx_mock.get(url__startswith=API_URL).mock(side_effect=despachar)
        return chamadas

    return configurar


OUTRO_CNPJ = "98765432000110"


def cnpj_handler(por_cnpj: dict[str, dict | httpx.Response]):
    """Handler de `route=cnpj` que responde conforme o CNPJ pedido."""

    def handler(request):
        resposta = por_cnpj[request.url.params["cnpj"]]
        return resposta if isinstance(resposta, httpx.Response) else ok(resposta)

    return handler


@pytest.fixture
def api_dentista(api):
    """API simulada com 2 leads descobertos: um ativo (CNPJ_PADRAO) e um baixado (OUTRO_CNPJ)."""
    return api(
        {
            "status": {"status": "ok"},
            "avancada": {"items": [{"cnpj_completo": CNPJ_PADRAO}, {"cnpj_completo": OUTRO_CNPJ}], "total_pages": 1},
            "cnpj": cnpj_handler(
                {CNPJ_PADRAO: make_cnpj_raw(), OUTRO_CNPJ: make_cnpj_raw(matriz={"situacao_cadastral": "08"})}
            ),
        }
    )
