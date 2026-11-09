"""Tabela de preços do fornecedor (tabelas/precos.xlsx) e apelidos (tabelas/apelidos.xlsx).

Transforma o SKU de um anúncio em custo (Tabela.custo) ou nos SKUs da tabela que o compõem
(Tabela.pecas), sempre pelas mesmas regras:
  1. apelidos.xlsx (sku -> composição), para os casos que não dá para deduzir;
     a composição pode ser um número, que vira o custo fixo; 0 = item grátis, que custa 0
     sozinho e R$ 1 por unidade (GRATIS_EM_KIT) quando está num kit com algum item pago
  2. o SKU inteiro igual ao da tabela (ignora maiúsculas e espaços)
  3. separa por "+" e soma as peças; cada peça pode ter quantidade na frente ("3xKA-250")
     e é procurada: igual, ignorando pontuação ("Cab-bico042" = "CAB-BICO-042"),
     e abrindo a barra dentro de um trecho ("TP-036.M/F.GOLF" = TP-036.M.GOLF + TP-036.F.GOLF)
"""
import itertools
import re
from pathlib import Path

from openpyxl import load_workbook

from comum.ml import BASE

PASTA = BASE / 'tabelas'
PRECOS = PASTA / 'precos.xlsx'
APELIDOS = PASTA / 'apelidos.xlsx'
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
        self.nome = {}       # norm -> SKU como está na tabela
        self.nome_solto = {}  # sem pontuação -> SKUs da tabela
        for sku, preco in precos.items():
            self.exato[norm(sku)] = preco
            self.sem_pont.setdefault(solto(sku), set()).add(preco)
            self.nome[norm(sku)] = sku
            self.nome_solto.setdefault(solto(sku), set()).add(sku)
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

    def _nomes(self, s):
        """SKUs da tabela de uma peça (sem quantidade), pelas mesmas regras do _peca; [] se não achar."""
        if norm(s) in self.nome:
            return [self.nome[norm(s)]]
        nomes = self.nome_solto.get(solto(s))
        if nomes and len(nomes) == 1:
            return list(nomes)
        if '/' in s:
            trechos = [t.split('/') for t in s.split('.')]
            nomes = ['.'.join(c) for c in itertools.product(*trechos)]
            if len(nomes) > 1 and all(norm(n) in self.nome for n in nomes):
                return [self.nome[norm(n)] for n in nomes]
        return []

    def _pecas_apelido(self, nome, qtd):
        comp = self.apelidos[norm(nome)]
        fixo = numero(comp)
        if fixo == 0:  # item grátis: conta o próprio SKU, se ele estiver na tabela
            return [(n, qtd, True) for n in self._nomes(nome)], []
        if fixo is not None:  # custo fixo: não aponta para nenhum SKU da tabela
            return [], []
        achados, faltas = self.pecas(comp, usar_apelido=False)
        return [(n, q * qtd, g) for n, q, g in achados], faltas

    def pecas(self, sku, usar_apelido=True):
        """SKUs da tabela que formam o SKU do anúncio, com as mesmas regras do custo:
        ([(sku_da_tabela, quantidade, é_grátis)], peças que não estão na tabela)."""
        sku = str(sku or '').strip()
        if not sku:
            return [], ['(sem sku)']
        if usar_apelido and norm(sku) in self.apelidos:
            return self._pecas_apelido(sku, 1)
        if norm(sku) in self.nome:
            return [(self.nome[norm(sku)], 1, False)], []
        achados, faltas = [], []
        for parte in [p.strip() for p in sku.split('+') if p.strip()]:
            m = re.match(r'^(\d+)\s*[xX]\s*(.+)$', parte)
            qtd, nome = (int(m.group(1)), m.group(2).strip()) if m else (1, parte)
            if norm(nome) in self.apelidos:
                a, f = self._pecas_apelido(nome, qtd)
            else:
                nomes = self._nomes(nome)
                a, f = [(n, qtd, False) for n in nomes], ([] if nomes else [nome])
            achados += a
            faltas += f
        return achados, faltas

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


def carregar(precos=PRECOS, apelidos=APELIDOS):
    """Tabela com os preços e apelidos (padrão: os de tabelas/)."""
    return Tabela(carregar_precos(precos), carregar_apelidos(Path(apelidos)))
