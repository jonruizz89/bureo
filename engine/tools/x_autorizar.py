"""Autoriza la app de Limón en X a publicar en otra cuenta (p. ej. la de Bilbao) y te da su token.

Se ejecuta EN TU ORDENADOR, nunca en GitHub (el repositorio es público y sus registros también):
    python3 x_autorizar.py

1. Te pide la API Key y el API Key Secret de la app (developer.x.com → tu app → Keys and tokens). No se guardan en ningún sitio.
2. Te da un enlace: ábrelo en el navegador CON LA SESIÓN DE LA CUENTA NUEVA iniciada (la de Bilbao, no la de Madrid) y pulsa «Autorizar».
3. X te enseña un PIN: pégalo aquí.
4. Te muestra el Access Token y su Secret de esa cuenta: guárdalos en GitHub (Settings → Secrets and variables → Actions)
   como X_<ZONA>_TOKEN y X_<ZONA>_SECRET (para Bilbao: X_BIZKAIA_TOKEN y X_BIZKAIA_SECRET). Luego cierra el terminal.
Solo usa la biblioteca estándar de Python (no hay que instalar nada).
"""
import base64, getpass, hashlib, hmac, secrets, time, urllib.parse, urllib.request

API = 'https://api.x.com'
q = lambda s: urllib.parse.quote(s, safe='')


def llamada(url, ck, cs, tk='', ts='', extra=None):
    o = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16), 'oauth_signature_method': 'HMAC-SHA1',
         'oauth_timestamp': str(int(time.time())), 'oauth_version': '1.0'}
    if tk:
        o['oauth_token'] = tk
    o.update(extra or {})
    base = '&'.join(['POST', q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(o.items())))])
    o['oauth_signature'] = base64.b64encode(hmac.new(f'{q(cs)}&{q(ts)}'.encode(), base.encode(), hashlib.sha1).digest()).decode()
    cab = 'OAuth ' + ', '.join(f'{q(k)}="{q(v)}"' for k, v in sorted(o.items()))
    req = urllib.request.Request(url, data=b'', method='POST', headers={'Authorization': cab})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return dict(urllib.parse.parse_qsl(r.read().decode()))
    except urllib.error.HTTPError as e:
        raise SystemExit(f'X respondió {e.code}: {e.read().decode()[:300]}')


def main():
    print('Autorizar la app de Limón en otra cuenta de X\n')
    ck = getpass.getpass('API Key de la app (no se ve al escribir): ').strip()
    cs = getpass.getpass('API Key Secret de la app (no se ve al escribir): ').strip()
    r = llamada(f'{API}/oauth/request_token', ck, cs, extra={'oauth_callback': 'oob'})
    if r.get('oauth_callback_confirmed') != 'true':
        raise SystemExit('X no ha aceptado la petición. Revisa las claves de la app.')
    print('\n1) Abre este enlace con la sesión de la CUENTA NUEVA iniciada y pulsa «Autorizar»:\n')
    print(f"   {API}/oauth/authorize?oauth_token={r['oauth_token']}\n")
    pin = input('2) Escribe aquí el PIN que te muestra X: ').strip()
    a = llamada(f'{API}/oauth/access_token', ck, cs, r['oauth_token'], r['oauth_token_secret'], {'oauth_verifier': pin})
    print(f"\nListo: autorizada la cuenta @{a.get('screen_name')}. Comprueba que es la que querías.\n")
    print('Guarda estos dos valores en GitHub y cierra el terminal:\n')
    print(f"   X_<ZONA>_TOKEN  = {a['oauth_token']}")
    print(f"   X_<ZONA>_SECRET = {a['oauth_token_secret']}\n")


if __name__ == '__main__':
    main()
