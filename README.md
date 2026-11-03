# 🧭 base-scraping

**Captação e enriquecimento de leads B2B brasileiros**, construído sobre o [BaseCNPJ](https://basecnpj.app) — roda como *skill* do [Claude Code](https://claude.com/claude-code) ou como CLI standalone.

<p>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white">
  <img alt="uv" src="https://img.shields.io/badge/deps-uv-6340ac">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-informational">
  <img alt="Saída" src="https://img.shields.io/badge/output-JSON-black?logo=json">
</p>

> 💬 Você pede em linguagem natural — *"óticas ativas na região da Lapa, em São Paulo capital"* — e recebe uma lista de empresas **reais**, validadas contra o cadastro oficial da Receita Federal, com contato e site verificados quando disponíveis.

```
$ uv run python -m scripts.pipeline_cli prospectar "dentista" --uf SP --municipio Campinas --porte ME

{
  "total": 8,
  "leads": [ { "razao_social": "CLINICA ODONTOLOGICA QUILES LTDA", "situacao_cadastral": "Ativa", "score": 58.0, ... } ]
}
```

---

## 📚 Sumário

- [🧠 Como funciona](#-como-funciona)
- [🔁 O pipeline, estágio por estágio](#-o-pipeline-estágio-por-estágio)
- [📦 Instalação](#-instalação)
- [🔑 Token do BaseCNPJ](#-token-do-basecnpj)
- [🚀 Como usar](#-como-usar)
- [📊 Exemplo de saída](#-exemplo-de-saída)
- [⚠️ Limites de design](#️-limites-de-design)
- [🗂️ Estrutura do projeto](#️-estrutura-do-projeto)
- [🔒 LGPD e compliance](#-lgpd-e-compliance)
- [📄 Licença](#-licença)

---

## 🧠 Como funciona

O **BaseCNPJ é a espinha dorsal** do projeto: ele define quem é lead e valida quem continua no funil. O scraping é periferia — só preenche o que o cadastro oficial não tem (site, e-mail comercial vivo, redes sociais).

```
🗂️  BaseCNPJ  =  QUEM é a empresa   (verdade cadastral, oficial, estruturada)
🌐  Scraping   =  COMO falar com ela hoje (verdade de mercado, volátil)
```

Essa separação é o que torna o resultado confiável: **se o scraping falhar num lead, o lead continua existindo e utilizável** — só com menos campos preenchidos. Nenhum dado inventado, nenhum lead perdido por causa de um site fora do ar.

```
 "dentista em Campinas"
        │
        ▼
 🔤  nicho → CNAE (references/cnae.md)         8630-5/04
        │
        ▼
 🔎  busca avançada no BaseCNPJ (S0→S1)        até 100 validados, paginado
        │
        ▼
 ✅  validação cadastral (S3)                  situação ativa, CNAE, porte...
        │
        ▼
 🌐  site oficial + e-mail + contato (S4)       scraping, opcional
        │
        ▼
 🏆  score + JSON final com proveniência (S6)
```

---

## 🔁 O pipeline, estágio por estágio

| Estágio | O que faz | Rota/fonte |
|---|---|---|
| **S0** — ICP | Traduz o pedido em filtros estruturados (nicho→CNAE, cidade, porte) | `route=status` (preflight) |
| **S1** — Descoberta | Pagina a busca avançada e valida em rodadas até **100 leads validados** (descartes não encolhem a entrega; teto de segurança de 500 descobertos) | `route=avancada` |
| **S2** — Resolução | Resolve candidatos sem CNPJ (comando `validar-lista`); desempate só por UF, e só na `busca` | `route=sugestao`, `route=busca` |
| **S3** — Validação | Filtra por situação cadastral, CNAE, porte; sinaliza empresa nova | `route=cnpj` |
| **S4** — Enriquecimento | Valida o site oficial, extrai contato, verifica e-mail em 5 níveis | scraping externo |
| **S6** — Score e saída | Pontua (0-100) com breakdown por dimensão, exporta JSON com proveniência | — |

> ℹ️ Não existe **S5** ativo nesta versão — a expansão por rede societária (`route=rede`) já está implementada no cliente, mas fica reservada pra uma fase futura. Ver [Limites de design](#️-limites-de-design).

---

## 📦 Instalação

Pré-requisito: [`uv`](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone <url-do-repo> base-scraping
cd base-scraping
uv sync
```

## 🔑 Token do BaseCNPJ

1. Crie uma conta no [BaseCNPJ](https://basecnpj.app) — plano **Business** (busca avançada e rede societária exigem esse plano).
2. No painel, vá em **API Keys** e gere um token.
3. Copie o template e preencha:
   ```bash
   cp .env.example .env
   # edite .env e preencha BASECNPJ_TOKEN=<seu-token>
   ```

📍 O `.env` é lido da **raiz da skill** (e, como fallback, do diretório atual), então o comando funciona de qualquer pasta. Sem token o CLI responde `erro: token_ausente`; token recusado pela API é `erro: token_invalido`.

🔐 O token **nunca** entra no repositório — `.env` está no `.gitignore`, só `.env.example` é versionado.

---

## 🚀 Como usar

### 🧩 Opção A — como skill do Claude Code

Claude Code carrega skills a partir de um arquivo `SKILL.md` colocado numa pasta específica. **Não existe instalador automático** que baixe e configure isso sozinho só porque alguém pede "instala essa skill" — o passo que importa de verdade é copiar a pasta pro lugar certo.

1. Clone este repositório em qualquer lugar:
   ```bash
   git clone <url-do-repo> base-scraping
   ```
2. Copie a pasta pro diretório de skills do Claude Code — escolha uma das duas:

   | Escopo | Onde fica disponível | Comando |
   |---|---|---|
   | 🌍 **Pessoal** | Qualquer projeto que você abrir | `mkdir -p ~/.claude/skills && cp -r base-scraping ~/.claude/skills/base-scraping` |
   | 📁 **Só este projeto** | Só dentro do repo onde você colar | `mkdir -p .claude/skills && cp -r base-scraping .claude/skills/base-scraping` |

3. Entre na pasta copiada e instale as dependências:
   ```bash
   cd ~/.claude/skills/base-scraping   # ou .claude/skills/base-scraping, se for por projeto
   uv sync
   ```
4. Configure o token — ver [🔑 Token do BaseCNPJ](#-token-do-basecnpj).
5. Abra uma sessão **nova** do Claude Code (ou reinicie a atual) — skills são carregadas no início da sessão.
6. Teste pedindo algo como:
   > 💬 *"me traz 10 dentistas ativos em Campinas, porte ME"*

> ⚠️ **Só dizer "instala essa skill" pro Claude Code, sem mais contexto, não é confiável.** Ou você segue o passo a passo acima manualmente, ou pede explicitamente pra ele rodar os comandos (ele tem acesso a bash, então consegue clonar/copiar/`uv sync` se instruído). É uma escolha de design: esta é uma "skill simples" (clonar + copiar pasta), sem manifesto de plugin/marketplace.

### 💻 Opção B — CLI direto

Três comandos, todos sempre devolvendo **JSON** no stdout:

```bash
# 🔍 S0→S3 — descoberta + validação cadastral, sem scraping
uv run python -m scripts.pipeline_cli descobrir "dentista" --uf SP --municipio Campinas --porte ME

# 🏆 S0→S4→S6 — pipeline completo, com score e enriquecimento de contato
uv run python -m scripts.pipeline_cli prospectar "dentista" --uf SP --municipio Campinas --porte ME

# 📋 S2→S3→S4→S6 — valida uma lista própria (CNPJs ou nomes), sem descoberta
uv run python -m scripts.pipeline_cli validar-lista --arquivo lista.json
```

`lista.json` é uma lista de `{"cnpj": "..."}` ou `{"nome": "...", "uf": "SP", "municipio": "Campinas"}`, com teto de 100 itens (o excedente é ignorado e informado em `aviso`). Nomes são resolvidos por `route=sugestao`/`route=busca`; match ambíguo vai para `nao_resolvidos` (nunca chuta). A validação não checa CNAE. A saída traz `total`, `leads`, `descartados` e `nao_resolvidos`.

> ⚠️ O desempate de nomes ambíguos só funciona por **UF**, e só na `busca`: a API real não devolve `uf` na `sugestao` nem `municipio` em nenhuma das duas rotas.

**Flags disponíveis** (ambos os comandos):

| Flag | Efeito |
|---|---|
| `--uf` | UF do cadastro (ex. `SP`) |
| `--municipio` | nome do município |
| `--porte` | `ME`, `EPP` ou `demais` |
| `--arquivo` | (só em `validar-lista`) JSON com a lista de CNPJs/nomes |
| `--cep` | 📍 prefixo de CEP — filtra por **bairro/região** dentro de um município (ex. `--cep 0507` cobre a região da Lapa em São Paulo capital) |
| `--matriz` | só estabelecimentos **matriz** (exclui filiais; envia `matriz_filial=1` na busca avançada) |
| `--continuar` | (em `descobrir` e `prospectar`) entrega os **próximos 100 validados** da mesma consulta, sem repetir CNPJ já entregue. Sem a flag, repetir a consulta devolve sempre o lote 1. A saída informa `lote` |
| `--situacao` | (também em `validar-lista`) situação cadastral desejada (padrão: `ativa`) |
| `--db-path` | onde fica o checkpoint SQLite (padrão: `.cache/pipeline.sqlite3`) |
| `--verbose` / `-v` | detalhe de cada lead no **stderr** (o stdout continua só com o JSON) |
| `--quiet` / `-q` | só avisos e erros no stderr (sem progresso) |
| `--sites-json` | (em `prospectar` e `validar-lista`) arquivo `{"cnpj": "url candidata"}` pra habilitar o enriquecimento por scraping (site, e-mail, redes sociais) |
| `--excluir` | arquivo do CRM (`.csv`, `.txt` ou `.json`, sem preparo) com CNPJs e/ou e-mails/domínios de quem já é cliente ou lead em andamento. CNPJ exclui a **empresa inteira** (todas as filiais); domínio exclui quem tem o mesmo domínio no e-mail cadastral (Gmail/Hotmail são ignorados). Os excluídos não aparecem nem em `descartados`, e a descoberta repõe outros para manter os 100. A saída informa `excluidos` |
| `--perfil` | `icp.yaml` com o perfil do cliente ideal (ver abaixo). Padrão: `icp.yaml` na raiz da skill, se existir |

Sem `--sites-json`, `prospectar` tenta sozinho o **domínio do e-mail cadastral** quando ele é da empresa (não Gmail/Hotmail etc., não de contador) e só mantém o site se ele confirmar ser a empresa (`site_origem: email_cadastral`). Em dentistas de Campinas isso confirmou 10 sites em 100 leads sem nenhuma busca web. Para os demais, a URL candidata vem da busca web da skill (ver `SKILL.md`) via `--sites-json`, que tem prioridade.

### 🎯 Perfil do cliente ideal (`icp.yaml`)

O nicho diz **quem buscar**; o perfil diz **quem vale mais para o seu produto**. Copie o [`icp.example.yaml`](icp.example.yaml) para `icp.yaml` (ou passe `--perfil arquivo.yaml`) e ajuste:

- `pesos`: as dimensões do score, que somam 100. Uma dimensão `fit` mede o encaixe com o perfil.
- **Critérios de fit:**
  - `capital_social`, `anos_de_mercado` e `unidades` em faixas `minimo`/`ideal`, contínuas, o que também desempata leads.
  - `portes_preferidos`, `municipios_prioritarios`, `cnaes_prioritarios`, `tecnologias` (do site) e `whatsapp`.
- `excluir`: critérios eliminatórios (`mei`, `empresa_nova`). O lead é descartado na validação e a descoberta repõe outro.
- `prioridade`: os cortes de score para A/B/C.

Sem perfil, o score é o padrão (30/30/20/20). Na prática, em dentistas de Campinas, o top 20 passou de **1 score distinto** (todos 64) para **20 distintos** com o perfil de exemplo. Empate restante desempata por fit, depois capital, depois tempo de mercado.

♻️ Rodar o mesmo comando de novo com os mesmos parâmetros é seguro e rápido: o checkpoint SQLite evita refazer chamadas já feitas.

---

## 📊 Exemplo de saída

```json
{
  "total": 8,
  "leads": [
    {
      "cnpj": "43292842000177",
      "razao_social": "CLINICA ODONTOLOGICA QUILES LTDA",
      "cnae_principal": "8630504",
      "situacao_cadastral": "Ativa",
      "endereco": { "municipio": "HOLAMBRA", "uf": "SP", "cep": "13825-000" },
      "telefone_cadastral": "(19) 3802-1183",
      "enriquecimento": { "status": "ok", "motivo": null },
      "score": 58.0,
      "score_breakdown": {
        "aderencia_icp": 30.0,
        "qualidade_contato": 8.0,
        "presenca_digital": 0.0,
        "saude_cadastral": 20.0
      },
      "proveniencia": {
        "razao_social": { "fonte": "basecnpj", "rota_ou_url": "route=cnpj", "timestamp": 1788869605.6 }
      }
    }
  ],
  "descartados": [{ "cnpj": "11111111000111", "motivo": "situacao_cadastral=04 (esperado 02)" }]
}
```

🧭 `icp` (em `descobrir` e `prospectar`) mostra qual entrada de `references/cnae.md` e quais CNAEs foram usados. Nicho que casa com mais de uma entrada (ex. `loja`) é recusado com `erro: nicho_ambiguo` em vez de escolher uma.

📋 `descartados` lista os leads que não entraram na saída (`cnpj` + `motivo`: ICP divergente ou CNPJ não encontrado), e `enriquecimento.status` (`ok`, `requer_navegador`, `match_fraco` ou `falha`) explica o resultado do scraping de cada lead com site candidato.

📐 O formato da saída é um contrato tipado (`scripts/pipeline/schemas.py`, JSON Schema em [`schemas/lead.schema.json`](./schemas/lead.schema.json)), validado antes de ser impresso.

🔎 Todo campo carrega sua origem em `proveniencia` — pra qualquer dado, dá pra responder **"de onde veio isso e quando"**.

---

## ⚠️ Limites de design

| Limite | Detalhe |
|---|---|
| 🔢 Teto de leads | **100 por execução**, fixo — a busca avançada para de paginar ao chegar nesse número |
| 📤 Formato de saída | **Sempre JSON.** SQLite é só checkpoint interno, nunca é a saída final |
| 🧩 Resolução de identidade (S2) | Só no `validar-lista`; `descobrir`/`prospectar` não passam por ela. Desempate de nome só por UF, e só na `busca` (a API não devolve município) |
| 🕸️ Rede societária (S5) | Cliente já expõe `routes.rede()`, mas o pipeline não a chama — fica pra uma fase 2 |
| 🎭 Sites com JavaScript | Marcados (`requer_navegador=true`) e pulados — sem Playwright nesta versão |
| 🚫 LinkedIn | Fora de escopo — viola os Termos de Uso da plataforma e é o maior risco de LGPD do projeto |

---

## 🗂️ Estrutura do projeto

```
base-scraping/
├── SKILL.md                    # 🧭 instruções completas pra rodar como skill do Claude Code
├── .env.example                 # 🔑 BASECNPJ_TOKEN=
├── references/
│   ├── basecnpj.md              # 📡 rotas, filtros, erros e limites confirmados da API
│   ├── cnae.md                  # 📖 dicionário nicho → CNAE
│   ├── enriquecimento.md        # 🌐 receita do scraping (site, contato, e-mail, contexto)
│   └── compliance.md            # 🔒 LGPD, robots.txt, rate limiting
├── schemas/
│   ├── lead.md                  # 🧾 contrato de dados do Lead (documentação)
│   └── lead.schema.json         # 📐 JSON Schema das saídas (gerado dos modelos Pydantic)
├── scripts/
│   ├── basecnpj/                # 📡 cliente da API (cache, retry, mapa de erros)
│   ├── pipeline/                # 🔁 descoberta, validação, score, export
│   ├── scraping/                # 🌐 site oficial, contatos, verificação de e-mail
│   ├── utils.py                 # 🧰 helpers compartilhados (only_digits)
│   ├── log.py                   # 📣 logs de progresso em stderr (--verbose/--quiet)
│   ├── cli.py                   # 🛠️ CLI de teste manual das 6 rotas do BaseCNPJ
│   └── pipeline_cli.py          # 🚀 CLI principal: descobrir / prospectar / validar-lista
```

> 🧪 `tests/` (pytest, sem rede e sem token) e `uv.lock` são versionados e rodam no CI a cada push/PR (`ruff`, `mypy`, `pytest`). Localmente: `uv sync --group dev && uv run pytest`; contra a API real: `uv run pytest -m integration`.

---

## 🔒 LGPD e compliance

Dado de empresa é público; nome e e-mail de pessoa física são dados pessoais, tratados com finalidade clara (contato B2B) e proveniência registrada por campo. Rate limiting rígido nos sites externos:

- 1️⃣ requisição simultânea por domínio
- 🤖 `robots.txt`/`Crawl-delay` sempre respeitados
- 5️⃣ páginas de teto por domínio
- 🕐 cache de 7 dias pra não bater duas vezes no mesmo site

Detalhes completos em [`references/compliance.md`](./references/compliance.md).

---

## 📄 Licença

MIT — ver [`LICENSE`](./LICENSE).
