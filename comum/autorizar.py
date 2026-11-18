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


def url_autorizacao():
    """Gera a URL de autorização e guarda o state (e o code_verifier, com PKCE) para conferir na volta."""
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
    return ml.AUTH_URL + '?' + urllib.parse.urlencode(params)


def concluir(arg):
    """Troca o code (ou a URL de retorno inteira) pelos tokens. Devolve o /users/me; ValueError se falhar."""
    pending = json.loads(PENDING.read_text(encoding='utf-8')) if PENDING.exists() else {}
    arg = arg.strip()
    if arg.startswith('http'):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        code = (qs.get('code') or [''])[0]
        state = (qs.get('state') or [''])[0]
        if pending.get('state') and state != pending['state']:
            raise ValueError('O state não confere com a URL gerada: gere uma URL nova e autorize de novo')
    else:
        code = arg
    if not code:
        raise ValueError('Não achei o code= na URL')

    fields = {'grant_type': 'authorization_code', 'client_id': ml.APP_ID, 'client_secret': ml.SECRET,
              'code': code, 'redirect_uri': ml.REDIRECT_URI}
    if pending.get('code_verifier'):
        fields['code_verifier'] = pending['code_verifier']
    try:
        js = ml._post_form(ml.TOKEN_URL, fields)
    except ml.MLError as e:
        raise ValueError(f'O ML recusou a troca do code: {e}') from None
    ml.save_tokens(js)
    PENDING.unlink(missing_ok=True)
    return ml.MLClient().get('/users/me')


def faltando():
    return [k for k in ('ML_APP_ID', 'ML_SECRET_KEY', 'ML_REDIRECT_URI') if not ml.ENV.get(k)]


def main():
    if faltando():
        sys.exit(f'Preencha no .env: {", ".join(faltando())}  (modelo em .env.example)')
    if len(sys.argv) > 1:
        try:
            me = concluir(sys.argv[1])
        except ValueError as e:
            sys.exit(str(e))
        print(f'Autorizado: {me.get("nickname")} (id {me.get("id")}). Tokens gravados em data/tokens.json')
    else:
        print('Abra esta URL logado na conta principal da loja:\n')
        print(url_autorizacao())
        print('\nDepois rode:  .venv/bin/python -m comum.autorizar "<URL para onde o ML redirecionou>"')


if __name__ == '__main__':
    main()
