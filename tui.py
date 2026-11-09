"""Menu do ml-margem: setas para escolher, Enter para confirmar, Ctrl+C para voltar.

Uso (no servidor):  ./run.sh   (ou .venv/bin/python tui.py)

Roda o margem.py, o custo.py, o skus.py e o authorize.py com os argumentos certos, mostra o estado
da última planilha e muda as configurações do .env. As opções de diagnóstico ficam em "Avançado".
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
NEGRITO, AMARELO, VERDE, CINZA, FIM = '\033[1m', '\033[33m', '\033[32m', '\033[2m', '\033[0m'


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


def margem_meta(env=None, chave='MARGEM_META', padrao='0.18'):
    return float((env or ler_env()).get(chave, padrao).replace(',', '.'))


def margem_atacado(env=None):
    return margem_meta(env, 'MARGEM_META_ATACADO', '0.175')


def pct(x):
    return f'{x * 100:g}%'


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


def coleta_parada():
    c = coleta_atual()
    return c if c and not c['concluida'] else None


def planilhas(prefixo):
    return sorted(glob.glob(str(BASE / 'saidas' / f'{prefixo}-*.xlsx')), reverse=True)


def quando(arq):
    return datetime.fromtimestamp(os.path.getmtime(arq)).strftime('%d/%m %H:%M')


def token():
    """(ok, descrição) do token do ML."""
    arq = BASE / 'data' / 'tokens.json'
    if not arq.exists():
        return False, 'o app ainda não foi autorizado no ML'
    try:
        t = json.loads(arq.read_text(encoding='utf-8'))
    except ValueError:
        return False, 'o arquivo do token está ilegível: autorize o app de novo'
    if not t.get('refresh_token'):
        return False, 'o token não renova sozinho: autorize o app de novo'
    resto = t.get('expires_at', 0) - time.time()
    return True, (f'ok, renova sozinho (o atual vale mais {resto / 3600:.1f} h)' if resto > 0
                  else 'ok, renova na próxima coleta')


def cabecalho():
    print('\033[2J\033[H', end='')  # limpa a tela
    print(f'{NEGRITO} ml-margem{FIM}  {CINZA}margem e preço sugerido dos anúncios do ML{FIM}\n')
    ult = planilhas('custo')[:1]
    print(f'  Última planilha  {Path(ult[0]).name + "  (" + quando(ult[0]) + ")" if ult else "nenhuma ainda"}')
    print(f'  Margem alvo      {pct(margem_meta())}  (atacado {pct(margem_atacado())})')
    c = coleta_parada()
    if c:
        print(f'\n  {AMARELO}! Atualização interrompida em {c["feitos"]}/{c["total"]} anúncios: '
              f'dá para continuar de onde parou.{FIM}')
    ok, desc = token()
    if not ok:
        print(f'\n  {AMARELO}! Acesso ao ML: {desc}.{FIM}')
    print()


# ---------------------------------------------------------------- ações

def rodar(titulo, *args):
    """Roda um dos scripts com a saída direto na tela. Ctrl+C interrompe só o script."""
    print(f'\n{NEGRITO}{titulo}{FIM}\n')
    proc = subprocess.Popen([PY, *args], cwd=BASE)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.wait()  # o script também recebe o Ctrl+C e salva o que fez
    return proc.returncode


def pausa():
    print()
    questionary.press_any_key_to_continue('Pressione qualquer tecla para voltar ao menu...').ask()


def coletar_e_calcular(*args_coleta):
    """Coleta e, se a coleta terminar bem, já gera a planilha de margem."""
    if rodar('Buscando os anúncios no ML...', 'margem.py', *args_coleta) != 0:
        print(f'\n{AMARELO}A busca parou antes do fim. O que já foi feito está salvo: '
              f'escolha "Continuar a atualização" no menu.{FIM}')
        return pausa()
    if rodar('Calculando custo e margem...', 'custo.py') == 0:
        print(f'\n{VERDE}Pronto!{FIM}')
    pausa()


def acao_atualizar():
    c = coleta_parada()
    if not c:
        return coletar_e_calcular('--status', 'todos')
    esc = questionary.select(
        f'A última atualização parou em {c["feitos"]}/{c["total"]} anúncios:',
        choices=[Choice('Continuar de onde parou', 'continuar'),
                 Choice('Começar do zero', 'novo'),
                 Choice('Voltar', VOLTAR)], style=ESTILO).ask()
    if esc in (None, VOLTAR):
        return
    # mesmo status da busca parada: com outro status o margem.py descartaria o progresso
    coletar_e_calcular('--status', c['status'], *(['--novo'] if esc == 'novo' else []))


def acao_recalcular():
    if not planilhas('margem'):
        print('Ainda não há anúncios buscados: use "Atualizar margens" primeiro.')
        return pausa()
    rodar('Calculando custo e margem com a última busca...', 'custo.py')
    pausa()


def acao_skus():
    rodar('Comparando os SKUs da tabela com os anúncios do ML...', 'skus.py')
    pausa()


def numero_valido(minimo, maximo):
    def v(t):
        try:
            x = float(t.replace(',', '.'))
        except ValueError:
            return 'Digite um número'
        return minimo <= x <= maximo or f'Use um valor entre {minimo:g} e {maximo:g}'
    return v


def acao_meta(chave='MARGEM_META', atual=None, nome='margem alvo', exemplo='18'):
    meta = margem_meta() if atual is None else atual
    t = questionary.text(f'Nova {nome} em % (ex.: {exemplo}):', default=f'{meta * 100:g}',
                         validate=numero_valido(1, 60), style=ESTILO).ask()
    if not t:
        return
    gravar_env(chave, f'{float(t.replace(",", ".")) / 100:g}')
    print('Salvo.')
    if planilhas('margem') and questionary.confirm(
            'Recalcular a planilha agora com a nova margem? (não busca no ML)',
            default=True, style=ESTILO).ask():
        acao_recalcular()


def acao_meta_atacado():
    acao_meta('MARGEM_META_ATACADO', margem_atacado(), 'margem do atacado', '17.5')


def acao_autorizar():
    if rodar('Gerando a URL de autorização...', 'authorize.py') != 0:
        return pausa()
    url = questionary.text('Depois de autorizar, cole aqui a URL para onde o ML redirecionou (vazio cancela):',
                           style=ESTILO).ask()
    if url:
        rodar('Salvando o token...', 'authorize.py', url.strip())
    pausa()


# ---------------------------------------------------------------- avançado

def av_status():
    esc = questionary.select('Quais anúncios buscar?', choices=[
        Choice('Só ativos', 'active'), Choice('Só pausados', 'paused'), Choice('Voltar', VOLTAR)],
        style=ESTILO).ask()
    if esc not in (None, VOLTAR):
        coletar_e_calcular('--status', esc)


def av_teste():
    ids = questionary.text('Códigos dos anúncios, separados por vírgula (ex.: MLB6926478482,MLB6624766636):',
                           validate=lambda t: bool(re.fullmatch(r'\s*(MLB)?\d+(\s*,\s*(MLB)?\d+)*\s*', t, re.I))
                           or 'Use códigos como MLB6926478482, separados por vírgula',
                           style=ESTILO).ask()
    if not ids:
        return
    rodar('Testando com os anúncios informados...', 'margem.py', '--ids', re.sub(r'\s+', '', ids))
    pausa()


def av_coleta_antiga():
    coletas = planilhas('margem')
    if not coletas:
        print('Ainda não há anúncios buscados.')
        return pausa()
    escolhas = [Choice(f'{Path(a).name}  ({quando(a)})' + ('  ← mais recente' if i == 0 else ''), a)
                for i, a in enumerate(coletas[:10])]
    arq = questionary.select('Com qual busca?', choices=escolhas + [Choice('Voltar', VOLTAR)], style=ESTILO).ask()
    if arq in (None, VOLTAR):
        return
    rodar('Calculando custo e margem...', 'custo.py', '--coleta', arq)
    pausa()


def av_exportar():
    if coleta_atual() is None:
        print('Nenhuma busca para exportar.')
        return pausa()
    rodar('Gerando a planilha da busca atual...', 'margem.py', '--exportar')
    pausa()


def av_log():
    log = BASE / 'logs' / 'margem.log'
    if not log.exists():
        print('Ainda não há log.')
    else:
        linhas = log.read_text(encoding='utf-8', errors='replace').splitlines()
        print('\n'.join(linhas[-40:]))
    pausa()


def av_rps():
    env = ler_env()
    t = questionary.text('Requisições por segundo na busca (ex.: 4):', default=env.get('ML_MAX_RPS', '2'),
                         validate=numero_valido(0.5, 10), style=ESTILO).ask()
    if t:
        gravar_env('ML_MAX_RPS', f'{float(t.replace(",", ".")):g}')
        print('Salvo. Vale a partir da próxima busca. Fique de olho nos "429 recebidos" no fim dela.')
        pausa()


def av_autorizar():
    ok, desc = token()
    print(f'Acesso ao ML: {desc}.')
    print('Só é preciso autorizar de novo se o ML recusar o acesso (ex.: 6 meses sem uso, permissão revogada).')
    if questionary.confirm('Gerar a URL de autorização?', default=not ok, style=ESTILO).ask():
        acao_autorizar()


def acao_avancado():
    acoes = {'status': av_status, 'teste': av_teste, 'antiga': av_coleta_antiga, 'exportar': av_exportar,
             'log': av_log, 'rps': av_rps, 'autorizar': av_autorizar}
    while True:
        cabecalho()
        esc = questionary.select('Avançado:', choices=[
            Choice('Atualizar só os ativos ou só os pausados', 'status'),
            Choice('Testar com alguns anúncios', 'teste'),
            Choice('Recalcular com uma busca mais antiga', 'antiga'),
            Choice('Gerar a planilha da busca atual (sem chamar o ML)', 'exportar'),
            Separator(),
            Choice('Ver o log recente', 'log'),
            Choice(f'Limite de requisições por segundo: {ler_env().get("ML_MAX_RPS", "2")}', 'rps'),
            Choice('Acesso ao ML (token e reautorização)', 'autorizar'),
            Separator(),
            Choice('Voltar', VOLTAR),
        ], style=ESTILO).ask()
        if esc in (None, VOLTAR):
            return
        try:
            acoes[esc]()
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------- menu

def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit('O menu precisa de um terminal interativo (abra pelo SSH normal, não por pipe ou cron).')
    acoes = {'atualizar': acao_atualizar, 'recalcular': acao_recalcular, 'skus': acao_skus, 'meta': acao_meta,
             'meta_atacado': acao_meta_atacado,
             'autorizar': acao_autorizar, 'avancado': acao_avancado}
    while True:
        cabecalho()
        opcoes = []
        if not token()[0]:
            opcoes.append(Choice('Autorizar o app no ML', 'autorizar'))
        opcoes += [
            Choice('Continuar a atualização' if coleta_parada() else
                   'Atualizar margens (busca no ML e gera a planilha)', 'atualizar'),
            Choice('Recalcular sem buscar no ML (após mudar preços ou apelidos)', 'recalcular'),
            Choice('Comparar SKUs da tabela com os anúncios (sem anúncio / sem solo)', 'skus'),
            Choice(f'Mudar a margem alvo ({pct(margem_meta())})', 'meta'),
            Choice(f'Mudar a margem do atacado ({pct(margem_atacado())})', 'meta_atacado'),
            Separator(),
            Choice('Avançado...', 'avancado'),
            Choice('Sair', 'sair'),
        ]
        esc = questionary.select('O que fazer?', choices=opcoes, style=ESTILO, instruction='(setas e Enter)').ask()
        if esc in (None, 'sair'):
            break
        try:
            acoes[esc]()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
