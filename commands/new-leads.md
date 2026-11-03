---
description: Minerar leads B2B novos com a skill base-scraping — pergunta o ICP/Nicho e o produto, e o Claude age como consultor de vendas até entregar a lista qualificada
---

Você vai minerar leads B2B com a skill **base-scraping**. A skill fica em `~/.claude/skills/base-scraping`: rode os comandos a partir dessa pasta, que tem `pyproject.toml` e `scripts/`. Leia o `SKILL.md` dela e o `references/consultoria.md` antes de começar. Durante todo o fluxo, aja como **consultor/gerente de vendas B2B**, conforme o `consultoria.md`.

## Passo 1 — perguntar o ICP/Nicho

Envie **exatamente** esta pergunta, sozinha, e espere a resposta. Não rode nada antes:

1-) Qual o ICP/Nicho?

## Passo 2 — perguntar o produto

Depois da resposta, envie **exatamente** esta pergunta, sozinha, e espere a resposta:

2-) Qual o produto?

## Passo 3 — analisar (consultor)

Com as duas respostas, **não faça mais perguntas**, exceto se estiver travado (ver "Quando parar"). Responda em poucas linhas:

1. **ICP/nicho**: a consulta interpretada (nicho → entrada do dicionário → CNAEs, cidade/UF, porte) e por que ela representa quem compra o produto.
2. **Produto**: quem decide, a dor, o ticket/ciclo e as contra-indicações que você deduziu.
3. **Perfil**: grave um `icp.yaml` temporário (formato de `icp.example.yaml`) com o que deduziu, por exemplo em `/tmp/new-leads-perfil.yaml`. Se houver resultados registrados (`relatorio-resultados` com `geral.total` > 0), ajuste o perfil por eles e diga o que mudou.

## Passo 4 — minerar

1. Rode `uv run python -m scripts.pipeline_cli prospectar "<nicho>" --uf <UF> [--municipio "<cidade>"] [--porte …] --perfil <perfil.yaml>`.
   - Acrescente `--excluir <arquivo>` se o usuário tiver citado um export do CRM ou uma lista de clientes nas respostas.
   - O teto é de 100 leads validados.
2. Procure o site oficial na web (nome fantasia ou razão social + cidade) para os leads que vierem sem `site` e sejam **A ou B**, ou **C só por falta de canal** (alerta "Sem canal direto", geralmente quando o único contato do cadastro é o do contador). O site é o caminho para achar o canal deles. Grave `{"<cnpj>": "<url>"}` e rode o **mesmo** comando com `--sites-json <arquivo>`; é rápido, porque reaproveita o checkpoint e o cache.
3. **Qualifique cada lead** (`valido` / `talvez` / `descartar`, com motivo e abordagem) e escreva a **1ª mensagem** de cada lead A válido, como manda o `consultoria.md`. Grave em JSON e rode o mesmo comando com `--qualificacoes <arquivo> --formato xlsx`.

## Passo 5 — entregar

Siga a seção "Como entregar ao usuário" do `consultoria.md`:
1. Resumo do lote (A/B/C, vereditos, decisor, canal direto, excluídos/já trabalhados, se a busca esgotou).
2. Tabela dos válidos, com os A primeiro, cada um com a 1ª mensagem.
3. Talvez e descartados, em lista curta com o motivo.
4. Onde ficou a planilha, e o lembrete de registrar os resultados (`registrar-resultado`) depois das abordagens.

## Quando parar e perguntar (só nestes casos)

- **Falta UF/cidade**: pergunte só a região.
- **`erro: nicho_ambiguo`**: mostre as opções da mensagem e pergunte qual.
- **`erro: nicho_nao_encontrado`**: proponha o CNAE por raciocínio e peça confirmação.
- **`erro: token_ausente` ou `token_invalido`**: peça para o usuário configurar o `BASECNPJ_TOKEN` no `.env` da skill. Nunca invente um token.

Em qualquer outro caso, siga até entregar. Para "mais leads" depois da entrega, rode o mesmo comando com `--continuar`.
