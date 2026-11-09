"""Compara os SKUs da tabela de preços com os anúncios do ML: quais não têm anúncio e quais não têm anúncio solo.

Uso (no servidor, na raiz do projeto):  .venv/bin/python -m skus [--ids MLB1,MLB2]

Busca na API só o SKU, o título e o status de cada anúncio (todos os status) e gera
skus/saidas/skus-AAAA-MM-DD_HHMM.xlsx:
  aba skus: cada SKU da tabela com qtd_anuncios_comp, qtd_anuncios_solo, as mesmas contagens
            só dos ativos e a situacao ("sem anuncio", "sem solo" ou "ok")
  aba sem tabela: anúncios com alguma peça do SKU que não está na tabela

O SKU do anúncio vira SKUs da tabela pelas regras de comum/tabela.py, as mesmas do custo (apelidos, pontuação, barra, "3x").
Solo: o anúncio só tem aquele SKU pago, em qualquer quantidade ("3xKA-250" é solo do KA-250).
Itens grátis (apelido 0) não tiram o solo do item pago: "KA-032+3xMG-CORRUGADA" é solo do KA-032
e comp do MG-CORRUGADA. Um anúncio só com itens grátis é solo deles se for um SKU só.
Comp: o anúncio tem o SKU junto com outros. Se uma peça não estiver na tabela, as que estão
contam como comp e o anúncio vai para a aba "sem tabela".
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from tqdm import tqdm

from comum import ml
from comum.ml import LOTE, sku_de, titulo_de
from comum.tabela import APELIDOS, PRECOS, Tabela, carregar_apelidos, carregar_precos

SAIDAS = Path(__file__).resolve().parent / 'saidas'

CAMPOS = 'id,title,family_name,status,attributes,seller_custom_field'


def listar_ids(c, seller):
    ids, params = [], {'search_type': 'scan', 'limit': 100}
    while True:
        r = c.get(f'/users/{seller}/items/search', params)
        lote = r.get('results') or []
        if not lote:
            return ids
        ids += lote
        params = {'search_type': 'scan', 'limit': 100, 'scroll_id': r['scroll_id']}


def buscar(c, ids):
    """[(codigo, sku, titulo, status)] dos anúncios, em lotes de 20."""
    anuncios = []
    pb = tqdm(total=len(ids), desc='Anúncios', unit='anúncio', dynamic_ncols=True)
    for n in range(0, len(ids), LOTE):
        lote = ids[n:n + LOTE]
        for x in c.get('/items', {'ids': ','.join(lote), 'attributes': CAMPOS}):
            b = x.get('body') or {}
            if x.get('code') != 200 or 'id' not in b:
                raise ml.MLError(x.get('code'), '/items', b)
            anuncios.append((b['id'], sku_de(b), titulo_de(b), b.get('status') or ''))
        pb.update(len(lote))
    pb.close()
    return anuncios


def classificar(tab, sku):
    """(SKUs da tabela em que o anúncio é solo, SKUs em que é comp, peças fora da tabela)."""
    achados, faltas = tab.pecas(sku)
    todos = {n for n, _, _ in achados}
    pagos = {n for n, _, g in achados if not g}
    base = pagos or todos  # só itens grátis: vale o próprio grátis
    if len(base) == 1 and not faltas:
        return base, todos - base, faltas
    return set(), todos, faltas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ids', help='só estes anúncios, separados por vírgula (teste)')
    ap.add_argument('--precos', default=str(PRECOS))
    ap.add_argument('--apelidos', default=str(APELIDOS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format='%(message)s')  # avisos do cliente (429, token)

    precos = carregar_precos(args.precos)
    tab = Tabela(precos, carregar_apelidos(Path(args.apelidos)))
    c = ml.MLClient()
    try:
        if args.ids:
            ids = [i if i.startswith('MLB') else 'MLB' + i
                   for i in (x.strip().upper() for x in args.ids.split(',')) if i]
        else:
            seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
            print('Listando os anúncios...')
            ids = listar_ids(c, seller)
        anuncios = buscar(c, ids)
    except KeyboardInterrupt:
        sys.exit(130)
    except ml.MLError as e:
        sys.exit(f'Erro na API do ML: {e}\nNada foi gerado: rode de novo.')

    cont = {s: {'comp': 0, 'solo': 0, 'ativos_comp': 0, 'ativos_solo': 0} for s in precos}
    sem_tabela = []
    for codigo, sku, titulo, status in anuncios:
        solo, comp, faltas = classificar(tab, sku)
        for tipo, skus in (('solo', solo), ('comp', comp)):
            for s in skus:
                cont[s][tipo] += 1
                if status == 'active':
                    cont[s]['ativos_' + tipo] += 1
        if faltas:
            sem_tabela.append([codigo, sku, titulo, status, ', '.join(faltas)])

    wb = Workbook()
    ws = wb.active
    ws.title = 'skus'
    ws.append(['sku', 'qtd_anuncios_comp', 'qtd_anuncios_solo', 'qtd_ativos_comp', 'qtd_ativos_solo', 'situacao'])
    cores = {'sem anuncio': PatternFill('solid', start_color='F8CBAD'),
             'sem solo': PatternFill('solid', start_color='FFE699')}
    sit_cont = {'sem anuncio': 0, 'sem solo': 0, 'ok': 0}
    for s, k in cont.items():  # na ordem do precos.xlsx
        sit = 'sem anuncio' if not k['comp'] + k['solo'] else 'sem solo' if not k['solo'] else 'ok'
        sit_cont[sit] += 1
        ws.append([s, k['comp'], k['solo'], k['ativos_comp'], k['ativos_solo'], sit])
        if sit in cores:
            for cel in ws[ws.max_row]:
                cel.fill = cores[sit]
    for col, w in zip('ABCDEF', (28, 19, 18, 17, 16, 13)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = ws.dimensions

    wt = wb.create_sheet('sem tabela')
    wt.append(['codigo', 'sku', 'titulo', 'status', 'fora_da_tabela'])
    for linha in sem_tabela:
        wt.append(linha)
    for col, w in zip('ABCDE', (16, 30, 50, 12, 30)):
        wt.column_dimensions[col].width = w
    wt.freeze_panes = 'A2'
    for aba in (ws, wt):
        for cel in aba[1]:
            cel.font = Font(bold=True)

    nome = 'skus-teste' if args.ids else 'skus'
    arq = SAIDAS / f'{nome}-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    SAIDAS.mkdir(exist_ok=True)
    wb.save(arq)
    print(f'Anúncios: {len(anuncios)} | requisições: {c.contagem["requisicoes"]} | 429: {c.contagem["429"]}')
    print(f'SKUs da tabela: {len(cont)} | sem anúncio: {sit_cont["sem anuncio"]} | '
          f'sem solo: {sit_cont["sem solo"]} | ok: {sit_cont["ok"]}')
    print(f'Anúncios com peça fora da tabela: {len(sem_tabela)} (aba "sem tabela")')
    print(f'Planilha: {arq}')


if __name__ == '__main__':
    main()
