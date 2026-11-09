# ml-tools

Ferramentas para os anúncios da conta do Mercado Livre:

- **[Margem](#margem)**: coleta os anúncios e calcula custo, margem e preço sugerido de cada um, a partir da tabela de preços do fornecedor.
- **[Comparador de SKUs](#comparador-de-skus)**: mostra quais SKUs da tabela não têm nenhum anúncio e quais não têm anúncio solo.
- **[Somador de envio](#somador-de-envio)**: lista os SKUs e as quantidades de um PDF de envio do Full.

Tudo pode ser feito pelo menu (`run.sh`), sem digitar argumentos.

## Estrutura

```
ml-tools/
├── run.sh, menu.py      menu
├── tabelas/             precos.xlsx e apelidos.xlsx (usadas pela margem e pelo comparador)
├── comum/               API do ML (ml.py), autorização (autorizar.py) e tabela de preços (tabela.py)
├── margem/              coleta.py e custo.py; data/, logs/ e saidas/ da margem
├── skus/                comparar.py; saidas/ do comparador
├── envio/               somar.py; input/ com os PDFs de envio
└── data/                token do ML
```

As pastas `data/`, `logs/`, `saidas/` e `input/` (de qualquer módulo) ficam fora do Git.

## Instalação

Requer Python 3 com `venv`.

```bash
cp .env.example .env      # e preencha com os dados do app do ML
./run.sh                  # cria a .venv e instala as dependências na primeira vez
```

No menu, use **Autorizar o app no ML** uma vez para gerar o token da conta. Depois disso ele é renovado automaticamente.

### `.env`

| Variável | O que é |
|---|---|
| `ML_APP_ID`, `ML_SECRET_KEY` | dados do app criado no painel de desenvolvedor do ML |
| `ML_REDIRECT_URI` | redirect URI exatamente como cadastrada no app |
| `ML_PKCE` | `1` se o PKCE estiver ligado no app, senão `0` |
| `ML_MAX_RPS` | requisições por segundo na API do ML (padrão 2) |
| `MARGEM_META` | margem alvo do preço sugerido (`0.18` = 18%) |
| `MARGEM_META_ATACADO` | margem alvo do preço de atacado (`0.175` = 17,5%) |

## Menu

```bash
./run.sh
```

Setas e Enter para escolher, Ctrl+C para voltar. A tela inicial lista os módulos:

- **Margem e preço sugerido...**: abre o menu da margem (veja [Margem](#margem)).
- **Comparar SKUs da tabela com os anúncios**: roda o [comparador de SKUs](#comparador-de-skus).
- **Somar os SKUs do PDF de envio**: roda o [somador de envio](#somador-de-envio).
- **Acesso ao ML e configurações...**: estado do token, reautorização do app e limite de requisições por segundo.

Enquanto o app não estiver autorizado, a tela inicial mostra **Autorizar o app no ML** no topo.

Cada módulo também roda direto, na raiz do projeto, com o Python da venv (`.venv/bin/python -m <módulo>`); os comandos estão em cada seção abaixo.

## Tabelas

As duas planilhas ficam em `tabelas/` e são usadas pela margem e pelo comparador.

### `precos.xlsx`

Tabela do fornecedor: `SKU` e `PRECO`, **sem** o desconto (o desconto de 15% é aplicado no cálculo da margem). Fórmulas simples entre células, como `=B390` ou `=B439+B394`, são aceitas. É também a lista de todos os SKUs para o comparador.

### `apelidos.xlsx`

Para SKUs de anúncio que não dá para ligar à tabela automaticamente. Colunas:

- `sku`: o SKU do anúncio, ou uma peça dele;
- `composicao`: um destes:
  - outros SKUs da tabela separados por `+` (somados);
  - `0` para item grátis: custa 0 sozinho e R$ 1 por unidade num kit com algum item pago;
  - outro número, que vira custo fixo;
- `obs`: anotação livre (não é lida).

### Como o SKU do anúncio vira SKUs da tabela

1. Se o SKU estiver no `apelidos.xlsx`, usa o apelido.
2. Se o SKU inteiro existir no `precos.xlsx` (ignorando maiúsculas e espaços), usa esse SKU.
3. Senão, separa por `+` e procura cada peça. Cada peça pode ter:
   - quantidade na frente: `3xKA-250`;
   - diferença só de pontuação: `Cab-bico042` = `CAB-BICO-042`;
   - barra dentro de um trecho: `TP-036.M/F.GOLF` = `TP-036.M.GOLF` + `TP-036.F.GOLF`.

Na margem, o custo é a soma dos preços das peças; se faltar o preço de alguma, o anúncio fica sem custo e o resumo na tela diz qual peça faltou.

## Margem

O fluxo tem duas etapas:

1. **Coleta** (`margem/coleta.py`): busca na API do ML o preço atual, a taxa, o custo de envio, o SKU, o título e o status de cada anúncio.
2. **Custo** (`margem/custo.py`): cruza a coleta com as [tabelas](#tabelas) e gera a planilha de margem. Não chama a API.

### Menu da margem

- **Atualizar margens**: busca todos os anúncios no ML e já gera a planilha de custo e margem. Se a busca for interrompida, a opção vira **Continuar a atualização**.
- **Recalcular sem buscar no ML**: gera a planilha de novo com a última busca, depois de mudar o `precos.xlsx` ou o `apelidos.xlsx`.
- **Mudar a margem alvo**: grava a `MARGEM_META` e oferece recalcular a planilha.
- **Mudar a margem do atacado**: grava a `MARGEM_META_ATACADO` e oferece recalcular a planilha.
- **Avançado**: buscar só ativos ou só pausados, testar com alguns anúncios, recalcular com uma busca mais antiga, gerar a planilha da busca atual sem chamar a API e ver o log.

### Comandos

```bash
.venv/bin/python -m margem.coleta                    # coleta todos os anúncios (retoma se tiver parado)
.venv/bin/python -m margem.coleta --ids MLB1,MLB2    # teste com alguns anúncios
.venv/bin/python -m margem.coleta --novo             # descarta a coleta parada e começa de novo
.venv/bin/python -m margem.coleta --exportar         # só gera a planilha da coleta atual
.venv/bin/python -m margem.coleta --status active    # só um status
.venv/bin/python -m margem.custo                     # custo e margem com a coleta mais recente
.venv/bin/python -m margem.custo --coleta margem/saidas/margem-....xlsx
```

### Coleta

- O progresso fica em `margem/data/margem.db` e cada anúncio é salvo ao terminar. Se a coleta for interrompida, a próxima execução continua de onde parou; quando ela termina, a próxima começa uma nova.
- Anúncios que dão erro vão para a aba `pendentes` e são tentados de novo na execução seguinte.
- A tela mostra uma barra de progresso; o detalhe de cada anúncio fica em `margem/logs/margem.log`. No fim aparece um resumo com o total de requisições e de respostas 429.
- Saída: `margem/saidas/margem-AAAA-MM-DD_HHMM.xlsx` com `codigo`, `sku`, `titulo`, `status`, `preco`, `taxa`, `adc` e `frete`.
  - `titulo` é o nome do anúncio sem a variação (`family_name`); se não houver, o título completo.
  - `frete` é o custo de envio dos anúncios com frete grátis; `adc` é o dos anúncios sem frete grátis. Um dos dois é sempre 0.

### Planilha de custo

`margem/saidas/custo-AAAA-MM-DD_HHMM.xlsx`:

| Coluna | O que é |
|---|---|
| `codigo`, `sku`, `titulo`, `status` | da coleta |
| `so_gratis` | `sim` se o anúncio só tem itens grátis |
| `preco`, `taxa`, `adc`, `frete` | da coleta |
| `custo` | soma dos preços do fornecedor, sem desconto |
| `custo_desc` | custo com o desconto de 15% |
| `lucro` | preço − taxa − adc − frete − custo_desc |
| `margem` | lucro ÷ preço (vermelho abaixo de META − 3 p.p., amarelo acima de META + 3 p.p.) |
| `preco_sugerido` | preço que dá a `MARGEM_META`; nos anúncios `so_gratis`, o preço atual |
| `diferenca` | preco_sugerido − preco (positivo: precisa subir) |
| `diferenca_pct` | diferenca ÷ preco em %, em valor absoluto (sempre positivo) |
| `preco_sugerido_atacado`, `diferenca_atacado`, `diferenca_pct_atacado` | o mesmo das três anteriores, com a `MARGEM_META_ATACADO` |

Da `custo_desc` em diante são fórmulas: ao corrigir um custo na planilha, o resto recalcula.

O `preco_sugerido` usa a taxa e o custo de envio do preço atual. Se o novo preço mudar a faixa de frete do ML, a margem real pode ficar um pouco diferente; a próxima coleta mostra o valor atualizado.

## Comparador de SKUs

`skus/comparar.py` busca no ML só o SKU, o título e o status de todos os anúncios (qualquer status) e cruza com o `precos.xlsx`, que serve de lista de todos os SKUs. Não depende da coleta de margem.

```bash
.venv/bin/python -m skus                      # todos os anúncios
.venv/bin/python -m skus --ids MLB1,MLB2      # alguns anúncios (teste)
```

O SKU do anúncio vira SKUs da tabela pelas [mesmas regras](#como-o-sku-do-anúncio-vira-skus-da-tabela) da margem. Cada anúncio conta para os SKUs dele como:

- **solo**: só aquele SKU pago, em qualquer quantidade (`3xKA-250` é solo do `KA-250`). Itens grátis (apelido `0`) não contam: `KA-032+3xMG-CORRUGADA` é solo do `KA-032` e comp do `MG-CORRUGADA`;
- **comp**: o SKU junto com outros (`KA-250+KA-055`, `TP-036.M/F.GOLF`).

Saída: `skus/saidas/skus-AAAA-MM-DD_HHMM.xlsx` (`skus-teste-...` com `--ids`).

Aba `skus`, um SKU da tabela por linha, na ordem do `precos.xlsx`:

| Coluna | O que é |
|---|---|
| `sku` | SKU da tabela |
| `qtd_anuncios_comp`, `qtd_anuncios_solo` | anúncios em que o SKU aparece em composição / solo, qualquer status |
| `qtd_ativos_comp`, `qtd_ativos_solo` | o mesmo, só dos anúncios ativos |
| `situacao` | `sem anuncio` (vermelho), `sem solo` (amarelo) ou `ok` |

Aba `sem tabela`: anúncios com alguma peça do SKU que não está na tabela (`fora_da_tabela`). As peças que estão na tabela contam como comp. Para corrigir, use o `apelidos.xlsx`.

## Somador de envio

Coloque em `envio/input/` o PDF de instruções de preparação de um envio do Full (`Inbound-...-preparation-instructions.pdf`) e rode o somador. Ele mostra os 3 PDFs mais recentes da pasta, por data, para você escolher um.

```bash
.venv/bin/python -m envio                            # escolhe entre os 3 PDFs mais recentes
.venv/bin/python -m envio envio/input/ARQUIVO.pdf    # um PDF específico
```

O somador separa os kits em peças (`KF-216+KA-035`, `3xKA-250`), junta os SKUs iguais (ignorando maiúsculas) e imprime um por linha, em ordem alfabética:

```
6x KA-035
11x KF-216
```

Antes, confere o número de produtos e de unidades com o cabeçalho do PDF; se não bater, mostra o erro. Os PDFs não são apagados.

## Acesso ao ML

O token da conta fica em `data/tokens.json` e é renovado sozinho. Para autorizar (ou reautorizar) fora do menu:

```bash
.venv/bin/python -m comum.autorizar                  # gera a URL de autorização
.venv/bin/python -m comum.autorizar "URL de retorno"
```
