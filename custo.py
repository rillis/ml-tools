"""Cruza a última coleta (saidas/margem-*.xlsx) com o precos.xlsx e calcula custo e margem.

Uso (no servidor):  .venv/bin/python custo.py [--coleta saidas/margem-....xlsx]

Não chama a API do ML. Gera saidas/custo-AAAA-MM-DD_HHMM.xlsx com custo, custo com desconto,
lucro, margem e preco_sugerido (preço de venda que dá a margem MARGEM_META do .env, padrão 0.18,
com a taxa, adc e frete atuais).
Menos o custo, são fórmulas: mudou o custo na planilha, o resto acompanha.

Como o SKU do anúncio vira custo (nesta ordem):
  1. apelidos.xlsx (sku -> composição), para os casos que não dá para deduzir;
     a composição pode ser um número, que vira o custo fixo; 0 = item grátis, que custa 0
     sozinho e R$ 1 por unidade (GRATIS_EM_KIT) quando está num kit com algum item pago
  2. o SKU inteiro igual ao da tabela (ignora maiúsculas e espaços)
  3. separa por "+" e soma as peças; cada peça pode ter quantidade na frente ("3xKA-250")
     e é procurada: igual, ignorando pontuação ("Cab-bico042" = "CAB-BICO-042"),
     e abrindo a barra dentro de um trecho ("TP-036.M/F.GOLF" = TP-036.M.GOLF + TP-036.F.GOLF)
Se alguma peça não tiver preço, o anúncio fica sem custo e o resumo na tela diz qual faltou.
A coluna diferenca é preco_sugerido - preco (positivo: precisa subir o preço).
A coluna so_gratis marca anúncios só com itens grátis: neles o preco_sugerido é o preço atual.
"""
import argparse
import glob
import itertools
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ml import load_env

BASE = Path(__file__).resolve().parent
DESCONTO = 0.15     # desconto do fornecedor
META = float(str(load_env().get('MARGEM_META') or '0.18').replace(',', '.'))  # margem alvo sobre o preço (.env)
TOLERANCIA = 0.03   # fica destacado fora de META ± TOLERANCIA
GRATIS_EM_KIT = 1.0  # item grátis (apelido com custo 0) num kit com item pago: custo por unidade


def linhas(arq):
    return list(load_workbook(arq, data_only=True).active.iter_rows(min_row=2, values_only=True))


def norm(s):
    return re.sub(r'\s+', '', str(s)).upper()


def solto(s):
    return re.sub(r'[^0-9A-Z]', '', str(s).upper())


class Tabela:
    def __init__(self, precos, apelidos):
        self.exato = {}
        self.sem_pont = {}
        for sku, preco in precos.items():
            self.exato[norm(sku)] = preco
            self.sem_pont.setdefault(solto(sku), set()).add(preco)
        self.apelidos = {norm(k): v for k, v in apelidos.items()}

    def _peca(self, s):
        """Preço de uma peça (sem quantidade) ou None."""
        if norm(s) in self.exato:
            return self.exato[norm(s)], 'exato'
        precos = self.sem_pont.get(solto(s))
        if precos and len(precos) == 1:
            return next(iter(precos)), 'pontuação'
        if '/' in s:
            # barra dentro de um trecho: TP-036.M/F.GOLF -> TP-036.M.GOLF + TP-036.F.GOLF
            trechos = [t.split('/') for t in s.split('.')]
            nomes = ['.'.join(c) for c in itertools.product(*trechos)]
            achados = [self.exato.get(norm(n)) for n in nomes]
            if len(nomes) > 1 and all(a is not None for a in achados):
                return sum(achados), 'barra'
        return None, None

    def _parte(self, parte):
        """Uma parte do "+": aplica apelido e quantidade ("3xKA-250").
        Devolve (custo, como, peças sem preço, unidades grátis)."""
        parte = parte.strip()
        m = re.match(r'^(\d+)\s*[xX]\s*(.+)$', parte)
        qtd, nome = (int(m.group(1)), m.group(2).strip()) if m else (1, parte)
        fixo = numero(self.apelidos[norm(nome)]) if norm(nome) in self.apelidos else None
        if fixo is not None:
            custo, como, falta = fixo, ('grátis' if fixo == 0 else 'apelido'), []  # custo fixo
        elif norm(nome) in self.apelidos:
            custo, como, falta = self.custo(self.apelidos[norm(nome)], usar_apelido=False)
            como = 'apelido'
        else:
            custo, como = self._peca(nome)
            falta = [nome] if custo is None else []
        if custo is None:
            return None, como, falta, 0
        return custo * qtd, (f'{qtd}x ' if qtd > 1 else '') + como, [], (qtd if fixo == 0 else 0)

    def _gratis(self, nome):
        return norm(nome) in self.apelidos and numero(self.apelidos[norm(nome)]) == 0

    def so_gratis(self, sku):
        """True se o anúncio só tem itens grátis (apelido com custo 0), com ou sem quantidade."""
        sku = str(sku or '').strip()
        if not sku:
            return False
        if norm(sku) in self.apelidos:
            return self._gratis(sku)
        partes = [re.sub(r'^\d+\s*[xX]\s*', '', p.strip()) for p in sku.split('+') if p.strip()]
        return bool(partes) and all(self._gratis(p) for p in partes)

    def custo(self, sku, usar_apelido=True):
        """(custo, como, peças sem preço) para o SKU do anúncio."""
        sku = str(sku or '').strip()
        if not sku:
            return None, 'sem sku', []
        if usar_apelido and norm(sku) in self.apelidos:
            fixo = numero(self.apelidos[norm(sku)])
            if fixo is not None:
                return fixo, ('grátis' if fixo == 0 else 'apelido'), []
            c, _, falta = self.custo(self.apelidos[norm(sku)], usar_apelido=False)
            return c, 'apelido', falta
        if norm(sku) in self.exato:
            return self.exato[norm(sku)], 'exato', []
        total, comos, faltas, gratis, pagos = 0, [], [], 0, 0
        for parte in [p for p in sku.split('+') if p.strip()]:
            c, como, falta, g = self._parte(parte)
            faltas += falta
            comos.append(como or '?')
            total += c or 0
            gratis += g
            pagos += 0 if g else 1
        if faltas:
            return None, 'sem preço: ' + ', '.join(faltas), faltas
        if gratis and pagos:  # grátis junto com item pago: R$ 1 por unidade grátis
            total += gratis * GRATIS_EM_KIT
            comos = [c.replace('grátis', f'grátis R${GRATIS_EM_KIT:g}') for c in comos]
        tipos = sorted(set(comos))
        return round(total, 2), ('soma ' if len(comos) > 1 else '') + '+'.join(tipos), []


def carregar_precos(arq):
    """SKU -> preço. Fórmulas simples entre células (=B390, =B439+B394) são calculadas aqui,
    porque um arquivo salvo fora do Excel não guarda o resultado das fórmulas."""
    ws_val = load_workbook(arq, data_only=True).active
    ws_form = load_workbook(arq).active
    cache = {}

    def valor(ref, pilha=()):
        if ref in cache:
            return cache[ref]
        v = ws_val[ref].value
        f = ws_form[ref].value
        if not isinstance(v, (int, float)) and isinstance(f, str) and f.startswith('='):
            expr = f[1:].replace('$', '')
            if ref in pilha or not re.fullmatch(r'[A-Z]+\d+|[\d.]+|[\s()+\-*/A-Z\d.]+', expr):
                v = None
            else:
                partes = {}
                for r in set(re.findall(r'[A-Z]+\d+', expr)):
                    partes[r] = valor(r, pilha + (ref,))
                if any(p is None for p in partes.values()):
                    v = None
                else:
                    expr = re.sub(r'[A-Z]+\d+', lambda m: repr(float(partes[m.group(0)])), expr)
                    v = eval(expr, {'__builtins__': {}}) if re.fullmatch(r'[\s()+\-*/\d.e]+', expr) else None
        cache[ref] = v if isinstance(v, (int, float)) else None
        return cache[ref]

    precos = {}
    for row in ws_form.iter_rows(min_row=2):
        sku = row[0].value
        if sku:
            p = valor(row[1].coordinate)
            if p is not None:
                precos[str(sku).strip()] = float(p)
    return precos


def numero(s):
    try:
        return float(str(s).replace(',', '.'))
    except ValueError:
        return None


def carregar_apelidos(arq):
    if not arq.exists():
        return {}
    return {str(r[0]).strip(): str(r[1]).strip() for r in linhas(arq)
            if r and r[0] and r[1] is not None and str(r[1]).strip() != ''}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--coleta', help='planilha da coleta (padrão: a mais recente em saidas/margem-*.xlsx)')
    ap.add_argument('--precos', default=str(BASE / 'precos.xlsx'))
    ap.add_argument('--apelidos', default=str(BASE / 'apelidos.xlsx'))
    args = ap.parse_args()

    coleta = args.coleta or max(glob.glob(str(BASE / 'saidas' / 'margem-*.xlsx')), default=None)
    if not coleta:
        raise SystemExit('Nenhuma coleta em saidas/: rode antes  .venv/bin/python margem.py')
    tab = Tabela(carregar_precos(args.precos), carregar_apelidos(Path(args.apelidos)))
    print(f'Coleta: {coleta}\nPreços: {len(tab.exato)} SKUs | apelidos: {len(tab.apelidos)}')

    wb = Workbook()
    ws = wb.active
    ws.title = 'margem'
    # A codigo, B sku, C titulo, D status, E so_gratis, F preco, G taxa, H adc, I frete,
    # J custo, K custo_desc, L lucro, M margem, N preco_sugerido, O diferenca
    cab = ['codigo', 'sku', 'titulo', 'status', 'so_gratis', 'preco', 'taxa', 'adc', 'frete',
           'custo', 'custo_desc', 'lucro', 'margem', 'preco_sugerido', 'diferenca']
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
        ])

    ult = ws.max_row
    for cel in ws[1]:
        cel.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for i in (5, 7, 8, 9, 10, 11, 13, 14):  # preco, adc, frete, custo, custo_desc, lucro, preco_sugerido, diferenca
            row[i].number_format = '#,##0.00'
        row[6].number_format = '0%'
        row[12].number_format = '0.00%'
    for i, w in enumerate([16, 22, 50, 9, 10, 11, 7, 9, 9, 11, 11, 11, 9, 14, 11], 1):
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

    arq = BASE / 'saidas' / f'custo-{datetime.now():%Y-%m-%d_%H%M}.xlsx'
    wb.save(arq)
    print(f'Com custo: {cont["com custo"]} | sem custo: {cont["sem custo"]} | só itens grátis: {cont["só grátis"]}')
    for s in sem_custo:  # a planilha não diz o motivo, então fica aqui
        print('  sem custo:', s)
    print(f'Planilha: {arq}')


if __name__ == '__main__':
    main()
