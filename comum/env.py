"""Leitura e gravação do .env da raiz do projeto (usado pelo menu e pelo site)."""
import re

from comum.ml import BASE

ENV = BASE / '.env'


def ler_env():
    env = {}
    if ENV.exists():
        for linha in ENV.read_text(encoding='utf-8').splitlines():
            if '=' in linha and not linha.lstrip().startswith('#'):
                k, v = linha.split('=', 1)
                env[k.strip()] = v.strip()
    return env


def gravar_env(chave, valor):
    """Troca (ou acrescenta) uma linha CHAVE=valor sem mexer no resto do .env."""
    linhas = ENV.read_text(encoding='utf-8').splitlines() if ENV.exists() else []
    achou = False
    for i, linha in enumerate(linhas):
        if re.match(rf'^\s*{re.escape(chave)}\s*=', linha):
            linhas[i] = f'{chave}={valor}'
            achou = True
    if not achou:
        linhas.append(f'{chave}={valor}')
    with open(ENV, 'w', encoding='utf-8') as f:  # mesmo arquivo: mantém dono e permissões
        f.write('\n'.join(linhas) + '\n')


def ler_pct(env=None, chave='MARGEM_LUCRO', padrao='0'):
    return float((env or ler_env()).get(chave, padrao).replace(',', '.'))


# A margem alvo é a soma de três partes, todas sobre o preço de venda
PARTES_META = {'lucro': ('MARGEM_LUCRO', '0.095'), 'gordura': ('MARGEM_GORDURA', '0.005'),
               'imposto': ('MARGEM_IMPOSTO', '0.08')}


def partes_meta(env=None):
    """{'lucro': 0.095, 'gordura': 0.005, 'imposto': 0.0795}: as partes da margem alvo."""
    env = env or ler_env()
    return {nome: ler_pct(env, chave, padrao) for nome, (chave, padrao) in PARTES_META.items()}


def margem_meta(env=None):
    """Margem alvo total = lucro + gordura + imposto."""
    return round(sum(partes_meta(env).values()), 6)
