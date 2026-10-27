"""Convierte un corte (calles, tramos, líneas y estaciones) en geometría para el mapa.

Formato que se guarda en closures.geom:
  {"segs": [{"l": [[lat, lon], …], "x": true|false, "n": "Calle de Serrano"}],
   "pts":  [{"p": [lat, lon], "n": "Plaza de Cibeles"}]}
x = true si es el tramo exacto que da el aviso; false si se pinta la vía entera porque el aviso no concreta.
"""
import heapq, json, math, os, re, subprocess, time, urllib.parse
from geo_index import RAW, street_key, name_key, fold, dist, simplify

UA = 'LimonBot/1.0 (+https://limonplanes.com)'
OSM_CACHE = os.path.join(RAW, 'osm_ways.json')
OSM_DAYS = 30
_O = None
_last = [0.0]

ALIAS = {'neptuno': 'canovas castillo', 'carlos v': 'emperador carlos v', 'calle 30': None, 'm-30': None,
         'bypass': None, 'nudo norte': None}
SIN_EJE = ('autovia', 'autopista', 'carretera')  # sin portales fiables: solo si hay tramo
MAX_ENTERA = 4500
PUNTUALES_T = ('plaza', 'glorieta', 'plazuela')  # m: una vía entera más larga que esto sin tramo no se pinta (sería engañoso)

_S = _M = None


def _load():
    global _S, _M
    if _S is None:
        p = os.path.join(RAW, 'geo_streets.json')
        _S = json.load(open(p)) if os.path.exists(p) else {}
        p = os.path.join(RAW, 'geo_metro.json')
        _M = json.load(open(p)) if os.path.exists(p) else {'stations': {}, 'lines': {}}
    return bool(_S)


def candidates(name):
    tipo, k = street_key(name)
    full = name_key(name)
    k = ALIAS.get(k, k)
    full = ALIAS.get(full, full)
    if k is None or full is None:
        return tipo, full, []
    out = list(_S.get(k, []))
    out += [e for e in _S.get(full, []) if e not in out]
    return tipo, full, out


def find(name):
    """La vía del callejero que mejor encaja con un nombre libre."""
    tipo, full, cands = candidates(name)
    if not cands:
        return None

    def score(e):
        return ((tipo is not None and e['tipo'] == tipo) * 3 + (e in _S.get(full, [])) * 2 +
                (tipo is None and e['tipo'] in ('calle', 'avenida', 'paseo')) + min(e['n'], 400) / 1000)
    return max(cands, key=score)


def official(e):
    """'Calle de la Puerta Del Sol' → 'Calle de la Puerta del Sol'."""
    return re.sub(r"\b(De|Del|La|Las|Los|El|Y|E)\b", lambda m: m.group(1).lower(), e['nombre'])


def osm_ways(street):
    """Trazados (OpenStreetMap, vía Nominatim) de las vías de Madrid que se llaman exactamente así.
    Se guardan en caché y como mucho se hace una consulta por segundo, como pide su política de uso."""
    global _O
    if _O is None:
        _O = json.load(open(OSM_CACHE)) if os.path.exists(OSM_CACHE) else {}
    k = fold(street).strip()
    hit = _O.get(k)
    if hit and time.time() - hit['t'] < OSM_DAYS * 86400:
        return hit['w']
    if os.environ.get('BUREO_NO_OSM'):
        return hit['w'] if hit else []
    wait = 1.1 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    q = urllib.parse.urlencode({'street': street, 'city': 'Madrid', 'country': 'es', 'format': 'jsonv2',
                                'polygon_geojson': 1, 'limit': 50, 'dedupe': 0, 'namedetails': 1})
    r = subprocess.run(['curl', '-sS', '-m', '40', '-A', UA, 'https://nominatim.openstreetmap.org/search?' + q],
                       capture_output=True, text=True)
    _last[0] = time.time()
    try:
        res = json.loads(r.stdout)
    except ValueError:
        return hit['w'] if hit else []
    want = name_key(street)
    ways = []
    for x in res:
        nm = (x.get('namedetails') or {}).get('name') or x.get('name') or ''
        if x.get('category') not in ('highway', 'place') or name_key(nm) != want:
            continue
        g = x.get('geojson') or {}
        if g.get('type') == 'LineString':
            ways.append([[round(c[1], 6), round(c[0], 6)] for c in g['coordinates']])
        elif g.get('type') == 'Polygon':
            ways.append([[round(c[1], 6), round(c[0], 6)] for c in g['coordinates'][0]])
        elif g.get('type') == 'MultiPolygon':
            ways += [[[round(c[1], 6), round(c[0], 6)] for c in poly[0]] for poly in g['coordinates']]
        elif g.get('type') == 'Point':
            ways.append([[round(g['coordinates'][1], 6), round(g['coordinates'][0], 6)]])
    _O[k] = {'t': time.time(), 'w': ways}
    return ways


def save_osm():
    if _O is not None:
        json.dump(_O, open(OSM_CACHE, 'w'), ensure_ascii=False, separators=(',', ':'))


def osm_for(name):
    """Trazado OSM de una vía nombrada libremente: primero con su nombre oficial del callejero, luego tal cual."""
    e = find(name)
    for q in ([official(e)] if e else []) + [name]:
        w = osm_ways(q)
        if w:
            return e, w
    return e, []


def graph(ways):
    g = {}
    for w in ways:
        for a, b in zip(w, w[1:]):
            ka, kb = tuple(a), tuple(b)
            d = dist(a, b)
            g.setdefault(ka, []).append((kb, d))
            g.setdefault(kb, []).append((ka, d))
    # puentes entre nodos muy cercanos: calzadas separadas por mediana y trozos que OSM no une
    ns = list(g)
    for i, a in enumerate(ns):
        for b in ns[i + 1:]:
            d = dist(a, b)
            if 0 < d < 45:
                g[a].append((b, d * 1.5))
                g[b].append((a, d * 1.5))
    return g


def path(g, a, b):
    """Camino más corto por las vías de la calle entre dos nodos (Dijkstra)."""
    best, prev, q = {a: 0}, {}, [(0, a)]
    while q:
        d, n = heapq.heappop(q)
        if n == b:
            out = [n]
            while n in prev:
                n = prev[n]
                out.append(n)
            return [list(x) for x in reversed(out)]
        if d > best.get(n, 1e18):
            continue
        for m, w in g.get(n, ()):
            if d + w < best.get(m, 1e18):
                best[m] = d + w
                prev[m] = n
                heapq.heappush(q, (d + w, m))
    return None


def osm_cross(nodes, cross):
    """Nodo de la calle principal donde la cruza `cross` (OSM). Entre homónimas, la más cercana."""
    _, _, cands = candidates(cross)
    names = [official(e) for e in cands] + [cross]
    best = None
    for nm in dict.fromkeys(names):
        for w in osm_ways(nm):
            for p in w:
                t = tuple(p)
                if t in nodes:
                    return t
                for n in nodes:
                    d = dist(n, p)
                    if best is None or d < best[0]:
                        best = (d, n)
    if best is None:  # OSM no la conoce con ese nombre: se usa el callejero (portales)
        for e in cands:
            for p in [e['c']] + [q for l in e.get('l', []) for q in densify(l, 20)]:
                for n in nodes:
                    d = dist(n, p)
                    if best is None or d < best[0]:
                        best = (d, n)
        return best[1] if best and best[0] < 200 else None
    return best[1] if best and best[0] < 120 else None


def merge(ways):
    """Une las vías OSM que comparten extremos para mandar menos trozos al mapa."""
    ways = [list(w) for w in ways if len(w) > 1]
    changed = True
    while changed:
        changed = False
        for i in range(len(ways)):
            for j in range(len(ways)):
                if i == j or not ways[i] or not ways[j]:
                    continue
                a, b = ways[i], ways[j]
                if a[-1] == b[0]:
                    ways[i] = a + b[1:]
                elif a[-1] == b[-1]:
                    ways[i] = a + b[::-1][1:]
                elif a[0] == b[-1]:
                    ways[i] = b + a[1:]
                else:
                    continue
                ways[j] = []
                changed = True
    return [[[round(x, 5), round(y, 5)] for x, y in simplify([tuple(p) for p in w], 3)] for w in ways if w]


def diag(ways):
    pts = [p for w in ways for p in w]
    return dist([min(p[0] for p in pts), min(p[1] for p in pts)], [max(p[0] for p in pts), max(p[1] for p in pts)])


def length(l):
    return sum(dist(l[i], l[i + 1]) for i in range(len(l) - 1))


def project(line, p):
    """Punto de la polilínea más cercano a p → (distancia, posición continua i+t, punto)."""
    best = (1e18, 0, line[0])
    ky = 111_320
    kx = 111_320 * math.cos(math.radians(40.42))
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        ax, ay, bx, by, px, py = a[1] * kx, a[0] * ky, b[1] * kx, b[0] * ky, p[1] * kx, p[0] * ky
        L2 = (bx - ax) ** 2 + (by - ay) ** 2
        t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / L2))
        q = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        d = dist(q, p)
        if d < best[0]:
            best = (d, i + t, q)
    if len(line) == 1:
        best = (dist(line[0], p), 0, line[0])
    return best


def densify(line, step=25):
    out = []
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        n = max(1, int(dist(a, b) // step))
        out += [[a[0] + (b[0] - a[0]) * j / n, a[1] + (b[1] - a[1]) * j / n] for j in range(n)]
    return out + line[-1:]


def crossing(pieces, name):
    """Dónde se cruza la vía principal con la vía `name` → (pieza, posición, punto de cruce).
    Entre las vías que se llaman así se queda con la más cercana (p. ej. «Quevedo» junto a Fuencarral es la glorieta)."""
    tipo, _, cands = candidates(name)
    best = None
    dense = [densify(l, 20) for l in pieces]
    for e in cands:
        other = [e['c']] + [p for l in e.get('l', []) for p in densify(l, 20)]
        for pi, line in enumerate(dense):
            for a in line:
                for b in other:
                    d = dist(a, b)
                    if best is None or d < best[0]:
                        best = (d, pi, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] if d > 30 else a)
    if not best or best[0] > 260:
        return None
    d, pi, p = best
    return pi, p


def tramo(line, pa, pb):
    """Trozo de la polilínea entre dos puntos de cruce; si un cruce queda más allá del último portal, se alarga."""
    line = [list(x) for x in line]
    da, a, qa = project(line, pa)
    db, b, qb = project(line, pb)
    for d, pos, p in ((da, a, pa), (db, b, pb)):
        if d > 25 and pos <= 0.001:
            line.insert(0, p)
        elif d > 25 and pos >= len(line) - 1.001:
            line.append(p)
    _, a, _ = project(line, pa)
    _, b, _ = project(line, pb)
    return cut(line, a, b) if abs(a - b) > 0.02 else None


def cut(line, a, b):
    a, b = sorted((a, b))
    i, j = int(a), int(b)
    pt = lambda x: [line[int(x)][0] + (line[min(int(x) + 1, len(line) - 1)][0] - line[int(x)][0]) * (x - int(x)),
                    line[int(x)][1] + (line[min(int(x) + 1, len(line) - 1)][1] - line[int(x)][1]) * (x - int(x))]
    seg = [pt(a)] + line[i + 1:j + 1] + [pt(b)]
    return [[round(x, 5), round(y, 5)] for x, y in seg]


def street_geom(name, frm=None, to=None):
    e, ways = osm_for(name)
    nombre = official(e) if e else name
    if ways:
        lines = [w for w in ways if len(w) > 1]
        if not lines or (e and e['tipo'] in PUNTUALES_T and not (frm and to)):
            pts = [p for w in ways for p in w]
            return {'pt': {'p': [round(sum(p[0] for p in pts) / len(pts), 5), round(sum(p[1] for p in pts) / len(pts), 5)],
                           'n': nombre}}
        if frm and to:
            g = graph(lines)
            a, b = osm_cross(g, frm), osm_cross(g, to)
            if a and b and a != b:
                pth = path(g, a, b)
                if pth and len(pth) > 1:
                    return {'segs': [{'l': merge([pth])[0], 'x': True, 'n': nombre}]}
        if (not e or e['tipo'] not in SIN_EJE) and diag(lines) <= MAX_ENTERA:
            return {'segs': [{'l': l, 'x': False, 'n': nombre} for l in merge(lines)]}
        if not e:
            return None
    if not e:
        return None
    # sin OpenStreetMap: eje aproximado a partir de los portales del callejero
    pieces = e.get('l') or []
    if not pieces:
        return {'pt': {'p': e['c'], 'n': nombre}}
    if frm and to:
        ca, cb = crossing(pieces, frm), crossing(pieces, to)
        if ca and cb and ca[0] == cb[0]:
            seg = tramo(pieces[ca[0]], ca[1], cb[1])
            if seg:
                return {'segs': [{'l': seg, 'x': True, 'n': nombre}]}
    if e['tipo'] in SIN_EJE or sum(length(l) for l in pieces) > MAX_ENTERA:
        return None
    return {'segs': [{'l': l, 'x': False, 'n': nombre} for l in pieces if len(l) > 1]}


def station(name):
    k = fold(name).strip()
    st = _M['stations']
    if k in st:
        return st[k]
    hits = [s for s in st if re.search(r'(^|\s)' + re.escape(k) + r'($|\s)', s)]
    return st[min(hits, key=len)] if hits else None


def metro_geom(lines, stations):
    segs, pts = [], []
    coords = [(s, station(s)) for s in stations]
    for s, c in coords:
        if c:
            pts.append({'p': c, 'n': s, 'metro': True})
    for ref in lines:
        r = ref.upper().strip()
        r = r if r.startswith('L') else 'L' + r
        ln = _M['lines'].get(r)
        found = [c for _, c in coords if c]
        if not ln:
            continue
        L = ln['l']
        if len(found) >= 2:
            a, b = project(L, found[0])[1], project(L, found[-1])[1]
            arc = cut(L, a, b)
            closed = dist(L[0], L[-1]) < 400  # línea circular (L6, L12): el arco más corto
            if closed:
                lo, hi = sorted((a, b))
                other = cut(L, hi, len(L) - 1)[:-1] + cut(L, 0, lo)
                arc = min(arc, other, key=length)
            segs.append({'l': arc, 'x': True, 'n': r, 'c': ln['color']})
        elif not found:
            segs.append({'l': L, 'x': False, 'n': r, 'c': ln['color']})
    return segs, pts


TRAMO_RX = re.compile(r'\bentre\s+(?:la |el |los |las )?(.+?)\s+y\s+(?:la |el |los |las )?(.+?)(?=[,.;:(]|\s+(?:los|las|el|del|de \d|desde|por|sentido|hasta|en ambos|a partir)\b|$)', re.I)


def tramos_from_text(streets, text):
    """Si el aviso nombra una sola vía y dice «entre X y Y», ese es el tramo."""
    if len(streets) != 1 or not text:
        return []
    m = TRAMO_RX.search(text)
    return [{'street': streets[0], 'from': m.group(1).strip(), 'to': m.group(2).strip()}] if m else []


def geom_for(row, tramos=None):
    if not _load():
        return None
    segs, pts = [], []
    if row['kind'] in ('metro', 'metro_ligero'):
        s, p = metro_geom(row.get('lines') or [], row.get('stations') or [])
        segs += s
        pts += p
    tramos = tramos or tramos_from_text(row.get('streets') or [], row.get('summary'))
    by_street = {}
    for t in tramos:
        if t.get('street') and t.get('from') and t.get('to'):
            by_street.setdefault(name_key(t['street']), []).append(t)
    for name in row.get('streets') or []:
        ts = by_street.get(name_key(name)) or [None]
        for t in ts:
            g = street_geom(name, t and t['from'], t and t['to'])
            if not g:
                continue
            if 'pt' in g:
                pts.append(g['pt'])
            else:
                segs += g['segs']
    if not segs and not pts:
        return None
    return {'segs': segs, 'pts': pts}
