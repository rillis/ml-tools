"""Tarefas demoradas do site (busca no ML, cálculo da margem, comparador de SKUs).

Cada tarefa roda os mesmos módulos do menu (python -m ...) num processo separado, um de cada vez.
A saída vai para data/web-job.log; o estado da última tarefa fica em data/web-job.json,
então o resultado continua na tela depois de recarregar a página ou reiniciar o container.
"""
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ESTADO = BASE / 'data' / 'web-job.json'
LOG = BASE / 'data' / 'web-job.log'
MARGEM_SAIDAS = BASE / 'margem' / 'saidas'
SKUS_SAIDAS = BASE / 'skus' / 'saidas'

# barra do tqdm:  "Anúncios:  45%|####5     | 450/1000 [...]"
RE_BARRA = re.compile(r'^\s*([^:]{1,30}):\s+\d+%\|.*?\|\s*(\d+)/(\d+)')
RE_ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

_lock = threading.Lock()
_proc = None
_parar = False
_estado = None


class Ocupado(Exception):
    pass


def _agora():
    return datetime.now().isoformat(timespec='seconds')


def _salvar():
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ESTADO.with_suffix('.tmp')
    tmp.write_text(json.dumps(_estado, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, ESTADO)


def _carregar():
    global _estado
    try:
        _estado = json.loads(ESTADO.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        _estado = None
        return
    if _estado.get('estado') == 'rodando':  # o container reiniciou no meio
        _estado.update(estado='parado', fim=_agora(),
                       mensagem='O serviço reiniciou no meio da tarefa. O que já foi buscado está salvo.')
        _salvar()


def _mais_recente(padrao):
    return max(glob.glob(str(padrao)), key=os.path.getmtime, default=None)


def _passos(tipo, opcoes):
    """[(descrição, função que devolve os argumentos do python -m)] de cada tipo de tarefa."""
    if tipo == 'atualizar':
        coleta = ['-m', 'margem.coleta', '--status', opcoes.get('status') or 'todos']
        if opcoes.get('novo'):
            coleta.append('--novo')
        return [('Buscando os anúncios no ML', lambda: coleta),
                ('Calculando custo e margem', lambda: ['-m', 'margem.custo'])]
    if tipo == 'recalcular':
        extra = ['--coleta', str(MARGEM_SAIDAS / opcoes['coleta'])] if opcoes.get('coleta') else []
        return [('Calculando custo e margem', lambda: ['-m', 'margem.custo', *extra])]
    if tipo == 'teste':
        return [('Buscando os anúncios do teste', lambda: ['-m', 'margem.coleta', '--ids', opcoes['ids']]),
                ('Calculando custo e margem do teste',
                 lambda: ['-m', 'margem.custo', '--coleta', _mais_recente(MARGEM_SAIDAS / 'teste-*.xlsx')])]
    if tipo == 'parcial':
        return [('Buscando os anúncios escolhidos no ML', lambda: ['-m', 'margem.coleta', '--atualizar', opcoes['ids']]),
                ('Calculando custo e margem', lambda: ['-m', 'margem.custo'])]
    if tipo == 'skus':
        return [('Comparando os SKUs com os anúncios', lambda: ['-m', 'skus'])]
    if tipo == 'pares':
        extra = ['--ids', opcoes['ids']] if opcoes.get('ids') else []
        return [('Procurando anúncios sem a outra opção de venda', lambda: ['-m', 'clonar.pares', *extra])]
    if tipo == 'gtin':
        return [('Corrigindo o GTIN duplicado', lambda: ['-m', 'clonar.gtin', '--item', opcoes['item'], '--corrigir'])]
    if tipo == 'clonar':
        return [('Criando a opção de venda e cravando a margem', lambda: ['-m', 'clonar.criar', '--itens', opcoes.get('itens') or opcoes['item']]),
                ('Atualizando o novo e o original na margem', lambda: ['-m', 'clonar.margem'])]
    raise ValueError(f'Tarefa desconhecida: {tipo}')


def _resultado(tipo):
    if tipo in ('pares', 'gtin'):
        return None
    if tipo == 'skus':
        return _mais_recente(SKUS_SAIDAS / 'skus-*.xlsx')
    if tipo == 'teste':
        return _mais_recente(MARGEM_SAIDAS / 'custo-teste-*.xlsx')
    return _mais_recente(MARGEM_SAIDAS / 'custo-2*.xlsx')


def _rodar(tipo, passos):
    global _proc
    codigo = 0
    with open(LOG, 'w', encoding='utf-8') as log:
        for n, (descricao, args) in enumerate(passos, 1):
            with _lock:
                if _parar:
                    break
                _estado.update(passo=n, descricao=descricao)
                _salvar()
            log.write(f'== {descricao}\n')
            log.flush()
            _proc = subprocess.Popen([sys.executable, *args()], cwd=BASE, stdout=log, stderr=subprocess.STDOUT,
                                     env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            codigo = _proc.wait()
            if codigo != 0:
                break
    with _lock:
        _proc = None
        if _parar:
            estado, msg = 'parado', 'Parado. O que já foi buscado está salvo: use Continuar para seguir de onde parou.'
        elif codigo != 0:
            estado, msg = 'erro', (ultimas_linhas(1) or ['A tarefa terminou com erro.'])[-1]
        else:
            estado, msg = 'ok', 'Pronto.'
        arq = _resultado(tipo) if estado == 'ok' else None
        _estado.update(estado=estado, fim=_agora(), mensagem=msg, codigo=codigo,
                       arquivo=Path(arq).name if arq else None, log=ultimas_linhas())
        _salvar()


def iniciar(tipo, opcoes=None):
    global _estado, _parar
    opcoes = opcoes or {}
    passos = _passos(tipo, opcoes)
    with _lock:
        if _estado and _estado.get('estado') == 'rodando':
            raise Ocupado('Já tem uma tarefa rodando. Espere ela terminar ou pare antes.')
        _parar = False
        _estado = {'id': int(time.time() * 1000), 'tipo': tipo, 'opcoes': opcoes, 'estado': 'rodando',
                   'inicio': _agora(), 'fim': None, 'passo': 0, 'passos': len(passos),
                   'descricao': passos[0][0], 'mensagem': '', 'arquivo': None, 'visto': False}
        _salvar()
    threading.Thread(target=_rodar, args=(tipo, passos), daemon=True).start()
    return atual()


def parar():
    global _parar
    with _lock:
        if not _estado or _estado.get('estado') != 'rodando':
            return False
        _parar = True
        if _proc and _proc.poll() is None:
            _proc.send_signal(signal.SIGINT)  # o módulo trata o Ctrl+C e salva o que já fez
        return True


def marcar_visto():
    with _lock:
        if _estado and _estado.get('estado') != 'rodando':
            _estado['visto'] = True
            _salvar()


def _linhas_log():
    try:
        with open(LOG, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16000))
            texto = f.read().decode('utf-8', 'replace')
    except FileNotFoundError:
        return []
    return [RE_ANSI.sub('', p).rstrip() for p in re.split(r'[\r\n]+', texto) if p.strip()]


def ultimas_linhas(n=12):
    """Últimas linhas do log, sem as barras de progresso."""
    return [l for l in _linhas_log() if not RE_BARRA.match(l) and not l.startswith('== ')][-n:]


def progresso():
    for l in reversed(_linhas_log()):
        m = RE_BARRA.match(l)
        if m:
            return {'rotulo': m.group(1).strip(), 'feitos': int(m.group(2)), 'total': int(m.group(3))}
        if l.startswith('== '):
            return None  # passo novo ainda sem barra
    return None


def atual():
    with _lock:
        if not _estado:
            return None
        e = dict(_estado)
    if e['estado'] == 'rodando':
        e['progresso'] = progresso()
        e['log'] = ultimas_linhas()
    return e


_carregar()
