"""Carga out/events.json en Supabase (upsert idempotente).

Uso: python3 src/load_supabase.py [ruta a un .env]  (o variables de entorno SUPABASE_URL y SUPABASE_SECRET)
Se puede ejecutar cada noche: actualiza por ext_key y reemplaza sesiones y categorías.
"""
import json, sys, hashlib, re, unicodedata, urllib.request, urllib.error

import os
env = dict(l.strip().split('=', 1) for l in open(sys.argv[1]) if '=' in l) if len(sys.argv) > 1 else os.environ
URL, KEY = env['SUPABASE_URL'].rstrip('/'), env['SUPABASE_SECRET']
CITY = os.environ.get('BUREO_CITY', 'madrid')
PFX = '' if CITY == 'madrid' else CITY + '|'  # Madrid conserva sus claves de siempre


def norm(t):
    t = unicodedata.normalize('NFD', (t or '').lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', t)).strip()


def api(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header('apikey', KEY)
    req.add_header('Authorization', 'Bearer ' + KEY)
    req.add_header('Content-Type', 'application/json')
    if prefer:
        req.add_header('Prefer', prefer)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {path}: {e.code} {e.read().decode()[:500]}')


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


data = json.load(open(os.path.join(os.environ.get('BUREO_OUT', 'out'), 'events.json')))
evs = data['eventos']
sources = {s['name']: s['id'] for s in api('GET', f'sources?select=id,name&city_id=eq.{CITY}')}
cats_ok = {c['id'] for c in api('GET', 'categories?select=id')}

# 1) recintos
venues = {}
for e in evs:
    if e.get('lugar'):
        k = norm(e['lugar'])[:200]
        if k and k not in venues:
            venues[k] = {'city_id': CITY, 'name': e['lugar'], 'name_key': k, 'district': e.get('distrito'),
                         'lat': e.get('lat'), 'lng': e.get('lng')}
vid = {}
# los recintos sin coordenadas no borran las que ya tenía la base (p. ej. corregidas a mano)
with_geo = [v for v in venues.values() if v.get('lat') is not None]
no_geo = [{k: v[k] for k in ('city_id', 'name', 'name_key')} for v in venues.values() if v.get('lat') is None]
for group in (with_geo, no_geo):
    for part in chunks(group, 500):
        for r in api('POST', 'venues?on_conflict=city_id,name_key&select=id,name_key', part,
                     'resolution=merge-duplicates,return=representation'):
            vid[r['name_key']] = r['id']
print('recintos', len(vid))

# 2) eventos
rows, seen = [], set()
for e in evs:
    k = hashlib.md5(f"{PFX}{norm(e['titulo'])}|{norm(e.get('lugar') or '')}|{e['inicio']}".encode()).hexdigest()
    if k in seen:
        continue
    seen.add(k)
    e['_key'] = k
    rows.append({
        'ext_key': k, 'city_id': CITY, 'title': e['titulo'][:500], 'description': e.get('descripcion') or None,
        'starts_on': e['inicio'], 'ends_on': max(e['inicio'], e['fin']), 'time_text': e.get('hora'),
        'venue_id': vid.get(norm(e.get('lugar') or '')[:200]), 'district': e.get('distrito'),
        'lat': e.get('lat'), 'lng': e.get('lng'), 'price_text': e.get('precio'), 'is_free': bool(e.get('gratis')),
        'image_url': e.get('imagen'), 'organizer_url': e.get('web_organizador'),
        'is_unique': bool(e.get('unico')), 'is_featured': bool(e.get('destacado')),
    })
    if PFX:  # en las demás ciudades «único» lo decide unicos.py después de clasificar: no se pisa
        rows[-1].pop('is_unique')
eid = {}
for part in chunks(rows, 400):
    for r in api('POST', 'events?on_conflict=ext_key&select=id,ext_key', part,
                 'resolution=merge-duplicates,return=representation'):
        eid[r['ext_key']] = r['id']
print('eventos', len(eid))

# 3) sesiones, categorías y fuentes (se reemplazan)
#    Las categorías de los eventos ya clasificados con IA no se tocan: las reglas solo sirven de pista inicial.
ids = list(eid.values())
ai_done = set()
for part in chunks(ids, 300):
    flt = 'id=in.(' + ','.join(map(str, part)) + ')'
    ai_done |= {r['id'] for r in api('GET', 'events?select=id&ai_hash=not.is.null&' + flt)}
for part in chunks(ids, 300):
    flt = 'event_id=in.(' + ','.join(map(str, part)) + ')'
    api('DELETE', 'event_sessions?' + flt)
    rule = [i for i in part if i not in ai_done]
    if rule:
        api('DELETE', 'event_categories?event_id=in.(' + ','.join(map(str, rule)) + ')')
sess, cats, srcs = [], [], []
for e in evs:
    i = eid.get(e.get('_key'))
    if not i:
        continue
    sess += [{'event_id': i, 'on_date': d} for d in sorted(set(e['sesiones']))]
    if i not in ai_done:
        cats += [{'event_id': i, 'category_id': c} for c in dict.fromkeys(e['cats']) if c in cats_ok]
    for f in e['fuentes']:
        if f['nombre'] in sources:
            srcs.append({'event_id': i, 'source_id': sources[f['nombre']], 'url': f.get('url')})
for tbl, rws, conflict in (('event_sessions', sess, 'event_id,on_date'), ('event_categories', cats, 'event_id,category_id'),
                           ('event_sources', srcs, 'event_id,source_id')):
    for part in chunks(rws, 1000):
        api('POST', f'{tbl}?on_conflict={conflict}', part, 'resolution=merge-duplicates,return=minimal')
    print(tbl, len(rws))
# 4) ciudades del motor genérico: retirar lo que ya no publica ninguna de sus fuentes (si todas se leyeron bien)
leidas = [sources[n] for n in (data.get('meta') or {}).get('por_fuente', {}) if n in sources]
if PFX and leidas:
    print('retirados', api('POST', 'rpc/retire_stale_events', {'p_city': CITY, 'p_keep': [r['ext_key'] for r in rows], 'p_sources': leidas}))
import datetime as _dt
api('PATCH', f'sources?city_id=eq.{CITY}&status=eq.activa', {'last_run_at': _dt.datetime.now(_dt.timezone.utc).isoformat()}, 'return=minimal')
print('ok')
