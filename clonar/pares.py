"""Lista os anúncios ativos e pausados cujo produto (user product) não tem a outra opção de venda.

Uso:  .venv/bin/python -m clonar.pares [--ids MLB1,MLB2]

Um anúncio Clássico sem nenhum Premium com o mesmo SKU nem no mesmo produto (ou o contrário) vira uma linha
em data/clonar.json, que a aba Clonar do site mostra. O par conta se estiver ativo ou pausado; anúncios antigos
ficam em produtos diferentes, por isso o SKU é a chave principal. Anúncio em revisão também conta como par. Um SKU vira uma linha só (a do anúncio ativo
fora de catálogo, de preferência). Anúncios só com itens grátis ficam de fora (kit com item pago entra).
Se o GTIN do anúncio está noutro anúncio seu de outra categoria, a linha sai marcada (gtin): o ML recusa
criar a opção nova nesse caso, e o clonar.gtin corrige.
Só lê o ML, não muda nada.
Com --ids, olha só esses anúncios (teste).
"""
import argparse
import logging
import sys

from tqdm import tqdm

from clonar import NOMES, OUTRO, PAR, PARES, agora, eh_kit, gravar_json, gtins_de
from comum import ml
from comum.ml import LOTE, sku_de, titulo_de
from comum.tabela import carregar, norm

CAMPOS = 'id,title,family_name,price,status,listing_type_id,user_product_id,category_id,domain_id,shipping,attributes,' \
         'seller_custom_field,catalog_listing'
log = logging.getLogger('clonar')


def listar(c, seller, status):
    ids, params = [], {'search_type': 'scan', 'limit': 100, 'status': status}
    while True:
        r = c.get(f'/users/{seller}/items/search', params)
        if not r.get('results'):
            return ids
        ids += r['results']
        params = {'search_type': 'scan', 'limit': 100, 'status': status, 'scroll_id': r['scroll_id']}


def main():
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ids', help='só estes anúncios (teste), separados por vírgula')
    args = ap.parse_args()
    trava = ml.travar_busca()  # noqa: F841 (segura a trava até o fim)

    c = ml.MLClient()
    seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
    try:
        if args.ids:
            ids = list(dict.fromkeys(i.strip().upper() for i in args.ids.split(',') if i.strip()))
        else:
            log.info('Listando os anúncios ativos, pausados e em revisão...')
            ids = list(dict.fromkeys(listar(c, seller, 'active') + listar(c, seller, 'paused')
                                     + listar(c, seller, 'under_review')))
        log.info(f'{len(ids)} anúncios: buscando o produto e o tipo de cada um...')
        itens = []
        pb = tqdm(total=len(ids), desc='Anúncios', unit='anúncio', dynamic_ncols=True,
                  bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
        for n in range(0, len(ids), LOTE):
            lote = ids[n:n + LOTE]
            for x in c.get('/items', {'ids': ','.join(lote), 'attributes': CAMPOS}):
                if x.get('code') == 200 and x.get('body'):
                    itens.append(x['body'])
            pb.update(len(lote))
        pb.close()
    except KeyboardInterrupt:
        log.warning('Parado: nada foi gravado.')
        sys.exit(130)
    except ml.MLError as e:
        log.error(f'Erro no ML: {e}')
        sys.exit(1)

    # Par = mesmo SKU (anúncios antigos ficam em produtos diferentes) ou mesmo produto do ML (user product),
    # com o outro tipo ativo ou pausado. Com --ids, pergunta ao ML os outros anúncios desse SKU e produto.
    vivos = [b for b in itens if b.get('status') in PAR and b.get('listing_type_id') in OUTRO]
    if args.ids:
        vistos = {b['id'] for b in vivos}
        extra = set()
        for b in vivos:
            if sku_de(b):
                extra |= set(c.get(f'/users/{seller}/items/search', {'seller_sku': sku_de(b)}).get('results') or [])
            if b.get('user_product_id'):
                extra |= set(c.get(f'/users/{seller}/items/search',
                                   {'user_product_id': b['user_product_id']}).get('results') or [])
        extra = sorted(extra - vistos)
        for n in range(0, len(extra), LOTE):
            for x in c.get('/items', {'ids': ','.join(extra[n:n + LOTE]), 'attributes': CAMPOS}):
                b = x.get('body') or {}
                if x.get('code') == 200 and b.get('status') in PAR and b.get('listing_type_id') in OUTRO:
                    vivos.append(b)
    tipos_sku, tipos_up = {}, {}
    for b in vivos:
        if norm(sku_de(b)):
            tipos_sku.setdefault(norm(sku_de(b)), set()).add(b['listing_type_id'])
        if b.get('user_product_id'):
            tipos_up.setdefault(b['user_product_id'], set()).add(b['listing_type_id'])

    candidatos = {}  # um por SKU (ou por produto, sem SKU): o ativo, fora de catálogo, de preferência
    sem_up = gratis = 0
    tab = carregar()
    alvos = itens if not args.ids else [b for b in vivos if b['id'] in set(ids)]
    for b in alvos:
        tipo = b.get('listing_type_id')
        if b.get('status') not in ('active', 'paused') or tipo not in OUTRO:
            continue
        sku = norm(sku_de(b))
        up = b.get('user_product_id')
        if tab.so_gratis(sku_de(b)):  # só itens grátis (kit com item pago entra normalmente)
            gratis += 1
            continue
        if OUTRO[tipo] in tipos_sku.get(sku, set()) or (up and OUTRO[tipo] in tipos_up.get(up, set())):
            continue
        if not up:
            sem_up += 1
            continue
        chave = sku or up
        atual = candidatos.get(chave)
        ordem = (b['status'] != 'active', bool(b.get('catalog_listing')))
        if atual is None or ordem < (atual['status'] != 'active', atual['catalogo']):
            envio = (b.get('shipping') or {}).get('logistic_type') or ''
            candidatos[chave] = {
                'up': up, 'codigo': b['id'], 'sku': sku_de(b), 'titulo': titulo_de(b), 'status': b['status'],
                'tipo': NOMES[tipo], 'falta': NOMES[OUTRO[tipo]], 'falta_id': OUTRO[tipo], 'preco': b.get('price'),
                'envio': 'Full' if envio == 'fulfillment' else 'Normal', 'catalogo': bool(b.get('catalog_listing')),
            }
    linhas = list(candidatos.values())

    # GTIN duplicado: o mesmo código num anúncio seu de outra categoria faz o ML recusar a opção nova
    por_gtin = {}
    for b in {x['id']: x for x in itens + vivos}.values():
        if b.get('status') in PAR:
            for g in gtins_de(b):
                por_gtin.setdefault(g, {})[b['id']] = b
    conflitos = 0
    for l in linhas:
        b = next(x for x in itens if x['id'] == l['codigo'])
        for g in gtins_de(b):
            outros = [o for o in por_gtin.get(g, {}).values() if o.get('category_id') != b.get('category_id')]
            if outros:
                l['gtin'] = {'codigo': g, 'outros': [
                    {'codigo': o['id'], 'sku': sku_de(o), 'categoria': o.get('category_id'), 'kit': eh_kit(sku_de(o)),
                     'catalogo': bool(o.get('catalog_listing'))} for o in outros]}
                conflitos += 1
                break
    analisados = sum(b.get('status') in ('active', 'paused') for b in itens)
    gravar_json(PARES, {'gerado': agora(), 'teste': bool(args.ids), 'analisados': analisados,
                        'sem_produto': sem_up, 'so_gratis': gratis, 'gtin_duplicado': conflitos, 'linhas': linhas})
    log.info(f'{len(linhas)} anúncios sem a outra opção de venda (de {analisados} ativos e pausados)'
             + (f'; {conflitos} com GTIN duplicado em outra categoria' if conflitos else '')
             + (f'; {sem_up} sem user product ficaram de fora' if sem_up else ''))


if __name__ == '__main__':
    main()
