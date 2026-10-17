"""Sitúa cada evento en el mapa y en su distrito.

Orden: (1) otro evento del mismo lugar que ya trae coordenadas; (2) la dirección postal contra los portales del
Callejero oficial (raw/geo_portals.json); (3) el nombre del lugar en OpenStreetMap (Nominatim, con caché y a 1 consulta
por segundo). El distrito sale de la rejilla de portales del callejero.
"""
import json, os, re, subprocess, time, unicodedata, urllib.parse
from geo_index import RAW, street_key, fold

UA = 'BureoBot/0.1 (+https://github.com/jonruizz89/bureo)'
DISTRITOS = {1: 'Centro', 2: 'Arganzuela', 3: 'Retiro', 4: 'Salamanca', 5: 'Chamartín', 6: 'Tetuán', 7: 'Chamberí',
             8: 'Fuencarral-El Pardo', 9: 'Moncloa-Aravaca', 10: 'Latina', 11: 'Carabanchel', 12: 'Usera',
             13: 'Puente de Vallecas', 14: 'Moratalaz', 15: 'Ciudad Lineal', 16: 'Hortaleza', 17: 'Villaverde',
             18: 'Villa de Vallecas', 19: 'Vicálvaro', 20: 'San Blas-Canillejas', 21: 'Barajas'}
CANON = {fold(v).replace('-', ' '): v for v in DISTRITOS.values()}
MAX_OSM = int(os.environ.get('BUREO_GEOCODE_MAX', '120'))  # consultas nuevas a Nominatim por ejecución
BBOX = (-3.89, 40.31, -3.52, 40.65)  # término municipal de Madrid, aprox.

_P = None
_O = None
_last = [0.0]
_spent = [0]


def _load():
    global _P, _O
    if _P is None:
        p = os.path.join(RAW, 'geo_portals.json')
        _P = json.load(open(p)) if os.path.exists(p) else {'portals': {}, 'cells': {}}
        p = os.path.join(RAW, 'osm_places.json')
        _O = json.load(open(p)) if os.path.exists(p) else {}


def save():
    if _O is not None:
        json.dump(_O, open(os.path.join(RAW, 'osm_places.json'), 'w'), ensure_ascii=False, separators=(',', ':'))


def canon_district(name):
    if not name:
        return None
    k = fold(name).replace('-', ' ').strip()
    return CANON.get(k, None if k in ('distrito', '') else name)


def district_at(lat, lng):
    _load()
    cells = _P['cells']
    i, j = round(lat / 0.005), round(lng / 0.006)
    votes = {}
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            d = cells.get(f'{i + di}:{j + dj}')
            if d:
                votes[d] = votes.get(d, 0) + (3 if di == dj == 0 else 1)
    if not votes:
        return None
    return DISTRITOS.get(int(max(votes.items(), key=lambda x: x[1])[0]))


def by_address(addr, strict=False):
    """'de Cea Bermúdez, 1' o 'Calle de Alcalá 42, 28014' → (lat, lng) del portal más cercano en número."""
    _load()
    if not addr:
        return None
    a = re.sub(r'\b\d{5}\b', ' ', addr)
    m = re.match(r'\s*(.+?)[,\s]+(?:n\.?º\s*)?(\d{1,4})\b', a)
    street, num = (m.group(1), int(m.group(2))) if m else (a.split(',')[0], None)
    tipo, k = street_key(street)
    cands = _P['portals'].get(k) or []
    if not cands:
        return None
    pick = [c for c in cands if tipo and c['tipo'] == tipo] or cands
    if strict and not tipo and len({c['tipo'] for c in cands}) > 1:
        return None  # «de Colón, 1»: ¿calle o plaza? mejor probar antes con el nombre del lugar
    pts = max(pick, key=lambda c: len(c['p']))['p']
    if num is None:
        p = pts[len(pts) // 2]
    else:
        same = [x for x in pts if x[0] % 2 == num % 2] or pts
        p = min(same, key=lambda x: abs(x[0] - num))
    return p[1], p[2]


def by_place(name):
    """Coordenadas de un lugar por su nombre en OpenStreetMap, dentro de Madrid."""
    _load()
    if not name:
        return None
    k = fold(name).strip()
    if k in _O:
        return tuple(_O[k]) if _O[k] else None
    if _spent[0] >= MAX_OSM or os.environ.get('BUREO_NO_OSM'):
        return None
    wait = 1.1 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    q = urllib.parse.urlencode({'q': name + ', Madrid', 'format': 'jsonv2', 'limit': 1, 'countrycodes': 'es',
                                'viewbox': ','.join(map(str, BBOX)), 'bounded': 1})
    r = subprocess.run(['curl', '-sS', '-m', '30', '-A', UA, 'https://nominatim.openstreetmap.org/search?' + q],
                       capture_output=True, text=True)
    _last[0] = time.time()
    _spent[0] += 1
    try:
        res = json.loads(r.stdout)
    except ValueError:
        return None
    _O[k] = [round(float(res[0]['lat']), 6), round(float(res[0]['lon']), 6)] if res else None
    return tuple(_O[k]) if _O[k] else None


def geocode_events(evs, norm):
    """Rellena lat, lng y distrito de los eventos que no los traen. `norm` normaliza nombres de lugar."""
    _load()
    known = {}
    for e in evs:
        e['distrito'] = canon_district(e.get('distrito'))
        if e.get('lat') and e.get('lng') and e.get('lugar'):
            known.setdefault(norm(e['lugar']), (e['lat'], e['lng']))
    stats = {'lugar': 0, 'direccion': 0, 'osm': 0, 'sin': 0}
    for e in evs:
        if e.get('no_geo') or any(f.get('nombre') == 'Estrenos de cine' for f in e.get('fuentes') or []):
            continue
        if not (e.get('lat') and e.get('lng')):
            ll, how = None, None
            lk = norm(e.get('lugar') or '')
            if lk in known:
                ll, how = known[lk], 'lugar'
            if not ll and e.get('direccion'):
                ll, how = by_address(e['direccion'], strict=True), 'direccion'
            if not ll and e.get('lugar'):
                ll, how = by_place(e['lugar']), 'osm'
            if not ll and e.get('direccion'):
                ll, how = by_address(e['direccion']), 'direccion'
            if ll:
                e['lat'], e['lng'] = ll
                if lk:
                    known.setdefault(lk, ll)
                stats[how] += 1
            else:
                stats['sin'] += 1
        if e.get('lat') and e.get('lng') and not e.get('distrito'):
            e['distrito'] = district_at(e['lat'], e['lng'])
    save()
    return stats
