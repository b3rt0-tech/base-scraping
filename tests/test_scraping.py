from __future__ import annotations

import threading

import httpx
import pytest

from scripts.scraping import http as scraping_http
from scripts.scraping.contacts import discover_contacts, extract_contacts_from_html
from scripts.scraping.context_signals import detect_context_signals, parece_requer_js
from scripts.scraping.email_verify import verify_email
from scripts.scraping.enrich import enrich
from scripts.scraping.site_discovery import validar_match

from .conftest import CNPJ_PADRAO

SITE = "https://sorriso.com.br"

HOME_HTML = """
<html><head><meta name="generator" content="WordPress 6.0"></head><body>
<header><a href="/contato">Contato</a> <a href="/sobre-nos/">Sobre</a> <a href="https://outro.com/contato">fora</a></header>
<main><h1>Clínica Sorriso</h1><p>Atendimento odontológico em Campinas. Fale com nossa diretora clínica.</p>
<p>Agende sua consulta conosco, temos horários flexíveis durante toda a semana para você e sua família.</p></main>
<footer>CNPJ 12.345.678/0001-95 · (19) 3234-5678 · contato@sorriso.com.br
<a href="https://www.instagram.com/sorriso">IG</a> <a href="https://wa.me/5519999999999">WhatsApp</a></footer>
</body></html>
"""

CONTATO_HTML = "<html><body>Fale com ana.souza@sorriso.com.br ou (19) 99999-8888</body></html>"


def mock_site(respx_mock, paginas: dict[str, str], robots: str = "") -> None:
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
    for path, html in paginas.items():
        respx_mock.get(f"{SITE}{path}").mock(return_value=httpx.Response(200, text=html))


# --- site_discovery (4.1) -------------------------------------------------


def test_validar_match_por_cnpj_formatado():
    resultado = validar_match(HOME_HTML, cnpj=CNPJ_PADRAO)
    assert resultado.matched
    assert resultado.sinais == ["cnpj"]


def test_validar_match_por_nome_e_telefone():
    resultado = validar_match(HOME_HTML, cnpj="99999999000199", nome_fantasia="clínica  SORRISO", telefone="(19) 3234-5678")
    assert resultado.sinais == ["nome_fantasia", "telefone"]


def test_validar_match_fraco():
    resultado = validar_match("<p>outra empresa 123</p>", cnpj=CNPJ_PADRAO, razao_social="CLINICA SORRISO LTDA")
    assert not resultado.matched


def test_validar_match_telefone_sem_ddd_precisa_terminar_no_numero():
    assert validar_match("tel 32345678", cnpj="0", telefone="32345678").matched
    assert not validar_match("cod 3234567811", cnpj="0", telefone="32345678").matched


# --- contacts (4.2) -------------------------------------------------------


def test_extract_contacts_from_html():
    extraido = extract_contacts_from_html(HOME_HTML)
    assert extraido.emails == {"contato@sorriso.com.br"}
    assert "1932345678" in extraido.telefones
    assert extraido.whatsapp == {"5519999999999"}
    assert extraido.redes_sociais == {"instagram": "https://www.instagram.com/sorriso"}


@pytest.mark.parametrize(
    "html",
    [
        '<p>veja <img src="x"> logo@2x.png e icon@3x.webp</p>',
        '<img src="logo@2x.png"> <script src="app@1.2.js"></script>',
        '<script>Sentry.init({dsn: "https://abc@o123.ingest.sentry.io/456"})</script>',
        '<body><style>.a{background:url(bg@2x.png)}</style><noscript>ping@track.com</noscript></body>',
    ],
)
def test_extract_emails_ignora_assets_e_conteudo_nao_visivel(html):
    assert extract_contacts_from_html(html).emails == set()


def test_extract_emails_continua_pegando_o_legitimo_ao_lado_do_ruido():
    html = '<script>var d="spam@tracker.com"</script><p>logo@2x.png fale com vendas@loja.com.br</p>'
    assert extract_contacts_from_html(html).emails == {"vendas@loja.com.br"}


def test_extract_emails_de_mailto():
    html = (
        '<a href="mailto:Ana.Souza@Empresa.com.br?subject=Or%C3%A7amento">escreva</a>'
        '<a href="MAILTO:a@x.com.br, b@x.com.br">dois</a>'
        '<a href="mailto:logo@2x.png">lixo</a><a href="mailto:">vazio</a>'
    )
    assert extract_contacts_from_html(html).emails == {"ana.souza@empresa.com.br", "a@x.com.br", "b@x.com.br"}


async def test_discover_contacts_segue_paginas_de_alto_sinal(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML, "/contato": CONTATO_HTML, "/sobre-nos/": "<p>sobre</p>"})
    resultado = await discover_contacts(f"{SITE}/", scraping_http.ScrapeSession())
    assert resultado.paginas_visitadas == [f"{SITE}/", f"{SITE}/contato", f"{SITE}/sobre-nos/"]
    assert resultado.emails == {"contato@sorriso.com.br", "ana.souza@sorriso.com.br"}
    assert "19999998888" in resultado.telefones


# --- email_verify (4.3) ---------------------------------------------------


def _sem_mx(dominio):
    return []


@pytest.mark.parametrize(
    "email, kwargs, nivel, confianca",
    [
        ("nao-e-email", {}, 0, "invalido"),
        ("fulano@gmail.com", {}, 1, "baixa"),
        ("x@mailinator.com", {}, 1, "baixa"),
        ("contato@empresa.com.br", {"mx_resolver": _sem_mx}, 2, "media"),
        ("contato@empresa.com.br", {}, 3, "boa"),
        ("contato@empresa.com.br", {"found_on_official_site": True}, 4, "alta"),
        ("ana@empresa.com.br", {"found_on_official_site": True}, 5, "alta_nominal"),
    ],
)
def test_verify_email_escada(email, kwargs, nivel, confianca):
    resultado = verify_email(email, **kwargs)
    assert resultado.nivel == nivel
    assert resultado.confianca == confianca


def test_verify_email_cruzamento_cadastral_e_maxima():
    resultado = verify_email(" Contato@Empresa.com.br", email_cadastral="contato@empresa.com.br ")
    assert resultado.cruzamento_cadastral
    assert resultado.confianca == "maxima"


def test_verify_email_erro_de_dns_nao_quebra():
    import dns.exception

    def resolver_com_erro(dominio):
        raise dns.exception.Timeout()

    assert verify_email("a@empresa.com.br", mx_resolver=resolver_com_erro).nivel == 2


# --- context_signals (4.4) ------------------------------------------------


def test_detect_context_signals():
    sinais = detect_context_signals(HOME_HTML + "<button>Adicionar ao carrinho</button>")
    assert sinais.ecommerce
    assert sinais.tecnologias == ["wordpress"]


@pytest.mark.xfail(
    strict=True,
    reason="bug: 'diretora' casa 'diretor' e 'diretora' em TEAM_TITLE_KEYWORDS (texto.count por substring)",
)
def test_indicios_equipe_nao_conta_feminino_em_dobro():
    assert detect_context_signals("<p>Fale com nossa diretora clínica.</p>").indicios_equipe == 1


def test_parece_requer_js():
    assert parece_requer_js('<html><body><div id="root"></div></body></html>')
    assert not parece_requer_js(HOME_HTML)


# --- http -----------------------------------------------------------------


async def test_fetch_usa_cache_e_conta_paginas(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML})
    sessao = scraping_http.ScrapeSession()
    await scraping_http.fetch(f"{SITE}/", sessao)
    resultado = await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
    assert resultado["status_code"] == 200
    assert respx_mock.routes[1].call_count == 1  # segunda sessão veio do cache
    assert sessao.pages_fetched("sorriso.com.br") == 1


async def test_fetch_respeita_robots(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML}, robots="User-agent: *\nDisallow: /")
    with pytest.raises(scraping_http.RobotsDisallowed):
        await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())


async def test_fetch_robots_404_permite(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(200, text="ok"))
    assert (await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession()))["text"] == "ok"


async def test_fetch_teto_de_paginas_por_dominio(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML})
    sessao = scraping_http.ScrapeSession(page_counts={"sorriso.com.br": 5})
    with pytest.raises(scraping_http.PageBudgetExceeded):
        await scraping_http.fetch(f"{SITE}/", sessao)


async def test_fetch_503_nao_e_cacheado_e_e_retentado(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    rota = respx_mock.get(f"{SITE}/").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, text="voltou")]
    )
    resultado = await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
    assert resultado["text"] == "voltou"
    assert rota.call_count == 3

    # a página boa foi cacheada, os 503 anteriores não
    await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
    assert rota.call_count == 3


async def test_fetch_429_respeita_retry_after(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    rota = respx_mock.get(f"{SITE}/").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, text="ok")]
    )
    assert (await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession()))["text"] == "ok"
    assert rota.call_count == 2


async def test_fetch_503_persistente_levanta_apos_3_tentativas_e_nao_cacheia(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    rota = respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(503))
    sessao = scraping_http.ScrapeSession()
    with pytest.raises(scraping_http.HttpStatusError) as info:
        await scraping_http.fetch(f"{SITE}/", sessao)
    assert info.value.status_code == 503
    assert rota.call_count == 3
    assert sessao.pages_fetched("sorriso.com.br") == 0

    with pytest.raises(scraping_http.HttpStatusError):
        await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
    assert rota.call_count == 6  # nada ficou em cache


async def test_fetch_404_nao_e_retentado_nem_cacheado(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    rota = respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(404))
    for _ in range(2):
        with pytest.raises(scraping_http.HttpStatusError) as info:
            await scraping_http.fetch(f"{SITE}/", scraping_http.ScrapeSession())
        assert info.value.status_code == 404
    assert rota.call_count == 2


async def test_fetch_segue_redirect(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("http://sorriso.com.br/").mock(
        return_value=httpx.Response(301, headers={"Location": f"{SITE}/"})
    )
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(200, text="destino"))
    resultado = await scraping_http.fetch("http://sorriso.com.br/", scraping_http.ScrapeSession())
    assert resultado["text"] == "destino"
    assert resultado["url"] == f"{SITE}/"


# --- enrich (S4) ----------------------------------------------------------


async def test_enrich_completo(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML, "/contato": CONTATO_HTML, "/sobre-nos/": "<p>sobre</p>"})
    resultado = await enrich(
        f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO, email_cadastral="contato@sorriso.com.br"
    )
    assert resultado.site_matched
    assert resultado.erro is None
    por_email = {e.email: e for e in resultado.emails}
    assert por_email["contato@sorriso.com.br"].confianca == "maxima"
    assert por_email["ana.souza@sorriso.com.br"].confianca == "alta_nominal"
    assert resultado.whatsapp == ["5519999999999"]
    assert resultado.context.tecnologias == ["wordpress"]


async def test_enrich_sem_url():
    resultado = await enrich(None, scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO)
    assert resultado.erro == "sem_site_candidato"


async def test_enrich_match_fraco(respx_mock):
    mock_site(respx_mock, {"/": "<p>outra empresa qualquer</p>"})
    resultado = await enrich(f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO)
    assert not resultado.site_matched
    assert resultado.erro == "match_fraco"


async def test_enrich_site_spa(respx_mock):
    mock_site(respx_mock, {"/": '<div id="root"></div>CNPJ 12.345.678/0001-95'})
    resultado = await enrich(f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO)
    assert resultado.site_matched and resultado.requer_navegador


async def test_enrich_falha_de_rede_nao_levanta(respx_mock):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(side_effect=httpx.ConnectError("fora do ar"))
    resultado = await enrich(f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO)
    assert resultado.erro.startswith("ConnectError")


@pytest.mark.parametrize("status", [404, 500])
async def test_enrich_home_com_erro_http_vira_falha(respx_mock, status):
    respx_mock.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{SITE}/").mock(return_value=httpx.Response(status, text="<p>CNPJ 12.345.678/0001-95</p>"))
    resultado = await enrich(f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO)
    assert resultado.erro == f"http_{status}"
    assert not resultado.site_matched
    assert resultado.emails == []


async def test_enrich_verifica_mx_fora_do_event_loop(respx_mock):
    mock_site(respx_mock, {"/": HOME_HTML, "/contato": CONTATO_HTML, "/sobre-nos/": "<p>sobre</p>"})
    threads: list[threading.Thread] = []

    def resolver(dominio):
        threads.append(threading.current_thread())
        return ["mx.fake"]

    resultado = await enrich(f"{SITE}/", scraping_http.ScrapeSession(), cnpj=CNPJ_PADRAO, mx_resolver=resolver)

    assert resultado.emails and threads
    assert all(thread is not threading.main_thread() for thread in threads)  # DNS síncrono não trava o loop
