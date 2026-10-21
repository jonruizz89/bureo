"""Completa los eventos sin descripción leyendo su ficha original (la web de la fuente o del organizador).

Muchas fuentes (p. ej. la agenda municipal de Madrid en datos abiertos) dan título, fecha y lugar pero no el texto,
que sí está en la página del evento. Se guarda en `events.details`, que la carga nocturna no toca, y la app lo muestra
cuando no hay descripción. Cada ficha se lee una vez (y otra a los 30 días si sigue vacía), sin IA.
Variables: SUPABASE_URL, SUPABASE_SECRET. Opcional: BUREO_ENRICH_MAX (fichas por ejecución, 400), BUREO_CITY (solo esa ciudad).
"""
import concurrent.futures as cf, datetime as dt, html, json, os, re, time, urllib.parse, urllib.request
from html.parser import HTMLParser

URL = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ['SUPABASE_SECRET']
MAX = int(os.environ.get('BUREO_ENRICH_MAX', '400'))
CITY = os.environ.get('BUREO_CITY')
UA = 'LimonBot/1.0 (+https://limonplanes.com; hola@limonplanes.com)'
MAXLEN = 1500


def sb(method, path, body=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    for k, v in (('apikey', KEY), ('Authorization', 'Bearer ' + KEY), ('Content-Type', 'application/json'), ('Prefer', 'return=minimal')):
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'es-ES,es;q=0.9'})
    with urllib.request.urlopen(req, timeout=20) as r:
        if 'html' not in (r.headers.get('content-type') or 'html'):
            return ''
        raw = r.read(2_000_000)
        cs = r.headers.get_content_charset() or (re.search(rb'charset=["\']?([\w-]+)', raw[:3000]) or [None, b'utf-8'])[1].decode()
        return raw.decode(cs, errors='replace')


def clean(t):
    return re.sub(r'\s+', ' ', html.unescape(t or '')).strip()


SKIP = {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'aside', 'form', 'svg', 'button', 'select', 'iframe', 'template'}
BLOCK = {'p', 'li', 'h2', 'h3', 'h4', 'dd', 'dt', 'blockquote', 'td'}
NOISE = re.compile(r'cookie|newsletter|suscr[ií]bete|comparte|s[ií]guenos|iniciar sesi[oó]n|todos los derechos|aviso legal|pol[ií]tica de privacidad|'
                   r'accesibilidad|mapa web|redes sociales|volver arriba|compartir en|whatsapp|facebook|twitter|instagram', re.I)


class Blocks(HTMLParser):
    """Bloques de texto de la página fuera de menús, cabecera, pie y formularios, con su contenedor más cercano."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skip, self.cur, self.out = [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'img', 'meta', 'link', 'input', 'hr'):
            return
        a = dict(attrs)
        cls = (a.get('class') or '') + ' ' + (a.get('id') or '')
        bad = tag in SKIP or re.search(r'nocontent|cookie|menu|breadcrumb|share|social|related|relacionad|sidebar|banner|footer|header|nav\b', cls, re.I)
        self.stack.append((tag, bool(bad)))
        if bad:
            self.skip += 1
        if tag in BLOCK and not self.skip and self.cur is None:
            self.cur = [tag, [], len([t for t, _ in self.stack if t in ('div', 'section', 'article', 'main')])]

    def handle_endtag(self, tag):
        while self.stack:
            t, bad = self.stack.pop()
            if bad:
                self.skip -= 1
            if self.cur and t == self.cur[0]:
                txt = clean(' '.join(self.cur[1]))
                if txt:
                    self.out.append((self.cur[0], txt, self.cur[2]))
                self.cur = None
            if t == tag:
                break

    def handle_data(self, d):
        if self.cur is not None and not self.skip:
            self.cur[1].append(d)


def jsonld_desc(page):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack += o
            elif isinstance(o, dict):
                t = o.get('@type')
                if (t == 'Event' or (isinstance(t, str) and t.endswith('Event')) or (isinstance(t, list) and any(str(x).endswith('Event') for x in t))) and o.get('description'):
                    return clean(re.sub(r'<[^>]+>', ' ', str(o['description'])))
                stack += [v for v in o.values() if isinstance(v, (dict, list))]
    return ''


def meta_desc(page):
    for prop in ('og:description', 'description', 'twitter:description'):
        m = re.search(r'<meta[^>]+(?:name|property)=["\']' + prop + r'["\'][^>]*content=["\']([^"\']+)', page, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']' + prop + r'["\']', page, re.I)
        if m:
            return clean(m.group(1))
    return ''


def fold(t):
    return re.sub(r'[^a-z0-9 ]', '', clean(t).lower().translate(str.maketrans('áéíóúüñ', 'aeiouun')))


def extract(page, title):
    """Mejor texto disponible: ficha de madrid.es, JSON-LD del evento, cuerpo de la página o metadescripción."""
    ft = fold(title)
    useful = lambda t: len(t) >= 80 and fold(t) not in (ft, ft + ' ayuntamiento de madrid') and not fold(t).startswith(ft + ' ayuntamiento')
    # madrid.es (agenda municipal): el contenido va en bloques «tiny-text»
    tiny = [clean(re.sub(r'<[^>]+>', ' ', m)) for m in re.findall(r'<div class="tiny-text">(.*?)</div>', page, re.S)]
    tiny = [t for t in tiny if t and not re.match(r'^organizaci[oó]n\b', t, re.I)]
    if tiny and sum(map(len, tiny)) >= 80:
        return '\n\n'.join(tiny)
    ld = jsonld_desc(page)
    if useful(ld):
        return ld
    p = Blocks()
    try:
        p.feed(page)
    except Exception:
        pass
    blocks = [(tag, t, d) for tag, t, d in p.out if len(t) >= 40 and not NOISE.search(t) and fold(t) != ft]
    # mejor los párrafos de verdad; listas y tablas suelen ser calendarios, menús o fichas técnicas
    paras = [x for x in blocks if x[0] == 'p']
    if sum(len(x[1]) for x in paras) >= 150:
        blocks = paras
    if blocks:
        # el contenedor con más texto (misma profundidad), en el orden de la página
        by = {}
        for tag, t, d in blocks:
            by.setdefault(d, []).append(t)
        best = max(by.values(), key=lambda ts: sum(map(len, ts)))
        txt = '\n\n'.join(dict.fromkeys(best))
        if useful(txt):
            return txt
    md = meta_desc(page)
    return md if useful(md) else ''


def cut(t):
    if len(t) <= MAXLEN:
        return t
    t = t[:MAXLEN]
    k = max(t.rfind('. '), t.rfind('\n'))
    return (t[:k + 1] if k > MAXLEN * 0.6 else t.rsplit(' ', 1)[0]).strip() + ' …'


def main():
    hoy = dt.date.today().isoformat()
    otra = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    flt = f'ends_on=gte.{hoy}&or=(description.is.null,description.eq.)&or=(details_at.is.null,and(details.eq.,details_at.lt.{otra}))'
    if CITY:
        flt += f'&city_id=eq.{CITY}'
    todo, off = [], 0
    while len(todo) < MAX:
        page = sb('GET', f'events?select=id,title,organizer_url,event_sources(url)&{flt}&order=starts_on&limit=1000&offset={off}') or []
        todo += page
        if len(page) < 1000:
            break
        off += 1000
    todo = todo[:MAX]
    print(f'{len(todo)} eventos sin descripción por revisar')
    last = {}

    def one(e):
        urls = [s['url'] for s in e.get('event_sources') or [] if s.get('url')] + ([e['organizer_url']] if e.get('organizer_url') else [])
        for u in dict.fromkeys(urls):
            u = re.sub(r'^http://', 'https://', u)
            sp = urllib.parse.urlsplit(u)
            if sp.path in ('', '/') and not sp.query:  # la portada de una web no habla de este evento
                continue
            host = urllib.parse.urlsplit(u).hostname or ''
            wait = 1.0 - (time.time() - last.get(host, 0))  # como mucho una petición por segundo a cada web
            if wait > 0:
                time.sleep(wait)
            last[host] = time.time()
            try:
                txt = extract(get(u), e['title'])
            except Exception:
                txt = ''
            if txt:
                return e['id'], cut(txt)
        return e['id'], ''

    ok = 0
    with cf.ThreadPoolExecutor(4) as ex:
        for eid, txt in ex.map(one, todo):
            sb('PATCH', f'events?id=eq.{eid}', {'details': txt, 'details_at': dt.datetime.utcnow().isoformat() + 'Z'})
            ok += bool(txt)
    print(f'{ok} completados con el texto de su ficha; {len(todo) - ok} sin texto útil')


if __name__ == '__main__':
    main()
