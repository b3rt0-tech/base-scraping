"""Smoke test contra a API real do BaseCNPJ. Não roda por padrão (usa rede e o
token do `.env`): `uv run pytest -m integration`.
"""

from __future__ import annotations

import os

import pytest

from scripts.basecnpj import routes
from scripts.basecnpj.errors import AuthError
from scripts.basecnpj.models import CNPJDetalhe

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("BASECNPJ_TOKEN"), reason="BASECNPJ_TOKEN não configurado"),
]

CNPJ_PETROBRAS = "33000167000101"


async def test_status():
    assert (await routes.status())["status"] == "ok"


async def test_cnpj_real_bate_com_o_modelo():
    raw = await routes.cnpj(CNPJ_PETROBRAS, use_cache=False)
    detalhe = CNPJDetalhe.model_validate(raw)
    assert "PETROLEO BRASILEIRO" in detalhe.razao_social
    assert detalhe.cnae_principal()
    assert detalhe.estabelecimento_matriz is not None


async def test_avancada_real_aceita_filtros_confirmados():
    data = await routes.avancada(uf="SP", municipio="Campinas", cnae="8630504", situacao="02", per_page=1, use_cache=False)
    assert "items" in data


async def test_token_invalido_vira_auth_error(monkeypatch):
    monkeypatch.setenv("BASECNPJ_TOKEN", "token-invalido")
    with pytest.raises(AuthError):
        await routes.cnpj(CNPJ_PETROBRAS, use_cache=False)


async def test_avancada_real_aceita_per_page_100():
    data = await routes.avancada(uf="SP", municipio="Campinas", cnae="8630504", situacao="02", per_page=100, use_cache=False)
    assert len(data["items"]) == 100
    assert data["per_page"] == 100


async def test_decisores_reais_sem_dado_pessoal_alem_do_nome():
    detalhe = CNPJDetalhe.model_validate(await routes.cnpj(CNPJ_PETROBRAS, use_cache=False))
    decisores = detalhe.decisores()
    assert 1 <= len(decisores) <= 3
    assert all(d.origem == "quadro_societario" and d.nome and d.qualificacao for d in decisores)
    assert "***" not in str(detalhe.model_dump())  # CPF mascarado não é guardado
