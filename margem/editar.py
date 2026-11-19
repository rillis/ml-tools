"""Edição à mão de um anúncio na última busca (sem chamar o ML), para corrigir divergências com o painel.

Uso:  .venv/bin/python -m margem.editar --item MLB123 [--preco 99.9] [--taxa 12] [--frete 17.15] [--adc 0]

Troca preço, tarifa (%), frete e/ou adc do anúncio em margem/data/margem.db, guarda o que havia antes em
data/edicoes-manuais.json e gera uma planilha nova da busca (margem-*.xlsx). Depois é só recalcular a margem
(o site faz isso na mesma tarefa). O próximo "Atualizar margens" (ou "Só alguns" com esse anúncio) busca de
novo no ML e desfaz a edição.
"""
import argparse
import json
import sys

from comum import ml
from comum.ml import BASE
from margem.coleta import DADOS, abrir_db, agora, exportar, meta, setup_log

HISTORICO = BASE / 'data' / 'edicoes-manuais.json'
CAMPOS = ('preco', 'taxa', 'adc', 'frete')


def editar(item, novos):
    path = DADOS / 'margem.db'
    if not path.exists():
        raise SystemExit('Ainda não há uma busca: use "Atualizar margens" primeiro.')
    db = abrir_db(path)
    if meta(db, 'concluida') != '1':
        raise SystemExit('A última busca parou no meio: continue ela antes de editar.')
    row = db.execute('SELECT id, sku, preco, taxa, adc, frete, ok FROM itens WHERE id=?', (item,)).fetchone()
    if not row or not row['ok']:
        raise SystemExit(f'{item} não está na última busca: use "Só alguns" com ele antes.')
    antes = {k: row[k] for k in CAMPOS}
    depois = {**antes, **{k: v for k, v in novos.items() if v is not None}}
    if depois == antes:
        raise SystemExit('Nada mudou.')
    db.execute('UPDATE itens SET preco=?, taxa=?, adc=?, frete=?, atualizado=? WHERE id=?',
               (depois['preco'], depois['taxa'], depois['adc'], depois['frete'], agora(), item))
    db.commit()
    mudou = ', '.join(f'{k} {antes[k]:g} → {depois[k]:g}' for k in CAMPOS if antes[k] != depois[k])
    print(f'{item} {row["sku"] or ""}: {mudou}  (tarifa em %)')

    try:
        hist = json.loads(HISTORICO.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        hist = []
    hist.insert(0, {'quando': agora(), 'item': item, 'sku': row['sku'], 'antes': antes, 'depois': depois})
    HISTORICO.parent.mkdir(parents=True, exist_ok=True)
    HISTORICO.write_text(json.dumps(hist[:500], ensure_ascii=False, indent=1), encoding='utf-8')
    exportar(db, 'margem')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--item', required=True)
    for k, ajuda in (('preco', 'preço de venda (R$)'), ('taxa', 'tarifa do ML em %'), ('frete', 'frete (R$)'),
                     ('adc', 'custo adicional (R$)')):
        ap.add_argument(f'--{k}', type=float, help=ajuda)
    args = ap.parse_args()
    novos = {k: getattr(args, k) for k in CAMPOS}
    if all(v is None for v in novos.values()):
        raise SystemExit('Informe ao menos um valor para mudar')
    if novos['preco'] is not None and novos['preco'] <= 0:
        raise SystemExit('Preço precisa ser maior que zero')
    if any(v is not None and v < 0 for v in novos.values()):
        raise SystemExit('Valores não podem ser negativos')
    setup_log()
    trava = ml.travar_busca()  # noqa: F841 (o margem.db é o mesmo da busca)
    editar(args.item.strip().upper(), novos)


if __name__ == '__main__':
    sys.exit(main())
