"""Primeira autorização (ou reautorização) do app do ml-tools no Mercado Livre.

1. python -m comum.autorizar     -> mostra a URL; abra logado na CONTA PRINCIPAL da loja
2. Autorize; o ML redireciona para a sua redirect_uri com ?code=...&state=...
3. python -m comum.autorizar "<URL inteira para onde o ML redirecionou>"
   (também aceita só o code, mas aí o state não é conferido)

Com ML_PKCE=1 no .env (só se o PKCE estiver ligado no app), usa code_challenge S256.
"""
import base64
import hashlib
import json
import secrets
import sys
import urllib.parse

from comum import ml

PENDING = ml.BASE / 'data' / 'authorize-pending.json'


def start():
    state = secrets.token_urlsafe(16)
    params = {'response_type': 'code', 'client_id': ml.APP_ID,
              'redirect_uri': ml.REDIRECT_URI, 'state': state}
    pending = {'state': state}
    if ml.ENV.get('ML_PKCE') == '1':
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        params.update(code_challenge=challenge, code_challenge_method='S256')
        pending['code_verifier'] = verifier
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(pending), encoding='utf-8')
    print('Abra esta URL logado na conta principal da loja:\n')
    print(ml.AUTH_URL + '?' + urllib.parse.urlencode(params))
    print('\nDepois rode:  .venv/bin/python -m comum.autorizar "<URL para onde o ML redirecionou>"')


def finish(arg):
    pending = json.loads(PENDING.read_text(encoding='utf-8')) if PENDING.exists() else {}
    if arg.startswith('http'):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        code = (qs.get('code') or [''])[0]
        state = (qs.get('state') or [''])[0]
        if pending.get('state') and state != pending['state']:
            sys.exit('O state não confere com a URL gerada: gere uma URL nova com  .venv/bin/python -m comum.autorizar')
    else:
        code = arg
    if not code:
        sys.exit('Não achei o code= na URL')

    fields = {'grant_type': 'authorization_code', 'client_id': ml.APP_ID, 'client_secret': ml.SECRET,
              'code': code, 'redirect_uri': ml.REDIRECT_URI}
    if pending.get('code_verifier'):
        fields['code_verifier'] = pending['code_verifier']
    try:
        js = ml._post_form(ml.TOKEN_URL, fields)
    except ml.MLError as e:
        sys.exit(f'O ML recusou a troca do code: {e}')
    ml.save_tokens(js)
    PENDING.unlink(missing_ok=True)
    me = ml.MLClient().get('/users/me')
    print(f'Autorizado: {me.get("nickname")} (id {me.get("id")}). Tokens gravados em data/tokens.json')


def main():
    missing = [k for k in ('ML_APP_ID', 'ML_SECRET_KEY', 'ML_REDIRECT_URI') if not ml.ENV.get(k)]
    if missing:
        sys.exit(f'Preencha no .env: {", ".join(missing)}  (modelo em .env.example)')
    if len(sys.argv) > 1:
        finish(sys.argv[1])
    else:
        start()


if __name__ == '__main__':
    main()
