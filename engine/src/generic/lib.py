"""Utilidades del motor genérico de ciudades: Supabase, Claude, descargas educadas (robots.txt) y texto de páginas.

Variables: SUPABASE_URL, SUPABASE_SECRET, ANTHROPIC_API_KEY, BUREO_CITY, BUREO_CITY_MAX_USD (2.0), BUREO_AI_MODEL.
"""
import gzip, html, json, os, re, time, unicodedata, urllib.error, urllib.parse, urllib.request, urllib.robotparser
from html.parser import HTMLParser

URL = os.environ['SUPABASE_URL'].rstrip('/')
SKEY = os.environ['SUPABASE_SECRET']
AKEY = os.environ.get('ANTHROPIC_API_KEY')
MODEL = os.environ.get('BUREO_AI_MODEL', 'claude-haiku-5-5')
MAX_USD = float(os.environ.get('BUREO_CITY_MAX_USD', '2.0'))
UA = 'LimonBot/1.0 (+https://limonplanes.com; hola@limonplanes.com)'
UA_API = 'Limon/1.0 (+https://limonplanes.com; hola@limonplanes.com)'  # APIs de datos abiertos que rechazan «bot»
COST = {'in': 0, 'out': 0, 'search': 0}
AGREGADORES = ('eventbrite.', 'meetup.', 'timeout.', 'feverup.', 'fever.', 'ticketmaster.', 'entradas.com', 'correrjuntos.',
               'instagram.', 'facebook.', 'x.com', 'twitter.', 'tiktok.', 'youtube.', 'google.', 'wikipedia.', 'tripadvisor.',
               'atrapalo.', 'ticketea.', 'allevents.', 'eventseeker.', 'songkick.', 'bandsintown.', 'dice.fm', 'wegow.', 'giglon.',
               'xceed.', 'fourvenues.', 'residentadvisor.', 'ra.co', 'clubbingspain.', 'linktr.ee')


def usd():
    return (COST['in'] * 0.10 + COST['out'] * 0.50) / 1e6 + COST['search'] * 0.01


def over_budget():
    return usd() >= MAX_USD


# ---------- Supabase
def sb(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    for k, v in (('apikey', SKEY), ('Authorization', 'Bearer ' + SKEY), ('Content-Type', 'application/json')):
        req.add_header(k, v)
    if prefer:
        req.add_header('Prefer', prefer)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            t = r.read().decode()
            return json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {path}: {e.code} {e.read().decode()[:400]}')


def load_city(cid):
    rows = sb('GET', f'cities?id=eq.{urllib.parse.quote(cid)}&select=*')
    if not rows:
        raise SystemExit(f'No existe la ciudad {cid} en la tabla cities')
    return rows[0]


# ---------- texto
def fold(t):
    t = unicodedata.normalize('NFD', str(t or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9ñ ]', ' ', ''.join(c for c in t if unicodedata.category(c) != 'Mn'))).strip()


def domain(u):
    try:
        h = urllib.parse.urlparse(u).hostname or ''
    except ValueError:
        return ''
    return re.sub(r'^www\d?\.', '', h.lower())


def is_aggregator(u):
    d = domain(u)
    return any(a in d for a in AGREGADORES)


# ---------- descargas
_robots = {}


def allowed(url):
    p = urllib.parse.urlparse(url)
    base = f'{p.scheme}://{p.netloc}'
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + '/robots.txt', headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                rp.parse(r.read().decode('utf-8', 'replace').splitlines())
        except Exception:
            rp.parse([])  # sin robots.txt (o inaccesible): se permite
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


_last = {}


def fetch(url, limit=4_000_000, accept='text/html,application/xhtml+xml,application/json,text/calendar,*/*;q=0.8', ua=UA):
    """(estado, tipo de contenido, texto, url final). Respeta robots.txt y espera 1 s entre peticiones al mismo sitio."""
    if not allowed(url):
        return 'robots', '', '', url
    d = domain(url)
    wait = 1.0 - (time.time() - _last.get(d, 0))
    if wait > 0:
        time.sleep(wait)
    _last[d] = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': accept,
                                               'Accept-Language': 'es-ES,es;q=0.9', 'Accept-Encoding': 'gzip'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read(limit)
            if r.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            ct = (r.headers.get('Content-Type') or '').lower()
            m = re.search(r'charset=([\w-]+)', ct)
            enc = m.group(1) if m else 'utf-8'
            try:
                txt = raw.decode(enc, 'replace')
            except LookupError:
                txt = raw.decode('utf-8', 'replace')
            return r.status, ct, txt, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, '', '', url
    except Exception as e:
        return 'error:' + type(e).__name__, '', '', url


class _Page(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'head', 'iframe', 'form'}
    BLOCK = {'p', 'div', 'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'article', 'section', 'dd', 'dt', 'time', 'td'}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.links, self.ld, self.alts = base, [], [], [], []
        self._skip, self._href, self._atext, self._ld = 0, None, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self._ld = []
            return
        if tag == 'link' and (a.get('rel') or '').lower() == 'alternate' and a.get('href'):
            self.alts.append((a.get('type') or '', urllib.parse.urljoin(self.base, a['href'])))
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.out.append('\n')
        if tag == 'a' and a.get('href'):
            self._href, self._atext = urllib.parse.urljoin(self.base, a['href'].strip()), []
        if tag == 'time' and a.get('datetime'):
            self.out.append(f' [{a["datetime"]}] ')

    def handle_endtag(self, tag):
        if tag == 'script' and self._ld is not None:
            self.ld.append(''.join(self._ld))
            self._ld = None
            return
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == 'a' and self._href:
            t = re.sub(r'\s+', ' ', ''.join(self._atext)).strip()
            self.links.append((t, self._href))
            if t and not self._skip:
                self.out.append(f' ‹{self._href}›')
            self._href = None

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        self.out.append(data)
        if self._href is not None:
            self._atext.append(data)


def parse_page(html_text, base):
    p = _Page(base)
    try:
        p.feed(html_text)
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(p.out))
    text = re.sub(r'\n\s*\n+', '\n', text).strip()
    return {'text': text, 'links': p.links, 'ld': p.ld, 'alts': p.alts}


# ---------- Claude
def claude(body, timeout=300):
    if not AKEY:
        raise SystemExit('Falta ANTHROPIC_API_KEY')
    for attempt in range(3):
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
        for k, v in (('x-api-key', AKEY), ('anthropic-version', '2023-06-01'), ('content-type', 'application/json')):
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print('  API', e.code, e.read().decode()[:200])
            time.sleep(10 * (attempt + 1))
            continue
        u = res.get('usage') or {}
        COST['in'] += u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0) + 0.1 * u.get('cache_read_input_tokens', 0)
        COST['out'] += u.get('output_tokens', 0)
        COST['search'] += (u.get('server_tool_use') or {}).get('web_search_requests', 0)
        return res
    return {}


def tool_call(system, user, tool, max_tokens=8000, extra_tools=None, forced=True):
    """Llama a Claude obligándole a responder con una herramienta; devuelve su entrada (dict) o None."""
    body = {'model': MODEL, 'max_tokens': max_tokens, 'system': system, 'tools': (extra_tools or []) + [tool],
            'messages': [{'role': 'user', 'content': user}]}
    if forced and not extra_tools:
        body['tool_choice'] = {'type': 'tool', 'name': tool['name']}
    for _ in range(4):
        res = claude(body)
        blk = [b for b in res.get('content', []) if b.get('type') == 'tool_use' and b.get('name') == tool['name']]
        if blk:
            return blk[0]['input']
        if not res:
            return None
        if res.get('stop_reason') == 'pause_turn':
            body['messages'] += [{'role': 'assistant', 'content': res.get('content', [])}]
            continue
        body['messages'] += [{'role': 'assistant', 'content': res.get('content', [])},
                             {'role': 'user', 'content': f'Guarda ahora el resultado con {tool["name"]}.'}]
        body['tool_choice'] = {'type': 'tool', 'name': tool['name']}
    return None
