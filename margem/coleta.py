"""Puxa os anúncios do ML e gera a planilha de margem (codigo, sku, titulo, status, preco, taxa, adc, frete,
tipo, envio).

tipo = Clássico ou Premium (listing_type_id); envio = Full ou Normal (shipping.logistic_type).

titulo = family_name do anúncio (nome sem a variação); se não houver, o title.

Roda no servidor, com a venv da pasta (criada com: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

Uso (na raiz do projeto, troque "python" por .venv/bin/python):
  python -m margem.coleta                    todos os anúncios (retoma de onde parou)
  python -m margem.coleta --ids MLB1,MLB2    teste com alguns anúncios (progresso separado do completo)
  python -m margem.coleta --novo             descarta o progresso e começa de novo
  python -m margem.coleta --exportar         só gera a planilha com o que já foi coletado
  python -m margem.coleta --status active    só um status (padrão: todos; ex.: active, paused)
  python -m margem.coleta --atualizar MLB1,MLB2   busca de novo só estes e troca as linhas deles na última busca completa

O progresso fica em margem/data/margem.db: cada anúncio é gravado assim que termina, então
se o programa parar (Ctrl+C, queda de luz, erro de rede) é só rodar de novo.
Quando uma coleta termina, a próxima execução começa uma coleta nova.
O log fica em margem/logs/margem.log e a planilha em margem/saidas/.
"""
import argparse
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from comum import ml
from comum.ml import LOTE, sku_de, titulo_de

DIR = Path(__file__).resolve().parent
DADOS, LOGS, SAIDAS = DIR / 'data', DIR / 'logs', DIR / 'saidas'
CAMPOS_ITEM = 'id,title,family_name,price,attributes,shipping,category_id,listing_type_id,status'
TIPOS = {'gold_special': 'Clássico', 'gold_pro': 'Premium', 'free': 'Grátis'}
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
    LOGS.mkdir(exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', '%d/%m %H:%M:%S')
    for h in (TelaHandler(), logging.FileHandler(LOGS / 'margem.log', encoding='utf-8')):
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
        CREATE TABLE IF NOT EXISTS taxas_preco (
            category_id TEXT, listing_type_id TEXT, preco REAL, percentual REAL,
            PRIMARY KEY (category_id, listing_type_id, preco));
    ''')
    colunas = {r['name'] for r in db.execute('PRAGMA table_info(itens)')}
    for nova in ('titulo', 'status', 'logistica'):  # bancos criados antes dessas colunas
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
                          listing_type_id=?, logistica=?, base_ok=1, erro=NULL WHERE id=?''',
                       (sku_de(b), titulo_de(b), b.get('status') or '', b.get('price'), 1 if (b.get('shipping') or {}).get('free_shipping') else 0,
                        b.get('category_id'), b.get('listing_type_id'), (b.get('shipping') or {}).get('logistic_type') or '', b['id']))
        db.commit()
        pb.update(len(lote))
        log.info(f'  lote {n // LOTE + 1}/{-(-len(pendentes) // LOTE)}', extra=SO_ARQUIVO)
    pb.close()


def taxa_ml(c, category_id, listing_type_id, preco):
    """Percentual da tarifa de venda do ML para esse preço (muda conforme a faixa de preço)."""
    r = c.get('/sites/MLB/listing_prices',
              {'price': preco, 'listing_type_id': listing_type_id, 'category_id': category_id})
    det = r.get('sale_fee_details') or {}
    pct = det.get('percentage_fee')
    if pct is None:
        raise ml.MLError('sem percentage_fee', 'listing_prices', r)
    if det.get('fixed_fee'):
        log.warning(f'Tarifa com valor fixo de R$ {det["fixed_fee"]} em {category_id} a R$ {preco}: '
                    'a margem considera só o percentual')
    return pct


def preco_venda(c, iid, preco_anuncio):
    """Preço que o comprador paga hoje (com promoção, se houver)."""
    try:
        sp = c.get(f'/items/{iid}/sale_price', {'context': 'channel_marketplace'})
        return sp.get('amount') or preco_anuncio
    except ml.MLError as e:
        if e.status != 404:
            raise
        return preco_anuncio


def custo_envio(c, seller, iid, frete_gratis):
    """(adc, frete): o custo de envio vai em frete nos anúncios com frete grátis e em adc nos outros."""
    fr = c.get(f'/users/{seller}/shipping_options/free', {'item_id': iid})
    custo = ((fr.get('coverage') or {}).get('all_country') or {}).get('list_cost')
    if custo is None:
        raise ml.MLError('sem list_cost', 'shipping_options', fr)
    return (0, custo) if frete_gratis else (custo, 0)


def taxa_de(db, c, category_id, listing_type_id, preco):
    """Percentual da tarifa. Depende da categoria, do tipo de anúncio e também do preço
    (na mesma categoria, R$ 150 pode pagar 11% e R$ 1.000, 12%), então busca por categoria + tipo + preço."""
    preco = round(preco, 2)
    row = db.execute('SELECT percentual FROM taxas_preco WHERE category_id=? AND listing_type_id=? AND preco=?',
                     (category_id, listing_type_id, preco)).fetchone()
    if row:
        return row['percentual']
    pct = taxa_ml(c, category_id, listing_type_id, preco)
    db.execute('INSERT OR REPLACE INTO taxas_preco VALUES (?, ?, ?, ?)', (category_id, listing_type_id, preco, pct))
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
            preco = preco_venda(c, iid, it['preco_anuncio'])
            taxa = taxa_de(db, c, it['category_id'], it['listing_type_id'], preco)
            adc, frete = custo_envio(c, seller, iid, it['frete_gratis'])
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

def tipo_de(listing_type_id):
    return TIPOS.get(listing_type_id or '', listing_type_id or '')


def envio_de(logistica):
    """Full ou Normal; vazio quando a busca é de antes de guardar a logística."""
    if logistica is None:
        return ''
    return 'Full' if logistica == 'fulfillment' else 'Normal'


def exportar(db, nome):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'anuncios'
    cab = ['codigo', 'sku', 'titulo', 'status', 'preco', 'taxa', 'adc', 'frete', 'tipo', 'envio']
    ws.append(cab)
    for row in db.execute('SELECT id, sku, titulo, status, preco, taxa, adc, frete, listing_type_id, logistica '
                          'FROM itens WHERE ok=1 ORDER BY ordem'):
        ws.append([row['id'], row['sku'], row['titulo'] or '', row['status'] or '', row['preco'],
                   row['taxa'] / 100, row['adc'], row['frete'], tipo_de(row['listing_type_id']),
                   envio_de(row['logistica'])])
    for cel in ws[1]:
        cel.font = Font(bold=True)
    for r in ws.iter_rows(min_row=2):
        r[4].number_format = r[6].number_format = r[7].number_format = '#,##0.00'
        r[5].number_format = '0%'
    for i, w in enumerate([16, 22, 60, 12, 12, 8, 10, 10, 10, 9], 1):
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

    SAIDAS.mkdir(exist_ok=True)
    arq = SAIDAS / f'{nome}-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    wb.save(arq)
    log.info(f'Planilha: {arq}  ({ws.max_row - 1} anúncios' + (f', {len(erros)} pendentes)' if erros else ')'))
    return arq


# ---------------------------------------------------------------- principal

def atualizar_alguns(ids):
    """Busca de novo só estes anúncios e troca as linhas deles na última coleta completa (margem.db).
    Os outros anúncios ficam como estão. Anúncio que não estava na coleta entra no fim."""
    path = DADOS / 'margem.db'
    if not path.exists():
        raise SystemExit('Ainda não há uma busca completa: use "Atualizar margens" primeiro.')
    db = abrir_db(path)
    if meta(db, 'concluida') != '1':
        raise SystemExit('A última busca completa parou no meio: continue ela antes de atualizar só alguns anúncios.')
    ordem = db.execute('SELECT COALESCE(MAX(ordem), 0) FROM itens').fetchone()[0]
    for i in ids:
        if db.execute('INSERT OR IGNORE INTO itens (id, ordem) VALUES (?, ?)', (i, ordem + 1)).rowcount:
            ordem += 1
        db.execute('UPDATE itens SET base_ok=0, ok=0, erro=NULL WHERE id=?', (i,))
    db.commit()
    log.info(f'Atualizando só {len(ids)} anúncios na busca de {meta(db, "inicio")}: {", ".join(ids)}')

    c = ml.MLClient()
    seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
    try:
        etapa_base(db, c)
        etapa_detalhe(db, c, seller)
    except KeyboardInterrupt:
        log.warning('Interrompido: rode de novo com os mesmos anúncios.')
        sys.exit(130)
    except ml.MLError as e:
        log.error(f'{e}\nRode de novo para tentar esses anúncios.')
        sys.exit(1)
    erros = db.execute(f'SELECT id, erro FROM itens WHERE ok=0 AND id IN ({",".join("?" * len(ids))})', ids).fetchall()
    for e in erros:
        log.warning(f'{e["id"]}: {e["erro"] or "não processado"} (fica na aba "pendentes")')
    log.info(f'{len(ids) - len(erros)} de {len(ids)} anúncios atualizados')
    exportar(db, 'margem')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ids', help='teste com estes anúncios, separados por vírgula')
    ap.add_argument('--atualizar', help='busca de novo só estes anúncios (MLB, separados por vírgula) '
                                        'e troca as linhas deles na última busca completa')
    ap.add_argument('--novo', action='store_true', help='descarta o progresso e começa de novo')
    ap.add_argument('--exportar', action='store_true', help='só gera a planilha')
    ap.add_argument('--status', default='todos', help='só anúncios com este status (padrão: todos)')
    args = ap.parse_args()
    setup_log()
    # antes de mexer no banco: outra coleta rodando usa o mesmo margem.db
    trava = None if args.exportar else ml.travar_busca()  # noqa: F841 (solta quando o processo termina)
    if args.atualizar:
        ids = [i if i.startswith('MLB') else 'MLB' + i
               for i in (x.strip().upper() for x in re.split(r'[\s,;]+', args.atualizar)) if i]
        return atualizar_alguns(list(dict.fromkeys(ids)))

    teste = bool(args.ids)
    ids_teste = ','.join(i if i.startswith('MLB') else 'MLB' + i
                         for i in (x.strip().upper() for x in (args.ids or '').split(',')) if i)
    path = DADOS / ('teste.db' if teste else 'margem.db')
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
