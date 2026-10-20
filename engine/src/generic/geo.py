"""Situar lugares en el mapa dentro de los límites de una ciudad.

1. Lo que ya está en la tabla `venues` (incluidas las correcciones a mano) manda.
2. Si no, se prueban varias formas del nombre en Nominatim (OpenStreetMap) y en Photon, siempre dentro del recuadro de la ciudad:
   el nombre tal cual, la dirección si la hay, lo que va entre paréntesis, la parte antes o después de « - » o de la coma…
3. El distrito sale de la dirección que devuelve OpenStreetMap (city_district, borough, suburb…) comparada con los de la ciudad.
"""
import json, os, re, time, unicodedata, urllib.parse, urllib.request
from lib import fold, UA

RAW = os.environ.get('BUREO_RAW', 'raw')


def norm(t):  # igual que load_supabase.py: clave de recinto
    t = unicodedata.normalize('NFD', (t or '').lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', t)).strip()


NO_PLACE = re.compile(r'^(por determinar|online|en linea|varios|varias|en \d+ municipios|centro municipales de)$|^en \d+ ', re.I)
ADDR = re.compile(r'((?:c/|calle|kalea|zumarkalea|alameda|avenida|av\.|plaza|plazа|pza\.?|paseo|etorbidea|kalea)\s*[^,()]*\d+[^,()]*)|([^,()]*\b\d{1,3}\b[^,()]*)', re.I)


def variants(name, address=None):
    n = re.sub(r'\s+', ' ', (name or '').strip().strip('"«»'))
    out = []
    add = lambda x: out.append(x.strip(' .,-')) if x and len(x.strip(' .,-')) >= 4 and x.strip(' .,-') not in out else None
    if address:
        add(address)
    add(n)
    m = ADDR.search(n)
    if m:
        add(m.group(0))
    for p in re.findall(r'\(([^)]+)\)', n):
        add(p)
    base = re.sub(r'\([^)]*\)', ' ', n)
    for part in re.split(r'\s+[-–/]\s+|,|\.\s', base):
        add(part)
    add(re.sub(r'^(sala|centro municipal de|centro de|local de|atrio de la|sede de la)\s+', '', base, flags=re.I))
    add(re.sub(r'\s*[-–]\s*(fever|bilbao)$', '', base, flags=re.I))
    return out


class Geocoder:
    def __init__(self, city, known=None, max_calls=400):
        self.city = city
        self.path = os.path.join(RAW, f'geo_{city["id"]}.json')
        try:
            self.cache = json.load(open(self.path))
        except Exception:
            self.cache = {}
        self.known = known or {}  # norm(nombre del recinto) → [lat, lng, distrito]
        self.calls, self.max_calls, self.last = 0, max_calls, 0.0
        self.bbox = city.get('bbox') or [city['lng'] - 0.1, city['lat'] - 0.08, city['lng'] + 0.1, city['lat'] + 0.08]
        self.dists = city.get('districts') or []

    def district(self, addr):
        import difflib
        for k in ('borough', 'city_district', 'suburb', 'quarter', 'neighbourhood', 'district'):
            v = fold(addr.get(k))
            if not v:
                continue
            for d in self.dists:
                fd = fold(d)
                if fd == v or v in fd.split(' ') or fd.split(' ')[0] == v.split(' ')[0]:
                    return d
            # grafías en euskera y castellano (Basurtu-Zorrotza / Basurto-Zorroza, Errekalde / Rekalde)
            best = max(self.dists, key=lambda d: difflib.SequenceMatcher(None, fold(d), v).ratio(), default=None)
            if best and difflib.SequenceMatcher(None, fold(best), v).ratio() >= 0.75:
                return best
        return None

    def inside(self, lat, lng):
        b = self.bbox
        return b[0] <= lng <= b[2] and b[1] <= lat <= b[3]

    def _get(self, url):
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time(); self.calls += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def _nominatim(self, q):
        res = self._get('https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
            {'q': q + ', ' + self.city['name'], 'format': 'jsonv2', 'limit': 1, 'addressdetails': 1, 'countrycodes': 'es',
             'viewbox': ','.join(map(str, self.bbox)), 'bounded': 1}))
        if res:
            return [float(res[0]['lat']), float(res[0]['lon']), self.district(res[0].get('address') or {})]
        return None

    def _photon(self, q):
        res = self._get('https://photon.komoot.io/api/?' + urllib.parse.urlencode(
            {'q': q + ' ' + self.city['name'], 'limit': 3, 'lang': 'default', 'lat': self.city['lat'], 'lon': self.city['lng'],
             'bbox': ','.join(map(str, self.bbox))}))
        for f in (res or {}).get('features', []):
            lng, lat = f['geometry']['coordinates']
            if self.inside(lat, lng):
                return [lat, lng, None]
        return None

    def reverse_district(self, lat, lng):
        res = self._get('https://nominatim.openstreetmap.org/reverse?' + urllib.parse.urlencode(
            {'lat': lat, 'lon': lng, 'format': 'jsonv2', 'zoom': 16, 'addressdetails': 1}))
        return self.district((res or {}).get('address') or {})

    def find(self, name, address=None):
        if not name or NO_PLACE.search(norm(name)):
            return None
        k = norm(name)
        if k in self.known:
            return self.known[k]
        if k in self.cache:
            return self.cache[k]
        if self.calls >= self.max_calls:
            return None
        hit = None
        for v in variants(name, address)[:5]:
            if self.calls >= self.max_calls:
                return None
            hit = self._nominatim(v)
            if hit:
                break
        if not hit:
            for v in variants(name, address)[:3]:
                if self.calls >= self.max_calls:
                    return None
                hit = self._photon(v)
                if hit:
                    break
        if hit and not hit[2] and self.calls < self.max_calls:
            hit[2] = self.reverse_district(hit[0], hit[1])
        self.cache[k] = hit
        return hit

    def save(self):
        os.makedirs(RAW, exist_ok=True)
        json.dump(self.cache, open(self.path, 'w'), ensure_ascii=False)
