"""Paso 3 · Lectores genéricos: schema.org/Event (JSON-LD), iCal, RSS/Atom, datos abiertos (CSV/JSON) y lectura con IA.

Todos devuelven eventos «crudos» con el mismo formato:
  {titulo, inicio, fin, hora, lugar, direccion, municipio, precio, gratis, url, descripcion, imagen, lat, lng}
con fechas YYYY-MM-DD. La normalización final (ventana de fechas, municipio, duplicados) la hace extract.py.
"""
import csv, datetime as dt, html, io, json, re, urllib.parse
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from lib import tool_call, fold

TZ = ZoneInfo('Europe/Madrid')


def clean(t, n=None):
    t = html.unescape(re.sub(r'<[^>]+>', ' ', str(t or '')))
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:n] if n else t


def parse_when(v):
    """'2026-10-20', '2026-10-20T19:30:00+02:00', '20261020T193000Z' → (fecha, hora 'HH:MM' o None)."""
    if not v:
        return None, None
    s = str(v).strip()
    m = re.match(r'^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$', s)
    if m:
        y, mo, d, hh, mi, _, z = m.groups()
        if hh is None:
            return f'{y}-{mo}-{d}', None
        t = dt.datetime(int(y), int(mo), int(d), int(hh), int(mi))
        if z:
            t = t.replace(tzinfo=dt.timezone.utc).astimezone(TZ)
        return t.date().isoformat(), (t.strftime('%H:%M') if (t.hour or t.minute) else None)
    try:
        t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if t.tzinfo:
            t = t.astimezone(TZ)
        hora = t.strftime('%H:%M') if (len(s) > 10 and (t.hour or t.minute)) else None
        return t.date().isoformat(), hora
    except ValueError:
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
        if m:
            return m.group(0), None
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
        if m:
            return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}', None
    return None, None


# ---------- schema.org/Event en JSON-LD
def _walk(o):
    if isinstance(o, list):
        for x in o:
            yield from _walk(x)
    elif isinstance(o, dict):
        t = o.get('@type')
        ts = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.endswith('Event') for x in ts):
            yield o
        for k in ('@graph', 'itemListElement', 'item', 'subEvent', 'event', 'events'):
            if k in o:
                yield from _walk(o[k])


def jsonld_events(blocks, base):
    out = []
    for b in blocks:
        try:
            data = json.loads(b.strip().rstrip(';'))
        except ValueError:
            try:
                data = json.loads(re.sub(r'[\x00-\x1f]', ' ', b))
            except ValueError:
                continue
        for e in _walk(data):
            ini, hora = parse_when(e.get('startDate'))
            fin, _ = parse_when(e.get('endDate'))
            if not ini or not e.get('name'):
                continue
            loc = e.get('location') or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            if isinstance(loc, str):
                loc = {'name': loc}
            addr = loc.get('address') or {}
            if isinstance(addr, str):
                addr = {'streetAddress': addr}
            geo = loc.get('geo') or {}
            offers = e.get('offers') or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get('price') if isinstance(offers, dict) else None
            free = e.get('isAccessibleForFree') in (True, 'true', 'True') or str(price) in ('0', '0.0', '0.00')
            img = e.get('image')
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get('url')
            out.append({
                'titulo': clean(e.get('name'), 300), 'inicio': ini, 'fin': fin or ini, 'hora': hora,
                'lugar': clean(loc.get('name'), 200) or None,
                'direccion': clean(addr.get('streetAddress'), 200) or None,
                'municipio': clean(addr.get('addressLocality'), 80) or None,
                'precio': (f'{price} €' if price not in (None, '') and not free else None), 'gratis': free,
                'url': urllib.parse.urljoin(base, e.get('url') or '') if e.get('url') else base,
                'descripcion': clean(e.get('description'), 600) or None, 'imagen': img if isinstance(img, str) else None,
                'lat': _num(geo.get('latitude')), 'lng': _num(geo.get('longitude')),
            })
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- iCal
def ical_events(text, base):
    text = re.sub(r'\r?\n[ \t]', '', text)  # líneas plegadas
    out = []
    for blk in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', text, re.S):
        f = {}
        for line in blk.strip().splitlines():
            if ':' not in line:
                continue
            k, v = line.split(':', 1)
            f.setdefault(k.split(';')[0].upper(), v)
        ini, hora = parse_when(f.get('DTSTART'))
        fin, _ = parse_when(f.get('DTEND'))
        if not ini or not f.get('SUMMARY'):
            continue
        if fin and fin > ini and 'T' not in (f.get('DTEND') or '') and 'VALUE=DATE' in blk:
            fin = (dt.date.fromisoformat(fin) - dt.timedelta(days=1)).isoformat()  # DTEND de día completo es exclusivo
        un = lambda s: (s or '').replace('\\n', ' ').replace('\\,', ',').replace('\\;', ';')
        out.append({'titulo': clean(un(f.get('SUMMARY')), 300), 'inicio': ini, 'fin': fin or ini, 'hora': hora,
                    'lugar': clean(un(f.get('LOCATION')), 200) or None, 'direccion': None, 'municipio': None, 'precio': None, 'gratis': False,
                    'url': f.get('URL') or base, 'descripcion': clean(un(f.get('DESCRIPTION')), 600) or None, 'imagen': None,
                    'lat': None, 'lng': None})
    return out


# ---------- RSS / Atom: los ítems se pasan a la IA (casi nunca traen la fecha del evento en un campo propio)
def feed_items(text):
    try:
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter():
        tag = it.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        g = lambda n: next((c for c in it if c.tag.split('}')[-1] == n), None)
        link = g('link')
        href = (link.get('href') if link is not None and link.get('href') else (link.text if link is not None else '')) or ''
        title = g('title'); desc = g('description') or g('summary') or g('content')
        items.append(f"{clean(title.text if title is not None else '', 200)} ‹{href.strip()}›\n{clean(desc.text if desc is not None else '', 700)}")
    return items


# ---------- datos abiertos (CSV / JSON): la IA decide una vez qué columna es qué y se guarda en la receta
MAP_TOOL = {'name': 'mapa_columnas', 'description': 'Qué columna del conjunto de datos corresponde a cada campo.',
            'input_schema': {'type': 'object', 'required': ['es_agenda'], 'properties': {
                'es_agenda': {'type': 'boolean', 'description': 'true si cada fila es un evento o actividad con fecha'},
                **{k: {'type': ['string', 'null'], 'description': d} for k, d in (
                    ('titulo', 'columna del título'), ('inicio', 'columna de la fecha (y hora) de inicio'), ('fin', 'columna de la fecha de fin'),
                    ('hora', 'columna de la hora, si va aparte'), ('lugar', 'columna del lugar o recinto'), ('direccion', 'columna de la dirección'),
                    ('municipio', 'columna del municipio'), ('precio', 'columna del precio'), ('gratis', 'columna que indica si es gratis'),
                    ('url', 'columna con el enlace a la ficha'), ('descripcion', 'columna de la descripción'), ('lat', 'columna de la latitud'),
                    ('lng', 'columna de la longitud'), ('ruta', 'si es JSON: clave o ruta con puntos hasta la lista de eventos (ej.: data.items)'))}}}}


def table_rows(text, ct):
    """Filas (lista de dicts) de un CSV o JSON."""
    t = text.strip()
    if t.startswith('{') or t.startswith('['):
        try:
            data = json.loads(t)
        except ValueError:
            return [], None
        return data, 'json'
    try:
        dialect = csv.Sniffer().sniff(t[:4000], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(t), dialect=dialect)), 'csv'


def dig(data, ruta):
    for k in (ruta or '').split('.'):
        if not k:
            continue
        if isinstance(data, dict):
            data = data.get(k)
        elif isinstance(data, list) and k.isdigit():
            data = data[int(k)]
    return data if isinstance(data, list) else []


def guess_mapping(sample, kind):
    shown = json.dumps(sample, ensure_ascii=False)[:6000]
    return tool_call('Analizas conjuntos de datos abiertos de agenda de eventos. Indica qué columna o clave corresponde a cada campo '
                     '(usa el nombre exacto que aparece) o null si no existe.', f'Formato: {kind}\nMuestra:\n{shown}', MAP_TOOL, max_tokens=1500)


def mapped_events(rows, m, base):
    out = []
    g = lambda r, k: (r.get(m[k]) if m.get(k) and isinstance(r, dict) else None)
    for r in rows:
        ini, hora = parse_when(g(r, 'inicio'))
        if not ini or not g(r, 'titulo'):
            continue
        fin, _ = parse_when(g(r, 'fin'))
        gr = str(g(r, 'gratis') or '').lower()
        out.append({'titulo': clean(g(r, 'titulo'), 300), 'inicio': ini, 'fin': fin or ini, 'hora': clean(g(r, 'hora'), 40) or hora,
                    'lugar': clean(g(r, 'lugar'), 200) or None, 'direccion': clean(g(r, 'direccion'), 200) or None,
                    'municipio': clean(g(r, 'municipio'), 80) or None, 'precio': clean(g(r, 'precio'), 80) or None,
                    'gratis': gr in ('1', 'true', 'si', 'sí', 'yes', 'gratis', 'gratuito', 'doan', 'dohainik'),
                    'url': clean(g(r, 'url'), 500) or base, 'descripcion': clean(g(r, 'descripcion'), 600) or None, 'imagen': None,
                    'lat': _num(g(r, 'lat')), 'lng': _num(g(r, 'lng'))})
    return out


# ---------- lectura con IA de una página sin formato estructurado
EXTRACT_TOOL = {'name': 'guardar_eventos', 'description': 'Guarda los eventos que aparecen en la página.',
                'input_schema': {'type': 'object', 'required': ['eventos'], 'properties': {
                    'eventos': {'type': 'array', 'items': {'type': 'object', 'required': ['titulo', 'inicio'], 'properties': {
                        'titulo': {'type': 'string'},
                        'inicio': {'type': 'string', 'description': 'YYYY-MM-DD'},
                        'fin': {'type': ['string', 'null'], 'description': 'YYYY-MM-DD si dura varios días'},
                        'hora': {'type': ['string', 'null'], 'description': 'HH:MM o texto corto'},
                        'lugar': {'type': ['string', 'null']},
                        'direccion': {'type': ['string', 'null']},
                        'municipio': {'type': ['string', 'null'], 'description': 'Si la página lo indica'},
                        'precio': {'type': ['string', 'null']},
                        'gratis': {'type': 'boolean'},
                        'url': {'type': ['string', 'null'], 'description': 'Enlace a la ficha del evento (los enlaces van entre ‹ ›)'},
                        'descripcion': {'type': ['string', 'null'], 'description': 'Hasta 2 frases, tal como viene'}}}},
                    'siguiente': {'type': ['string', 'null'], 'description': 'Enlace a la página siguiente del listado, si lo hay'}}}}


def llm_events(text, base, city_name, today):
    if len(text) < 200:
        return [], None
    system = (f'Hoy es {today}. Extraes eventos de páginas de agenda de {city_name} para Limón. Devuelve solo eventos concretos con fecha '
              '(conciertos, obras, exposiciones, talleres, visitas, fiestas, deportes…), no noticias ni convocatorias de subvenciones. '
              'Si un año no aparece, deduce el más cercano a hoy. No inventes nada que no esté en el texto.')
    out = tool_call(system, f'Página: {base}\n\n{text[:14000]}', EXTRACT_TOOL, max_tokens=8000) or {}
    evs = []
    for e in out.get('eventos') or []:
        ini, _ = parse_when(e.get('inicio')); fin, _ = parse_when(e.get('fin'))
        if not ini or not e.get('titulo'):
            continue
        evs.append({'titulo': clean(e['titulo'], 300), 'inicio': ini, 'fin': fin or ini, 'hora': clean(e.get('hora'), 40) or None,
                    'lugar': clean(e.get('lugar'), 200) or None, 'direccion': clean(e.get('direccion'), 200) or None,
                    'municipio': clean(e.get('municipio'), 80) or None, 'precio': clean(e.get('precio'), 80) or None,
                    'gratis': bool(e.get('gratis')), 'url': urllib.parse.urljoin(base, e['url']) if e.get('url') else base,
                    'descripcion': clean(e.get('descripcion'), 600) or None, 'imagen': None, 'lat': None, 'lng': None})
    nxt = out.get('siguiente')
    return evs, (urllib.parse.urljoin(base, nxt) if nxt else None)
