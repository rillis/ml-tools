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


def margem_meta(env=None, chave='MARGEM_META', padrao='0.18'):
    return float((env or ler_env()).get(chave, padrao).replace(',', '.'))


def margem_atacado(env=None):
    return margem_meta(env, 'MARGEM_META_ATACADO', '0.175')
