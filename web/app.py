"""API do ml-tools para a aba Margem do ml-app (que repassa as chamadas para cá)."""
import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import clonar
from clonar import gtin as clonar_gtin
from comum import autorizar, ml
from comum.env import PARTES_META, gravar_env, ler_env, margem_meta, partes_meta
from comum.tabela import APELIDOS, PASTA as TABELAS, PRECOS, Tabela, carregar_apelidos, carregar_precos
from envio import somar
from margem import custo as margem_custo
from web import jobs

log = logging.getLogger('web')
BASE = ml.BASE
SAIDAS = {'margem': BASE / 'margem' / 'saidas', 'skus': BASE / 'skus' / 'saidas'}
TABELAS_ARQ = {'precos': PRECOS, 'apelidos': APELIDOS}
ANTERIORES = BASE / 'data' / 'tabelas-anteriores'
ENVIO = BASE / 'envio' / 'input'
RE_PLANILHA = re.compile(r'^[\w-]+\.xlsx$')
RE_IDS = re.compile(r'^(MLB)?\d+(,(MLB)?\d+)*$', re.I)


def _quando(arq):
    return datetime.fromtimestamp(os.path.getmtime(arq)).isoformat(timespec='seconds')


def _ultimo_json(pasta, padrao):
    arq = max(glob.glob(str(pasta / padrao)), key=os.path.getmtime, default=None)
    if not arq:
        return None
    return json.loads(Path(arq).read_text(encoding='utf-8'))


def _coleta_atual():
    """Estado da coleta em margem/data/margem.db (como no menu)."""
    db_path = BASE / 'margem' / 'data' / 'margem.db'
    if not db_path.exists():
        return None
    try:
        db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        meta = dict(db.execute('SELECT k, v FROM meta').fetchall())
        total, feitos = db.execute('SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM itens').fetchone()
        db.close()
    except sqlite3.Error:
        return None
    return {'inicio': meta.get('inicio'), 'feitos': feitos, 'total': total,
            'concluida': meta.get('concluida') == '1', 'status': meta.get('status', 'todos')}


def _token():
    arq = ml.TOKENS_FILE
    if not arq.exists():
        return {'ok': False, 'texto': 'O app ainda não foi autorizado no ML.'}
    try:
        t = json.loads(arq.read_text(encoding='utf-8'))
    except ValueError:
        return {'ok': False, 'texto': 'O arquivo do token está ilegível: autorize o app de novo.'}
    if not t.get('refresh_token'):
        return {'ok': False, 'texto': 'O token não renova sozinho: autorize o app de novo.'}
    resto = t.get('expires_at', 0) - time.time()
    return {'ok': True, 'user_id': t.get('user_id'),
            'texto': f'Ok, renova sozinho (o atual vale mais {resto / 3600:.1f} h).' if resto > 0
            else 'Ok, renova na próxima chamada.'}


def _planilhas(modulo):
    arqs = sorted(glob.glob(str(SAIDAS[modulo] / '*.xlsx')), key=os.path.getmtime, reverse=True)
    return [{'nome': Path(a).name, 'quando': _quando(a), 'tamanho': os.path.getsize(a)}
            for a in arqs if not Path(a).name.startswith('~$')][:30]


def _tabela_info(nome):
    arq = TABELAS_ARQ[nome]
    if not arq.exists():
        return {'nome': arq.name, 'existe': False}
    try:
        n = len(carregar_precos(arq)) if nome == 'precos' else len(carregar_apelidos(arq))
    except Exception as e:  # planilha corrompida
        n, erro = None, str(e)
    else:
        erro = None
    return {'nome': arq.name, 'existe': True, 'quando': _quando(arq), 'linhas': n, 'erro': erro}


_cache_tabela = {}


def _tabela():
    """Tabela de preços + apelidos, lida de novo só quando um dos arquivos muda."""
    chave = tuple(os.path.getmtime(a) if a.exists() else 0 for a in (PRECOS, APELIDOS))
    if _cache_tabela.get('chave') != chave:
        _cache_tabela.update(chave=chave, tab=Tabela(carregar_precos(PRECOS), carregar_apelidos(APELIDOS)))
    return _cache_tabela['tab']


def _valor(valor, nome, minimo=0.0, maximo=100000.0):
    """Número digitado no site ("13,85", vazio = 0)."""
    texto = str(valor if valor is not None else '').strip().replace('R$', '').replace(' ', '')
    if not texto:
        return 0.0
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        x = float(texto)
    except ValueError:
        raise ValueError(f'{nome}: digite um número') from None
    if not minimo <= x <= maximo:
        raise ValueError(f'{nome}: use um valor entre {minimo:g} e {maximo:g}')
    return x


def _pct(valor, minimo, maximo):
    try:
        x = float(str(valor).replace(',', '.'))
    except ValueError:
        raise ValueError('Digite um número') from None
    if not minimo <= x <= maximo:
        raise ValueError(f'Use um valor entre {minimo:g} e {maximo:g}')
    return x


def create_app():
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

    @app.after_request
    def _sem_cache(resp):
        if request.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-store'
        return resp

    def _corpo():
        return request.get_json(silent=True) or {}

    def _erro(texto, codigo=400):
        return jsonify({'erro': texto}), codigo

    # ------------------------------------------------------------ estado geral

    @app.get('/api/estado')
    def api_estado():
        env = ler_env()
        coletas = sorted(glob.glob(str(SAIDAS['margem'] / 'margem-*.xlsx')), key=os.path.getmtime, reverse=True)
        return jsonify({
            'job': jobs.atual(),
            'coleta': _coleta_atual(),
            'coletas': [{'nome': Path(a).name, 'quando': _quando(a)} for a in coletas[:10]],
            'meta': margem_meta(env), 'partes': partes_meta(env),
            'rps': float(env.get('ML_MAX_RPS', '2').replace(',', '.')),
            'token': _token(),
        })

    # ------------------------------------------------------------ tarefas

    @app.get('/api/job')
    def api_job():
        return jsonify({'job': jobs.atual()})

    @app.post('/api/job')
    def api_job_iniciar():
        c = _corpo()
        tipo = c.get('tipo')
        opcoes = {}
        if tipo == 'atualizar':
            status = c.get('status') or 'todos'
            if status not in ('todos', 'active', 'paused'):
                return _erro('Status inválido')
            opcoes = {'status': status, 'novo': bool(c.get('novo'))}
        elif tipo == 'recalcular':
            if not glob.glob(str(SAIDAS['margem'] / 'margem-*.xlsx')):
                return _erro('Ainda não há anúncios buscados: use Atualizar margens primeiro.')
            if c.get('coleta'):
                if not RE_PLANILHA.match(c['coleta']) or not (SAIDAS['margem'] / c['coleta']).exists():
                    return _erro('Busca não encontrada')
                opcoes = {'coleta': c['coleta']}
        elif tipo == 'teste':
            ids = re.sub(r'\s+', '', str(c.get('ids') or '')).upper()
            if not RE_IDS.match(ids) or len(ids.split(',')) > 20:
                return _erro('Use até 20 códigos como MLB6926478482, separados por vírgula')
            opcoes = {'ids': ids}
        elif tipo == 'parcial':
            # códigos com ou sem MLB (MLB123, MLB-123 ou só 123), separados por vírgula, espaço ou linha;
            # repetidos contam uma vez
            ids = list(dict.fromkeys('MLB' + n for n in re.findall(r'(?:MLB-?)?(\d{6,})', str(c.get('ids') or '').upper())))
            if not ids:
                return _erro('Cole os códigos dos anúncios (com ou sem MLB)')
            if len(ids) > 200:
                return _erro('Use até 200 anúncios por vez; para mais, use Atualizar margens')
            col = _coleta_atual()
            if not col:
                return _erro('Ainda não há uma busca completa: use Atualizar margens primeiro.')
            if not col['concluida']:
                return _erro('A última busca completa parou no meio: continue ela antes.')
            opcoes = {'ids': ','.join(ids)}
        elif tipo == 'pares':
            ids = list(dict.fromkeys(re.findall(r'MLB\d+', str(c.get('ids') or '').upper())))
            opcoes = {'ids': ','.join(ids[:20])} if ids else {}
        elif tipo == 'clonar':
            itens = list(dict.fromkeys(re.findall(r'MLB\d+', ' '.join(map(str, c.get('itens') or [c.get('item') or '']))
                                                  .upper())))
            if not itens:
                return _erro('Escolha os anúncios')
            if len(itens) > 20:
                return _erro('Até 20 anúncios por vez')
            opcoes = {'itens': ','.join(itens)}
        elif tipo == 'formato':
            item = str(c.get('item') or '').strip().upper()
            if not re.fullmatch(r'MLB\d+', item):
                return _erro('Anúncio inválido')
            if c.get('formato') not in ('unidade', 'pack'):
                return _erro('Escolha Unidade ou Kit/pack')
            try:
                qtd = int(str(c.get('qtd')).strip())
            except ValueError:
                return _erro('Quantidade: digite um número inteiro')
            if not 1 <= qtd <= 100:
                return _erro('Quantidade de 1 a 100')
            opcoes = {'itens': item, 'formato': c['formato'], 'qtd': qtd}
        elif tipo == 'gtin':
            item = str(c.get('item') or '').strip().upper()
            if not re.fullmatch(r'MLB\d+', item):
                return _erro('Anúncio inválido')
            opcoes = {'item': item}
        elif tipo != 'skus':
            return _erro('Tarefa desconhecida')
        if tipo != 'recalcular' and not _token()['ok']:
            return _erro('Autorize o app no ML antes (aba Acesso).', 409)
        try:
            return jsonify({'job': jobs.iniciar(tipo, opcoes)}), 202
        except jobs.Ocupado as e:
            return _erro(str(e), 409)

    @app.post('/api/job/parar')
    def api_job_parar():
        return jsonify({'ok': jobs.parar()})

    @app.post('/api/job/visto')
    def api_job_visto():
        jobs.marcar_visto()
        return jsonify({'ok': True})

    # ------------------------------------------------------------ resultados

    @app.get('/api/margem')
    def api_margem():
        return jsonify({'resultado': _ultimo_json(SAIDAS['margem'], 'custo-2*.json')})

    @app.get('/api/margem/teste')
    def api_margem_teste():
        return jsonify({'resultado': _ultimo_json(SAIDAS['margem'], 'custo-teste-*.json')})

    @app.get('/api/skus')
    def api_skus():
        return jsonify({'resultado': _ultimo_json(SAIDAS['skus'], 'skus-2*.json')})

    @app.get('/api/planilhas/<modulo>')
    def api_planilhas(modulo):
        if modulo not in SAIDAS:
            abort(404)
        return jsonify({'planilhas': _planilhas(modulo)})

    @app.get('/arquivos/<modulo>/<nome>')
    def arquivo(modulo, nome):
        if modulo not in SAIDAS or not RE_PLANILHA.match(nome):
            abort(404)
        return send_from_directory(SAIDAS[modulo], nome, as_attachment=True)

    # ------------------------------------------------------------ clonar

    @app.get('/api/clonar')
    def api_clonar():
        pares = clonar.ler_json(clonar.PARES, None)
        hist = clonar.ler_json(clonar.HISTORICO, [])
        if pares:
            # some da lista o que já foi clonado depois da busca (o par existe agora)
            feitos = {h.get('up') for h in hist if h.get('novo') and (h.get('inicio') or '') >= pares['gerado']}
            pares['linhas'] = [l for l in pares['linhas'] if l['up'] not in feitos]
        return jsonify({'pares': pares, 'historico': hist[:100]})

    @app.get('/api/clonar/gtin')
    def api_clonar_gtin():
        """Plano de correção do GTIN duplicado de um anúncio (só lê o ML)."""
        item = str(request.args.get('item') or '').strip().upper()
        if not re.fullmatch(r'MLB\d+', item):
            return _erro('Anúncio inválido')
        if not _token()['ok']:
            return _erro('Autorize o app no ML antes (aba Acesso).', 409)
        try:
            c = ml.MLClient()
            return jsonify({'plano': clonar_gtin.plano(c, ml.read_tokens().get('user_id') or c.get('/users/me')['id'], item)})
        except clonar_gtin.Falha as e:
            return _erro(str(e))
        except ml.MLError as e:
            return _erro(f'Erro no ML: {e}', 502)

    @app.get('/api/clonar/andamento')
    def api_clonar_andamento():
        return jsonify({'andamento': clonar.ler_json(clonar.ANDAMENTO, None)})

    # ------------------------------------------------------------ simulador

    @app.post('/api/simular')
    def api_simular():
        c = _corpo()
        sku = str(c.get('sku') or '').strip()
        if not sku:
            return _erro('Digite o SKU')
        if len(sku) > 300:
            return _erro('SKU longo demais')
        if not PRECOS.exists():
            return _erro('Envie a tabela de preços antes (aba Tabelas).', 409)
        try:
            taxa = _valor(c.get('taxa'), 'Taxa', 0, 60) / 100
            frete = _valor(c.get('frete'), 'Frete', 0, 10000)
            adc = _valor(c.get('adc'), 'Adc', 0, 10000)
            env = ler_env()
            return jsonify(margem_custo.simular(_tabela(), sku, taxa, frete, adc,
                                                margem_meta(env), partes_meta(env)['imposto']))
        except ValueError as e:
            return _erro(str(e))

    # ------------------------------------------------------------ configurações

    @app.post('/api/config')
    def api_config():
        c = _corpo()
        try:
            # partes da margem alvo: lucro + gordura + imposto (confere a soma antes de gravar)
            novas = {**partes_meta(), **{n: _pct(c[n], 0, 40) / 100 for n in PARTES_META if n in c}}
            if sum(novas.values()) >= 0.7:
                return _erro('A soma de lucro, gordura e imposto ficou alta demais')
            for nome, (chave, _) in PARTES_META.items():
                if nome in c:
                    gravar_env(chave, f'{novas[nome]:g}')
            if 'rps' in c:
                gravar_env('ML_MAX_RPS', f'{_pct(c["rps"], 0.5, 10):g}')
        except ValueError as e:
            return _erro(str(e))
        return api_estado()

    # ------------------------------------------------------------ tabelas

    @app.get('/api/tabelas')
    def api_tabelas():
        return jsonify({k: _tabela_info(k) for k in TABELAS_ARQ})

    @app.get('/tabelas/<nome>')
    def tabela_baixar(nome):
        if nome not in TABELAS_ARQ or not TABELAS_ARQ[nome].exists():
            abort(404)
        return send_from_directory(TABELAS, TABELAS_ARQ[nome].name, as_attachment=True)

    @app.post('/api/tabelas/<nome>')
    def tabela_trocar(nome):
        if nome not in TABELAS_ARQ:
            abort(404)
        f = request.files.get('arquivo')
        if not f or not f.filename.lower().endswith('.xlsx'):
            return _erro('Envie um arquivo .xlsx')
        ANTERIORES.mkdir(parents=True, exist_ok=True)
        novo = ANTERIORES / f'{nome}-recebido.tmp.xlsx'
        f.save(novo)
        try:
            n = len(carregar_precos(novo)) if nome == 'precos' else len(carregar_apelidos(novo))
        except Exception as e:
            novo.unlink(missing_ok=True)
            return _erro(f'Não consegui ler a planilha: {e}')
        if nome == 'precos' and n == 0:
            novo.unlink(missing_ok=True)
            return _erro('Nenhum SKU com preço na planilha (colunas SKU e PRECO, com cabeçalho na linha 1)')
        destino = TABELAS_ARQ[nome]
        if destino.exists():  # guarda a versão anterior antes de trocar
            shutil.copy2(destino, ANTERIORES / f'{nome}-{datetime.now():%Y-%m-%d_%H%M%S}.xlsx')
        os.replace(novo, destino)
        log.warning('Tabela %s trocada pelo site (%s linhas)', destino.name, n)
        return jsonify({nome: _tabela_info(nome)})

    # ------------------------------------------------------------ envio

    @app.post('/api/envio')
    def api_envio():
        f = request.files.get('arquivo')
        if not f or not f.filename.lower().endswith('.pdf'):
            return _erro('Envie o PDF de instruções de preparação do envio')
        ENVIO.mkdir(parents=True, exist_ok=True)
        nome = secure_filename(f.filename) or f'envio-{datetime.now():%Y-%m-%d_%H%M%S}.pdf'
        arq = ENVIO / nome
        f.save(arq)
        try:
            frete, linhas = somar.ler(arq)
        except Exception as e:  # PDF ilegível ou fora do formato
            return _erro(f'{nome}: {e}', 422)
        total = somar.somar(linhas)
        return jsonify({
            'arquivo': nome, 'frete': frete, 'produtos': len(linhas), 'unidades': sum(q for _, q in linhas),
            'skus': [{'sku': s, 'qtd': total[s]} for s in sorted(total)],
        })

    # ------------------------------------------------------------ acesso ao ML

    @app.post('/api/acesso/url')
    def acesso_url():
        if autorizar.faltando():
            return _erro(f'Preencha no .env do ml-tools-web: {", ".join(autorizar.faltando())}', 409)
        return jsonify({'url': autorizar.url_autorizacao()})

    @app.post('/api/acesso/concluir')
    def acesso_concluir():
        retorno = str(_corpo().get('retorno') or '').strip()
        if not retorno:
            return _erro('Cole a URL para onde o ML redirecionou')
        try:
            me = autorizar.concluir(retorno)
        except (ValueError, ml.MLError) as e:
            return _erro(str(e))
        return jsonify({'nickname': me.get('nickname'), 'id': me.get('id'), 'token': _token()})

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok'})

    return app
