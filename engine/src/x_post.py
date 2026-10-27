"""Publica en X (Twitter) las cuentas de Limón por zona: cortes del día, planes de hoy y «este finde».

Uso: python3 src/x_post.py <zona> <cortes|hoy|finde>     (lee la URL y la clave pública de web/config.js)
Credenciales (secretos de GitHub, nunca en el código):
  X_API_KEY, X_API_SECRET           la app de developer.x.com (una para todas las cuentas)
  X_<ZONA>_TOKEN, X_<ZONA>_SECRET   el acceso de cada cuenta (p. ej. X_MADRID_TOKEN)
Sin credenciales o con X_DRY=1 solo escribe el texto en la salida (sirve para probar).
Coste: X cobra por publicación y bastante más si el texto lleva un enlace, así que solo «finde» lleva enlace; el resto remite al perfil.
"""
import base64, datetime as dt, hashlib, hmac, json, os, re, secrets, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = open(os.path.join(HERE, '..', '..', 'web', 'config.js'), encoding='utf-8').read()
SB_URL = re.search(r"supabaseUrl:\s*'([^']+)'", cfg).group(1)
SB_KEY = re.search(r"supabaseKey:\s*'([^']+)'", cfg).group(1)
HOY = dt.date.today()
DIAS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']
EMO = {'musica': '🎵', 'escena': '🎭', 'arte': '🖼️', 'patrimonio': '🏛️', 'cine': '🎬', 'letras': '📚', 'ciencia': '🔬', 'gastro': '🍽️', 'aprender': '✏️',
       'naturaleza': '🌿', 'ciudad': '🎉', 'deporte': '🏃', 'mascotas': '🐾', 'ferias': '🛍️', 'familia': '🧒', 'comunidad': '🤝'}
KIND = {'manifestacion': ('📣', 0), 'evento': ('🎉', 1), 'metro': ('🚇', 1), 'cercanias': ('🚆', 1), 'autobus': ('🚌', 2), 'obras': ('🚧', 3), 'recurrente': ('🔁', 4), 'otro': ('⚠️', 4)}
MAX = 255  # X cuenta los emojis como 2 caracteres: margen


def get(path):
    req = urllib.request.Request(f'{SB_URL}/rest/v1/{path}', headers={'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def corta(t, n):
    return t if len(t) <= n else t[:n - 1].rsplit(' ', 1)[0].rstrip(',;:·-') + '…'


def junta(cabecera, items, pie):
    """Mete tantos elementos como quepan en 280 caracteres."""
    out = cabecera
    for it in items:
        if len(out + '\n' + it + '\n\n' + pie) > MAX:
            break
        out += '\n' + it
    return out + '\n\n' + pie


def zona(z):
    c = get(f'cities?select=id,name,kind,parent&id=eq.{z}')[0]
    hijos = [x['id'] for x in get(f'cities?select=id&parent=eq.{z}')] if c['kind'] == 'provincia' else []
    return c, [z] + hijos


def cortes(z):
    c, ids = zona(z)
    hoy, wd = HOY.isoformat(), HOY.isoweekday()
    rows = get(f'closures?select=title,kind,streets,lines,ranges&city_id=in.({",".join(ids)})')
    vale = []
    for r in rows:
        for g in r.get('ranges') or []:
            if g.get('from', '') <= hoy <= g.get('to', '') and (not g.get('weekdays') or wd in g['weekdays']):
                emo, prio = KIND.get(r['kind'], ('⚠️', 4))
                h = f" ({g['start']}–{g['end']} h)" if g.get('start') and g.get('end') else ''
                vale.append((prio, f"{emo} {corta(r['title'], 90)}{h}"))
                break
    if not vale:
        return None
    vale.sort(key=lambda x: x[0])
    return junta(f"Cortes y avisos hoy en {c['name']} ({DIAS[HOY.weekday()]} {HOY.day}):", [t for _, t in vale],
                 f"Todos ({len(vale)}) en el mapa de Limón: enlace en el perfil 🍋")


def grupos():
    return {x['id']: x['group_id'] for x in get('categories?select=id,group_id')}


def planes(z, desde, hasta, n):
    c, ids = zona(z)
    g = grupos()
    rows = get(f'events?select=id,title,starts_on,ends_on,time_text,is_free,is_unique,ai_unique,image_url,venues(name),event_categories(category_id)'
               f'&city_id=in.({",".join(ids)})&starts_on=lte.{hasta}&ends_on=gte.{desde}&limit=3000')
    def corto(e):  # fuera exposiciones y ciclos largos: en X interesan los planes con fecha
        return (dt.date.fromisoformat(e['ends_on']) - dt.date.fromisoformat(e['starts_on'])).days <= 3
    def nota(e):
        return (3 if e.get('is_unique') or e.get('ai_unique') else 0) + (1 if e.get('is_free') else 0) + (0.5 if e.get('time_text') else 0) + (0.3 if e.get('image_url') else 0)
    sel, usados = [], {}
    for e in sorted(filter(corto, rows), key=lambda e: (-nota(e), e['starts_on'], e['id'])):
        if re.search(r'agotad|sold.?out|completo|aplazad|cancelad', e['title'], re.I):
            continue
        gr = next((g.get(x['category_id']) for x in e.get('event_categories') or [] if g.get(x['category_id']) in EMO), None)
        if usados.get(gr, 0) >= 2:  # variedad
            continue
        usados[gr] = usados.get(gr, 0) + 1
        sel.append((e, gr))
        if len(sel) >= n:
            break
    return c, sel


def linea(e, gr, con_dia=False, desde=None):
    d = dt.date.fromisoformat(max(e['starts_on'], (desde or HOY).isoformat()))
    cuando = (DIAS[d.weekday()] + ' ' if con_dia and d.isoformat() == e['starts_on'] or con_dia and e['starts_on'] == e['ends_on'] else '') + (e['time_text'] if e.get('time_text') and len(e['time_text']) <= 12 else '')
    extra = ' · '.join(x for x in (cuando.strip(), 'gratis' if e.get('is_free') else '') if x)  # sin el sitio: en 280 caracteres caben más planes
    return f"{EMO.get(gr, '🍋')} {corta(e['title'], 48)}" + (f" ({extra})" if extra else '')


def hoy(z):
    c, sel = planes(z, HOY.isoformat(), HOY.isoformat(), 6)
    if len(sel) < 2:
        return None
    return junta(f"Planes para hoy en {c['name']} ({DIAS[HOY.weekday()]} {HOY.day}):", [linea(e, g) for e, g in sel], 'Muchos más en Limón: enlace en el perfil 🍋')


def finde(z):
    vie = HOY + dt.timedelta(days=(4 - HOY.weekday()) % 7)
    dom = vie + dt.timedelta(days=2)
    c, sel = planes(z, vie.isoformat(), dom.isoformat(), 6)
    if len(sel) < 3:
        return None
    url = f"https://limonplanes.com/planes/{z}/este-finde/"  # un enlace cuenta como 23 caracteres en X
    cab = f"Este finde en {c['name']} 🍋"
    out = cab
    for e, g in sel:
        if len(out) + 1 + len(linea(e, g, True, vie)) + 2 + 23 > MAX:
            break
        out += '\n' + linea(e, g, True, vie)
    return out + '\n\n' + url


def firma(method, url, ck, cs, tk, ts):
    o = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16), 'oauth_signature_method': 'HMAC-SHA1',
         'oauth_timestamp': str(int(time.time())), 'oauth_token': tk, 'oauth_version': '1.0'}
    q = lambda s: urllib.parse.quote(s, safe='')
    base = '&'.join([method, q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(o.items())))])
    o['oauth_signature'] = base64.b64encode(hmac.new(f'{q(cs)}&{q(ts)}'.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(f'{q(k)}="{q(v)}"' for k, v in sorted(o.items()))


def publica(z, texto):
    p = 'X_' + z.upper().replace('-', '_')
    cred = [(os.environ.get(k) or '').strip() for k in ('X_API_KEY', 'X_API_SECRET', p + '_TOKEN', p + '_SECRET')]
    print(texto, f'\n({len(texto)} caracteres)')
    if os.environ.get('X_DRY') == '1' or not all(cred):
        print('Sin publicar (prueba o faltan credenciales).')
        return
    url = 'https://api.x.com/2/tweets'
    req = urllib.request.Request(url, data=json.dumps({'text': texto}).encode(), method='POST',
                                 headers={'Authorization': firma('POST', url, *cred), 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print('Publicado:', json.load(r).get('data', {}).get('id'))
    except urllib.error.HTTPError as e:
        raise SystemExit(f'X respondió {e.code}: {e.read().decode()[:400]}')


def yo(z):
    """Comprueba las credenciales sin publicar: devuelve el usuario de la cuenta."""
    p = 'X_' + z.upper().replace('-', '_')
    cred = [(os.environ.get(k) or '').strip() for k in ('X_API_KEY', 'X_API_SECRET', p + '_TOKEN', p + '_SECRET')]
    faltan = [k for k, v in zip(('X_API_KEY', 'X_API_SECRET', p + '_TOKEN', p + '_SECRET'), cred) if not v]
    if faltan:
        raise SystemExit('Faltan los secretos: ' + ', '.join(faltan))
    url = 'https://api.x.com/2/users/me'
    req = urllib.request.Request(url, headers={'Authorization': firma('GET', url, *cred)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r).get('data', {})
            print(f"Credenciales correctas: @{d.get('username')} ({d.get('name')})")
    except urllib.error.HTTPError as e:
        raise SystemExit(f'X respondió {e.code}: {e.read().decode()[:400]}')


if __name__ == '__main__':
    z, modo = sys.argv[1], sys.argv[2]
    if modo == 'yo':
        yo(z)
        sys.exit()
    t = {'cortes': cortes, 'hoy': hoy, 'finde': finde}[modo](z)
    if t:
        publica(z, t)
    else:
        print('Nada que publicar hoy.')
