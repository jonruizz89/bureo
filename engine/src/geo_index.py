"""Índices geográficos para pintar los cortes en el mapa.

- raw/geo_streets.json: eje aproximado de cada vía de Madrid, construido con los portales del Callejero oficial
  del Ayuntamiento (datos abiertos, con coordenadas). Clave: nombre normalizado → lista de vías con esa clave.
- raw/geo_metro.json: estaciones de Metro y trazado de cada línea, del GTFS del Consorcio de Transportes.

Se rehace como mucho una vez por semana (el callejero ocupa unos 35 MB).
"""
import csv, io, json, math, os, re, subprocess, sys, time, unicodedata, zipfile
from collections import defaultdict

RAW = os.environ.get('BUREO_RAW', 'raw')
CALLEJERO = ('https://datos.madrid.es/dataset/213605-0-callejero-oficial-madrid/resource/'
             '213605-4-callejero-oficial-madrid-csv/download/213605-4-callejero-oficial-madrid-csv.csv')
METRO_GTFS = 'https://www.arcgis.com/sharing/rest/content/items/5c7f2951962540d69ffe8f640d94c246/data'
UA = 'LimonBot/1.0 (+https://limonplanes.com)'

TIPOS = ('calle', 'avenida', 'paseo', 'plaza', 'glorieta', 'ronda', 'carretera', 'camino', 'travesia', 'pasaje',
         'autovia', 'autopista', 'bulevar', 'callejon', 'cuesta', 'costanilla', 'carrera', 'plazuela', 'puerta',
         'via', 'gran via', 'pista', 'acceso', 'puente', 'tunel', 'c/', 'avda', 'avda.', 'pza', 'pza.', 'p.º', 'pº')
PUNTUALES = ('plaza', 'glorieta', 'plazuela')


def fold(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def street_key(name):
    """'Calle de Francisco Largo Caballero' → ('calle', 'francisco largo caballero')."""
    s = re.sub(r'[^a-z0-9ñ/ .ºª-]', ' ', fold(name)).strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'^(la|el|los|las) ', '', s)
    tipo = None
    if not s.startswith('gran via'):  # «Gran Vía» y «Gran Vía del Este» son nombres, no tipos
        for t in sorted(TIPOS, key=len, reverse=True):
            if s.startswith(t + ' '):
                tipo, s = t.rstrip('.'), s[len(t) + 1:]
                break
    tipo = {'c/': 'calle', 'avda': 'avenida', 'pza': 'plaza', 'p.º': 'paseo', 'pº': 'paseo'}.get(tipo, tipo)
    s = re.sub(r'^(de los|de las|de la|del|de|la|el|los|las)\s+', '', s)
    s = re.sub(r'\b(de los|de las|de la|del|de)\b', ' ', s)
    return tipo, re.sub(r'\s+', ' ', s).strip(' .-')


def name_key(name):
    """Nombre completo sin tipo de vía delante ni partículas: 'Puerta del Sol' → 'puerta sol'."""
    s = re.sub(r'[^a-z0-9ñ/ .ºª-]', ' ', fold(name))
    s = re.sub(r'\b(de los|de las|de la|del|de)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip(' .-')


def dms(s):
    m = re.match(r"\s*(\d+)°(\d+)'([\d.]+)''\s*([NSEW])", s or '')
    if not m:
        return None
    v = int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3)) / 3600
    return -v if m.group(4) in 'SW' else v


def dist(a, b):
    """Distancia aproximada en metros entre (lat, lon)."""
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(40.42))
    return math.hypot(dx, dy)


def simplify(pts, tol=8):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    best, idx = -1, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        # distancia del punto a la recta a-b (aprox. plana)
        ax, ay = a[1] * 84_800, a[0] * 111_320
        bx, by = b[1] * 84_800, b[0] * 111_320
        px, py = p[1] * 84_800, p[0] * 111_320
        L = math.hypot(bx - ax, by - ay) or 1e-9
        d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [a, b]
    return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def side_line(pts):
    """Portales de un lado ordenados por número, partidos donde hay huecos grandes."""
    pts = sorted(pts)
    out, cur = [], []
    for _, p in pts:
        if cur and dist(cur[-1], p) > 350:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def axis(odd, even):
    """Eje de la calle: los portales de un lado acercados a la mitad con los del lado contrario."""
    a = side_line(odd) if len(odd) >= len(even) else side_line(even)
    other = [p for _, p in (even if len(odd) >= len(even) else odd)]
    lines = []
    for seg in a:
        line = []
        for p in seg:
            near = min(other, key=lambda q: dist(p, q)) if other else None
            if near and dist(p, near) < 70:
                p = ((p[0] + near[0]) / 2, (p[1] + near[1]) / 2)
            line.append(p)
        # suavizado ligero
        sm = []
        for i in range(len(line)):
            w = line[max(0, i - 1):i + 2]
            sm.append((sum(x[0] for x in w) / len(w), sum(x[1] for x in w) / len(w)))
        lines.append([[round(x[0], 5), round(x[1], 5)] for x in simplify(sm)])
    return [l for l in lines if l]


def curl(url, dest):
    for i in range(4):
        r = subprocess.run(['curl', '-sSL', '-m', '600', '-A', UA, '-o', dest, '-w', '%{http_code}', url],
                           capture_output=True, text=True)
        if r.stdout == '200' and os.path.getsize(dest) > 1000:
            return True
        print('descarga', url[:60], 'intento', i + 1, r.stdout, flush=True)
        time.sleep(15 * (i + 1))
    return False


def build_streets():
    tmp = os.path.join(RAW, 'callejero.csv')
    if not curl(CALLEJERO, tmp):
        print('aviso: no se pudo descargar el callejero')
        return False
    vias = {}
    portals = defaultdict(list)   # clave de calle → [número, lat, lon, distrito]
    grid = defaultdict(lambda: defaultdict(int))  # celda de ~500 m → distritos
    with open(tmp, encoding='latin-1') as f:
        for r in csv.DictReader(f, delimiter=';'):
            if r['CLASE_APP'] != 'NUMERO':
                continue
            lat, lon = dms(r['LATITUD']), dms(r['LONGITUD'])
            try:
                n = int(r['NUMERO'])
            except ValueError:
                continue
            if lat is None or lon is None:
                continue
            v = vias.setdefault(r['COD_VIA'], {'clase': r['VIA_CLASE'], 'par': r['VIA_PAR'],
                                               'nombre': r['VIA_NOMBRE_ACENTOS'], 'odd': [], 'even': []})
            v['odd' if n % 2 else 'even'].append((n, (lat, lon)))
            try:
                dcode = int(r['DISTRITO'])
            except ValueError:
                dcode = 0
            v['d'] = dcode
            grid[f'{round(lat / 0.005)}:{round(lon / 0.006)}'][dcode] += 1
    idx = defaultdict(list)
    for cod, v in vias.items():
        tipo = fold(v['clase'])
        allp = [p for _, p in v['odd'] + v['even']]
        c = [round(sum(p[0] for p in allp) / len(allp), 5), round(sum(p[1] for p in allp) / len(allp), 5)]
        entry = {'cod': cod, 'tipo': tipo, 'nombre': f"{v['clase'].title()} {v['par'].lower() + ' ' if v['par'] else ''}{v['nombre'].title()}",
                 'c': c, 'n': len(allp)}
        if tipo not in PUNTUALES:
            entry['l'] = axis(v['odd'], v['even'])
        _, k = street_key(v['nombre'])
        idx[k].append(entry)
        pts = sorted([(n, p) for n, p in v['odd'] + v['even']])
        portals[k].append({'tipo': tipo, 'p': [[n, round(p[0], 5), round(p[1], 5)] for n, p in pts], 'd': v.get('d', 0)})
        full = name_key(v['nombre'])
        if full != k:
            idx[full].append(entry)
    json.dump(idx, open(os.path.join(RAW, 'geo_streets.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    cells = {c: max(ds.items(), key=lambda x: x[1])[0] for c, ds in grid.items()}
    json.dump({'portals': portals, 'cells': cells}, open(os.path.join(RAW, 'geo_portals.json'), 'w'),
              ensure_ascii=False, separators=(',', ':'))
    os.remove(tmp)
    print(len(vias), 'vías en el índice de calles')
    return True


def build_metro():
    tmp = os.path.join(RAW, 'metro_gtfs.zip')
    if not curl(METRO_GTFS, tmp):
        print('aviso: no se pudo descargar el GTFS de Metro')
        return False
    z = zipfile.ZipFile(tmp)
    rd = lambda n: csv.DictReader(io.TextIOWrapper(z.open(n), encoding='utf-8-sig'))
    stations = {}
    for s in rd('stops.txt'):
        if s['location_type'] == '1':
            stations.setdefault(fold(s['stop_name']).strip(), [round(float(s['stop_lat']), 5), round(float(s['stop_lon']), 5)])
    routes = {r['route_id']: {'ref': r['route_short_name'], 'color': '#' + r['route_color']} for r in rd('routes.txt')}
    shape_of = {}
    for t in rd('trips.txt'):
        if t['direction_id'] == '0':
            shape_of.setdefault(t['route_id'], set()).add(t['shape_id'])
    pts = defaultdict(list)
    for p in rd('shapes.txt'):
        pts[p['shape_id']].append((int(p['shape_pt_sequence']), float(p['shape_pt_lat']), float(p['shape_pt_lon'])))
    lines = {}
    for rid, r in routes.items():
        shapes = [sorted(pts[s]) for s in shape_of.get(rid, ()) if pts.get(s)]
        if not shapes:
            continue
        sh = max(shapes, key=len)
        line = simplify([(la, lo) for _, la, lo in sh], 5)
        lines['L' + r['ref'].upper()] = {'color': r['color'], 'l': [[round(a, 5), round(b, 5)] for a, b in line]}
    json.dump({'stations': stations, 'lines': lines}, open(os.path.join(RAW, 'geo_metro.json'), 'w'),
              ensure_ascii=False, separators=(',', ':'))
    os.remove(tmp)
    print(len(stations), 'estaciones y', len(lines), 'líneas de Metro')
    return True


def fresh(name, days=7):
    p = os.path.join(RAW, name)
    return os.path.exists(p) and time.time() - os.path.getmtime(p) < days * 86400


if __name__ == '__main__':
    os.makedirs(RAW, exist_ok=True)
    force = '--force' in sys.argv
    if force or not fresh('geo_streets.json') or not fresh('geo_portals.json'):
        build_streets()
    if force or not fresh('geo_metro.json'):
        build_metro()
