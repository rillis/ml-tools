"""Cliente da API do Mercado Livre do ml-tools (app separado do ml-jrf).

- Lê ML_APP_ID, ML_SECRET_KEY e ML_REDIRECT_URI do .env da raiz do projeto.
- Guarda os tokens em data/tokens.json e renova sozinho quando o access token vence.
- Faz no máximo ML_MAX_RPS requisições por segundo e espera quando o ML responde 429.
"""
import fcntl
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # raiz do projeto
API = 'https://api.mercadolibre.com'
TOKEN_URL = API + '/oauth/token'
AUTH_URL = 'https://auth.mercadolivre.com.br/authorization'


def load_env(path=BASE / '.env'):
    env = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
APP_ID = ENV.get('ML_APP_ID', '')
SECRET = ENV.get('ML_SECRET_KEY', '')
REDIRECT_URI = ENV.get('ML_REDIRECT_URI', '')
MAX_RPS = float(ENV.get('ML_MAX_RPS', '2'))
TOKENS_FILE = BASE / 'data' / 'tokens.json'

log = logging.getLogger('margem.ml')  # filho do log da coleta: os avisos vão para o margem.log


class MLError(Exception):
    def __init__(self, status, url, body=''):
        super().__init__(f'ML {status} em {url}: {str(body)[:300]}')
        self.status = status
        self.url = url
        self.body = body


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers={
        'accept': 'application/json', 'content-type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise MLError(e.code, url, e.read().decode('utf-8', 'replace')) from None


def save_tokens(js):
    """Grava access e refresh token (escrita atômica: nunca deixa o arquivo pela metade)."""
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'token': js['access_token'],
        'refresh_token': js['refresh_token'],
        'user_id': js.get('user_id'),
        'expires_at': int(time.time()) + int(js.get('expires_in', 21600)),
    }
    tmp = TOKENS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=4), encoding='utf-8')
    os.replace(tmp, TOKENS_FILE)
    return data


def read_tokens():
    if not TOKENS_FILE.exists():
        raise RuntimeError('Sem tokens: use "Autorizar o app no ML" no menu (ou  python -m comum.autorizar)')
    return json.loads(TOKENS_FILE.read_text(encoding='utf-8'))


def travar_busca():
    """Garante uma busca no ML por vez (menu, linha de comando ou site): todas usam o mesmo token
    e o mesmo limite de requisições. Fica travado até o processo terminar."""
    arq = BASE / 'data' / 'busca.lock'
    arq.parent.mkdir(parents=True, exist_ok=True)
    f = open(arq, 'w')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Já tem uma busca no ML rodando (pelo menu ou pelo site). Espere ela terminar.')
    return f  # manter a referência: fechar o arquivo solta a trava


LOTE = 20  # máximo de anúncios por chamada do /items?ids=


def sku_de(body):
    """SKU do anúncio: atributo SELLER_SKU ou, nos anúncios antigos, o seller_custom_field."""
    for a in body.get('attributes') or []:
        if a.get('id') == 'SELLER_SKU' and a.get('value_name'):
            return a['value_name']
    return body.get('seller_custom_field') or ''


def titulo_de(body):
    """Nome sem a variação (family_name); se o anúncio não tiver, o título completo."""
    return (body.get('family_name') or body.get('title') or '').strip()


class MLClient:
    MAX_ATTEMPTS = 4

    def __init__(self, max_rps=MAX_RPS):
        self._interval = 1.0 / max_rps if max_rps > 0 else 0
        self._last = 0.0
        self._lock = threading.Lock()
        self.contagem = {'requisicoes': 0, '429': 0, 'tentativas': 0}

    def _throttle(self):
        with self._lock:
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def refresh(self):
        with self._lock:
            current = read_tokens()
            js = _post_form(TOKEN_URL, {
                'grant_type': 'refresh_token',
                'client_id': APP_ID,
                'client_secret': SECRET,
                'refresh_token': current['refresh_token'],
            })
            log.info('Token renovado')
            return save_tokens(js)

    def get(self, path, params=None):
        url = path if path.startswith('http') else API + path
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        tokens = read_tokens()
        if tokens.get('expires_at', 0) - 60 < time.time():
            tokens = self.refresh()
        refreshed = False
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._throttle()
            self.contagem['requisicoes'] += 1
            req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tokens['token']})
            curta = url.replace(API, '').split('?')[0]
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', 'replace')
                if e.code == 401 and not refreshed:
                    log.warning(f'401 em {curta}: renovando o token')
                    tokens, refreshed = self.refresh(), True
                    continue
                if e.code == 429:
                    self.contagem['429'] += 1
                # o ML às vezes responde 403 ResourceDeprecated numa chamada que funciona logo depois
                passageiro = e.code == 403 and 'ResourceDeprecated' in body
                if (e.code == 429 or e.code >= 500 or passageiro) and attempt < self.MAX_ATTEMPTS:
                    espera = 5 * attempt  # 429: o ML pede para tentar de novo em alguns segundos
                    self.contagem['tentativas'] += 1
                    log.warning(f'{e.code} em {curta}: esperando {espera}s (tentativa {attempt}/{self.MAX_ATTEMPTS})')
                    time.sleep(espera)
                    continue
                raise MLError(e.code, url, body) from None
            except urllib.error.URLError as e:
                if attempt < self.MAX_ATTEMPTS:
                    self.contagem['tentativas'] += 1
                    log.warning(f'Erro de rede em {curta} ({e.reason}): tentando de novo em {2 * attempt}s')
                    time.sleep(2 * attempt)
                    continue
                raise MLError('rede', url, e) from None
        raise MLError('tentativas esgotadas', url)
