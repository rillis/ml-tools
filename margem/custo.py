"""Cruza a última coleta (margem/saidas/margem-*.xlsx) com a tabela de preços e calcula custo e margem.

Uso (no servidor):  .venv/bin/python -m margem.custo [--coleta margem/saidas/margem-....xlsx]

Não chama a API do ML. Gera margem/saidas/custo-AAAA-MM-DD_HHMM.xlsx com custo, custo com desconto,
lucro, margem, imposto, lucro líquido e preco_sugerido (preço de venda que dá a margem alvo com a
taxa, adc e frete atuais).
A margem alvo é a soma de MARGEM_LUCRO + MARGEM_GORDURA + MARGEM_IMPOSTO do .env (padrão 9,5% + 0,5% + 8%).
A margem é antes do imposto e é comparada com essa soma; o imposto (preço × MARGEM_IMPOSTO) e o lucro
líquido (lucro − imposto) aparecem em colunas à parte.
Menos o custo, são fórmulas: mudou o custo na planilha, o resto acompanha.
Ao lado da planilha fica um .json com os mesmos valores já calculados, que o site mostra.
Com uma coleta de teste (teste-*.xlsx), a saída é custo-teste-*.xlsx.

O SKU do anúncio vira custo pelas regras de comum/tabela.py (apelidos, pontuação, barra, "3x").
Se alguma peça não tiver preço, o anúncio fica sem custo e o resumo na tela diz qual faltou.
A coluna diferenca é preco_sugerido - preco (positivo: precisa subir o preço);
diferenca_pct é |diferenca| / preco (sempre positivo).
As colunas tipo (Clássico/Premium) e envio (Full/Normal) vêm da coleta; numa coleta de antes delas,
o tipo vem do margem.db e o envio fica vazio até a próxima busca.
A coluna so_gratis marca anúncios só com itens grátis: neles o preco_sugerido é o preço atual.
"""
import argparse
import glob
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from comum.env import margem_meta, partes_meta
from comum.tabela import APELIDOS, PRECOS, Tabela, carregar_apelidos, carregar_precos, linhas
from margem.coleta import tipo_de

DIR = Path(__file__).resolve().parent
SAIDAS = DIR / 'saidas'
DESCONTO = 0.15     # desconto do fornecedor
TOLERANCIA = 0.001  # fica destacado fora de META ± TOLERANCIA (0,1 p.p.)


def sugerido(preco, taxa, adc, frete, custo_desc, meta, gratis):
    """Preço que dá a margem alvo total com a taxa, adc e frete atuais (mesma conta da fórmula da planilha)."""
    if gratis:
        return preco
    return round((adc + frete + custo_desc) / (1 - taxa - meta), 2)


def simular(tab, sku, taxa, frete, adc, meta, imposto):
    """Preço sugerido de um SKU com a taxa, frete e adc informados (mesma conta da planilha),
    com o custo de cada parte do SKU para conferir."""
    d = tab.detalhe(sku)
    if d['custo'] is not None:
        custo_desc = d['custo'] * (1 - DESCONTO)
        if 1 - taxa - meta <= 0:
            raise ValueError('Taxa + margem alvo passam de 100%: não há preço que dê a margem')
        p = sugerido(0, taxa, adc, frete, custo_desc, meta, False)
        recebe = p * (1 - taxa) - adc - frete
        lucro = recebe - custo_desc
        d.update(custo_desc=round(custo_desc, 2), preco_sugerido=p, valor_taxa=round(p * taxa, 2),
                 recebe=round(recebe, 2), lucro=round(lucro, 2), margem=lucro / p if p > 0 else None,
                 imposto=round(p * imposto, 2), lucro_liquido=round(lucro - p * imposto, 2))
    d.update(taxa=taxa, frete=frete, adc=adc, meta=meta, desconto=DESCONTO)
    return d


def tipos_do_banco():
    """codigo -> Clássico/Premium pelo margem.db, para coletas exportadas antes da coluna tipo."""
    db_path = DIR / 'data' / 'margem.db'
    if not db_path.exists():
        return {}
    try:
        db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        tipos = {i: tipo_de(t) for i, t in db.execute('SELECT id, listing_type_id FROM itens')}
        db.close()
    except sqlite3.Error:
        return {}
    return tipos


def calcular(coleta, tab, meta, imposto):
    """Linhas da coleta com custo, margem e preços sugeridos já calculados (o que as fórmulas dão)."""
    saida = []
    do_banco = None
    for r in linhas(coleta):
        codigo, sku, titulo, status, preco, taxa, adc, frete = r[:8]
        tipo, envio = (list(r[8:10]) + [None, None])[:2]
        if not tipo:
            do_banco = tipos_do_banco() if do_banco is None else do_banco
            tipo = do_banco.get(codigo, '')
        preco, taxa, adc, frete = (float(x or 0) for x in (preco, taxa, adc, frete))
        custo, como, _ = tab.custo(sku)
        gratis = tab.so_gratis(sku)
        linha = {'codigo': codigo, 'sku': sku or '', 'titulo': titulo or '', 'status': status or '',
                 'so_gratis': gratis, 'preco': preco, 'taxa': taxa, 'adc': adc, 'frete': frete,
                 'custo': custo, 'como': como, 'tipo': tipo or '', 'envio': envio or ''}
        if custo is not None:
            custo_desc = custo * (1 - DESCONTO)
            lucro = preco * (1 - taxa) - adc - frete - custo_desc  # antes do imposto
            valor_imposto = preco * imposto
            sug = sugerido(preco, taxa, adc, frete, custo_desc, meta, gratis)
            linha.update(
                custo_desc=round(custo_desc, 2), lucro=round(lucro, 2),
                imposto=round(valor_imposto, 2), lucro_liquido=round(lucro - valor_imposto, 2),
                margem=lucro / preco if preco > 0 else None,
                preco_sugerido=sug, diferenca=round(sug - preco, 2),
                diferenca_pct=abs(sug - preco) / preco if preco > 0 else None)
        saida.append(linha)
    return saida


def planilha(dados, meta, imposto, arq):
    wb = Workbook()
    ws = wb.active
    ws.title = 'margem'
    # A codigo, B sku, C titulo, D status, E so_gratis, F preco, G taxa, H adc, I frete, J custo,
    # K custo_desc, L lucro, M margem, N imposto, O lucro_liquido, P preco_sugerido, Q diferenca, R diferenca_pct,
    # S tipo, T envio
    cab = ['codigo', 'sku', 'titulo', 'status', 'so_gratis', 'preco', 'taxa', 'adc', 'frete', 'custo',
           'custo_desc', 'lucro', 'margem', 'imposto', 'lucro_liquido', 'preco_sugerido', 'diferenca', 'diferenca_pct',
           'tipo', 'envio']
    ws.append(cab)
    for n, d in enumerate(dados, start=2):
        tem = d['custo'] is not None
        ws.append([
            d['codigo'], d['sku'], d['titulo'], d['status'], 'sim' if d['so_gratis'] else 'não',
            d['preco'], d['taxa'], d['adc'], d['frete'],
            d['custo'],
            f'=J{n}*(1-{DESCONTO})' if tem else None,
            f'=F{n}*(1-G{n})-H{n}-I{n}-K{n}' if tem else None,  # lucro antes do imposto
            f'=IF(F{n}>0,L{n}/F{n},"")' if tem else None,
            f'=F{n}*{imposto}' if tem else None,  # imposto sobre o preço de venda
            f'=L{n}-N{n}' if tem else None,  # lucro líquido
            # preço que dá a margem alvo (lucro + gordura + imposto) com a taxa, adc e frete atuais:
            # P*(1-taxa) - adc - frete - custo_desc = META*P  ->  P = (adc+frete+custo_desc)/(1-taxa-META)
            # anúncio só com itens grátis: a margem não importa, mantém o preço atual
            f'=IF(E{n}="sim",F{n},ROUND((H{n}+I{n}+K{n})/(1-G{n}-{meta}),2))' if tem else None,
            f'=P{n}-F{n}' if tem else None,  # preco_sugerido - preco
            f'=IF(F{n}>0,ABS(Q{n})/F{n},"")' if tem else None,  # |diferenca| / preco
            d['tipo'], d['envio'],
        ])

    ult = ws.max_row
    for cel in ws[1]:
        cel.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        # preco, adc, frete, custo, custo_desc, lucro, imposto, lucro_liquido, preco_sugerido, diferenca
        for i in (5, 7, 8, 9, 10, 11, 13, 14, 15, 16):
            row[i].number_format = '#,##0.00'
        row[6].number_format = '0%'
        row[12].number_format = '0.00%'
        row[17].number_format = '0.00%'
    for i, w in enumerate([16, 22, 50, 9, 10, 11, 7, 9, 9, 11, 11, 11, 9, 10, 13, 14, 11, 13, 10, 9], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = ws.dimensions
    vermelho = PatternFill('solid', start_color='F8CBAD')
    amarelo = PatternFill('solid', start_color='FFE699')
    # destaca margem fora do alvo, menos nos anúncios só com itens grátis (lá a margem não importa)
    ws.conditional_formatting.add(f'M2:M{ult}', FormulaRule(
        formula=[f'AND($E2<>"sim",ISNUMBER(M2),M2<{meta - TOLERANCIA})'], fill=vermelho))
    ws.conditional_formatting.add(f'M2:M{ult}', FormulaRule(
        formula=[f'AND($E2<>"sim",ISNUMBER(M2),M2>{meta + TOLERANCIA})'], fill=amarelo))
    wb.save(arq)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--coleta', help='planilha da coleta (padrão: a mais recente em saidas/margem-*.xlsx)')
    ap.add_argument('--precos', default=str(PRECOS))
    ap.add_argument('--apelidos', default=str(APELIDOS))
    args = ap.parse_args()

    coleta = args.coleta or max(glob.glob(str(SAIDAS / 'margem-*.xlsx')), default=None)
    if not coleta:
        raise SystemExit('Nenhuma coleta em margem/saidas/: rode antes  .venv/bin/python -m margem.coleta')
    partes = partes_meta()
    meta, imposto = margem_meta(), partes['imposto']
    tab = Tabela(carregar_precos(args.precos), carregar_apelidos(Path(args.apelidos)))
    print(f'Coleta: {coleta}\nPreços: {len(tab.exato)} SKUs | apelidos: {len(tab.apelidos)} | '
          f'margem alvo: {meta * 100:g}% (lucro {partes["lucro"] * 100:g}% + gordura {partes["gordura"] * 100:g}% '
          f'+ imposto {imposto * 100:g}%)')

    dados = calcular(coleta, tab, meta, imposto)
    cont = {'com custo': 0, 'sem custo': 0, 'só grátis': 0}
    sem_custo = []
    for d in dados:
        cont['com custo' if d['custo'] is not None else 'sem custo'] += 1
        cont['só grátis'] += d['so_gratis']
        if d['custo'] is None:
            sem_custo.append(f'{d["codigo"]} {d["sku"]}: {d["como"]}')

    SAIDAS.mkdir(exist_ok=True)
    teste = Path(coleta).name.startswith('teste')
    arq = SAIDAS / f'custo{"-teste" if teste else ""}-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    planilha(dados, meta, imposto, arq)
    resumo = {
        'gerado': datetime.now().isoformat(timespec='seconds'),
        'coleta': Path(coleta).name,
        'coleta_data': datetime.fromtimestamp(os.path.getmtime(coleta)).isoformat(timespec='seconds'),
        'planilha': arq.name, 'meta': meta, 'partes': partes, 'imposto': imposto, 'tolerancia': TOLERANCIA,
        'desconto': DESCONTO, 'contagem': cont, 'linhas': dados,
    }
    arq.with_suffix('.json').write_text(json.dumps(resumo, ensure_ascii=False), encoding='utf-8')
    print(f'Com custo: {cont["com custo"]} | sem custo: {cont["sem custo"]} | só itens grátis: {cont["só grátis"]}')
    for s in sem_custo:  # a planilha não diz o motivo, então fica aqui
        print('  sem custo:', s)
    print(f'Planilha: {arq}')


if __name__ == '__main__':
    main()
