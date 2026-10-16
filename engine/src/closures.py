"""Cortes de calle de Madrid.

1. Lee la página "Principales incidencias en la ciudad" de madrid.es y sigue cada aviso
   (carreras, procesiones, partidos, obras, peatonalizaciones de fin de semana…).
2. Extrae el texto de cada aviso y, solo si ha cambiado desde la última vez, se lo pasa a Claude Haiku
   para convertirlo en cortes estructurados: calles, distrito y franjas de días y horas.
3. Reemplaza la tabla closures de Supabase con el resultado.

Variables: SUPABASE_URL, SUPABASE_SECRET, ANTHROPIC_API_KEY (sin ella se reutiliza la caché).
Caché: raw/closures_cache.json (texto de cada aviso, su huella y lo extraído).
"""
import json, os, re, sys, html, hashlib, subprocess, time, datetime as dt, urllib.request, urllib.error

RAW = os.environ.get('BUREO_RAW', 'raw')
CACHE = os.path.join(RAW, 'closures_cache.json')
URL = os.environ['SUPABASE_URL'].rstrip('/')
SKEY = os.environ['SUPABASE_SECRET']
AKEY = os.environ.get('ANTHROPIC_API_KEY')
MODEL = os.environ.get('BUREO_AI_MODEL', 'claude-haiku-5-5')
MAX_USD = float(os.environ.get('BUREO_CLOSURES_MAX_USD', '0.20'))
BASE = 'https://www.madrid.es'
INDEX = (BASE + '/portales/munimadrid/es/Inicio/Movilidad-y-transportes/Incidencias-de-Trafico/'
         'Principales-incidencias-en-la-ciudad/?vgnextfmt=default&vgnextchannel=cbe0aa2dedcb1610VgnVCM2000001f4a900aRCRD')
UA = 'BureoBot/0.1 (+https://github.com/jonruizz89/bureo)'  # madrid.es bloquea agentes que se hacen pasar por navegador
SKIP = ('Recomendaciones', 'aparcamientos', 'Estado del tráfico', 'Zonas de Bajas', 'Medidas a adoptar',
        'Servicio sustitutivo', 'ColIncidencias', 'Planifica')


def get(url):
    for i in range(3):
        r = subprocess.run(['curl', '-sSL', '-m', '60', '-A', UA, '-w', '\n%{http_code}', url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition('\n')
        if code == '200' and body:
            return body
        time.sleep(10 * (i + 1))
    return None


def main_text(page):
    t = re.sub(r'<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>', ' ', page, flags=re.S | re.I)
    m = re.search(r'<main.*?</main>', t, re.S | re.I)
    t = m.group(0) if m else t
    t = re.sub(r'<(br|/p|/li|/tr|/h\d)[^>]*>', '\n', t, flags=re.I)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n\s*\n+', '\n', t).strip()
    for end in ('Información relacionada', 'Otros sitios de interés', 'Compartir en'):
        if end in t:
            t = t[:t.index(end)]
    return t[:6000]


def sb(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in (('apikey', SKEY), ('Authorization', 'Bearer ' + SKEY), ('Content-Type', 'application/json')):
        req.add_header(k, v)
    if prefer:
        req.add_header('Prefer', prefer)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            t = r.read().decode()
            return json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f'Supabase {method} {path}: {e.code} {e.read().decode()[:400]}')


TOOL = {
    'name': 'guardar_cortes',
    'description': 'Guarda los cortes de calle descritos en el aviso.',
    'input_schema': {'type': 'object', 'required': ['cortes'], 'properties': {'cortes': {'type': 'array', 'items': {
        'type': 'object', 'required': ['title', 'kind', 'streets', 'ranges', 'summary'],
        'properties': {
            'title': {'type': 'string', 'description': 'Título corto del motivo, p. ej. "Carrera de la Ciencia"'},
            'kind': {'type': 'string', 'enum': ['evento', 'obras', 'recurrente', 'partido', 'manifestacion', 'otro']},
            'summary': {'type': 'string', 'description': 'Una o dos frases en castellano para el usuario: qué se corta y cuándo'},
            'district': {'type': ['string', 'null']},
            'streets': {'type': 'array', 'items': {'type': 'string'},
                        'description': 'Nombres de calles, plazas o vías afectadas, tal como se escriben habitualmente (p. ej. "Paseo de la Castellana", "Calle de Serrano")'},
            'ranges': {'type': 'array', 'items': {'type': 'object', 'required': ['from', 'to'], 'properties': {
                'from': {'type': 'string', 'description': 'YYYY-MM-DD'},
                'to': {'type': 'string', 'description': 'YYYY-MM-DD; igual que from si es un solo día'},
                'weekdays': {'type': 'array', 'items': {'type': 'integer'}, 'description': '1=lunes … 7=domingo; vacío = todos los días del rango'},
                'holidays': {'type': 'boolean', 'description': 'true si también aplica en festivos'},
                'last_sunday': {'type': 'boolean', 'description': 'true si solo es el último domingo de cada mes'},
                'start': {'type': ['string', 'null'], 'description': 'HH:MM o null si es todo el día'},
                'end': {'type': ['string', 'null']}}}}}}}}}}

SPENT = {'in': 0, 'out': 0}


def extract(title, text):
    today = dt.date.today()
    system = (f"Hoy es {today.isoformat()} ({['lunes','martes','miércoles','jueves','viernes','sábado','domingo'][today.weekday()]}). "
              "Lees avisos oficiales de cortes de tráfico del Ayuntamiento de Madrid y los conviertes en datos para una app que avisa "
              "a la gente de qué calles estarán cortadas. Reglas: un elemento por motivo y franja coherente; si el aviso tiene varias "
              "fases o días con horarios distintos, usa varios rangos. Para cortes recurrentes sin fecha de fin (peatonalizaciones de "
              "fin de semana), usa from=hoy y to=hoy+90 días con weekdays. Para obras de larga duración sin fechas claras, from=hoy y "
              "to=hoy+60 días. No inventes calles ni horas que no estén en el texto. Si el aviso no describe ningún corte concreto, "
              "devuelve una lista vacía.")
    body = {'model': MODEL, 'max_tokens': 4000, 'system': system,
            'tools': [TOOL], 'tool_choice': {'type': 'tool', 'name': 'guardar_cortes'},
            'messages': [{'role': 'user', 'content': f'Aviso: {title}\n\n{text}'}]}
    for attempt in range(3):
        if (SPENT['in'] * 0.10 + SPENT['out'] * 0.50) / 1e6 >= MAX_USD:
            raise RuntimeError('tope de gasto alcanzado')
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
        req.add_header('x-api-key', AKEY)
        req.add_header('anthropic-version', '2023-06-01')
        req.add_header('content-type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode())
            SPENT['in'] += res['usage'].get('input_tokens', 0)
            SPENT['out'] += res['usage'].get('output_tokens', 0)
            blk = [b for b in res['content'] if b.get('type') == 'tool_use']
            if blk:
                return blk[0]['input'].get('cortes') or []
        except urllib.error.HTTPError as e:
            print('  API', e.code, e.read().decode()[:150])
            time.sleep(10 * (attempt + 1))
    return None


cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
index = get(INDEX)
if not index:
    print('aviso: no se pudo leer el índice de incidencias; se reutiliza lo anterior')
    links = [(v['title'], u) for u, v in cache.items()]
else:
    links, seen = [], set()
    for u, t in re.findall(r'<a[^>]+href="([^"]*vgnextoid=[^"]*)"[^>]*>\s*([^<]{15,200})</a>', index):
        t = html.unescape(t.strip())
        u = html.unescape(u)
        u = u if u.startswith('http') else BASE + u
        if t in seen or t.endswith('...') or any(k.lower() in t.lower() for k in SKIP):
            continue
        if re.search(r'corte|incidencia|plan de movilidad|manifestaci', t, re.I):
            seen.add(t)
            links.append((t, u))
print(len(links), 'avisos')

new_cache, rows = {}, []
for title, u in links:
    page = get(u) if index else None
    text = main_text(page) if page else (cache.get(u) or {}).get('text', '')
    h = hashlib.md5(text.encode()).hexdigest()
    prev = cache.get(u)
    if prev and prev.get('hash') == h and prev.get('items') is not None:
        items = prev['items']
    elif AKEY and text:
        try:
            items = extract(title, text)
        except RuntimeError as ex:
            print('  ', ex)
            items = None
        print(f'  IA: {title[:70]} → {len(items or [])} cortes')
    else:
        items = (prev or {}).get('items')
    new_cache[u] = {'title': title, 'text': text, 'hash': h, 'items': items}
    for c in items or []:
        kind = c.get('kind') if c.get('kind') in ('evento', 'obras', 'recurrente', 'partido', 'manifestacion', 'otro') else 'otro'
        rows.append({'city_id': 'madrid', 'title': (c.get('title') or title)[:200], 'kind': kind,
                     'summary': c.get('summary'), 'district': c.get('district'),
                     'streets': [s for s in c.get('streets') or [] if s][:30], 'ranges': c.get('ranges') or [],
                     'source_url': u, 'source_name': 'Ayuntamiento de Madrid · ' + title[:120]})
json.dump(new_cache, open(CACHE, 'w'), ensure_ascii=False)

# solo los que siguen vigentes
hoy = dt.date.today().isoformat()
rows = [r for r in rows if any((x.get('to') or '') >= hoy for x in r['ranges'])]
if rows or index:
    sb('DELETE', 'closures?city_id=eq.madrid')
    if rows:
        sb('POST', 'closures', rows, 'return=minimal')
print(f'{len(rows)} cortes vigentes cargados. Coste IA aproximado: {(SPENT["in"] * 0.10 + SPENT["out"] * 0.50) / 1e6:.3f} USD')
