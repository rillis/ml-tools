# ml-tools

Ferramentas para os anúncios da conta do Mercado Livre. A principal coleta os anúncios e calcula custo, margem e preço sugerido de cada um, a partir da tabela de preços do fornecedor.

O fluxo de margem tem duas etapas:

1. **Coleta** (`margem.py`): busca na API do ML o preço atual, a taxa, o custo de envio, o SKU, o título e o status de cada anúncio.
2. **Custo** (`custo.py`): cruza a coleta com o `precos.xlsx` e o `apelidos.xlsx` e gera a planilha de margem. Não chama a API.

À parte, o **comparador de SKUs** (`skus.py`) mostra quais SKUs da tabela não têm nenhum anúncio e quais não têm anúncio solo.

E o **somador de envio** (`envio.py`) lista os SKUs e as quantidades de um PDF de envio do Full.

Tudo pode ser feito pelo menu (`run.sh`), sem digitar argumentos.

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
| `ML_MAX_RPS` | requisições por segundo da coleta (padrão 2) |
| `MARGEM_META` | margem alvo do preço sugerido (`0.18` = 18%) |
| `MARGEM_META_ATACADO` | margem alvo do preço de atacado (`0.175` = 17,5%) |

## Uso

```bash
./run.sh
```

Abre o menu (setas e Enter, Ctrl+C volta):

- **Atualizar margens**: busca todos os anúncios no ML e já gera a planilha de custo e margem. Se a busca for interrompida, a opção vira **Continuar a atualização**.
- **Recalcular sem buscar no ML**: gera a planilha de novo com a última busca, depois de mudar o `precos.xlsx` ou o `apelidos.xlsx`.
- **Comparar SKUs da tabela com os anúncios**: busca o SKU de todos os anúncios no ML e gera a planilha do comparador (veja abaixo).
- **Somar os SKUs do PDF de envio**: escolhe um dos 3 PDFs mais recentes da pasta `input/` (veja abaixo).
- **Mudar a margem alvo**: grava a `MARGEM_META` e oferece recalcular a planilha.
- **Mudar a margem do atacado**: grava a `MARGEM_META_ATACADO` e oferece recalcular a planilha.
- **Avançado**: buscar só ativos ou só pausados, testar com alguns anúncios, recalcular com uma busca mais antiga, gerar a planilha da busca atual sem chamar a API, ver o log, mudar o limite de requisições e reautorizar o app.

Enquanto o app não estiver autorizado, o menu mostra **Autorizar o app no ML** no topo.

Os scripts também podem ser rodados direto, com o Python da venv:

```bash
.venv/bin/python margem.py                    # coleta todos os anúncios (retoma se tiver parado)
.venv/bin/python margem.py --ids MLB1,MLB2    # teste com alguns anúncios
.venv/bin/python margem.py --novo             # descarta a coleta parada e começa de novo
.venv/bin/python margem.py --exportar         # só gera a planilha da coleta atual
.venv/bin/python margem.py --status active    # só um status
.venv/bin/python custo.py                     # custo e margem com a coleta mais recente
.venv/bin/python custo.py --coleta saidas/margem-....xlsx
.venv/bin/python skus.py                      # comparador de SKUs (busca os anúncios no ML)
.venv/bin/python skus.py --ids MLB1,MLB2      # comparador com alguns anúncios (teste)
.venv/bin/python envio.py                     # escolhe um dos 3 PDFs de envio mais recentes de input/ e soma os SKUs
.venv/bin/python envio.py input/ARQUIVO.pdf   # soma os SKUs de um PDF específico
.venv/bin/python authorize.py                 # gera a URL de autorização
.venv/bin/python authorize.py "URL de retorno"
```

### Coleta

- O progresso fica em `data/margem.db` e cada anúncio é salvo ao terminar. Se a coleta for interrompida, a próxima execução continua de onde parou; quando ela termina, a próxima começa uma nova.
- Anúncios que dão erro vão para a aba `pendentes` e são tentados de novo na execução seguinte.
- A tela mostra uma barra de progresso; o detalhe de cada anúncio fica em `logs/margem.log`. No fim aparece um resumo com o total de requisições e de respostas 429.
- Saída: `saidas/margem-AAAA-MM-DD_HHMM.xlsx` com `codigo`, `sku`, `titulo`, `status`, `preco`, `taxa`, `adc` e `frete`.
  - `titulo` é o nome do anúncio sem a variação (`family_name`); se não houver, o título completo.
  - `frete` é o custo de envio dos anúncios com frete grátis; `adc` é o dos anúncios sem frete grátis. Um dos dois é sempre 0.

## Planilhas de entrada

### `precos.xlsx`

Tabela do fornecedor: `SKU` e `PRECO`, **sem** o desconto (o desconto de 15% é aplicado no cálculo). Fórmulas simples entre células, como `=B390` ou `=B439+B394`, são aceitas.

### `apelidos.xlsx`

Para SKUs de anúncio que não dá para ligar à tabela automaticamente. Colunas:

- `sku`: o SKU do anúncio, ou uma peça dele;
- `composicao`: um destes:
  - outros SKUs da tabela separados por `+` (somados);
  - `0` para item grátis: custa 0 sozinho e R$ 1 por unidade num kit com algum item pago;
  - outro número, que vira custo fixo;
- `obs`: anotação livre (não é lida).

### Como o SKU do anúncio vira custo

1. Se o SKU estiver no `apelidos.xlsx`, usa o apelido.
2. Se o SKU inteiro existir no `precos.xlsx` (ignorando maiúsculas e espaços), usa o preço.
3. Senão, separa por `+` e soma as peças. Cada peça pode ter:
   - quantidade na frente: `3xKA-250`;
   - diferença só de pontuação: `Cab-bico042` = `CAB-BICO-042`;
   - barra dentro de um trecho: `TP-036.M/F.GOLF` = `TP-036.M.GOLF` + `TP-036.F.GOLF`.

Se faltar o preço de alguma peça, o anúncio fica sem custo e o resumo na tela diz qual peça faltou.

## Planilha de custo

`saidas/custo-AAAA-MM-DD_HHMM.xlsx`:

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

`skus.py` busca no ML só o SKU, o título e o status de todos os anúncios (qualquer status) e cruza com o `precos.xlsx`, que serve de lista de todos os SKUs. Não depende da coleta de margem.

O SKU do anúncio vira SKUs da tabela pelas mesmas regras do custo (apelidos, pontuação, barra, quantidade). Cada anúncio conta para os SKUs dele como:

- **solo**: só aquele SKU pago, em qualquer quantidade (`3xKA-250` é solo do `KA-250`). Itens grátis (apelido `0`) não contam: `KA-032+3xMG-CORRUGADA` é solo do `KA-032` e comp do `MG-CORRUGADA`;
- **comp**: o SKU junto com outros (`KA-250+KA-055`, `TP-036.M/F.GOLF`).

Saída: `saidas/skus-AAAA-MM-DD_HHMM.xlsx` (`skus-teste-...` com `--ids`).

Aba `skus`, um SKU da tabela por linha, na ordem do `precos.xlsx`:

| Coluna | O que é |
|---|---|
| `sku` | SKU da tabela |
| `qtd_anuncios_comp`, `qtd_anuncios_solo` | anúncios em que o SKU aparece em composição / solo, qualquer status |
| `qtd_ativos_comp`, `qtd_ativos_solo` | o mesmo, só dos anúncios ativos |
| `situacao` | `sem anuncio` (vermelho), `sem solo` (amarelo) ou `ok` |

Aba `sem tabela`: anúncios com alguma peça do SKU que não está na tabela (`fora_da_tabela`). As peças que estão na tabela contam como comp. Para corrigir, use o `apelidos.xlsx`.


## Somador de envio

Coloque em `input/` o PDF de instruções de preparação de um envio do Full (`Inbound-...-preparation-instructions.pdf`) e rode `envio.py`. Ele mostra os 3 PDFs mais recentes da pasta, por data, para você escolher um.

O script separa os kits em peças (`KF-216+KA-035`, `3xKA-250`), junta os SKUs iguais (ignorando maiúsculas) e imprime um por linha, em ordem alfabética:

```
6x KA-035
11x KF-216
```

Antes, confere o número de produtos e de unidades com o cabeçalho do PDF; se não bater, mostra o erro. Os PDFs não são apagados.
