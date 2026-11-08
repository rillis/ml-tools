"""Puxa os anúncios do ML e gera a planilha de margem (codigo, sku, titulo, status, preco, taxa, adc, frete).

titulo = family_name do anúncio (nome sem a variação); se não houver, o title.

Roda no servidor, com a venv da pasta (criada com: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

Uso (troque "python" por .venv/bin/python):
  python margem.py                    todos os anúncios ativos (retoma de onde parou)
  python margem.py --ids MLB1,MLB2      teste com alguns anúncios (progresso separado do completo)
  python margem.py --novo               descarta o progresso e começa de novo
  python margem.py --exportar           só gera a planilha com o que já foi coletado
  python margem.py --status active      só um status (padrão: todos; ex.: active, paused)

O progresso fica em data/margem.db: cada anúncio é gravado assim que termina, então
se o programa parar (Ctrl+C, queda de luz, erro de rede) é só rodar de novo.
Quando uma coleta termina, a próxima execução começa uma coleta nova.
O log fica em logs/margem.log e a planilha em saidas/.
"""
import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

import ml

BASE = ml.BASE
LOTE = 20  # máximo do /items?ids=
CAMPOS_ITEM = 'id,title,family_name,price,attributes,shipping,category_id,listing_type_id,status'
SO_ARQUIVO = {'so_arquivo': True}  # extra= de linhas que vão só para o log (na tela fica a barra)

log = logging.getLogger('margem')


class TelaHandler(logging.Handler):
    """Escreve na tela sem quebrar a barra do tqdm e pula as linhas marcadas como só do arquivo."""

    def emit(self, record):
        if getattr(record, 'so_arquivo', False):
            return
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


def setup_log():
    (BASE / 'logs').mkdir(exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', '%d/%m %H:%M:%S')
    for h in (TelaHandler(), logging.FileHandler(BASE / 'logs' / 'margem.log', encoding='utf-8')):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def barra(total, desc, inicial=0):
    return tqdm(total=total, initial=inicial, desc=desc, unit='anúncio', dynamic_ncols=True,
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} '
                           '[{elapsed}<{remaining}, {rate_fmt}]{postfix}')


# ---------------------------------------------------------------- banco de progresso

def abrir_db(path):
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS itens (
            id TEXT PRIMARY KEY, ordem INTEGER,
            sku TEXT, preco_anuncio REAL, frete_gratis INTEGER, category_id TEXT, listing_type_id TEXT,
            base_ok INTEGER DEFAULT 0,
            preco REAL, taxa REAL, adc REAL, frete REAL,
            ok INTEGER DEFAULT 0, erro TEXT, atualizado TEXT);
        CREATE TABLE IF NOT EXISTS taxas (
            category_id TEXT, listing_type_id TEXT, percentual REAL,
            PRIMARY KEY (category_id, listing_type_id));
    ''')
    colunas = {r['name'] for r in db.execute('PRAGMA table_info(itens)')}
    for nova in ('titulo', 'status'):  # bancos criados antes dessas colunas
        if nova not in colunas:
            db.execute(f'ALTER TABLE itens ADD COLUMN {nova} TEXT')
    db.commit()
    return db


def meta(db, k, v=None):
    if v is None:
        row = db.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        return row['v'] if row else None
    db.execute('INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)', (k, str(v)))
    db.commit()


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------- etapas

def etapa_lista(db, c, seller, status):
    """1. Lista os IDs dos anúncios (scan aceita mais de 1000 anúncios)."""
    if meta(db, 'lista_ok') == '1':
        return
    filtro = {} if status == 'todos' else {'status': status}
    log.info('Listando ' + ('todos os anúncios' if not filtro else f'anúncios com status {status}') + '...')
    ordem = db.execute('SELECT COALESCE(MAX(ordem), 0) FROM itens').fetchone()[0]
    params = {'search_type': 'scan', 'limit': 100, **filtro}
    total = None
    while True:
        r = c.get(f'/users/{seller}/items/search', params)
        total = r.get('paging', {}).get('total', total)
        ids = r.get('results') or []
        if not ids:
            break
        for i in ids:
            ordem += 1
            db.execute('INSERT OR IGNORE INTO itens (id, ordem) VALUES (?, ?)', (i, ordem))
        db.commit()
        log.info(f'  {db.execute("SELECT COUNT(*) FROM itens").fetchone()[0]}/{total} IDs', extra=SO_ARQUIVO)
        params = {'search_type': 'scan', 'limit': 100, 'scroll_id': r['scroll_id'], **filtro}
    meta(db, 'lista_ok', 1)


def sku_de(body):
    for a in body.get('attributes') or []:
        if a.get('id') == 'SELLER_SKU' and a.get('value_name'):
            return a['value_name']
    return body.get('seller_custom_field') or ''


def titulo_de(body):
    """Nome sem a variação (family_name); se o anúncio não tiver, o título completo."""
    return (body.get('family_name') or body.get('title') or '').strip()


def etapa_base(db, c):
    """2. Dados do anúncio em lotes de 20 (preço, SKU, categoria, tipo, envio)."""
    pendentes = [r['id'] for r in db.execute('SELECT id FROM itens WHERE base_ok=0 ORDER BY ordem')]
    if not pendentes:
        return
    log.info(f'Buscando dados de {len(pendentes)} anúncios em lotes de {LOTE}...')
    pb = barra(len(pendentes), 'Dados')
    for n in range(0, len(pendentes), LOTE):
        lote = pendentes[n:n + LOTE]
        try:
            resp = c.get('/items', {'ids': ','.join(lote), 'attributes': CAMPOS_ITEM})
        except ml.MLError as e:
            # um lote com erro não para a coleta: os 20 ficam para a próxima execução
            db.executemany('UPDATE itens SET erro=? WHERE id=?', [(f'dados: {e}'[:300], i) for i in lote])
            db.commit()
            log.warning(f'Lote {n // LOTE + 1} ({lote[0]}...) falhou: ML {e.status}  (fica para a próxima execução)')
            pb.update(len(lote))
            continue
        for x in resp:
            b = x.get('body') or {}
            if x.get('code') != 200 or 'id' not in b:
                iid = b.get('id') or (lote[resp.index(x)] if len(resp) == len(lote) else '?')
                db.execute('UPDATE itens SET erro=? WHERE id=?', (f'items {x.get("code")}', iid))
                continue
            db.execute('''UPDATE itens SET sku=?, titulo=?, status=?, preco_anuncio=?, frete_gratis=?, category_id=?,
                          listing_type_id=?, base_ok=1, erro=NULL WHERE id=?''',
                       (sku_de(b), titulo_de(b), b.get('status') or '', b.get('price'), 1 if (b.get('shipping') or {}).get('free_shipping') else 0,
                        b.get('category_id'), b.get('listing_type_id'), b['id']))
        db.commit()
        pb.update(len(lote))
        log.info(f'  lote {n // LOTE + 1}/{-(-len(pendentes) // LOTE)}', extra=SO_ARQUIVO)
    pb.close()


def taxa_de(db, c, category_id, listing_type_id, preco):
    """Percentual da tarifa: depende da categoria e do tipo de anúncio, então busca uma vez por par."""
    row = db.execute('SELECT percentual FROM taxas WHERE category_id=? AND listing_type_id=?',
                     (category_id, listing_type_id)).fetchone()
    if row:
        return row['percentual']
    r = c.get('/sites/MLB/listing_prices',
              {'price': preco, 'listing_type_id': listing_type_id, 'category_id': category_id})
    pct = (r.get('sale_fee_details') or {}).get('percentage_fee')
    if pct is None:
        raise ml.MLError('sem percentage_fee', 'listing_prices', r)
    db.execute('INSERT OR REPLACE INTO taxas VALUES (?, ?, ?)', (category_id, listing_type_id, pct))
    db.commit()
    return pct


def etapa_detalhe(db, c, seller):
    """3. Por anúncio: preço de venda atual, tarifa e custo de envio."""
    pend = db.execute('SELECT * FROM itens WHERE base_ok=1 AND ok=0 ORDER BY ordem').fetchall()
    total = db.execute('SELECT COUNT(*) FROM itens').fetchone()[0]
    feitos = db.execute('SELECT COUNT(*) FROM itens WHERE ok=1').fetchone()[0]
    if not pend:
        return
    log.info(f'Detalhando {len(pend)} anúncios ({feitos}/{total} já prontos)...')
    erros = 0
    pb = barra(total, 'Anúncios', inicial=feitos)
    for it in pend:
        iid = it['id']
        try:
            try:
                sp = c.get(f'/items/{iid}/sale_price', {'context': 'channel_marketplace'})
                preco = sp.get('amount') or it['preco_anuncio']
            except ml.MLError as e:
                if e.status != 404:
                    raise
                preco = it['preco_anuncio']
            taxa = taxa_de(db, c, it['category_id'], it['listing_type_id'], preco)
            fr = c.get(f'/users/{seller}/shipping_options/free', {'item_id': iid})
            custo = ((fr.get('coverage') or {}).get('all_country') or {}).get('list_cost')
            if custo is None:
                raise ml.MLError('sem list_cost', 'shipping_options', fr)
            adc, frete = (0, custo) if it['frete_gratis'] else (custo, 0)
            db.execute('UPDATE itens SET preco=?, taxa=?, adc=?, frete=?, ok=1, erro=NULL, atualizado=? WHERE id=?',
                       (preco, taxa, adc, frete, agora(), iid))
            db.commit()
            feitos += 1
            log.info(f'[{feitos}/{total}] {iid} {it["sku"] or "-"}  preço {preco:.2f}  taxa {taxa:g}%  '
                     f'adc {adc:.2f}  frete {frete:.2f}', extra=SO_ARQUIVO)
        except ml.MLError as e:
            erros += 1
            db.execute('UPDATE itens SET erro=?, atualizado=? WHERE id=?', (str(e)[:300], agora(), iid))
            db.commit()
            log.warning(f'{iid}: {e}  (fica para a próxima execução)')
        pb.set_postfix_str(f'{iid}' + (f' | erros: {erros}' if erros else ''), refresh=False)
        pb.update(1)
    pb.close()


# ---------------------------------------------------------------- planilha

def exportar(db, nome):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'anuncios'
    cab = ['codigo', 'sku', 'titulo', 'status', 'preco', 'taxa', 'adc', 'frete']
    ws.append(cab)
    for row in db.execute('SELECT id, sku, titulo, status, preco, taxa, adc, frete FROM itens WHERE ok=1 ORDER BY ordem'):
        ws.append([row['id'], row['sku'], row['titulo'] or '', row['status'] or '', row['preco'],
                   row['taxa'] / 100, row['adc'], row['frete']])
    for cel in ws[1]:
        cel.font = Font(bold=True)
    for r in ws.iter_rows(min_row=2):
        r[4].number_format = r[6].number_format = r[7].number_format = '#,##0.00'
        r[5].number_format = '0%'
    for i, w in enumerate([16, 22, 60, 12, 12, 8, 10, 10], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions

    erros = db.execute('SELECT id, sku, titulo, status, erro FROM itens WHERE ok=0 ORDER BY ordem').fetchall()
    if erros:
        we = wb.create_sheet('pendentes')
        we.append(['codigo', 'sku', 'titulo', 'status', 'erro'])
        for row in erros:
            we.append([row['id'], row['sku'], row['titulo'] or '', row['status'] or '',
                       row['erro'] or 'não processado'])

    (BASE / 'saidas').mkdir(exist_ok=True)
    arq = BASE / 'saidas' / f'{nome}-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    wb.save(arq)
    log.info(f'Planilha: {arq}  ({ws.max_row - 1} anúncios' + (f', {len(erros)} pendentes)' if erros else ')'))
    return arq


# ---------------------------------------------------------------- principal

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ids', help='teste com estes anúncios, separados por vírgula')
    ap.add_argument('--novo', action='store_true', help='descarta o progresso e começa de novo')
    ap.add_argument('--exportar', action='store_true', help='só gera a planilha')
    ap.add_argument('--status', default='todos', help='só anúncios com este status (padrão: todos)')
    args = ap.parse_args()
    setup_log()

    teste = bool(args.ids)
    ids_teste = ','.join(i if i.startswith('MLB') else 'MLB' + i
                         for i in (x.strip().upper() for x in (args.ids or '').split(',')) if i)
    path = BASE / 'data' / ('teste.db' if teste else 'margem.db')
    if path.exists() and not args.exportar:
        db = abrir_db(path)
        concluida = meta(db, 'concluida') == '1'
        status_ant = meta(db, 'status')
        outros_ids = teste and meta(db, 'ids') != ids_teste
        db.close()
        if args.novo or concluida or outros_ids or (status_ant is not None and status_ant != args.status):
            path.replace(path.with_suffix('.anterior.db'))
            if not teste:
                log.info('Coleta anterior ' + ('concluída' if concluida else 'descartada') + ': começando uma nova')
    db = abrir_db(path)

    if args.exportar:
        exportar(db, 'teste' if teste else 'margem')
        return

    c = ml.MLClient()
    seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
    if meta(db, 'inicio') is None:
        meta(db, 'inicio', agora())
        meta(db, 'status', args.status)
    else:
        log.info(f'Retomando a coleta iniciada em {meta(db, "inicio")}')

    t0 = time.monotonic()

    def resumo():
        k = c.contagem
        seg = time.monotonic() - t0
        dur = f'{seg / 60:.1f} min' if seg >= 60 else f'{seg:.0f}s'
        log.info(f'Nesta execução: {k["requisicoes"]} requisições em {dur} '
                 f'({k["requisicoes"] / seg if seg else 0:.1f}/s, limite {ml.MAX_RPS:g}/s) | '
                 f'429 recebidos: {k["429"]} | novas tentativas: {k["tentativas"]}')

    try:
        if teste and meta(db, 'lista_ok') != '1':
            for n, i in enumerate(ids_teste.split(',')):
                db.execute('INSERT OR IGNORE INTO itens (id, ordem) VALUES (?, ?)', (i, n + 1))
            db.commit()
            meta(db, 'ids', ids_teste)
            meta(db, 'lista_ok', 1)
        etapa_lista(db, c, seller, args.status)
        etapa_base(db, c)
        etapa_detalhe(db, c, seller)
    except KeyboardInterrupt:
        resumo()
        log.warning('Interrompido. O que já foi feito está salvo: rode de novo para continuar.')
        sys.exit(130)
    except ml.MLError as e:
        resumo()
        log.error(f'{e}\nO que já foi feito está salvo: rode de novo para continuar.')
        sys.exit(1)
    resumo()

    pend = db.execute('SELECT COUNT(*) FROM itens WHERE ok=0').fetchone()[0]
    if pend:
        log.warning(f'{pend} anúncios com erro: rode de novo para tentar só eles (ou veja a aba "pendentes")')
    else:
        meta(db, 'concluida', 1)
        log.info('Coleta concluída')
    exportar(db, 'teste' if teste else 'margem')


if __name__ == '__main__':
    main()
