# Dicionário de nichos comerciais → CNAE

Não é a tabela CNAE inteira (mais de 1.300 subclasses — inviável e desnecessário). É um dicionário dos nichos que aparecem em prospecção B2B real, cada um com: termos que o usuário usaria, CNAEs correspondentes (principal e vizinhos), e a largura de prefixo recomendada.

## Largura de prefixo — quando usar cada uma

| Largura | Exemplo | Alcance | Quando usar |
|---|---|---|---|
| Subclasse (7 dígitos) | `8630504` | estreito e preciso | nicho bem definido |
| Classe (5 dígitos) | `86305` | médio | nicho com variações |
| Grupo (4 dígitos) | `4520` | amplo | categoria geral |

**Regra de negócio**: o skill sempre propõe a subclasse mais precisa, mostra ao usuário quantas empresas cada largura retorna, e deixa ele escolher entre precisão e volume antes de rodar o pipeline inteiro.

## Nichos com múltiplos CNAEs

Muitos nichos reais não cabem em um CNAE só (ex.: "oficina mecânica" cobre mecânica, elétrica, funilaria e alinhamento). Nesses casos o skill trata o nicho como **conjunto de CNAEs**: executa uma consulta por CNAE e deduplica por CNPJ na união dos resultados.

## CNAE secundário

O detalhe do CNPJ (`route=cnpj`) traz os CNAEs secundários. Empresa cujo CNAE **principal** é o do nicho → lead forte. Empresa que tem o CNAE apenas como **secundário** → lead fraco (atividade existe, mas não é o core do negócio) — isso alimenta o score, não descarta.

## Dicionário

| Nicho (como o usuário fala) | CNAEs | Prefixo sugerido |
|---|---|---|
| dentista, clínica odontológica | 8630-5/04 | `8630504` |
| clínica médica, consultório médico | 8630-5/01, 8630-5/02, 8630-5/03 | `8630501`, `8630502`, `8630503` |
| fisioterapia, clínica de fisioterapia | 8650-0/04 | `8650004` |
| psicólogo, clínica de psicologia | 8650-0/03 | `8650003` |
| nutricionista | 8650-0/02 | `8650002` |
| fonoaudiologia, fonoaudiólogo | 8650-0/06 | `8650006` |
| laboratório de análises clínicas | 8640-2/02 | `8640202` |
| clínica de imagem, radiologia, ultrassom | 8640-2/04 a /07 | `8640204`, `8640205`, `8640206`, `8640207` |
| hospital | 8610-1/01, 8610-1/02 | `86101` |
| home care | 8712-3/00 | `8712300` |
| casa de repouso, asilo, ilpi | 8711-5/02 | `8711502` |
| drogaria, farmácia | 4771-7/01 | `4771701` |
| farmácia de manipulação | 4771-7/02 | `4771702` |
| clínica veterinária, veterinário | 7500-1/00 | `7500100` |
| ótica, ótica de óculos, loja de óculos | 4774-1/00 | `4774100` |
| salão de beleza, barbearia | 9602-5/01, 9602-5/02 | `96025` |
| clínica de estética, estética, esteticista | 9602-5/02 | `9602502` |
| academia, box de crossfit | 9313-1/00 | `9313100` |
| escola infantil, creche, berçário | 8511-2/00, 8512-1/00 | `8511200`, `8512100` |
| escola particular, colégio | 8513-9/00, 8520-1/00 | `8513900`, `8520100` |
| faculdade, ensino superior | 8531-7/00, 8532-5/00, 8533-3/00 | `8531700`, `8532500`, `8533300` |
| escola de idiomas, curso de inglês | 8593-7/00 | `8593700` |
| autoescola, centro de formação de condutores | 8599-6/01 | `8599601` |
| cursinho, curso preparatório | 8599-6/05 | `8599605` |
| treinamento corporativo, curso profissionalizante | 8599-6/04 | `8599604` |
| escola de música | 8592-9/03 | `8592903` |
| escola de dança, estúdio de dança | 8592-9/01 | `8592901` |
| escolinha de futebol, escola de esportes | 8591-1/00 | `8591100` |
| supermercado, hipermercado | 4711-3/01, 4711-3/02 | `47113` |
| mercearia, minimercado, mercadinho | 4712-1/00 | `4712100` |
| padaria | 4721-1/02, 1091-1/02 | dois CNAEs distintos |
| doceria, loja de doces | 4721-1/04 | `4721104` |
| açougue | 4722-9/01 | `4722901` |
| hortifruti, sacolão | 4724-5/00 | `4724500` |
| adega, loja de bebidas | 4723-7/00 | `4723700` |
| distribuidora de alimentos, atacadista de alimentos | 4639-7/01 | `4639701` |
| restaurante, lanchonete | 5611-2/01 a /05 | `56112` |
| bar, boteco, choperia | 5611-2/04, 5611-2/05 | `5611204`, `5611205` |
| cafeteria, casa de sucos | 5611-2/03 | `5611203` |
| buffet, bufê para eventos | 5620-1/02 | `5620102` |
| marmitaria, refeições para empresas | 5620-1/01, 5620-1/04 | `5620101`, `5620104` |
| hotel, pousada | 5510-8/01 | `5510801` |
| motel | 5510-8/03 | `5510803` |
| loja de roupas, boutique | 4781-4/00 | `4781400` |
| loja de calçados, sapataria | 4782-2/01 | `4782201` |
| joalheria, relojoaria | 4783-1/01, 4783-1/02 | `47831` |
| perfumaria, loja de cosméticos | 4772-5/00 | `4772500` |
| floricultura | 4789-0/02 | `4789002` |
| papelaria | 4761-0/03 | `4761003` |
| livraria | 4761-0/01 | `4761001` |
| loja de brinquedos | 4763-6/01 | `4763601` |
| loja de artigos esportivos | 4763-6/02 | `4763602` |
| loja de móveis | 4754-7/01 | `4754701` |
| loja de eletrodomésticos | 4753-9/00 | `4753900` |
| loja de informática | 4751-2/01 | `4751201` |
| loja de celular | 4752-1/00 | `4752100` |
| material de construção, home center | 4744-0/99, 4744-0/05 | `4744099`, `4744005` |
| loja de ferragens | 4744-0/01 | `4744001` |
| loja de tintas | 4741-5/00 | `4741500` |
| material elétrico | 4742-3/00 | `4742300` |
| vidraçaria | 4743-1/00 | `4743100` |
| pet shop, banho e tosa | 4789-0/04, 9609-2/08 | dois CNAEs distintos |
| autopeças | 4530-7/03 | `4530703` |
| concessionária, revenda de veículos | 4511-1/01, 4511-1/02 | `45111` |
| posto de combustível | 4731-8/00 | `4731800` |
| oficina mecânica, auto center | 4520-0/01 a /05 | `4520` |
| lava-rápido, lava jato | 4520-0/05 | `4520005` |
| estacionamento | 5223-1/00 | `5223100` |
| advocacia | 6911-7/01 | `6911701` |
| contabilidade, escritório contábil | 6920-6/01, 6920-6/02 | `69206` |
| imobiliária, corretor de imóveis | 6821-8/01, 6822-6/00 | `6821801`, `6822600` |
| condomínio, condomínio residencial | 8112-5/00 | `8112500` |
| construtora | 4120-4/00 | `4120400` |
| arquitetura, escritório de arquitetura | 7111-1/00 | `7111100` |
| engenharia, escritório de engenharia | 7112-0/00 | `7112000` |
| agência de marketing, agência de publicidade | 7311-4/00 | `7311400` |
| agência de viagens | 7911-2/00 | `7911200` |
| software house, desenvolvimento de software | 6201-5/01, 6202-3/00, 6203-1/00 | `6201501`, `6202300`, `6203100` |
| consultoria empresarial | 7020-4/00 | `7020400` |
| corretora de seguros | 6622-3/00 | `6622300` |
| gráfica | 1813-0/01, 1813-0/99 | `1813001`, `1813099` |
| transportadora | 4930-2/01, 4930-2/02 | `4930201`, `4930202` |
| empresa de mudanças | 4930-2/04 | `4930204` |
| empresa de limpeza, limpeza predial | 8121-4/00 | `8121400` |
| segurança privada, vigilância | 8011-1/01 | `8011101` |
| dedetizadora, controle de pragas | 8122-2/00 | `8122200` |
| lavanderia | 9601-7/01 | `9601701` |
| chaveiro | 9529-1/02 | `9529102` |
| assistência técnica de informática, conserto de celular | 9511-8/00, 9512-6/00 | `9511800`, `9512600` |
| instalação elétrica, eletricista | 4321-5/00 | `4321500` |
| instalação hidráulica, encanador | 4322-3/01 | `4322301` |
| ar condicionado, refrigeração | 4322-3/02 | `4322302` |
| estúdio fotográfico, fotógrafo | 7420-0/01 | `7420001` |
| funerária | 9603-3/04 | `9603304` |
| clube social, clube recreativo | 9312-3/00 | `9312300` |
| confecção, fábrica de roupas | 1412-6/01 | `1412601` |
| marcenaria, móveis planejados | 3101-2/00 | `3101200` |
| serralheria | 2512-8/00 | `2512800` |
| cervejaria artesanal | 1113-5/02 | `1113502` |

> Cobertura: 99 nichos (eram 11). Todo CNAE acima foi conferido contra a API real em 2026-11-03 (busca avançada em SP, situação ativa, com resultado). Os 11 nichos originais continuam com os mesmos CNAEs.

## Nicho fora do dicionário

Quando o nicho pedido não estiver aqui, o Claude propõe o CNAE por raciocínio e **pede confirmação do usuário antes de rodar**. A entrada confirmada deve ser adicionada a este arquivo, que assim melhora com o uso.
