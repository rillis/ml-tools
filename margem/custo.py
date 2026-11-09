"""Cruza a última coleta (margem/saidas/margem-*.xlsx) com a tabela de preços e calcula custo e margem.

Uso (no servidor):  .venv/bin/python -m margem.custo [--coleta margem/saidas/margem-....xlsx]

Não chama a API do ML. Gera margem/saidas/custo-AAAA-MM-DD_HHMM.xlsx com custo, custo com desconto,
lucro, margem e preco_sugerido (preço de venda que dá a margem MARGEM_META do .env, padrão 0.18,
com a taxa, adc e frete atuais).
Menos o custo, são fórmulas: mudou o custo na planilha, o resto acompanha.

O SKU do anúncio vira custo pelas regras de comum/tabela.py (apelidos, pontuação, barra, "3x").
Se alguma peça não tiver preço, o anúncio fica sem custo e o resumo na tela diz qual faltou.
A coluna diferenca é preco_sugerido - preco (positivo: precisa subir o preço);
diferenca_pct é |diferenca| / preco (sempre positivo).
As colunas *_atacado repetem preco_sugerido, diferenca e diferenca_pct com a margem
MARGEM_META_ATACADO do .env (padrão 0.175).
A coluna so_gratis marca anúncios só com itens grátis: neles o preco_sugerido é o preço atual.
"""
import argparse
import glob
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from comum.ml import load_env
from comum.tabela import APELIDOS, PRECOS, Tabela, carregar_apelidos, carregar_precos, linhas

DIR = Path(__file__).resolve().parent
SAIDAS = DIR / 'saidas'
DESCONTO = 0.15     # desconto do fornecedor
META = float(str(load_env().get('MARGEM_META') or '0.18').replace(',', '.'))  # margem alvo sobre o preço (.env)
META_ATACADO = float(str(load_env().get('MARGEM_META_ATACADO') or '0.175').replace(',', '.'))  # margem do atacado (.env)
TOLERANCIA = 0.03   # fica destacado fora de META ± TOLERANCIA


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--coleta', help='planilha da coleta (padrão: a mais recente em saidas/margem-*.xlsx)')
    ap.add_argument('--precos', default=str(PRECOS))
    ap.add_argument('--apelidos', default=str(APELIDOS))
    args = ap.parse_args()

    coleta = args.coleta or max(glob.glob(str(SAIDAS / 'margem-*.xlsx')), default=None)
    if not coleta:
        raise SystemExit('Nenhuma coleta em margem/saidas/: rode antes  .venv/bin/python -m margem.coleta')
    tab = Tabela(carregar_precos(args.precos), carregar_apelidos(Path(args.apelidos)))
    print(f'Coleta: {coleta}\nPreços: {len(tab.exato)} SKUs | apelidos: {len(tab.apelidos)}')

    wb = Workbook()
    ws = wb.active
    ws.title = 'margem'
    # A codigo, B sku, C titulo, D status, E so_gratis, F preco, G taxa, H adc, I frete,
    # J custo, K custo_desc, L lucro, M margem, N preco_sugerido, O diferenca, P diferenca_pct,
    # Q preco_sugerido_atacado, R diferenca_atacado, S diferenca_pct_atacado
    cab = ['codigo', 'sku', 'titulo', 'status', 'so_gratis', 'preco', 'taxa', 'adc', 'frete',
           'custo', 'custo_desc', 'lucro', 'margem', 'preco_sugerido', 'diferenca', 'diferenca_pct',
           'preco_sugerido_atacado', 'diferenca_atacado', 'diferenca_pct_atacado']
    ws.append(cab)
    cont = {'com custo': 0, 'sem custo': 0, 'só grátis': 0}
    sem_custo = []
    for n, r in enumerate(linhas(coleta), start=2):
        codigo, sku, titulo, status, preco, taxa, adc, frete = r[:8]
        custo, como, _ = tab.custo(sku)
        cont['com custo' if custo is not None else 'sem custo'] += 1
        tem = custo is not None
        if not tem:
            sem_custo.append(f'{codigo} {sku}: {como}')
        gratis = tab.so_gratis(sku)
        cont['só grátis'] += gratis
        ws.append([
            codigo, sku, titulo, status, 'sim' if gratis else 'não', preco, taxa, adc, frete,
            custo,
            f'=J{n}*(1-{DESCONTO})' if tem else None,
            f'=F{n}*(1-G{n})-H{n}-I{n}-K{n}' if tem else None,
            f'=IF(F{n}>0,L{n}/F{n},"")' if tem else None,
            # preço que dá META de margem com a taxa, adc e frete atuais:
            # P*(1-taxa) - adc - frete - custo_desc = META*P  ->  P = (adc+frete+custo_desc)/(1-taxa-META)
            # anúncio só com itens grátis: a margem não importa, mantém o preço atual
            f'=IF(E{n}="sim",F{n},ROUND((H{n}+I{n}+K{n})/(1-G{n}-{META}),2))' if tem else None,
            f'=N{n}-F{n}' if tem else None,  # preco_sugerido - preco
            f'=IF(F{n}>0,ABS(O{n})/F{n},"")' if tem else None,  # |diferenca| / preco
            # o mesmo com a margem do atacado
            f'=IF(E{n}="sim",F{n},ROUND((H{n}+I{n}+K{n})/(1-G{n}-{META_ATACADO}),2))' if tem else None,
            f'=Q{n}-F{n}' if tem else None,
            f'=IF(F{n}>0,ABS(R{n})/F{n},"")' if tem else None,
        ])

    ult = ws.max_row
    for cel in ws[1]:
        cel.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for i in (5, 7, 8, 9, 10, 11, 13, 14):  # preco, adc, frete, custo, custo_desc, lucro, preco_sugerido, diferenca
            row[i].number_format = '#,##0.00'
        row[6].number_format = '0%'
        row[12].number_format = '0.00%'
        row[15].number_format = '0.00%'
        row[16].number_format = row[17].number_format = '#,##0.00'
        row[18].number_format = '0.00%'
    for i, w in enumerate([16, 22, 50, 9, 10, 11, 7, 9, 9, 11, 11, 11, 9, 14, 11, 13, 22, 17, 21], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = ws.dimensions
    vermelho = PatternFill('solid', start_color='F8CBAD')
    amarelo = PatternFill('solid', start_color='FFE699')
    # destaca margem fora do alvo, menos nos anúncios só com itens grátis (lá a margem não importa)
    ws.conditional_formatting.add(f'M2:M{ult}', FormulaRule(
        formula=[f'AND($E2<>"sim",ISNUMBER(M2),M2<{META - TOLERANCIA})'], fill=vermelho))
    ws.conditional_formatting.add(f'M2:M{ult}', FormulaRule(
        formula=[f'AND($E2<>"sim",ISNUMBER(M2),M2>{META + TOLERANCIA})'], fill=amarelo))

    SAIDAS.mkdir(exist_ok=True)
    arq = SAIDAS / f'custo-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    wb.save(arq)
    print(f'Com custo: {cont["com custo"]} | sem custo: {cont["sem custo"]} | só itens grátis: {cont["só grátis"]}')
    for s in sem_custo:  # a planilha não diz o motivo, então fica aqui
        print('  sem custo:', s)
    print(f'Planilha: {arq}')


if __name__ == '__main__':
    main()
