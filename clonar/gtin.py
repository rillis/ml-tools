"""GTIN duplicado: tira o código universal de quem não deveria ter, para o ML aceitar a opção de venda nova.

Uso:  .venv/bin/python -m clonar.gtin --item MLB123             mostra o plano (só lê o ML)
      .venv/bin/python -m clonar.gtin --item MLB123 --corrigir  executa o plano

O ML recusa criar um anúncio com um GTIN que você já usa num anúncio de outra categoria (é comum o kit
usar o GTIN de uma das peças). O plano junta os anúncios com o mesmo GTIN por produto (user product) e
escolhe quem fica com ele:
  1. quem é do mesmo domínio do produto de catálogo do ML que tem esse GTIN;
  2. sem catálogo que decida, o SKU solo (o kit perde);
  3. se nem assim der para decidir, não mexe e pede para resolver no painel.
Quem perde fica com "Meu produto não o tem" (EMPTY_GTIN_REASON): "kit ou pack" para kit e
"não tem código cadastrado" para solo. Edita um anúncio por produto (o ML replica nos outros do mesmo produto),
de preferência o que não é de catálogo, e confere depois que o GTIN saiu.
"""
import argparse
import json
import logging
import sys
import time

from clonar import PARES, agora, eh_kit, gravar_json, gtins_de, ler_json
from comum import ml
from comum.ml import sku_de

CAMPOS = 'id,status,category_id,domain_id,user_product_id,catalog_listing,catalog_product_id,attributes,seller_custom_field'
MOTIVO_KIT = ('17055159', 'O produto é um kit ou pack')
MOTIVO_SOLO = ('17055160', 'O produto não tem código cadastrado')
log = logging.getLogger('clonar')


class Falha(Exception):
    pass


def _itens(c, ids):
    out = []
    ids = list(dict.fromkeys(ids))
    for n in range(0, len(ids), 20):
        for x in c.get('/items', {'ids': ','.join(ids[n:n + 20]), 'attributes': CAMPOS}):
            if x.get('code') == 200 and x.get('body'):
                out.append(x['body'])
    return out


def plano(c, seller, item):
    """{'gtin', 'catalogo': [domínios], 'grupos': [{up, sku, kit, dominio, categoria, anuncios, editar,
    fica, motivo}], 'decidido', 'texto'}. Só lê o ML."""
    alvo = _itens(c, [item])
    if not alvo:
        raise Falha(f'Anúncio {item} não encontrado')
    alvo = alvo[0]
    gtins = gtins_de(alvo)
    if not gtins:
        raise Falha('O anúncio não tem GTIN: nada a corrigir')
    pares = ler_json(PARES, {}) or {}
    linha = next((l for l in pares.get('linhas', []) if l['codigo'] == item), None)
    g = (linha or {}).get('gtin', {}).get('codigo') or gtins[0]
    outros_ids = [o['codigo'] for o in (linha or {}).get('gtin', {}).get('outros', [])]
    if not outros_ids:  # o ML não busca anúncios por GTIN: quem diz onde mais ele está é a busca de pares
        raise Falha('Não sei em que outros anúncios esse GTIN está: use 🔍 Buscar anúncios sem par de novo')
    # o produto de cada anúncio inteiro entra (o GTIN é do produto): busca os irmãos de cada um
    base = _itens(c, [item] + outros_ids)
    ups = {b.get('user_product_id') for b in base if b.get('user_product_id')}
    irmaos = []
    for up in ups:
        irmaos += c.get(f'/users/{seller}/items/search', {'user_product_id': up}).get('results') or []
    todos = {b['id']: b for b in base + _itens(c, irmaos)}
    com_gtin = [b for b in todos.values() if g in gtins_de(b) and b.get('status') in ('active', 'paused', 'under_review')]

    grupos = {}
    for b in com_gtin:
        chave = b.get('user_product_id') or b['id']
        gr = grupos.setdefault(chave, {'up': b.get('user_product_id'), 'sku': sku_de(b), 'kit': eh_kit(sku_de(b)),
                                       'dominio': b.get('domain_id'), 'categoria': b.get('category_id'),
                                       'anuncios': [], 'editar': None})
        gr['anuncios'].append({'codigo': b['id'], 'catalogo': bool(b.get('catalog_listing')), 'status': b['status']})
    for gr in grupos.values():
        normais = [a for a in gr['anuncios'] if not a['catalogo']]
        gr['editar'] = (normais or gr['anuncios'])[0]['codigo']
        gr['alvo'] = any(a['codigo'] == item for a in gr['anuncios'])

    categorias = {gr['categoria'] for gr in grupos.values()}
    res = {'gtin': g, 'item': item, 'grupos': list(grupos.values()), 'catalogo': [], 'decidido': False, 'texto': ''}
    if len(categorias) < 2:
        res['texto'] = 'O GTIN não está em outra categoria agora: dá para tentar criar de novo.'
        return res

    try:
        prods = c.get('/products/search', {'status': 'active', 'site_id': 'MLB', 'product_identifier': g}).get('results') or []
    except ml.MLError:
        prods = []
    dominios = sorted({p.get('domain_id') for p in prods if p.get('domain_id')})
    res['catalogo'] = [{'id': p['id'], 'dominio': p.get('domain_id'), 'nome': p.get('name')} for p in prods[:3]]
    gs = res['grupos']
    ficam = [gr for gr in gs if gr['dominio'] in dominios]
    if ficam and len(ficam) < len(gs) and len({gr['categoria'] for gr in ficam}) == 1:
        res['texto'] = f'O catálogo do ML diz que o GTIN {g} é de {res["catalogo"][0]["nome"]} ({dominios[0]})'
    else:
        ficam = [gr for gr in gs if not gr['kit']]
        if ficam and len(ficam) < len(gs) and len({gr['categoria'] for gr in ficam}) == 1:
            res['texto'] = f'O GTIN {g} fica com o SKU solo; o kit perde'
        else:
            res['texto'] = (f'Não dá para decidir sozinho quem fica com o GTIN {g} '
                            f'({len(gs)} produtos em {len(categorias)} categorias): resolva no painel do ML')
            return res
    for gr in gs:
        gr['fica'] = gr in ficam
        if not gr['fica']:
            gr['motivo'] = (MOTIVO_KIT if gr['kit'] else MOTIVO_SOLO)[1]
            gr['motivo_id'] = (MOTIVO_KIT if gr['kit'] else MOTIVO_SOLO)[0]
    res['decidido'] = True
    return res


def corrigir(c, seller, item):
    p = plano(c, seller, item)
    log.info(p['texto'])
    if not p['decidido']:
        raise Falha(p['texto'])
    feitos = []
    for gr in p['grupos']:
        nomes = ', '.join(a['codigo'] for a in gr['anuncios'])
        if gr['fica']:
            log.info(f'Fica com o GTIN: {gr["sku"]} ({nomes})')
            continue
        log.info(f'Tirando o GTIN {p["gtin"]} de {gr["sku"]} ({nomes}) pelo {gr["editar"]} · motivo: {gr["motivo"]}')
        try:
            c.put(f'/items/{gr["editar"]}', {'attributes': [{'id': 'GTIN', 'value_name': None},
                                                              {'id': 'EMPTY_GTIN_REASON', 'value_id': gr['motivo_id']}]})
        except ml.MLError as e:
            raise Falha(f'O ML recusou tirar o GTIN de {gr["editar"]}: {e}') from None
        feitos.append(gr)
    # confere: o GTIN saiu de quem perdeu (o ML replica nos outros anúncios do produto em segundos)
    for gr in feitos:
        for tentativa in range(3):
            time.sleep(5)
            restos = [b['id'] for b in _itens(c, [a['codigo'] for a in gr['anuncios']]) if p['gtin'] in gtins_de(b)]
            if not restos:
                break
        if gr['editar'] in restos:
            raise Falha(f'O ML aceitou a edição, mas o GTIN continua em {gr["editar"]}: confira no painel')
        if restos:
            log.warning(f'{gr["sku"]}: o GTIN ainda aparece em {", ".join(restos)} (o ML pode demorar para replicar)')
        else:
            log.info(f'{gr["sku"]}: GTIN removido')
    # a linha da busca deixa de marcar o GTIN duplicado
    pares = ler_json(PARES, None)
    if pares:
        for l in pares.get('linhas', []):
            if (l.get('gtin') or {}).get('codigo') == p['gtin']:
                l['gtin']['corrigido'] = agora()
        gravar_json(PARES, pares)
    log.info('Pronto: tente criar a opção de venda de novo')
    return p


def main():
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--item', required=True, help='MLB do anúncio sem par que deu GTIN duplicado')
    ap.add_argument('--corrigir', action='store_true', help='executa o plano (sem isso, só mostra)')
    args = ap.parse_args()
    c = ml.MLClient()
    seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
    item = args.item.strip().upper()
    try:
        if args.corrigir:
            trava = ml.travar_busca()  # noqa: F841
            corrigir(c, seller, item)
        else:
            print(json.dumps(plano(c, seller, item), ensure_ascii=False, indent=1))
    except (Falha, ml.MLError) as e:
        log.error(f'Erro: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
