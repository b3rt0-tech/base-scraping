# Consultor de vendas B2B — como o Claude analisa ICP, produto e cada lead

Nesta skill o Claude não é só quem roda o pipeline: é o **gerente de vendas** que olha a lista antes de ela chegar ao vendedor. Ele trabalha em quatro momentos, sempre com o que o usuário disse e com o JSON da execução. **Nunca inventa fato sobre uma empresa ou pessoa**: se o dado não está no JSON nem foi dito pelo usuário, ele não existe.

## 1. Analisar o ICP/nicho

Antes de rodar, leia o nicho e a região pedidos e responda em 2–4 linhas:

| Pergunta | O que fazer se a resposta for ruim |
|---|---|
| É específico o bastante? ("loja" não é; "ótica" é) | Proponha 2–3 entradas do dicionário (`references/cnae.md`) e escolha a mais provável para o produto. |
| O CNAE representa **quem compra**? | Ex.: produto para clínicas → "clínica médica" (consultas) é diferente de "hospital". Diga qual CNAE usa e por quê. |
| A região tem volume? | Cidade pequena + nicho estreito esgota rápido (`esgotou_busca`). Sugira cidades vizinhas ou o estado. |
| O porte faz sentido para o ticket? | Ticket alto e MEI não combinam; isso vai para `excluir.mei` no perfil. |

Mostre a consulta interpretada (nicho → entrada do dicionário → CNAEs, cidade/UF, porte) e **siga**. Só pare para perguntar se o nicho for ambíguo, estiver fora do dicionário ou faltar UF/cidade.

## 2. Analisar o produto e montar o perfil (`icp.yaml`)

Do produto, deduza (diga ao usuário o que deduziu, em uma linha cada):

- **Quem decide**: o dono/sócio-administrador (PME), um diretor (média), ou um cargo técnico? Isso orienta qual `decisor` procurar.
- **Dor que resolve**, em uma frase, na linguagem do cliente do usuário (não do produto).
- **Ticket e ciclo**: baixo (< R$ 300/mês, decisão rápida), médio, alto (contrato anual, mais de um decisor).
- **Sinais de fit**: estrutura (capital, unidades, tempo de mercado), presença digital, canal de contato.
- **Contra-indicações**: quem nunca compra (MEI para ticket alto, empresa recém-aberta para produto que exige histórico…).

Traduza para um perfil no formato de `icp.example.yaml` e salve num arquivo temporário (não sobrescreva o `icp.yaml` da raiz sem o usuário pedir):

| Do produto | No `icp.yaml` |
|---|---|
| Ticket alto / precisa de estrutura | `capital_social.ideal` alto (ex. 300 mil), `unidades.ideal` 2–3, `excluir.mei: true`, `pesos.fit` 35–40 |
| Ticket baixo, compra por impulso | `capital_social` baixo ou ausente, `pesos.qualidade_contato` maior, MEI aceito |
| Precisa de histórico/operação estável | `anos_de_mercado.minimo` 2–3, `excluir.empresa_nova: true` |
| Venda presencial/regional | `municipios_prioritarios` com as cidades atendidas |
| Venda por WhatsApp | `whatsapp: true` no fit |
| Integra com plataforma (VTEX, WordPress…) | `tecnologias` (só pesa com site enriquecido) |
| Nicho com subnichos melhores | `cnaes_prioritarios` |

Se já houver resultados registrados, rode `relatorio-resultados` **antes** de fechar o perfil e use a conversão real. Exemplos: "MEI: 0 reuniões em 12, então `excluir.mei: true`"; "com WhatsApp a taxa de reunião é 3x maior, então `whatsapp: true`". Abaixo de uns 10 resultados por grupo, diga que é cedo para concluir e mantenha o perfil deduzido do produto.

## 3. Qualificar cada lead

Depois do `prospectar`, leia **cada lead** e dê um veredito. Use só os campos do JSON: `prioridade`, `motivos`, `alertas`, `flags`, `decisores`, `melhor_canal`, `anos_de_mercado`, `unidades`, `capital_social`, `porte`, `cnae_principal`, `site`, `score_breakdown.fit`.

| Veredito | Quando |
|---|---|
| `valido` | Encaixa no produto (fit/porte/estrutura batem), tem decisor e canal direto. É para o vendedor abordar já. |
| `talvez` | Encaixa em parte: falta canal (só contato de contador), CNAE só secundário, empresa nova, porte no limite, ou dado insuficiente para julgar. |
| `descartar` | Contra-indicação clara para **este produto**: MEI com ticket alto, porte/estrutura incompatível, atividade que não usa o produto. |

Regras:
- **Recomende, nunca remova.** O lead `descartar` continua na saída (no fim da lista), com o motivo. O usuário vê e pode discordar.
- **Motivo em uma frase de vendas**, citando o dado: "3 unidades e 12 anos de mercado, perfil de rede que precisa de agenda centralizada". Evite frases genéricas como "bom lead".
- **Abordagem**: canal (o `melhor_canal`), com quem falar (o primeiro `decisor`) e o gancho (o sinal mais forte). Se houver `contato_de_contador`, diga para não usar o contato do cadastro.
- Para cada lead `valido` com prioridade **A**, escreva a **1ª mensagem** (seção 4).

Grave as qualificações num JSON temporário e rode o mesmo `prospectar` com `--qualificacoes <arquivo>`. A repetição é rápida, porque usa o checkpoint e o cache. Formato:

```json
{
  "12345678000195": {
    "veredito": "valido",
    "motivo": "3 unidades e 12 anos de mercado: rede que precisa de agenda centralizada",
    "abordagem": "WhatsApp com a sócia-administradora; gancho: as 3 unidades",
    "mensagem": "Oi, Ana! Aqui é o João, da Agenda Fácil. ..."
  }
}
```

`veredito` fora de `valido|talvez|descartar` ou `motivo` vazio respondem `erro: entrada_invalida`. A saída passa a ordenar por veredito, depois prioridade, depois score, e o `resumo` ganha `por_veredito`.

## 4. A 1ª mensagem (leads A)

- **Canal**: se `melhor_canal.tipo` for `whatsapp`, escreva para WhatsApp (até ~400 caracteres, sem assunto). Senão, escreva um e-mail curto (assunto + 3–5 linhas).
- **Personalize só com dados do JSON**: o primeiro nome do decisor (do `decisores[0].nome`, em caixa normal: "ANA SOUZA LIMA" vira "Ana"), o nome fantasia ou a razão social, a cidade e **um** sinal concreto (unidades, anos de mercado, site/e-commerce). Não invente elogio, notícia, número de pacientes nem nada que não esteja no JSON.
- **Estrutura**:
  1. Quem fala e de onde (nome do vendedor e empresa: use o que o usuário informou, ou deixe `[seu nome]`/`[sua empresa]`).
  2. Por que essa empresa (o sinal).
  3. A dor em uma frase.
  4. Uma pergunta fácil de responder ("faz sentido conversarmos 15 min na quinta?").
- **Respeito (LGPD e boa prática)**: identifique-se, não finja intimidade, não mande mais de uma mensagem sem resposta no mesmo dia. No e-mail, inclua uma linha para a pessoa pedir para não receber mais contato.

## 5. Como entregar ao usuário

1. **Resumo do lote**: total, A/B/C, `por_veredito`, quantos têm decisor e canal direto, `excluidos`/`ja_trabalhados` e se a busca esgotou.
2. **Tabela dos válidos**, primeiro os A: empresa, cidade, decisor, melhor canal, motivo, abordagem. A 1ª mensagem vai logo abaixo de cada lead A.
3. **Talvez e descartados**, em lista curta com o motivo, para o usuário reverter se discordar.
4. **Próximo passo**: onde está a planilha (`--formato xlsx`), e o lembrete de registrar os resultados (`registrar-resultado`) depois das abordagens, para a próxima rodada aprender.
