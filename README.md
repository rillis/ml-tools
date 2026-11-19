# ml-tools-web

Ferramentas para os anúncios da conta do Mercado Livre:

- **[Margem](#margem)**: coleta os anúncios e calcula custo, margem e preço sugerido de cada um, a partir da tabela de preços do fornecedor.
- **[Comparador de SKUs](#comparador-de-skus)**: mostra quais SKUs da tabela não têm nenhum anúncio e quais não têm anúncio solo.
- **[Somador de envio](#somador-de-envio)**: lista os SKUs e as quantidades de um PDF de envio do Full.

Tudo pode ser feito pela aba **Margem** do ml-app (veja [Site](#site)) ou pelo menu do terminal (`run.sh`). Os dois usam o mesmo código, as mesmas tabelas e as mesmas saídas.

## Estrutura

```
ml-tools-web/
├── run.sh, menu.py      menu do terminal
├── web/                 API do site (app.py) e tarefas em segundo plano (jobs.py)
├── Dockerfile, docker-compose.yml, gunicorn.conf.py
├── tabelas/             precos.xlsx e apelidos.xlsx (usadas pela margem e pelo comparador)
├── comum/               API do ML (ml.py), autorização (autorizar.py), tabela de preços (tabela.py) e .env (env.py)
├── margem/              coleta.py e custo.py; data/, logs/ e saidas/ da margem
├── skus/                comparar.py; saidas/ do comparador
├── clonar/              pares.py, criar.py, margem.py, gtin.py e formato.py: opção de venda Clássico/Premium que falta
├── envio/               somar.py; input/ com os PDFs de envio
└── data/                token do ML, estado da última tarefa do site, versões anteriores das tabelas e clonar*.json
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
| `MARGEM_LUCRO`, `MARGEM_GORDURA`, `MARGEM_IMPOSTO` | as três partes da margem alvo, sobre o preço de venda (`0.095` = 9,5%). A margem alvo do preço sugerido é a soma delas; o imposto também vira as colunas `imposto` e `lucro_liquido` |

## Site

A aba **Margem** do ml-app (`/margem/`) é a interface web. O ml-app só repassa as chamadas para este serviço, que roda no container `ml-tools-web` na porta 51016.

- **Margem**: atualizar (ou continuar) a busca no ML, atualizar só alguns anúncios (**Só alguns**, pelos códigos MLB), recalcular sem buscar, mudar a margem alvo (lucro + gordura + imposto), tabela dos anúncios com etiquetas de tipo (Clássico/Premium) e envio (Full/Normal) e filtros (margem, status, tipo e envio) e a planilha para baixar. Em **Avançado**: buscar só ativos ou pausados, começar do zero, testar com alguns anúncios, recalcular com uma busca antiga e a lista das planilhas geradas.
- **Simulador**: preço sugerido de um SKU com a taxa, o frete e o adc digitados, pela mesma conta da planilha, mostrando o custo de cada parte do SKU.
- **Clonar**: lista os anúncios ativos e pausados sem a outra opção de venda (Clássico sem Premium ou o contrário) no mesmo produto e cria a que falta, um anúncio ou vários marcados (até 20 por vez). Veja [Clonar](#clonar).
- **SKUs**: roda o comparador e mostra os SKUs sem anúncio ou sem solo e os anúncios com peça fora da tabela.
- **Envio**: envia o PDF do envio do Full e copia a lista somada.
- **Tabelas**: baixar e trocar o `precos.xlsx` e o `apelidos.xlsx`. A versão anterior fica em `data/tabelas-anteriores/`.
- **Acesso**: estado do token, reautorização do app no ML e limite de requisições por segundo.

As buscas no ML e os cálculos rodam em segundo plano, uma tarefa por vez, com os mesmos módulos do menu (`python -m ...`). A tela mostra o progresso e dá para fechar a aba. O botão **Parar** interrompe a tarefa como um Ctrl+C: o que já foi buscado fica salvo e **Continuar a atualização** retoma de onde parou. Uma busca no ML pelo menu e outra pelo site não rodam ao mesmo tempo.

O `custo.py` e o comparador gravam, ao lado de cada planilha, um `.json` com os valores já calculados. É ele que o site mostra.

### Deploy

```bash
docker compose up -d --build
```

A pasta do projeto é montada em `/app`, então o container usa o mesmo `.env`, as mesmas tabelas e as mesmas saídas do menu. Mudança em Python entra com `docker compose restart`; só é preciso `--build` ao mudar o `requirements.txt` ou o `Dockerfile`.

O ml-app acha o serviço por `MARGEM_URL` no `.env` dele (padrão `http://192.168.15.11:51016`).

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
- **Atualizar só alguns anúncios (MLB)**: busca de novo no ML só os anúncios informados (códigos com ou sem MLB: `MLB123`, `MLB-123` ou `123`) e troca as linhas deles na última busca completa; os outros ficam como estão. Depois gera a planilha. Precisa de uma busca completa terminada; anúncio que não estava nela entra no fim.
- **Edição à mão** (só no site, botão ✏️ na linha do anúncio): corrige preço, tarifa, frete ou adc de um anúncio na última busca, sem chamar o ML, para divergências com o painel. Gera uma planilha nova da busca e recalcula a margem; o antes e o depois ficam em `data/edicoes-manuais.json`. O próximo Atualizar margens (ou Só alguns com esse anúncio) desfaz a edição.
- **Recalcular sem buscar no ML**: gera a planilha de novo com a última busca, depois de mudar o `precos.xlsx` ou o `apelidos.xlsx`.
- **Mudar o lucro / a gordura / o imposto da margem alvo**: grava `MARGEM_LUCRO`, `MARGEM_GORDURA` ou `MARGEM_IMPOSTO` e oferece recalcular a planilha. A margem alvo é a soma das três.
- **Avançado**: buscar só ativos ou só pausados, testar com alguns anúncios, recalcular com uma busca mais antiga, gerar a planilha da busca atual sem chamar a API e ver o log.

### Comandos

```bash
.venv/bin/python -m margem.coleta                    # coleta todos os anúncios (retoma se tiver parado)
.venv/bin/python -m margem.coleta --ids MLB1,MLB2    # teste com alguns anúncios
.venv/bin/python -m margem.coleta --novo             # descarta a coleta parada e começa de novo
.venv/bin/python -m margem.coleta --exportar         # só gera a planilha da coleta atual
.venv/bin/python -m margem.coleta --status active    # só um status
.venv/bin/python -m margem.coleta --atualizar MLB1,MLB2   # só estes, trocando as linhas deles na última busca completa
.venv/bin/python -m margem.custo                     # custo e margem com a coleta mais recente
.venv/bin/python -m margem.editar --item MLB123 --frete 13.85   # edição à mão (também --preco, --taxa em %, --adc)
.venv/bin/python -m margem.custo --coleta margem/saidas/margem-....xlsx
```

### Coleta

- O progresso fica em `margem/data/margem.db` e cada anúncio é salvo ao terminar. Se a coleta for interrompida, a próxima execução continua de onde parou; quando ela termina, a próxima começa uma nova.
- Anúncios que dão erro vão para a aba `pendentes` e são tentados de novo na execução seguinte.
- A tela mostra uma barra de progresso; o detalhe de cada anúncio fica em `margem/logs/margem.log`. No fim aparece um resumo com o total de requisições e de respostas 429.
- Saída: `margem/saidas/margem-AAAA-MM-DD_HHMM.xlsx` com `codigo`, `sku`, `titulo`, `status`, `preco`, `taxa`, `adc`, `frete`, `tipo` e `envio`.
  - `taxa` é o percentual da tarifa de venda do ML, buscado pela categoria, pelo tipo de anúncio e pelo preço de cada anúncio (o percentual muda conforme a faixa de preço).
  - `titulo` é o nome do anúncio sem a variação (`family_name`); se não houver, o título completo.
  - `frete` é o custo de envio dos anúncios com frete grátis; `adc` é o dos anúncios sem frete grátis. Um dos dois é sempre 0.
  - `tipo` é Clássico ou Premium; `envio` é Full (anúncio no Full do ML) ou Normal.

### Planilha de custo

`margem/saidas/custo-AAAA-MM-DD_HHMM.xlsx`:

| Coluna | O que é |
|---|---|
| `codigo`, `sku`, `titulo`, `status` | da coleta |
| `so_gratis` | `sim` se o anúncio só tem itens grátis |
| `preco`, `taxa`, `adc`, `frete` | da coleta |
| `custo` | soma dos preços do fornecedor, sem desconto |
| `custo_desc` | custo com o desconto de 15% |
| `lucro` | preço − taxa − adc − frete − custo_desc (antes do imposto) |
| `margem` | lucro ÷ preço, comparada com a margem alvo (vermelho abaixo dela − 0,1 p.p., amarelo acima dela + 0,1 p.p.) |
| `imposto` | preço × `MARGEM_IMPOSTO` |
| `lucro_liquido` | lucro − imposto |
| `preco_sugerido` | preço que dá a margem alvo (lucro + gordura + imposto); nos anúncios `so_gratis`, o preço atual |
| `diferenca` | preco_sugerido − preco (positivo: precisa subir) |
| `diferenca_pct` | diferenca ÷ preco em %, em valor absoluto (sempre positivo) |
| `tipo`, `envio` | da coleta (Clássico/Premium e Full/Normal). Numa coleta de antes dessas colunas, o `tipo` vem do `margem.db` e o `envio` fica vazio até a próxima busca |

Da `custo_desc` em diante são fórmulas: ao corrigir um custo na planilha, o resto recalcula.

O `preco_sugerido` usa a taxa e o custo de envio do preço atual. Se o novo preço mudar a faixa de tarifa ou de frete do ML, a margem real pode ficar um pouco diferente; a próxima coleta mostra o valor atualizado.

## Clonar

Cria a opção de venda que falta (Clássico ↔ Premium) no mesmo produto do ML (user product), pelo preço da margem alvo.

```bash
.venv/bin/python -m clonar.pares                     # lista os anúncios sem par (data/clonar.json); só lê o ML
.venv/bin/python -m clonar.pares --ids MLB1,MLB2     # teste com alguns anúncios
.venv/bin/python -m clonar.criar --item MLB123       # cria a opção que falta deste anúncio e crava a margem
.venv/bin/python -m clonar.criar --itens MLB1,MLB2   # até 20: cria todos e confere todos a cada rodada
.venv/bin/python -m clonar.margem                    # atualiza o novo e o original na planilha de margem
.venv/bin/python -m clonar.gtin --item MLB123         # plano para o GTIN duplicado (só lê o ML)
.venv/bin/python -m clonar.gtin --item MLB123 --corrigir   # tira o GTIN de quem não deveria ter
.venv/bin/python -m clonar.formato --item MLB123 --formato pack --qtd 3   # formato de venda e quantidade
```

- **Sem par**: anúncio ativo ou pausado sem nenhum anúncio do outro tipo (ativo, pausado ou em revisão) com o mesmo SKU nem no mesmo produto. O SKU é a chave principal, porque anúncios antigos ficam em produtos diferentes. Cada SKU vira uma linha (a do anúncio ativo fora de catálogo, de preferência); anúncio sem SKU é comparado só pelo produto. Anúncio só com itens grátis fica de fora (kit com item pago entra).
- **Criar** (`clonar.criar`):
  1. Lê o original e confere de novo, pelo SKU e pelo produto, que a outra opção não existe.
  2. Calcula o preço da opção nova: custo do SKU pela tabela de preços, tarifa do tipo novo nesse preço e frete/adc do original.
  3. Cria a opção (`POST /user-products/{id}/items`) com a garantia do original. Ela nasce no envio normal, mesmo que o original seja Full, e usa o estoque do produto (o mesmo do original).
  4. Relê o anúncio novo a cada 10 s (até 10 vezes) e ajusta o preço até ele ficar no preço da margem alvo em duas leituras seguidas. Se o preço ficar indo e voltando entre dois valores (faixa de frete ou de tarifa), fica no maior.
  5. Relê o original junto: se a tarifa ou o frete dele mudou e a margem saiu do alvo, ajusta o preço dele e avisa (em promoção, só avisa). Original que já começou fora do alvo e não mudou fica como está.
- Em lote, cria todos primeiro e depois confere todos a cada rodada; o erro de um não para os outros. A etapa de cada anúncio (na fila, criando, conferindo, cravado, sem cravar, erro) fica em `data/clonar-andamento.json`, que o site mostra numa janela até a criação terminar.
- **GTIN duplicado**: o ML recusa criar a opção nova quando o GTIN do produto já está num anúncio seu de outra categoria (é comum o kit usar o GTIN de uma das peças). A busca marca esses anúncios, e no site o botão **Corrigir GTIN** mostra o plano antes de executar (`clonar.gtin`):
  1. Junta os anúncios com o mesmo GTIN por produto e escolhe quem fica com ele: o produto do mesmo domínio que o produto de catálogo do ML com esse GTIN; sem catálogo que decida, o SKU solo (o kit perde). Se nem assim der para decidir, não mexe.
  2. Quem perde fica com "Meu produto não o tem" (`EMPTY_GTIN_REASON`): "kit ou pack" para kit e "não tem código cadastrado" para solo. Edita um anúncio por produto, de preferência o que não é de catálogo; o ML replica nos outros do mesmo produto.
  3. Confere que o GTIN saiu; depois é só criar a opção de novo.
- **Formato de venda e quantidade**: algumas categorias pedem a "Quantidade de unidades" (`UNITS_PER_PACK`) sempre que o "Formato de venda" (`SALE_FORMAT`) está preenchido, e o ML recusa a opção nova sem ela. Esses campos não aparecem no painel. Quando a criação dá esse erro, o site mostra **Corrigir e tentar de novo** (no andamento e na lista): escolhe Unidade ou Kit/pack e a quantidade (sugerida pelo SKU), e a tarefa grava os dois (`clonar.formato`), espera 5 s, tenta criar de novo aquele anúncio e atualiza a margem. O estado de antes fica em `data/clonar-formato/`.
- No site, depois de criar, o novo e o original são atualizados na planilha de margem (como o **Só alguns**), se houver uma busca completa terminada.
- O resultado de cada criação (preços, margem e avisos) fica em `data/clonar-historico.json` e aparece na lista **Criados** da aba.

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
