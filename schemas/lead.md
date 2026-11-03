# Contrato de dados — o Lead

Cada campo carrega sua origem, o que permite rastrear proveniência ponta a ponta (exigido pela seção LGPD de `references/compliance.md`).

| Campo | Origem (abstrato) | Origem real (campo da API / código) | Presença |
|---|---|---|---|
| `cnpj` | BaseCNPJ | `cnpj_basico` + `cnpj_completo_formatado` (14 dígitos, só números) | **sempre** (chave) |
| `razao_social`, `nome_fantasia` | `route=cnpj` | `razao_social`, `estabelecimento_matriz.nome_fantasia` | quando houver dado |
| `cnae_principal`, `cnaes_secundarios` | `route=cnpj` | `cnaes[]` — item com `tipo="Principal"`; os demais são os secundários | quando houver dado |
| `situacao_cadastral`, `data_abertura`, `porte`, `capital_social` | `route=cnpj` | `estabelecimento_matriz.situacao_descricao`, `estabelecimento_matriz.data_inicio_atividade`, `porte_descricao`, `capital_social` | quando houver dado |
| `natureza_juridica` | `route=cnpj` | `natureza_juridica_descricao` | quando houver dado |
| `matriz_filial` (`"matriz"` \| `"filial"`) | `route=cnpj` | `estabelecimentos[]` — o item cujo `cnpj_completo_formatado` é o do lead; `matriz_filial_codigo` `"1"` = matriz | quando houver dado |
| `endereco` (logradouro, município, UF, CEP) | `route=cnpj` | `estabelecimento_matriz.{logradouro,municipio,uf,cep}` | quando houver dado |
| `email_cadastral` | `route=cnpj` | `estabelecimento_matriz.email` | quando houver dado |
| `telefone_cadastral` | `route=cnpj` | `telefones[0]` da **raiz** do payload (não da matriz), formatado `(DDD) número` | quando houver dado |
| `telefone_cadastral_celular` | `route=cnpj` | primeiro telefone cadastral que é celular, em `+55DDD9XXXXXXXX` (o formato antigo de 8 dígitos começando com 6–9 ganha o 9); ignora fax e telefone repetido entre empresas diferentes (do contador). **Provável WhatsApp** | com o flag `whatsapp_provavel` |
| `unidades` | `route=cnpj` | estabelecimentos ativos (matriz + filiais) | sempre |
| `anos_de_mercado` | `route=cnpj` | anos completos desde `data_inicio_atividade` | quando houver data |
| `decisores[]` (`nome`, `qualificacao`, `desde`, `origem`) | `route=cnpj` | `socios[]` da raiz do payload: só **pessoa física**, sem repetição, ordenada por qualificação (administrador > presidente > diretor > gerente > titular > sócio) e data de entrada, **máx. 3**. Empresário individual/MEI não tem `socios[]`: o titular (nome na `razao_social`) vem com `origem=razao_social_titular`. Não saem CPF mascarado nem faixa etária | quando houver dado |
| `grupo_economico[]`, `orange_flags[]` | `route=rede` (**fase 2 — fora do v1**) | — | não emitido |
| `site` | S4.1 (descoberta do site oficial) | URL final após redirects | só com site confirmado |
| `emails[]` (`valor`, `nivel` 0-5, `confianca`) | S4.2/4.3 (descoberta + verificação) | `confianca`: `invalido`, `baixa`, `media`, `boa`, `alta`, `alta_nominal` ou `maxima` (bate com o e-mail cadastral) | só com site confirmado |
| `telefones[]`, `whatsapp[]`, `redes_sociais{}` | S4 | só números (telefone/WhatsApp); `redes_sociais` é `{rede: url}` | só com site confirmado |
| `contatos[]` (nome, cargo, email) | S4.2 | **não emitido no v1** — extrair pessoa+cargo de HTML livre fica com o Claude, não com script | não emitido |
| `enriquecimento{status, motivo}` | S4 | `status`: `ok`, `requer_navegador`, `match_fraco` ou `falha`; `motivo` traz o erro (ex. `http_404`) | só com URL candidata |
| `score`, `score_breakdown{}` | S6 (scoring) | `score_breakdown` traz as 4 dimensões e o `total` | **sempre** |
| `flags[]` | S3 | `empresa_nova`, `cnae_secundario`, `contato_cadastral_disponivel`, `contato_de_contador` (e-mail/telefone cadastral provavelmente do escritório de contabilidade: não leva ao decisor e perde o bônus de contato no score), `whatsapp_provavel` (celular cadastral próprio: +6 em qualidade de contato), `mei` (microempreendedor individual: ticket e estrutura mínimos), `multiunidade` (mais de um estabelecimento ativo) | quando houver |
| `proveniencia{campo: {fonte, rota_ou_url, timestamp}}` | todo o pipeline | `fonte`: `basecnpj`, `scraping` ou `pipeline` | **sempre** |

**Campo sem dado não aparece** na saída (em vez de vir `null`); só `cnpj`, `score`, `score_breakdown` e `proveniencia` estão sempre presentes.

Ver `scripts/basecnpj/models.py` (`CNPJDetalhe`, `EstabelecimentoMatriz`) para os modelos Pydantic reais, e `references/basecnpj.md` para o levantamento completo do formato observado.

## Scoring (S6) — pesos por dimensão

| Dimensão | Peso | Fonte |
|---|---|---|
| aderência ao ICP (CNAE principal vs. secundário, porte, região) | 30 | BaseCNPJ |
| qualidade do contato (nível do e-mail, telefone, WhatsApp) | 30 | cadastral + scraping |
| presença digital (site validado, redes ativas) | 20 | scraping |
| saúde cadastral (situação, tempo de casa, capital) | 20 | BaseCNPJ |

## Contrato tipado (fonte da verdade)

O contrato de saída está em `scripts/pipeline/schemas.py` (modelos Pydantic) e **toda saída é validada contra ele antes de ser impressa**; se o pipeline produzir algo fora do contrato, o CLI responde `{"erro": "saida_invalida", ...}` em vez de imprimir. O JSON Schema equivalente está em [`schemas/lead.schema.json`](./lead.schema.json), gerado a partir dos modelos:

```bash
uv run python -m scripts.pipeline.schemas > schemas/lead.schema.json
```

Um teste garante que o arquivo versionado está em sincronia com os modelos, e `tests/snapshots/` guarda a saída de referência de `descobrir`, `prospectar` e `validar-lista`.

## Saída

**Sempre JSON**, com proveniência por campo (fonte, URL/rota, timestamp), no máximo **100 leads validados por execução** (`lote` diz qual lote é: 1 por padrão, N+1 com `--continuar`; `descobertos`, `validados` e `esgotou_busca` dizem quanto foi buscado e se o nicho acabou) — decisão fechada, seção 11 do `ARCTETURE.md`. SQLite é usado só como storage interno de checkpoint do pipeline (retomar de onde parou), nunca como formato de saída para o usuário.
