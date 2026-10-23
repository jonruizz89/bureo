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
    return hashlib.md5(f"{e['title']}|{texto(e)}|{v}|{e['starts_on']}".encode()).hexdigest()


def texto(e):
    # la descripción de la fuente; si no trae, lo leído de la ficha original (enrich.py)
    return e.get('description') or (e.get('details') or '')[:600]


# ---------- taxonomía
groups = sb('GET', 'category_groups?select=id,name&order=position')
cats = sb('GET', 'categories?select=id,group_id,label,is_generic&order=position')
valid = {c['id'] for c in cats if c['id'] != 'sin_categoria'}
FAMILIA = {c['id'] for c in cats if c['group_id'] == 'familia'}
tax_txt = '\n'.join(
    f"{g['name']}: " + ', '.join(f"{c['id']} ({c['label']})" for c in cats if c['group_id'] == g['id'] and c['id'] in valid)
    for g in groups if g['id'] != 'otros')

SYSTEM = f"""Clasificas eventos de la agenda de una ciudad española para una app de recomendaciones.

Taxonomía (usa SOLO estos identificadores; los que empiezan por g: son genéricos de grupo, úsalos solo si no encaja ninguno concreto):
{tax_txt}

Para cada evento devuelve:
- "cats": de 1 a 4 identificadores de la taxonomía, del más al menos relevante. Sé preciso: una película clásica es cine_clasico, una proyección de documental es documental, una exposición de pintura es pintura. No pongas categorías de familia (infantil, cuentacuentos, titeres, talleres_familia) salvo que el evento esté dirigido a niños o familias. Una sesión de DJ o una fiesta en discoteca o club es discotecas (y noche); un concierto en una sala no es discotecas. No confundas el nombre de un lugar con el contenido (el Hipódromo de la Zarzuela no es zarzuela; "Los pájaros" de Hitchcock no es observación de aves).
- "topics": de 0 a 5 temas concretos en español que un aficionado buscaría: personas (director, autor, artista, compositor, intérprete), obras famosas, épocas o civilizaciones, movimientos, lugares. Usa tu conocimiento: si el título es una película conocida, incluye su director (p. ej. "Alfred Hitchcock"); si es una ópera, su compositor. Nombres propios con su forma más común. Nada genérico como "cine" o "cultura".
- "series": si el evento forma parte claramente de un ciclo o serie (un ciclo de un director en un centro, un festival, una temporada), su nombre corto, p. ej. "Ciclo Hitchcock en el C.S. José de Espronceda"; si no, null.
- "publico": a quién va dirigido. "ninos" si es para niños o pide edades infantiles (p. ej. «de 7 a 12 años», «hasta 14 años», «a partir de 4 años»), aunque sea un curso, un deporte o un concierto; "adolescentes" si es para jóvenes de 12 a 17; "familias" si es para ir con niños de cualquier edad; "mayores" si está pensado para personas mayores o jubiladas (centros de mayores, envejecimiento activo, «a partir de 60/65 años»); "adultos" si es solo para mayores de edad o pide +18; "todos" en el resto.
- "unique": true solo si es una cita de ciudad singular, de las que pasan como mucho una vez al año y atraen a mucha gente (fiestas tradicionales, grandes carreras populares, festivales de referencia, noches especiales de museos); false en la programación habitual.

Entrega el resultado llamando a la herramienta guardar_clasificacion con un elemento por evento."""

TOOL = {
    'name': 'guardar_clasificacion',
    'description': 'Guarda la clasificación de todos los eventos del lote.',
    'input_schema': {
        'type': 'object',
        'properties': {'items': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'id': {'type': 'integer'},
                'cats': {'type': 'array', 'items': {'type': 'string'}},
                'topics': {'type': 'array', 'items': {'type': 'string'}},
                'series': {'type': ['string', 'null']},
                'unique': {'type': 'boolean'},
                'publico': {'type': 'string', 'enum': ['todos', 'ninos', 'adolescentes', 'familias', 'mayores', 'adultos']}},
            'required': ['id', 'cats', 'topics', 'series', 'unique', 'publico']}}},
        'required': ['items']}}


import threading
SPENT = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0, 'calls': 0}
LOCK = threading.Lock()
MAX_USD = float(os.environ.get('BUREO_AI_MAX_USD', '0.50'))


def spent_usd():
    # Precios de Claude Haiku 5.5 (USD por millón de tokens): entrada 0,10; salida 0,50; lectura de caché 0,01; escritura 0,125
    return (SPENT['input_tokens'] * 0.10 + SPENT['output_tokens'] * 0.50 +
            SPENT['cache_read_input_tokens'] * 0.01 + SPENT['cache_creation_input_tokens'] * 0.125) / 1e6


def call_model(batch):
    items = [{'id': e['id'], 'titulo': e['title'], 'lugar': (e.get('venues') or {}).get('name'),
              'fechas': f"{e['starts_on']}–{e['ends_on']}", 'precio': e.get('price_text'),
              'descripcion': texto(e)[:600],
              'pistas_de_la_fuente': [c['category_id'] for c in e.get('event_categories') or []]} for e in batch]
    body = {'model': MODEL, 'max_tokens': 6000,
            'system': [{'type': 'text', 'text': SYSTEM, 'cache_control': {'type': 'ephemeral'}}],
            'tools': [TOOL], 'tool_choice': {'type': 'tool', 'name': 'guardar_clasificacion'},
            'messages': [{'role': 'user', 'content': json.dumps(items, ensure_ascii=False)}]}
    for attempt in range(3):
        if spent_usd() >= MAX_USD:
            raise RuntimeError(f'tope de gasto alcanzado ({MAX_USD:.2f} USD): se para para no gastar de más')
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
        req.add_header('x-api-key', AKEY)
        req.add_header('anthropic-version', '2023-06-01')
        req.add_header('content-type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode())
            with LOCK:  # se cuenta todo lo consumido, aunque luego la respuesta no sirva
                SPENT['calls'] += 1
                for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
                    SPENT[k] += (res.get('usage') or {}).get(k) or 0
            blocks = [b for b in res.get('content', []) if b.get('type') == 'tool_use']
            if not blocks:
                raise ValueError('sin llamada a la herramienta (stop_reason=%s)' % res.get('stop_reason'))
            out = blocks[0]['input'].get('items')
            if not isinstance(out, list):
                raise ValueError('formato inesperado')
            return out, res.get('usage', {})
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code in (429, 500, 502, 503, 529):
                wait = float(e.headers.get('retry-after') or 10 * (attempt + 1))
                print(f'  API {e.code}, reintento en {wait:.0f} s: {msg}', flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f'API {e.code}: {msg}')
        except (ValueError, json.JSONDecodeError) as ex:
            print('  respuesta no válida, reintento:', ex, flush=True)
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
        # el público va aparte de las 4 categorías: un curso de tenis para niños es deporte Y actividad infantil
        extra = {'ninos': 'infantil', 'familias': 'infantil', 'adolescentes': 'jovenes', 'mayores': 'mayores'}.get(r.get('publico'))
        if extra and extra in valid and extra not in cs and (extra == 'mayores' or not any(c in FAMILIA for c in cs)):
            cs = [c for c in cs if c != 'sin_categoria'] + [extra]
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
cols = 'id,ext_key,city_id,title,description,details,starts_on,ends_on,price_text,ai_hash,venues(name),event_categories(category_id)'
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


with cf.ThreadPoolExecutor(max_workers=int(os.environ.get('BUREO_AI_WORKERS', '2'))) as pool:
    futs = {pool.submit(work, b): b for b in batches}
    for f in cf.as_completed(futs):
        try:
            n, u = f.result()
            total += n
            for k in usage:
                usage[k] += u.get(k) or 0
            print(f'  {total}/{len(pending)} ({time.strftime("%H:%M:%S")})', flush=True)
        except Exception as ex:
            print('  lote fallido:', ex, flush=True)
print(f'Clasificados {total}. Llamadas a la API: {SPENT["calls"]}. Tokens: {SPENT}. Coste real aproximado: {spent_usd():.3f} USD (tope {MAX_USD:.2f})')


# ---------- ciclos: mismo recinto + misma persona en varios eventos próximos
# El modelo ve cada lote por separado y no puede saber que hay otras películas del mismo director en el mismo
# centro; esto lo detecta mirando todos los eventos a la vez.
evs, offset = [], 0
while True:
    page = sb('GET', f'events?select=id,title,starts_on,topics,series,venue_id,venues(name),event_categories(categories(group_id))&ends_on=gte.{today}&venue_id=not.is.null&order=id&limit=1000&offset={offset}')
    evs += page
    if len(page) < 1000:
        break
    offset += 1000
groups = {}
for e in evs:
    for t in (e.get('topics') or [])[:2]:  # los primeros temas suelen ser la persona principal
        groups.setdefault((e['venue_id'], t), []).append(e)
ciclos = 0
for (vid, t), g in groups.items():
    if len(g) < 2 or len(t.split()) < 2:  # al menos dos eventos y un nombre propio de dos palabras
        continue
    if any(t.lower() in x['title'].lower() for x in g):  # el tema es el propio título, no una persona
        continue
    venue = ((g[0].get('venues') or {}).get('name') or '').lower()
    if t.lower() in venue or any(w in t.lower() for w in ('museo', 'teatro', 'centro', 'parque', 'biblioteca')):
        continue  # el tema es el propio recinto
    cultural = {'cine', 'escena', 'musica', 'letras', 'arte', 'patrimonio', 'ciencia'}
    if not all(any((c.get('categories') or {}).get('group_id') in cultural for c in x.get('event_categories') or []) for x in g):
        continue  # partidos de un mismo equipo no son un ciclo
    days = sorted(dt.date.fromisoformat(x['starts_on']) for x in g)
    if (days[-1] - days[0]).days > 90:
        continue
    name = f"Ciclo {t} en {(g[0].get('venues') or {}).get('name', 'el mismo centro')}"[:160]
    for x in g:
        if not x.get('series'):
            sb('PATCH', f"events?id=eq.{x['id']}", {'series': name}, 'return=minimal')
            x['series'] = name
            ciclos += 1
print(f'Ciclos detectados por recinto y persona: {ciclos} eventos asignados')
