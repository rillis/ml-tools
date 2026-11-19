"""Cria a opção de venda que falta (Clássico <-> Premium) de um anúncio e ajusta o preço até cravar a margem.

Uso:  .venv/bin/python -m clonar.criar --item MLB123
      .venv/bin/python -m clonar.criar --itens MLB1,MLB2,MLB3     (até 20: cria todos e confere todos a cada rodada)

1. Lê o anúncio original (preço, tarifa, frete/adc) e confere que não existe a outra opção ativa ou pausada
   (ou em revisão) com o mesmo SKU nem no mesmo produto.
2. Calcula o preço da opção nova pela margem alvo: custo do SKU (tabela de preços), tarifa do tipo novo
   nesse preço e o frete/adc do original.
3. Cria a opção nova no mesmo produto (POST /user-products/{id}/items). Ela nasce no envio normal e usa o
   estoque do produto, o mesmo do original.
4. Loop: a cada 10 s relê o anúncio novo (tarifa e frete podem mudar com o preço) e ajusta o preço até
   ele ficar igual ao preço da margem alvo em duas leituras seguidas (até 10 leituras).
   Também relê o original: se a tarifa ou o frete dele mudou e a margem saiu do alvo, ajusta o preço
   dele e avisa. Original que já começou fora do alvo e não mudou fica como está.
O resultado vai para data/clonar-historico.json e os dois MLBs para data/clonar-ultimo.txt.
"""
import argparse
import json
import logging
import sys
import time

from clonar import ANDAMENTO, NOMES, OUTRO, PAR, ULTIMO, agora, gravar_json, registrar
from comum import ml
from comum.env import margem_meta
from comum.ml import sku_de, titulo_de
from comum.tabela import carregar
from margem.coleta import custo_envio, preco_venda, taxa_ml
from margem.custo import DESCONTO, TOLERANCIA, sugerido

ESPERA = 10          # segundos entre as leituras do loop
RODADAS = 10         # leituras no máximo
ESTAVEL = 2          # leituras seguidas sem precisar mudar o preço para dar como cravado
MAXIMO = 20          # anúncios por vez
GARANTIA = ('WARRANTY_TYPE', 'WARRANTY_TIME')
CAMPOS = 'id,title,family_name,price,status,listing_type_id,user_product_id,category_id,shipping,attributes,' \
         'seller_custom_field,sale_terms,available_quantity'

log = logging.getLogger('clonar')


class Falha(Exception):
    pass


def ler(c, seller, iid):
    """Anúncio com o preço de venda, a tarifa (fração) e o frete/adc de agora."""
    b = c.get(f'/items/{iid}', {'attributes': CAMPOS})
    preco = preco_venda(c, iid, b['price'])
    taxa = taxa_ml(c, b['category_id'], b['listing_type_id'], preco) / 100
    adc, frete = custo_envio(c, seller, iid, (b.get('shipping') or {}).get('free_shipping'))
    return {'b': b, 'base': b['price'], 'preco': preco, 'taxa': taxa, 'adc': adc, 'frete': frete}


def margem_de(e, custo_desc):
    return (e['preco'] * (1 - e['taxa']) - e['adc'] - e['frete'] - custo_desc) / e['preco']


def alvo(e, custo_desc, meta):
    return sugerido(0, e['taxa'], e['adc'], e['frete'], custo_desc, meta, False)


def motivo(e, b=None):
    """Mensagem legível de um erro do ML: os motivos (cause) em vez do JSON cru."""
    try:
        corpo = json.loads(e.body) if isinstance(e.body, str) else {}
    except ValueError:
        corpo = {}
    causas = [x for x in corpo.get('cause') or [] if x.get('type') == 'error'] or corpo.get('cause') or []
    textos = []
    for x in causas:
        t = x.get('message') or x.get('code') or ''
        if x.get('code') == 'item.attribute.invalid_product_identifier' and b:
            gtin = next((a.get('value_name') for a in b.get('attributes') or [] if a.get('id') == 'GTIN'), None)
            if gtin:
                t += f' (GTIN {gtin}: ele já está num anúncio seu de outra categoria, como um kit)'
        textos.append(t)
    if not textos:
        return str(e)
    return f'ML {e.status}: ' + ' · '.join(dict.fromkeys(textos))


def brl(v):
    return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def pct(v):
    return f'{v * 100:.2f}%'.replace('.', ',')


def resumo(e, custo_desc):
    return (f'{brl(e["preco"])} · tarifa {pct(e["taxa"])} · frete {brl(e["frete"])} · adc {brl(e["adc"])} · '
            f'margem {pct(margem_de(e, custo_desc))}')


def dormir(segundos):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        time.sleep(min(1, fim - time.monotonic()))


class Clone:
    """Um anúncio sendo clonado: cria a opção nova e, a cada rodada, confere o novo e o original."""

    def __init__(self, c, seller, item, tab, meta):
        self.c, self.seller, self.item, self.tab, self.meta = c, seller, item, tab, meta
        self.registro = {'id': f'{item}-{int(time.time())}', 'inicio': agora(), 'original': item, 'novo': None,
                         'status': 'criando', 'avisos': []}
        self.nid = None
        self.etapa = 'fila'  # fila, criando, conferindo, ok, fora, erro, parado
        self.terminou = False
        self.estavel = 0
        self.ajustei_orig = False  # depois de eu mudar o preço do original, a tarifa dele muda: não é aviso
        self.e = None

    def log(self, texto, nivel=logging.INFO):
        log.log(nivel, f'[{self.registro.get("sku") or self.item}] {texto}')

    def aviso(self, texto):
        self.registro['avisos'].append(texto)
        self.log(texto, logging.WARNING)

    def criar(self):
        c, seller, item, registro, meta = self.c, self.seller, self.item, self.registro, self.meta
        self.etapa = 'criando'
        orig = ler(c, seller, item)
        b = orig['b']
        tipo, up = b.get('listing_type_id'), b.get('user_product_id')
        sku = sku_de(b)
        registro.update(up=up, sku=sku, titulo=titulo_de(b), tipo_original=NOMES.get(tipo, tipo))
        if tipo not in OUTRO:
            raise Falha(f'Tipo de anúncio {tipo}: só clono Clássico e Premium')
        if not up:
            raise Falha('O anúncio não tem user product (modelo antigo): não dá para criar outra opção nele')
        novo_tipo = OUTRO[tipo]
        registro['tipo_novo'] = NOMES[novo_tipo]
        self.log(f'{item} ({NOMES[tipo]}, {b.get("status")}) · produto {up}')

        # par pelo SKU (anúncios antigos ficam em produtos diferentes) ou pelo mesmo produto
        irmaos = c.get(f'/users/{seller}/items/search', {'user_product_id': up}).get('results') or []
        mesmo_sku = c.get(f'/users/{seller}/items/search', {'seller_sku': sku}).get('results') or [] if sku else []
        outros = sorted(set(irmaos + mesmo_sku) - {item})
        for n in range(0, len(outros), 20):
            for x in c.get('/items', {'ids': ','.join(outros[n:n + 20]), 'attributes': 'id,status,listing_type_id'}):
                o = x.get('body') or {}
                if o.get('listing_type_id') == novo_tipo and o.get('status') in PAR:
                    onde = 'O produto' if o['id'] in irmaos else f'O SKU {sku}'
                    raise Falha(f'{onde} já tem {NOMES[novo_tipo]}: {o["id"]} ({o["status"]})')

        custo, como, _ = self.tab.custo(sku)
        if custo is None:
            raise Falha(f'SKU {sku or "(vazio)"} sem custo: {como}')
        if self.tab.so_gratis(sku):
            raise Falha('Anúncio só com itens grátis: não clono')
        self.custo_desc = custo_desc = custo * (1 - DESCONTO)
        m0 = margem_de(orig, custo_desc)
        registro['original_inicio'] = {'preco': orig['preco'], 'margem': m0}
        self.log(f'Original: {resumo(orig, custo_desc)}'
                 + ('' if abs(m0 - meta) <= TOLERANCIA else ' (já começou fora da margem alvo)'))

        # preço inicial: tarifa do tipo novo no preço sugerido (a tarifa muda com a faixa de preço)
        p = orig['preco']
        for _ in range(6):
            taxa = taxa_ml(c, b['category_id'], novo_tipo, p) / 100
            p2 = sugerido(0, taxa, orig['adc'], orig['frete'], custo_desc, meta, False)
            if abs(p2 - p) < 0.005:
                break
            p = p2

        corpo = {'price': p, 'category_id': b['category_id'], 'currency_id': 'BRL', 'buying_mode': 'buy_it_now',
                 'listing_type_id': novo_tipo}
        termos = [{k: t[k] for k in ('id', 'value_id', 'value_name') if t.get(k) is not None}
                  for t in b.get('sale_terms') or [] if t.get('id') in GARANTIA]
        if termos:
            corpo['sale_terms'] = termos
        try:
            novo = c.post(f'/user-products/{up}/items', corpo)
        except ml.MLError as e:
            # 5xx ou rede: o ML pode ter criado mesmo assim; confere antes de dizer que falhou
            depois = set(c.get(f'/users/{seller}/items/search', {'user_product_id': up}).get('results') or [])
            criados = sorted(depois - set(irmaos))
            if not criados:
                raise Falha(f'O ML recusou a criação: {motivo(e, b)}') from None
            novo = c.get(f'/items/{criados[0]}', {'attributes': 'id'})
        self.nid = novo['id']
        self.tentados = {round(p, 2)}
        self.base_orig = {k: orig[k] for k in ('taxa', 'adc', 'frete')}
        registro.update(novo=self.nid, preco_inicial=p, status='ajustando')
        self.etapa = 'conferindo'
        registrar(registro)
        self.log(f'Criado {self.nid} ({NOMES[novo_tipo]}) a {brl(p)} (tarifa {pct(taxa)}, frete/adc do original)')

    def rodada(self, n):
        """Uma leitura do novo e do original; ajusta o preço se precisar."""
        c, seller, nid, item, meta, custo_desc = self.c, self.seller, self.nid, self.item, self.meta, self.custo_desc
        e = self.e = ler(c, seller, nid)
        s = alvo(e, custo_desc, meta)
        texto = f'Novo: {resumo(e, custo_desc)}'
        if abs(s - e['base']) < 0.005:
            self.estavel += 1
            texto += ' · cravado'
        elif round(s, 2) in self.tentados:
            # vai e volta entre dois preços (faixa de frete/tarifa): fica com o maior, que não perde margem
            fica = max(s, e['base'])
            if abs(fica - e['base']) >= 0.005:
                c.put(f'/items/{nid}', {'price': fica})
            self.aviso(f'O preço oscila entre {brl(min(s, e["base"]))} e {brl(max(s, e["base"]))} '
                       f'(faixa de frete ou tarifa): deixei {brl(fica)}')
            self.e = ler(c, seller, nid)  # margem do preço que ficou
            self.terminou = True
            return
        else:
            c.put(f'/items/{nid}', {'price': s})
            self.tentados.add(round(s, 2))
            self.estavel = 0
            texto += f' · preço ajustado para {brl(s)}'
        self.log(texto)
        self.registro.update(preco_final=e['base'], margem_final=margem_de(e, custo_desc), rodadas=n)
        registrar(self.registro)

        o = ler(c, seller, item)
        mudou = [k for k in ('taxa', 'adc', 'frete') if abs(o[k] - self.base_orig[k]) > 0.00001]
        if mudou and self.ajustei_orig and abs(margem_de(o, custo_desc) - meta) <= TOLERANCIA:
            self.base_orig = {k: o[k] for k in ('taxa', 'adc', 'frete')}  # efeito do meu ajuste: segue
            mudou = []
        if mudou:
            mo = margem_de(o, custo_desc)
            nomes = {'taxa': 'tarifa', 'adc': 'adc', 'frete': 'frete'}
            aviso = f'Original mudou ({", ".join(nomes[k] for k in mudou)}): {resumo(o, custo_desc)}'
            if abs(mo - meta) > TOLERANCIA:
                if abs(o['preco'] - o['base']) >= 0.005:
                    aviso += ' · está em promoção: não mexi no preço'
                else:
                    so = alvo(o, custo_desc, meta)
                    c.put(f'/items/{item}', {'price': so})
                    aviso += f' · ajustei o preço para {brl(so)}'
                    self.estavel = 0
                    self.ajustei_orig = True
            self.aviso(aviso)
            self.base_orig = {k: o[k] for k in ('taxa', 'adc', 'frete')}
            registrar(self.registro)
        if self.estavel >= ESTAVEL:
            self.terminou = True

    def andamento(self, rodada=None):
        r = self.registro
        e = self.e
        return {'original': self.item, 'sku': r.get('sku'), 'tipo_novo': r.get('tipo_novo'), 'novo': self.nid,
                'etapa': self.etapa, 'rodada': rodada if self.etapa == 'conferindo' else r.get('rodadas'),
                'preco': e['base'] if e else r.get('preco_inicial'),
                'margem': margem_de(e, self.custo_desc) if e else None,
                'erro': r.get('erro'), 'avisos': r.get('avisos', [])}

    def finalizar(self):
        registro, e = self.registro, self.e
        cravou = e is not None and abs(alvo(e, self.custo_desc, self.meta) - e['base']) < 0.005 and self.estavel >= 1
        registro.update(status='ok' if cravou else 'fora', fim=agora())
        if e is not None:
            registro.update(preco_final=e['base'], margem_final=margem_de(e, self.custo_desc))
        if not cravou and not any('oscila' in a for a in registro['avisos']):
            registro['avisos'].append(f'Não cravou a margem em {RODADAS} leituras: confira no ML')
        registrar(registro)
        self.etapa = registro['status']
        self.log(f'{"Cravado" if cravou else "Terminou sem cravar"}: {self.nid} a '
                 f'{brl(registro.get("preco_final") or registro["preco_inicial"])}'
                 + (f' · margem {pct(registro["margem_final"])}' if registro.get('margem_final') is not None else ''))

    def erro(self, e):
        self.etapa = 'erro'
        self.registro.update(status='erro', fim=agora(), erro=str(e)[:500])
        registrar(self.registro)
        self.log(f'Erro: {e}', logging.ERROR)


def main():
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--item', help='MLB do anúncio original')
    ap.add_argument('--itens', help=f'vários MLB separados por vírgula (até {MAXIMO})')
    args = ap.parse_args()
    itens = list(dict.fromkeys(i.strip().upper() for i in f'{args.item or ""},{args.itens or ""}'.split(',')
                               if i.strip()))
    if not itens or len(itens) > MAXIMO:
        raise SystemExit(f'Informe de 1 a {MAXIMO} anúncios')
    trava = ml.travar_busca()  # noqa: F841 (segura a trava até o fim)
    ULTIMO.unlink(missing_ok=True)
    c = ml.MLClient()
    seller = ml.read_tokens().get('user_id') or c.get('/users/me')['id']
    tab, meta = carregar(), margem_meta()
    log.info(f'Margem alvo {pct(meta)} · {len(itens)} anúncio{"s" if len(itens) > 1 else ""}')
    clones = [Clone(c, seller, i, tab, meta) for i in itens]
    inicio = agora()

    def salvar(rodada=None):
        gravar_json(ANDAMENTO, {'inicio': inicio, 'rodadas': RODADAS, 'espera': ESPERA,
                                'itens': [cl.andamento(rodada) for cl in clones]})

    salvar()
    try:
        # 1. cria todos
        for cl in clones:
            try:
                cl.etapa = 'criando'
                salvar()
                cl.criar()
            except (Falha, ml.MLError) as e:
                cl.erro(e)
                cl.terminou = True
            salvar()
        criados = [cl for cl in clones if cl.nid]
        if criados:
            ULTIMO.write_text(','.join(x for cl in criados for x in (cl.nid, cl.item)), encoding='utf-8')
        # 2. rodadas: espera e confere todos os que ainda não cravaram
        for n in range(1, RODADAS + 1):
            pendentes = [cl for cl in criados if not cl.terminou]
            if not pendentes:
                break
            log.info(f'Leitura {n}/{RODADAS} de {len(pendentes)} anúncio{"s" if len(pendentes) > 1 else ""} '
                     f'em {ESPERA}s...')
            salvar(n)
            dormir(ESPERA)
            for cl in pendentes:
                try:
                    cl.rodada(n)
                except ml.MLError as e:
                    cl.log(f'Erro ao conferir (tenta de novo na próxima leitura): {e}', logging.WARNING)
                if cl.terminou:
                    cl.finalizar()
                salvar(n)
        for cl in criados:
            if cl.etapa == 'conferindo':
                cl.finalizar()
        salvar()
    except KeyboardInterrupt:
        for cl in clones:
            if cl.registro['status'] in ('criando', 'ajustando'):
                cl.registro.update(status='parado', fim=agora())
                cl.registro['avisos'].append('Parado no meio' + (': confira o preço do anúncio novo' if cl.nid else ''))
                registrar(cl.registro)
            if cl.etapa not in ('ok', 'fora', 'erro'):
                cl.etapa = 'parado'
        salvar()
        log.warning('Parado.')
        sys.exit(130)
    ok = sum(cl.registro['status'] == 'ok' for cl in clones)
    log.info(f'Pronto: {ok} de {len(clones)} cravado{"s" if ok != 1 else ""}'
             + (f', {len(clones) - len(criados)} com erro' if len(criados) < len(clones) else ''))
    if not criados:
        sys.exit(1)


if __name__ == '__main__':
    main()
