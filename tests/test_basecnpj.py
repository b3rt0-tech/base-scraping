from __future__ import annotations

import httpx
import pytest

from scripts.basecnpj import cache, client, config, routes
from scripts.basecnpj.config import Settings
from scripts.basecnpj.errors import (
    AuthError,
    BaseCNPJError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
    map_error,
)
from scripts.basecnpj.models import CNPJDetalhe
from scripts.utils import only_digits

from .conftest import erro, make_cnpj_raw, ok


def test_only_digits():
    assert only_digits("12.345.678/0001-95") == "12345678000195"
    assert only_digits("") == ""


# --- errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    "status, esperado",
    [
        (400, ValidationError),
        (401, AuthError),
        (403, AuthError),
        (404, NotFoundError),
        (429, RateLimitError),
        (500, ServerError),
        (418, BaseCNPJError),
    ],
)
def test_map_error(status, esperado):
    exc = map_error(status, "x", "msg")
    assert type(exc) is esperado
    assert exc.status_code == status


def test_map_error_rate_limit_guarda_retry_after():
    exc = map_error(429, "rate_limit", retry_after=3.0)
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after == 3.0


# --- config ---------------------------------------------------------------


def test_timeout_maior_para_rotas_pesadas():
    settings = Settings(token=None, base_url="x", default_timeout=10, heavy_route_timeout=60)
    assert settings.timeout_for("avancada") == 60
    assert settings.timeout_for("rede") == 60
    assert settings.timeout_for("cnpj") == 10


def test_dotenv_da_raiz_da_skill_e_lido_de_outro_diretorio(tmp_path, monkeypatch):
    raiz = tmp_path / "skill"
    raiz.mkdir()
    (raiz / ".env").write_text("BASECNPJ_TOKEN=token-da-raiz\n")
    outro = tmp_path / "outro"
    outro.mkdir()
    monkeypatch.chdir(outro)
    monkeypatch.setattr(config, "SKILL_ROOT", raiz)
    monkeypatch.delenv("BASECNPJ_TOKEN")

    config.load_dotenv_files()

    assert config.get_settings().token == "token-da-raiz"


def test_dotenv_raiz_vence_cwd_e_ambiente_vence_os_dois(tmp_path, monkeypatch):
    raiz, cwd = tmp_path / "raiz", tmp_path / "cwd"
    raiz.mkdir()
    cwd.mkdir()
    (raiz / ".env").write_text("A=da-raiz\nB=so-na-raiz\n")
    (cwd / ".env").write_text("A=do-cwd\nC=so-no-cwd\n")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(config, "SKILL_ROOT", raiz)
    for chave in ("A", "B", "C", "D"):
        monkeypatch.delenv(chave, raising=False)
    monkeypatch.setenv("D", "do-ambiente")
    (cwd / ".env").write_text("A=do-cwd\nC=so-no-cwd\nD=do-cwd\n")

    config.load_dotenv_files()

    import os

    assert (os.environ["A"], os.environ["B"], os.environ["C"], os.environ["D"]) == (
        "da-raiz",
        "so-na-raiz",
        "so-no-cwd",
        "do-ambiente",
    )
    for chave in ("A", "B", "C"):
        monkeypatch.delenv(chave)


def test_dotenv_remove_aspas_do_valor(tmp_path, monkeypatch):
    arquivo = tmp_path / ".env"
    arquivo.write_text("X1=\"com-aspas\"\nX2='simples'\nX3=sem\n")
    for chave in ("X1", "X2", "X3"):
        monkeypatch.delenv(chave, raising=False)

    config.load_dotenv_files([arquivo])

    import os

    assert (os.environ["X1"], os.environ["X2"], os.environ["X3"]) == ("com-aspas", "simples", "sem")
    for chave in ("X1", "X2", "X3"):
        monkeypatch.delenv(chave)


def test_skill_root_aponta_para_a_raiz_do_projeto():
    assert (config.SKILL_ROOT / "pyproject.toml").is_file()


# --- cache ----------------------------------------------------------------


def test_cache_grava_le_e_expira(tmp_path):
    cache.write_cache("cnpj", {"cnpj": "1"}, {"a": 1}, cache_dir=tmp_path, now=1000)
    assert cache.read_cache("cnpj", {"cnpj": "1"}, cache_dir=tmp_path, now=1001) == {"a": 1}
    assert cache.read_cache("cnpj", {"cnpj": "2"}, cache_dir=tmp_path, now=1001) is None
    assert cache.read_cache("cnpj", {"cnpj": "1"}, cache_dir=tmp_path, ttl=10, now=1011) is None


def test_cache_nunca_guarda_status(tmp_path):
    cache.write_cache("status", {}, {"status": "ok"}, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_cache_arquivo_corrompido_vira_miss(tmp_path):
    cache.write_cache("cnpj", {"cnpj": "1"}, {"a": 1}, cache_dir=tmp_path)
    next(tmp_path.iterdir()).write_text("{nao é json")
    assert cache.read_cache("cnpj", {"cnpj": "1"}, cache_dir=tmp_path) is None


async def test_get_or_fetch_chama_fetch_uma_vez(tmp_path):
    chamadas = 0

    async def fetch():
        nonlocal chamadas
        chamadas += 1
        return {"v": chamadas}

    primeiro = await cache.get_or_fetch("cnpj", {"cnpj": "1"}, fetch, cache_dir=tmp_path)
    segundo = await cache.get_or_fetch("cnpj", {"cnpj": "1"}, fetch, cache_dir=tmp_path)
    assert primeiro == segundo == {"v": 1}
    assert chamadas == 1


# --- client ---------------------------------------------------------------


async def test_request_desempacota_e_envia_bearer(api):
    chamadas = api({"cnpj": {"razao_social": "X"}})
    data = await client.request("cnpj", {"cnpj": "1"})
    assert data == {"razao_social": "X"}
    assert chamadas[0].headers["Authorization"] == "Bearer token-de-teste"
    assert chamadas[0].url.params["route"] == "cnpj"


async def test_request_sem_token_nao_envia_header(api, monkeypatch):
    monkeypatch.delenv("BASECNPJ_TOKEN")
    chamadas = api({"status": {"status": "ok"}})
    await client.request("status")
    assert "Authorization" not in chamadas[0].headers


@pytest.mark.parametrize("status, code, esperado", [(401, "unauthorized", AuthError), (404, "not_found", NotFoundError), (400, "invalid_cnpj", ValidationError)])
async def test_request_erros_nao_sao_retentados(api, status, code, esperado):
    chamadas = api({"cnpj": erro(status, code)})
    with pytest.raises(esperado):
        await client.request("cnpj", {"cnpj": "1"})
    assert len(chamadas) == 1


async def test_request_500_retenta_tres_vezes(api):
    chamadas = api({"cnpj": erro(500, "server_error")})
    with pytest.raises(ServerError):
        await client.request("cnpj", {"cnpj": "1"})
    assert len(chamadas) == 3


async def test_request_429_respeita_retry_after_e_recupera(api):
    respostas = iter([erro(429, "rate_limit", headers={"Retry-After": "0"}), ok({"fim": True})])
    chamadas = api({"cnpj": lambda req: next(respostas)})
    assert await client.request("cnpj", {"cnpj": "1"}) == {"fim": True}
    assert len(chamadas) == 2


async def test_request_resposta_nao_json(api):
    api({"cnpj": httpx.Response(502, text="<html>bad gateway</html>")})
    with pytest.raises(BaseCNPJError) as info:
        await client.request("cnpj", {"cnpj": "1"})
    assert info.value.code == "invalid_json"


# --- routes ---------------------------------------------------------------


async def test_avancada_remove_filtros_none(api):
    chamadas = api({"avancada": {"items": []}})
    await routes.avancada(uf="SP", cnae="8630504", porte=None, use_cache=False)
    params = dict(chamadas[0].url.params)
    assert params == {"route": "avancada", "uf": "SP", "cnae": "8630504", "page": "1", "per_page": "15"}


async def test_cnpj_normaliza_e_usa_cache(api):
    chamadas = api({"cnpj": {"razao_social": "X"}})
    await routes.cnpj("12.345.678/0001-95")
    await routes.cnpj("12345678000195")
    assert len(chamadas) == 1
    assert chamadas[0].url.params["cnpj"] == "12345678000195"


async def test_status_nunca_usa_cache(api):
    chamadas = api({"status": {"status": "ok"}})
    await routes.status()
    await routes.status()
    assert len(chamadas) == 2


# --- models ---------------------------------------------------------------


def test_cnpj_detalhe_helpers():
    detalhe = CNPJDetalhe.model_validate(make_cnpj_raw())
    assert detalhe.cnae_principal() == "8630504"
    assert detalhe.cnaes_secundarios() == ["8630506"]
    assert detalhe.telefone_principal() == "(19) 32345678"
    assert detalhe.matriz_filial_codigo() == "1"


def test_cnpj_detalhe_aceita_campos_extras_e_ausentes():
    detalhe = CNPJDetalhe.model_validate(make_cnpj_raw(telefones=[], cnaes=[], campo_novo={"x": 1}))
    assert detalhe.telefone_principal() is None
    assert detalhe.cnae_principal() is None
    assert detalhe.campo_novo == {"x": 1}
