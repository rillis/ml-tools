"""Clonar: cria a opção de venda que falta (Clássico <-> Premium) no mesmo produto do ML (user product).

- clonar.pares: lista os anúncios ativos e pausados cujo produto não tem a outra opção (data/clonar.json)
- clonar.criar: cria a opção que falta pelo preço da margem alvo e ajusta o preço até cravar a margem
"""
import json
import os
from datetime import datetime

from comum.ml import BASE

PARES = BASE / 'data' / 'clonar.json'
HISTORICO = BASE / 'data' / 'clonar-historico.json'
ULTIMO = BASE / 'data' / 'clonar-ultimo.txt'  # MLBs do último clone, para atualizar na margem
ANDAMENTO = BASE / 'data' / 'clonar-andamento.json'  # etapa de cada anúncio da criação em andamento
OUTRO = {'gold_special': 'gold_pro', 'gold_pro': 'gold_special'}
NOMES = {'gold_special': 'Clássico', 'gold_pro': 'Premium'}
PAR = ('active', 'paused', 'under_review')  # status que contam como par já existente


def gtins_de(b):
    """Códigos universais (GTIN) do anúncio ou do user product: o atributo pode ter vários valores."""
    for a in b.get('attributes') or []:
        if a.get('id') == 'GTIN':
            nomes = [v.get('name') for v in a.get('values') or [] if v.get('name')] or [a.get('value_name')]
            return [g.strip() for n in nomes if n for g in str(n).split(',') if g.strip()]
    return []


def eh_kit(sku):
    """SKU com mais de uma peça ("A+B") ou com quantidade ("3xA")."""
    import re
    sku = str(sku or '')
    return '+' in sku or bool(re.match(r'^\s*\d+\s*[xX]', sku))


def agora():
    return datetime.now().isoformat(timespec='seconds')


def ler_json(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return padrao


def gravar_json(arq, dados):
    arq.parent.mkdir(parents=True, exist_ok=True)
    tmp = arq.with_suffix('.tmp')
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, arq)


def registrar(entrada):
    """Grava (ou atualiza, pelo 'id') uma linha do histórico de clones."""
    hist = ler_json(HISTORICO, [])
    for i, h in enumerate(hist):
        if h.get('id') == entrada['id']:
            hist[i] = entrada
            break
    else:
        hist.insert(0, entrada)
    gravar_json(HISTORICO, hist[:300])
