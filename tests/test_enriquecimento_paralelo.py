from __future__ import annotations

import asyncio
from collections import Counter

import httpx

from scripts.basecnpj.models import CNPJDetalhe
from scripts.pipeline.validate import ValidacaoResult
from scripts.pipeline_cli import _enriquecer_e_pontuar
from scripts.scraping import http as scraping_http

from .conftest import make_cnpj_raw


def _cnpj(i: int) -> str:
    return f"{i:014d}"


def _resultado(i: int) -> ValidacaoResult:
    detalhe = CNPJDetalhe.model_validate(make_cnpj_raw())
    return ValidacaoResult(cnpj=_cnpj(i), status="validado", detalhe=detalhe, cnae_match="principal")


def _pagina(i: int) -> str:
    return f"<html><body><p>CNPJ {_cnpj(i)}</p><p>{'texto da empresa ' * 20}</p></body></html>"


class Monitor:
    """Conta requisições simultâneas, por domínio e no total, enquanto atende com uma latência simulada."""

    def __init__(self, atraso: float = 0.03, atrasos: dict[str, float] | None = None):
        self.atraso = atraso
        self.atrasos = atrasos or {}
        self.ativos: Counter[str] = Counter()
        self.max_por_dominio: Counter[str] = Counter()
        self.ativos_total = 0
        self.max_total = 0

    async def atender(self, request: httpx.Request) -> httpx.Response:
        dominio = request.url.host
        self.ativos[dominio] += 1
        self.ativos_total += 1
        self.max_por_dominio[dominio] = max(self.max_por_dominio[dominio], self.ativos[dominio])
        self.max_total = max(self.max_total, self.ativos_total)
        try:
            await asyncio.sleep(self.atrasos.get(request.url.path, self.atraso))
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            indice = int(request.url.path.removeprefix("/p") or 0)
            return httpx.Response(200, text=_pagina(indice))
        finally:
            self.ativos[dominio] -= 1
            self.ativos_total -= 1


def _montar(respx_mock, monitor: Monitor) -> None:
    respx_mock.get(url__regex=r"https://.*").mock(side_effect=monitor.atender)


async def test_mesmo_dominio_nunca_roda_em_paralelo_mas_dominios_diferentes_sim(respx_mock):
    monitor = Monitor()
    _montar(respx_mock, monitor)
    # leads 1 e 2 no mesmo domínio; 3..6 em domínios próprios
    sites = {_cnpj(1): "https://mesmo.com.br/p1", _cnpj(2): "https://mesmo.com.br/p2"}
    sites.update({_cnpj(i): f"https://lead{i}.com.br/p{i}" for i in range(3, 7)})

    leads = await _enriquecer_e_pontuar([_resultado(i) for i in range(1, 7)], sites)

    assert all(lead["enriquecimento"]["status"] == "ok" for lead in leads)
    assert monitor.max_por_dominio["mesmo.com.br"] == 1  # inclui o robots.txt
    assert monitor.max_total > 1  # domínios diferentes andaram juntos


async def test_concorrencia_maxima_e_respeitada(respx_mock):
    monitor = Monitor()
    _montar(respx_mock, monitor)
    sites = {_cnpj(i): f"https://lead{i}.com.br/p{i}" for i in range(1, 9)}

    await _enriquecer_e_pontuar([_resultado(i) for i in range(1, 9)], sites, concorrencia=2)

    assert monitor.max_total == 2


async def test_concorrencia_1_equivale_ao_sequencial(respx_mock):
    monitor = Monitor()
    _montar(respx_mock, monitor)
    sites = {_cnpj(i): f"https://lead{i}.com.br/p{i}" for i in range(1, 5)}

    await _enriquecer_e_pontuar([_resultado(i) for i in range(1, 5)], sites, concorrencia=1)

    assert monitor.max_total == 1


async def test_ordem_dos_leads_e_preservada_mesmo_terminando_fora_de_ordem(respx_mock):
    monitor = Monitor(atraso=0.01, atrasos={"/p1": 0.15})  # o primeiro lead é o mais lento
    _montar(respx_mock, monitor)
    sites = {_cnpj(i): f"https://lead{i}.com.br/p{i}" for i in range(1, 5)}

    leads = await _enriquecer_e_pontuar([_resultado(i) for i in range(1, 5)], sites)

    assert [lead["cnpj"] for lead in leads] == [_cnpj(i) for i in range(1, 5)]


async def test_lead_sem_site_candidato_continua_na_saida(respx_mock):
    monitor = Monitor()
    _montar(respx_mock, monitor)

    leads = await _enriquecer_e_pontuar([_resultado(1), _resultado(2)], {_cnpj(2): "https://lead2.com.br/p2"})

    assert [lead["cnpj"] for lead in leads] == [_cnpj(1), _cnpj(2)]
    assert "enriquecimento" not in leads[0]
    assert leads[1]["enriquecimento"]["status"] == "ok"


async def test_teto_de_paginas_por_dominio_vale_com_tarefas_concorrentes(respx_mock):
    monitor = Monitor(atraso=0.01)
    _montar(respx_mock, monitor)
    sessao = scraping_http.ScrapeSession()

    resultados = await asyncio.gather(
        *(scraping_http.fetch(f"https://mesmo.com.br/p{i}", sessao) for i in range(8)), return_exceptions=True
    )

    assert sum(isinstance(r, dict) for r in resultados) == 5
    assert sum(isinstance(r, scraping_http.PageBudgetExceeded) for r in resultados) == 3
    assert sessao.pages_fetched("mesmo.com.br") == 5
