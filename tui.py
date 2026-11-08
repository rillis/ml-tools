"""Menu do ml-margem: setas para escolher, Enter para confirmar, Ctrl+C para voltar.

Uso (no servidor):  .venv/bin/python tui.py

Roda o margem.py, o custo.py e o authorize.py com os argumentos certos, mostra o estado
da coleta, das planilhas e do token, e muda as configurações do .env.
"""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import questionary
from questionary import Choice, Separator

BASE = Path(__file__).resolve().parent
PY = sys.executable
ENV = BASE / '.env'
ESTILO = questionary.Style([('qmark', 'fg:#4ea1d3 bold'), ('pointer', 'fg:#4ea1d3 bold'),
                            ('highlighted', 'fg:#4ea1d3 bold'), ('answer', 'fg:#7bc96f bold')])
VOLTAR = '__voltar__'


# ---------------------------------------------------------------- estado

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


def coleta_atual():
    """Estado da coleta em data/margem.db: None, ou dict com inicio, feitos, total, concluida."""
    db_path = BASE / 'data' / 'margem.db'
    if not db_path.exists():
        return None
    try:
        db = sqlite3.connect(db_path)
        meta = dict(db.execute('SELECT k, v FROM meta').fetchall())
        total, feitos = db.execute('SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM itens').fetchone()
        db.close()
    except sqlite3.Error:
        return None
    return {'inicio': meta.get('inicio'), 'feitos': feitos, 'total': total,
            'concluida': meta.get('concluida') == '1', 'status': meta.get('status', 'todos')}


def planilhas(prefixo):
    return sorted(glob.glob(str(BASE / 'saidas' / f'{prefixo}-*.xlsx')), reverse=True)


def quando(arq):
    return datetime.fromtimestamp(os.path.getmtime(arq)).strftime('%d/%m %H:%M')


def token():
    arq = BASE / 'data' / 'tokens.json'
    if not arq.exists():
        return 'sem autorização (use "Autorizar app no ML")'
    try:
        t = json.loads(arq.read_text(encoding='utf-8'))
    except ValueError:
        return 'tokens.json ilegível'
    if not t.get('refresh_token'):
        return 'sem refresh token (autorize de novo)'
    resto = t.get('expires_at', 0) - time.time()
    return f'ok, renova sozinho (atual vale mais {resto / 3600:.1f} h)' if resto > 0 else 'ok, renova na próxima coleta'


def cabecalho():
    print('\033[2J\033[H', end='')  # limpa a tela
    env = ler_env()
    c = coleta_atual()
    if c is None:
        estado = 'nenhuma'
    elif c['concluida']:
        estado = f'concluída ({c["total"]} anúncios, iniciada em {c["inicio"]})'
    else:
        estado = f'PARADA em {c["feitos"]}/{c["total"]} (iniciada em {c["inicio"]}) — dá para continuar'
    ult_m, ult_c = planilhas('margem')[:1], planilhas('custo')[:1]
    print('\033[1m ml-margem\033[0m')
    print(f'  Coleta ........ {estado}')
    print(f'  Última coleta . {Path(ult_m[0]).name + "  (" + quando(ult_m[0]) + ")" if ult_m else "nenhuma"}')
    print(f'  Último custo .. {Path(ult_c[0]).name + "  (" + quando(ult_c[0]) + ")" if ult_c else "nenhum"}')
    print(f'  Margem alvo ... {float(env.get("MARGEM_META", "0.18").replace(",", ".")):.0%}'
          f'   |   Limite: {env.get("ML_MAX_RPS", "2")} req/s')
    print(f'  Token do ML ... {token()}')
    print()


# ---------------------------------------------------------------- ações

def rodar(*args):
    """Roda um dos scripts com a saída direto na tela. Ctrl+C interrompe só o script."""
    print(f'\n\033[2m$ {Path(PY).name} {" ".join(args)}\033[0m\n')
    proc = subprocess.Popen([PY, *args], cwd=BASE)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.wait()  # o script também recebe o Ctrl+C e salva o que fez
    return proc.returncode


def pausa():
    print()
    questionary.press_any_key_to_continue('Pressione qualquer tecla para voltar ao menu...').ask()


def acao_coletar():
    c = coleta_atual()
    if c and not c['concluida']:
        esc = questionary.select(
            f'Existe uma coleta parada em {c["feitos"]}/{c["total"]}:',
            choices=[Choice('Continuar de onde parou', 'continuar'),
                     Choice('Descartar e começar do zero', 'novo'),
                     Choice('Voltar', VOLTAR)], style=ESTILO).ask()
        if esc in (None, VOLTAR):
            return
        rodar('margem.py', *(['--novo'] if esc == 'novo' else []))
    else:
        esc = questionary.select(
            'Quais anúncios coletar?',
            choices=[Choice('Todos', 'todos'), Choice('Só ativos', 'active'), Choice('Só pausados', 'paused'),
                     Choice('Voltar', VOLTAR)], style=ESTILO).ask()
        if esc in (None, VOLTAR):
            return
        rodar('margem.py', '--status', esc)
    if questionary.confirm('Calcular custo e margem com essa coleta agora?', default=True, style=ESTILO).ask():
        rodar('custo.py')
    pausa()


def acao_custo():
    coletas = planilhas('margem')
    if not coletas:
        print('Nenhuma coleta ainda: use "Coletar anúncios do ML" primeiro.')
        return pausa()
    escolhas = [Choice(f'{Path(a).name}  ({quando(a)})' + ('  ← mais recente' if i == 0 else ''), a)
                for i, a in enumerate(coletas[:10])]
    arq = questionary.select('Com qual coleta?', choices=escolhas + [Choice('Voltar', VOLTAR)], style=ESTILO).ask()
    if arq in (None, VOLTAR):
        return
    rodar('custo.py', '--coleta', arq)
    pausa()


def acao_teste():
    ids = questionary.text('Códigos dos anúncios, separados por vírgula (ex.: MLB6926478482,MLB6624766636):',
                           validate=lambda t: bool(re.fullmatch(r'\s*(MLB)?\d+(\s*,\s*(MLB)?\d+)*\s*', t, re.I))
                           or 'Use códigos como MLB6926478482, separados por vírgula',
                           style=ESTILO).ask()
    if not ids:
        return
    rodar('margem.py', '--ids', re.sub(r'\s+', '', ids))
    pausa()


def acao_exportar():
    c = coleta_atual()
    if c is None:
        print('Nenhuma coleta para exportar.')
        return pausa()
    rodar('margem.py', '--exportar')
    pausa()


def numero_valido(minimo, maximo):
    def v(t):
        try:
            x = float(t.replace(',', '.'))
        except ValueError:
            return 'Digite um número'
        return minimo <= x <= maximo or f'Use um valor entre {minimo:g} e {maximo:g}'
    return v


def acao_config():
    while True:
        env = ler_env()
        meta = float(env.get('MARGEM_META', '0.18').replace(',', '.'))
        esc = questionary.select('Configurações:', choices=[
            Choice(f'Margem alvo do preço sugerido: {meta:.0%}', 'meta'),
            Choice(f'Limite de requisições por segundo: {env.get("ML_MAX_RPS", "2")}', 'rps'),
            Choice('Voltar', VOLTAR)], style=ESTILO).ask()
        if esc in (None, VOLTAR):
            return
        if esc == 'meta':
            t = questionary.text('Margem alvo em % (ex.: 18):', default=f'{meta * 100:g}',
                                 validate=numero_valido(1, 60), style=ESTILO).ask()
            if t:
                gravar_env('MARGEM_META', f'{float(t.replace(",", ".")) / 100:g}')
                print('Salvo. Vale a partir do próximo cálculo de custo.')
        else:
            t = questionary.text('Requisições por segundo (ex.: 4):', default=env.get('ML_MAX_RPS', '2'),
                                 validate=numero_valido(0.5, 10), style=ESTILO).ask()
            if t:
                gravar_env('ML_MAX_RPS', f'{float(t.replace(",", ".")):g}')
                print('Salvo. Vale a partir da próxima coleta. Fique de olho nos "429 recebidos" no fim dela.')


def acao_log():
    log = BASE / 'logs' / 'margem.log'
    if not log.exists():
        print('Ainda não há log.')
    else:
        linhas = log.read_text(encoding='utf-8', errors='replace').splitlines()
        print('\n'.join(linhas[-40:]))
    pausa()


def acao_autorizar():
    print('A autorização só é necessária se o ML recusar o acesso (ex.: 6 meses sem uso, permissão revogada).')
    if not questionary.confirm('Gerar a URL de autorização?', default=False, style=ESTILO).ask():
        return
    if rodar('authorize.py') != 0:
        return pausa()
    url = questionary.text('Depois de autorizar, cole aqui a URL para onde o ML redirecionou (vazio cancela):',
                           style=ESTILO).ask()
    if url:
        rodar('authorize.py', url.strip())
    pausa()


ACOES = {
    'coletar': acao_coletar, 'custo': acao_custo, 'teste': acao_teste, 'exportar': acao_exportar,
    'config': acao_config, 'log': acao_log, 'autorizar': acao_autorizar,
}


def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit('O menu precisa de um terminal interativo (abra pelo SSH normal, não por pipe ou cron).')
    while True:
        cabecalho()
        esc = questionary.select('O que fazer?', choices=[
            Choice('Coletar anúncios do ML', 'coletar'),
            Choice('Calcular custo e margem', 'custo'),
            Separator(),
            Choice('Testar com alguns anúncios', 'teste'),
            Choice('Gerar planilha da coleta atual (sem chamar o ML)', 'exportar'),
            Choice('Ver o log recente', 'log'),
            Separator(),
            Choice('Configurações', 'config'),
            Choice('Autorizar app no ML', 'autorizar'),
            Choice('Sair', 'sair'),
        ], style=ESTILO, instruction='(setas e Enter)').ask()
        if esc in (None, 'sair'):
            break
        try:
            ACOES[esc]()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
