"""Clasifica con IA los eventos de Supabase que aún no se han clasificado o cuyo contenido ha cambiado.

Para cada evento, el modelo elige categorías de la taxonomía, extrae temas concretos (directores, autores,
épocas, artistas, lugares), detecta si forma parte de una serie o ciclo y si es un plan único de ciudad.
Variables de entorno: SUPABASE_URL, SUPABASE_SECRET, ANTHROPIC_API_KEY. Opcional: BUREO_AI_LIMIT, BUREO_AI_MODEL.
"""
import json, os, sys, hashlib, datetime as dt, urllib.request, urllib.error, concurrent.futures as cf, time

URL = os.environ['SUPABASE_URL'].rstrip('/')
SKEY = os.environ['SUPABASE_SECRET']
AKEY = os.environ.get('ANTHROPIC_API_KEY')
MODEL = os.environ.get('BUREO_AI_MODEL', 'claude-haiku-5-5')
LIMIT = int(os.environ.get('BUREO_AI_LIMIT', '100000'))
BATCH = 25
if not AKEY:
    print('Sin ANTHROPIC_API_KEY: no se clasifica con IA esta vez.')
    sys.exit(0)


def sb(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in (('apikey', SKEY), ('Authorization', 'Bearer ' + SKEY), ('Content-Type', 'application/json')):
        req.add_header(k, v)
    if prefer:
        req.add_header('Prefer', prefer)
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def content_hash(e):
    v = (e.get('venues') or {}).get('name') or ''
    return hashlib.md5(f"{e['title']}|{e.get('description') or ''}|{v}|{e['starts_on']}".encode()).hexdigest()


# ---------- taxonomía
groups = sb('GET', 'category_groups?select=id,name&order=position')
cats = sb('GET', 'categories?select=id,group_id,label,is_generic&order=position')
valid = {c['id'] for c in cats if c['id'] != 'sin_categoria'}
tax_txt = '\n'.join(
    f"{g['name']}: " + ', '.join(f"{c['id']} ({c['label']})" for c in cats if c['group_id'] == g['id'] and c['id'] in valid)
    for g in groups if g['id'] != 'otros')

SYSTEM = f"""Clasificas eventos de la agenda de una ciudad española para una app de recomendaciones.

Taxonomía (usa SOLO estos identificadores; los que empiezan por g: son genéricos de grupo, úsalos solo si no encaja ninguno concreto):
{tax_txt}

Para cada evento devuelve:
- "cats": de 1 a 4 identificadores de la taxonomía, del más al menos relevante. Sé preciso: una película clásica es cine_clasico, una proyección de documental es documental, una exposición de pintura es pintura. No pongas categorías de familia (infantil, cuentacuentos, titeres, talleres_familia) salvo que el evento esté dirigido a niños o familias. No confundas el nombre de un lugar con el contenido (el Hipódromo de la Zarzuela no es zarzuela; "Los pájaros" de Hitchcock no es observación de aves).
- "topics": de 0 a 5 temas concretos en español que un aficionado buscaría: personas (director, autor, artista, compositor, intérprete), obras famosas, épocas o civilizaciones, movimientos, lugares. Usa tu conocimiento: si el título es una película conocida, incluye su director (p. ej. "Alfred Hitchcock"); si es una ópera, su compositor. Nombres propios con su forma más común. Nada genérico como "cine" o "cultura".
- "series": si el evento forma parte claramente de un ciclo o serie (un ciclo de un director en un centro, un festival, una temporada), su nombre corto, p. ej. "Ciclo Hitchcock en el C.S. José de Espronceda"; si no, null.
- "unique": true solo si es una cita de ciudad singular, de las que pasan como mucho una vez al año y atraen a mucha gente (fiestas tradicionales, grandes carreras populares, festivales de referencia, noches especiales de museos); false en la programación habitual.

Responde SOLO con un array JSON, un objeto por evento, en el mismo orden: [{{"id": 123, "cats": [...], "topics": [...], "series": null, "unique": false}}]"""


def call_model(batch):
    items = [{'id': e['id'], 'titulo': e['title'], 'lugar': (e.get('venues') or {}).get('name'),
              'fechas': f"{e['starts_on']}–{e['ends_on']}", 'precio': e.get('price_text'),
              'descripcion': (e.get('description') or '')[:600],
              'pistas_de_la_fuente': [c['category_id'] for c in e.get('event_categories') or []]} for e in batch]
    body = {'model': MODEL, 'max_tokens': 4000,
            'system': [{'type': 'text', 'text': SYSTEM, 'cache_control': {'type': 'ephemeral'}}],
            'messages': [{'role': 'user', 'content': json.dumps(items, ensure_ascii=False)}]}
    for attempt in range(5):
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
        req.add_header('x-api-key', AKEY)
        req.add_header('anthropic-version', '2023-06-01')
        req.add_header('content-type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode())
            txt = ''.join(b.get('text', '') for b in res.get('content', []) if b.get('type') == 'text')
            out = json.loads(txt[txt.index('['): txt.rindex(']') + 1])
            return out, res.get('usage', {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(5 * (attempt + 1))
                continue
            raise SystemExit(f'API {e.code}: {e.read().decode()[:300]}')
        except (ValueError, json.JSONDecodeError):
            time.sleep(2)
    raise RuntimeError('el modelo no devolvió JSON válido')


def save(batch, result):
    """Guarda un lote: un PATCH por evento y las categorías del lote en dos escrituras."""
    by_id = {r.get('id'): r for r in result if isinstance(r, dict)}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows, cat_rows = [], []
    for e in batch:
        r = by_id.get(e['id'])
        if not r:
            continue
        cs = [c for c in dict.fromkeys(r.get('cats') or []) if c in valid][:4] or ['sin_categoria']
        topics = [str(t).strip()[:80] for t in (r.get('topics') or []) if str(t).strip()][:5]
        series = r.get('series') or None
        rows.append({'id': e['id'], 'topics': topics, 'series': str(series)[:160] if series else None,
                     'ai_unique': bool(r.get('unique')), 'ai_hash': e['_hash'], 'ai_model': MODEL, 'ai_at': now})
        cat_rows += [{'event_id': e['id'], 'category_id': c} for c in cs]
    if not rows:
        return 0
    for r in rows:  # PATCH por evento (la columna id es identity y no admite upsert con id explícito)
        rid = r.pop('id')
        sb('PATCH', f'events?id=eq.{rid}', r, 'return=minimal')
        r['id'] = rid
    sb('DELETE', 'event_categories?event_id=in.(' + ','.join(str(r['id']) for r in rows) + ')')
    sb('POST', 'event_categories?on_conflict=event_id,category_id', cat_rows, 'resolution=merge-duplicates,return=minimal')
    return len(rows)


# ---------- eventos pendientes
today = dt.date.today().isoformat()
pending, offset = [], 0
cols = 'id,ext_key,city_id,title,description,starts_on,ends_on,price_text,ai_hash,venues(name),event_categories(category_id)'
while True:
    page = sb('GET', f'events?select={cols}&ends_on=gte.{today}&order=id&limit=1000&offset={offset}')
    for e in page:
        e['_hash'] = content_hash(e)
        if e.get('ai_hash') != e['_hash']:
            pending.append(e)
    if len(page) < 1000:
        break
    offset += 1000
pending = pending[:LIMIT]
print(f'{len(pending)} eventos por clasificar con {MODEL}')

batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
total, usage = 0, {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0}
def work(b):
    res, u = call_model(b)
    return save(b, res), u


with cf.ThreadPoolExecutor(max_workers=6) as pool:
    futs = {pool.submit(work, b): b for b in batches}
    for f in cf.as_completed(futs):
        try:
            n, u = f.result()
            total += n
            for k in usage:
                usage[k] += u.get(k) or 0
            print(f'  {total}/{len(pending)}', flush=True)
        except Exception as ex:
            print('  lote fallido:', ex, flush=True)
cost = usage['input_tokens'] / 1e6 * 0.10 + usage['cache_read_input_tokens'] / 1e6 * 0.01 + usage['output_tokens'] / 1e6 * 0.50
print(f'Clasificados {total}. Tokens: {usage}. Coste aproximado: {cost:.3f} USD')
