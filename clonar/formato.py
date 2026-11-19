"""Formato de venda e quantidade: corrige o produto para o ML aceitar a opção de venda nova.

Uso:  .venv/bin/python -m clonar.formato --item MLB123 --formato unidade --qtd 1
      .venv/bin/python -m clonar.formato --item MLB123 --formato pack --qtd 3

Algumas categorias pedem a "Quantidade de unidades" (UNITS_PER_PACK) sempre que o "Formato de venda"
(SALE_FORMAT) está preenchido, seja Unidade ou Kit/pack. Anúncios antigos passaram sem ela, mas a opção nova
herda o produto e o ML recusa. Os dois são atributos de variação e não aparecem no painel do ML.
Grava os dois no anúncio (o ML replica no produto), confere e espera 5 s para a criação tentar de novo.
O estado de antes fica em data/clonar-formato/<MLB>-<data>.json para desfazer à mão.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime

from comum import ml
from comum.ml import BASE

ANTES = BASE / 'data' / 'clonar-formato'
log = logging.getLogger('clonar')


class Falha(Exception):
    pass


def valor(attrs, chave):
    return next((a.get('value_name') for a in attrs or [] if a.get('id') == chave), None)


def ids_formato(c, categoria):
    """{'unidade': id, 'pack': id} do SALE_FORMAT da categoria (os ids mudam de uma categoria para outra)."""
    attr = next((a for a in c.get(f'/categories/{categoria}/attributes') if a['id'] == 'SALE_FORMAT'), None)
    if not attr:
        raise Falha(f'A categoria {categoria} não tem "Formato de venda"')
    ids = {}
    for v in attr.get('values') or []:
        nome = v['name'].lower()
        if nome.startswith('unidade'):
            ids['unidade'] = v['id']
        elif nome.startswith(('pack', 'kit')):
            ids['pack'] = v['id']
    if set(ids) != {'unidade', 'pack'}:
        raise Falha(f'Não reconheci os valores do "Formato de venda" da categoria {categoria}: '
                    + ', '.join(v['name'] for v in attr.get('values') or []))
    return ids


def corrigir(c, item, formato, qtd):
    b = c.get(f'/items/{item}', {'attributes': 'id,category_id,user_product_id,attributes'})
    ANTES.mkdir(parents=True, exist_ok=True)
    arq = ANTES / f'{item}-{datetime.now():%Y-%m-%d_%H%M%S}.json'
    arq.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding='utf-8')
    ids = ids_formato(c, b['category_id'])
    log.info(f'{item}: formato de venda {valor(b["attributes"], "SALE_FORMAT") or "(vazio)"} / '
             f'quantidade {valor(b["attributes"], "UNITS_PER_PACK") or "(vazia)"} → '
             f'{"Unidade" if formato == "unidade" else "Kit/pack"} / {qtd}')
    try:
        c.put(f'/items/{item}', {'attributes': [{'id': 'SALE_FORMAT', 'value_id': ids[formato]},
                                                {'id': 'UNITS_PER_PACK', 'value_name': str(qtd)}]})
    except ml.MLError as e:
        raise Falha(f'O ML recusou a mudança: {e}') from None
    time.sleep(5)  # o ML replica no produto antes de a criação tentar de novo
    d = c.get(f'/items/{item}', {'attributes': 'attributes'})
    if valor(d['attributes'], 'UNITS_PER_PACK') != str(qtd):
        raise Falha(f'O ML aceitou, mas a quantidade continua {valor(d["attributes"], "UNITS_PER_PACK")}: confira')
    log.info(f'Gravado: {valor(d["attributes"], "SALE_FORMAT")} / {qtd} (antes salvo em data/clonar-formato/{arq.name})')


def main():
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--item', required=True)
    ap.add_argument('--formato', required=True, choices=('unidade', 'pack'))
    ap.add_argument('--qtd', required=True, type=int)
    args = ap.parse_args()
    if not 1 <= args.qtd <= 100:
        raise SystemExit('Quantidade de 1 a 100')
    trava = ml.travar_busca()  # noqa: F841
    try:
        corrigir(ml.MLClient(), args.item.strip().upper(), args.formato, args.qtd)
    except (Falha, ml.MLError) as e:
        log.error(f'Erro: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
