"""Menu do ml-tools: setas para escolher, Enter para confirmar, Ctrl+C para voltar.

Uso (no servidor):  ./run.sh   (ou .venv/bin/python menu.py)

A tela inicial lista os módulos; cada um abre um submenu:
  - Margem e preço sugerido: margem.coleta e margem.custo, margens alvo e o "Avançado" da coleta
  - Comparar SKUs com os anúncios: skus
  - Somar SKUs do PDF de envio: envio
  - Acesso ao ML e configurações: comum.autorizar e o limite de requisições
Os módulos rodam com  python -m <módulo>  na raiz do projeto.
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
MARGEM = BASE / 'margem'
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
    """Estado da coleta em margem/data/margem.db: None, ou dict com inicio, feitos, total, concluida."""
    db_path = MARGEM / 'data' / 'margem.db'
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
    return sorted(glob.glob(str(MARGEM / 'saidas' / f'{prefixo}-*.xlsx')), reverse=True)


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
                  else 'ok, renova na próxima chamada')


def cabecalho(secao=None):
    print('\033[2J\033[H', end='')  # limpa a tela
    print(f'{NEGRITO} ml-tools{FIM}' + (f'  {CINZA}›{FIM}  {NEGRITO}{secao}{FIM}' if secao
                                        else f'  {CINZA}ferramentas para os anúncios do ML{FIM}') + '\n')
    if secao == 'Margem':
        ult = planilhas('custo')[:1]
        print(f'  Última planilha  {Path(ult[0]).name + "  (" + quando(ult[0]) + ")" if ult else "nenhuma ainda"}')
        print(f'  Margem alvo      {pct(margem_meta())}  (atacado {pct(margem_atacado())})\n')
    c = coleta_parada()
    if c:
        print(f'  {AMARELO}! Atualização da margem interrompida em {c["feitos"]}/{c["total"]} anúncios: '
              f'dá para continuar de onde parou.{FIM}\n')
    ok, desc = token()
    if not ok:
        print(f'  {AMARELO}! Acesso ao ML: {desc}.{FIM}\n')


def submenu(secao, pergunta, opcoes, acoes):
    """Repete o submenu até escolher Voltar. opcoes é uma função, para os rótulos se atualizarem."""
    while True:
        cabecalho(secao)
        esc = questionary.select(pergunta, choices=opcoes() + [Separator(), Choice('Voltar', VOLTAR)],
                                 style=ESTILO).ask()
        if esc in (None, VOLTAR):
            return
        try:
            acoes[esc]()
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------- ações comuns

def rodar(titulo, *args):
    """Roda um módulo com a saída direto na tela. Ctrl+C interrompe só o módulo."""
    print(f'\n{NEGRITO}{titulo}{FIM}\n')
    proc = subprocess.Popen([PY, *args], cwd=BASE)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.wait()  # o módulo também recebe o Ctrl+C e salva o que fez
    return proc.returncode


def pausa():
    print()
    questionary.press_any_key_to_continue('Pressione qualquer tecla para voltar ao menu...').ask()


def numero_valido(minimo, maximo):
    def v(t):
        try:
            x = float(t.replace(',', '.'))
        except ValueError:
            return 'Digite um número'
        return minimo <= x <= maximo or f'Use um valor entre {minimo:g} e {maximo:g}'
    return v


# ---------------------------------------------------------------- margem

def coletar_e_calcular(*args_coleta):
    """Coleta e, se a coleta terminar bem, já gera a planilha de margem."""
    if rodar('Buscando os anúncios no ML...', '-m', 'margem.coleta', *args_coleta) != 0:
        print(f'\n{AMARELO}A busca parou antes do fim. O que já foi feito está salvo: '
              f'escolha "Continuar a atualização" no menu da margem.{FIM}')
        return pausa()
    if rodar('Calculando custo e margem...', '-m', 'margem.custo') == 0:
        print(f'\n{VERDE}Pronto!{FIM}')
    pausa()


def mg_atualizar():
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
    # mesmo status da busca parada: com outro status a coleta descartaria o progresso
    coletar_e_calcular('--status', c['status'], *(['--novo'] if esc == 'novo' else []))


def mg_recalcular():
    if not planilhas('margem'):
        print('Ainda não há anúncios buscados: use "Atualizar margens" primeiro.')
        return pausa()
    rodar('Calculando custo e margem com a última busca...', '-m', 'margem.custo')
    pausa()


def mg_meta(chave='MARGEM_META', atual=None, nome='margem alvo', exemplo='18'):
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
        mg_recalcular()


def mg_meta_atacado():
    mg_meta('MARGEM_META_ATACADO', margem_atacado(), 'margem do atacado', '17.5')


def mg_status():
    esc = questionary.select('Quais anúncios buscar?', choices=[
        Choice('Só ativos', 'active'), Choice('Só pausados', 'paused'), Choice('Voltar', VOLTAR)],
        style=ESTILO).ask()
    if esc not in (None, VOLTAR):
        coletar_e_calcular('--status', esc)


def mg_teste():
    ids = questionary.text('Códigos dos anúncios, separados por vírgula (ex.: MLB6926478482,MLB6624766636):',
                           validate=lambda t: bool(re.fullmatch(r'\s*(MLB)?\d+(\s*,\s*(MLB)?\d+)*\s*', t, re.I))
                           or 'Use códigos como MLB6926478482, separados por vírgula',
                           style=ESTILO).ask()
    if not ids:
        return
    rodar('Testando com os anúncios informados...', '-m', 'margem.coleta', '--ids', re.sub(r'\s+', '', ids))
    pausa()


def mg_coleta_antiga():
    coletas = planilhas('margem')
    if not coletas:
        print('Ainda não há anúncios buscados.')
        return pausa()
    escolhas = [Choice(f'{Path(a).name}  ({quando(a)})' + ('  ← mais recente' if i == 0 else ''), a)
                for i, a in enumerate(coletas[:10])]
    arq = questionary.select('Com qual busca?', choices=escolhas + [Choice('Voltar', VOLTAR)], style=ESTILO).ask()
    if arq in (None, VOLTAR):
        return
    rodar('Calculando custo e margem...', '-m', 'margem.custo', '--coleta', arq)
    pausa()


def mg_exportar():
    if coleta_atual() is None:
        print('Nenhuma busca para exportar.')
        return pausa()
    rodar('Gerando a planilha da busca atual...', '-m', 'margem.coleta', '--exportar')
    pausa()


def mg_log():
    log = MARGEM / 'logs' / 'margem.log'
    if not log.exists():
        print('Ainda não há log.')
    else:
        linhas = log.read_text(encoding='utf-8', errors='replace').splitlines()
        print('\n'.join(linhas[-40:]))
    pausa()


def mg_avancado():
    submenu('Margem › Avançado', 'Avançado:', lambda: [
        Choice('Atualizar só os ativos ou só os pausados', 'status'),
        Choice('Testar com alguns anúncios', 'teste'),
        Choice('Recalcular com uma busca mais antiga', 'antiga'),
        Choice('Gerar a planilha da busca atual (sem chamar o ML)', 'exportar'),
        Choice('Ver o log recente', 'log'),
    ], {'status': mg_status, 'teste': mg_teste, 'antiga': mg_coleta_antiga, 'exportar': mg_exportar,
        'log': mg_log})


def menu_margem():
    submenu('Margem', 'Margem e preço sugerido:', lambda: [
        Choice('Continuar a atualização' if coleta_parada() else
               'Atualizar margens (busca no ML e gera a planilha)', 'atualizar'),
        Choice('Recalcular sem buscar no ML (após mudar preços ou apelidos)', 'recalcular'),
        Choice(f'Mudar a margem alvo ({pct(margem_meta())})', 'meta'),
        Choice(f'Mudar a margem do atacado ({pct(margem_atacado())})', 'meta_atacado'),
        Separator(),
        Choice('Avançado...', 'avancado'),
    ], {'atualizar': mg_atualizar, 'recalcular': mg_recalcular, 'meta': mg_meta,
        'meta_atacado': mg_meta_atacado, 'avancado': mg_avancado})


# ---------------------------------------------------------------- skus e envio

def acao_skus():
    rodar('Comparando os SKUs da tabela com os anúncios do ML...', '-m', 'skus')
    pausa()


def acao_envio():
    rodar('Somando os SKUs do PDF de envio (envio/input/)...', '-m', 'envio')
    pausa()


# ---------------------------------------------------------------- acesso e configurações

def acao_autorizar():
    if rodar('Gerando a URL de autorização...', '-m', 'comum.autorizar') != 0:
        return pausa()
    url = questionary.text('Depois de autorizar, cole aqui a URL para onde o ML redirecionou (vazio cancela):',
                           style=ESTILO).ask()
    if url:
        rodar('Salvando o token...', '-m', 'comum.autorizar', url.strip())
    pausa()


def cfg_acesso():
    ok, desc = token()
    print(f'Acesso ao ML: {desc}.')
    print('Só é preciso autorizar de novo se o ML recusar o acesso (ex.: 6 meses sem uso, permissão revogada).')
    if questionary.confirm('Gerar a URL de autorização?', default=not ok, style=ESTILO).ask():
        acao_autorizar()


def cfg_rps():
    env = ler_env()
    t = questionary.text('Requisições por segundo na API do ML (ex.: 4):', default=env.get('ML_MAX_RPS', '2'),
                         validate=numero_valido(0.5, 10), style=ESTILO).ask()
    if t:
        gravar_env('ML_MAX_RPS', f'{float(t.replace(",", ".")):g}')
        print('Salvo. Vale a partir da próxima busca. Fique de olho nos "429 recebidos" no fim dela.')
        pausa()


def menu_config():
    submenu('Acesso ao ML e configurações', 'Configurações:', lambda: [
        Choice('Acesso ao ML (token e reautorização)', 'acesso'),
        Choice(f'Limite de requisições por segundo: {ler_env().get("ML_MAX_RPS", "2")}', 'rps'),
    ], {'acesso': cfg_acesso, 'rps': cfg_rps})


# ---------------------------------------------------------------- menu principal

def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit('O menu precisa de um terminal interativo (abra pelo SSH normal, não por pipe ou cron).')
    acoes = {'autorizar': acao_autorizar, 'margem': menu_margem, 'skus': acao_skus, 'envio': acao_envio,
             'config': menu_config}
    while True:
        cabecalho()
        opcoes = []
        if not token()[0]:
            opcoes.append(Choice('Autorizar o app no ML', 'autorizar'))
        opcoes += [
            Choice('Margem e preço sugerido...', 'margem'),
            Choice('Comparar SKUs da tabela com os anúncios (sem anúncio / sem solo)', 'skus'),
            Choice('Somar os SKUs do PDF de envio (envio/input/)', 'envio'),
            Separator(),
            Choice('Acesso ao ML e configurações...', 'config'),
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
