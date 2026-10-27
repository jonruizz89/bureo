"""Fuentes adicionales de Madrid (engine/sources_extra.json) → raw/extra_events.json.

Cada fuente se lee con el método más barato que admite:
- agendacultural: listado HTML de la Agenda Cultural del Ministerio (Filmoteca, BNE, museos estatales…).
- ical_links: calendarios iCal enlazados en la portada (Movistar Arena).
- jsonld: datos schema.org Event de la página.
- estrenos: listado de próximos estrenos de cine.
- llm: el texto de la página de agenda lo convierte Claude Haiku en eventos; solo se vuelve a llamar si la página cambia.

Respeta robots.txt (se comprobó al dar de alta cada fuente) y hace una petición cada pocos segundos.
Variables: ANTHROPIC_API_KEY (para las fuentes llm; sin ella se reutiliza la caché), BUREO_EXTRA_MAX_USD (0.15).
"""
import datetime as dt, hashlib, html, json, os, re, subprocess, sys, time, urllib.error, urllib.request

RAW = os.environ.get('BUREO_RAW', 'raw')
HERE = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(HERE, '..', 'sources_extra.json'), encoding='utf-8'))
CACHE = os.path.join(RAW, 'extra_llm_cache.json')
OUT = os.path.join(RAW, 'extra_events.json')
UA = 'LimonBot/1.0 (+https://limonplanes.com)'
AKEY = os.environ.get('ANTHROPIC_API_KEY')
MODEL = os.environ.get('BUREO_AI_MODEL', 'claude-haiku-5-5')
MAX_USD = float(os.environ.get('BUREO_EXTRA_MAX_USD', '0.15'))
HOY = dt.date.today()
SPENT = {'in': 0, 'out': 0}
MESES = {m: i + 1 for i, m in enumerate(['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
                                          'septiembre', 'octubre', 'noviembre', 'diciembre'])}


def get(url):
    for i in range(3):
        r = subprocess.run(['curl', '-sSL', '-m', '60', '-A', UA, '-w', '\n%{http_code}', url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition('\n')
        if code == '200' and body:
            time.sleep(3)
            return body
        time.sleep(8 * (i + 1))
    print('  sin respuesta:', url[:90], file=sys.stderr)
    return None


def text_of(page):
    t = re.sub(r'<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<nav.*?</nav>|<footer.*?</footer>|<header.*?</header>', ' ', page, flags=re.S | re.I)
    t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', lambda m: ' [' + m.group(1) + '] ', t)
    t = re.sub(r'<(br|/p|/li|/tr|/h\d|/div)[^>]*>', '\n', t, flags=re.I)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    t = re.sub(r'[ \t]+', ' ', t)
    return re.sub(r'\n\s*\n+', '\n', t).strip()


def ev(src, titulo, inicio, fin=None, **k):
    return dict({'fuente': src['name'], 'titulo': titulo, 'inicio': inicio, 'fin': fin or inicio, 'hora': None,
                 'lugar': src.get('venue'), 'direccion': src.get('address'), 'url': None, 'descripcion': '',
                 'precio': None, 'gratis': False, 'cats': list(src.get('cats') or [])}, **{x: y for x, y in k.items() if y})


# --------------------------------------------------------------------------- Agenda Cultural del Ministerio
AC_CATS = {'musica': ['g:musica'], 'cine': ['g:cine'], 'teatro': ['teatro'], 'exposiciones': ['g:arte'], 'exposicion': ['g:arte'],
           'conferencias': ['conf'], 'taller': ['talleres'], 'activinfantiles': ['infantil'], 'danza': ['danza'],
           'visitas': ['visitas'], 'literatura': ['libros'], 'jornadas': ['congresos'], 'opera': ['opera']}


def bonito(t):
    """'MUSEO DE AMÉRICA' → 'Museo de América'."""
    if not t.isupper():
        return t
    return ' '.join((w.lower() if w.lower() in ('de', 'del', 'la', 'las', 'los', 'el', 'y', 'e') and i else w.capitalize())
                    for i, w in enumerate(t.lower().split()))


def sede_info(sid, cache):
    """Dirección y municipio de una sede de la Agenda Cultural (se pide una vez y se guarda)."""
    sedes = cache.setdefault('_sedes', {})
    if sid in sedes:
        return sedes[sid]
    page = get('https://agendacultural.cultura.gob.es/listado/detalle-sede.html?id=' + sid)
    if not page:
        return {}
    t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<script.*?</script>|<style.*?</style>', '', page, flags=re.S))))
    dirc = re.search(r'Dirección: (.*?) (?:Municipio|Código postal|Provincia|País):', t)
    mun = re.search(r'Municipio: (.*?) (?:Código postal|Provincia|País):', t)
    sedes[sid] = {'direccion': dirc.group(1).strip() if dirc else None, 'municipio': mun.group(1).strip() if mun else None}
    return sedes[sid]


def agendacultural(src, cache):
    out = []
    a, b = HOY, HOY + dt.timedelta(days=27)
    url = f'https://agendacultural.cultura.gob.es/listado.html?desde={a:%d/%m/%Y}&hasta={b:%d/%m/%Y}'
    page = get(url)
    if not page:
        return None
    for blk in re.split(r'<div class="registro', page)[1:]:
        m = re.search(r'<h3>\s*<a href="(/listado/detalle\.html\?id=[^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        f = re.search(r'Desde (\d{2})/(\d{2})/(\d{4}) hasta el (\d{2})/(\d{2})/(\d{4})', blk)
        if not m or not f:
            continue
        sede = re.search(r'class="sede".*?<a href="[^"]*detalle-sede\.html\?id=([0-9a-f-]+)"[^>]*>(.*?)</a>', blk, re.S)
        info = sede_info(sede.group(1), cache) if sede else {}
        if (info.get('municipio') or '').strip().lower() != 'madrid':
            continue  # la agenda es nacional: solo sedes del municipio de Madrid
        cats = re.findall(r'/cat-([a-z]+)\.png', blk)
        desc = re.search(r'<p class="descripcion">(.*?)</p>', blk, re.S)
        ini = dt.date(int(f.group(3)), int(f.group(2)), int(f.group(1)))
        fin = dt.date(int(f.group(6)), int(f.group(5)), int(f.group(4)))
        base = []
        for c in cats:
            base += AC_CATS.get(c, [])
        out.append(ev(src, html.unescape(re.sub(r'\s+', ' ', m.group(2))).strip().strip('"'), ini.isoformat(), fin.isoformat(),
                      lugar=bonito(html.unescape(sede.group(2)).strip()) if sede else None, direccion=info.get('direccion'),
                      url='https://agendacultural.cultura.gob.es' + html.unescape(m.group(1)),
                      descripcion=html.unescape(re.sub(r'<[^>]+>|\s+', ' ', desc.group(1))).strip()[:420] if desc else '',
                      cats=base))
    return out


# --------------------------------------------------------------------------- iCal enlazados (Movistar Arena)
def ical_links(src):
    page = get(src['url'])
    if not page:
        return None
    base = re.match(r'https?://[^/]+', src['url']).group(0)
    links = sorted({html.unescape(u) for u in re.findall(r'href="([^"]*\.ics[^"]*|[^"]*ics\.php[^"]*)"', page)})
    out = []
    for u in links[:120]:
        cal = get(u if u.startswith('http') else base + u)
        if not cal:
            continue
        for ve in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', cal, re.S):
            f = dict(re.findall(r'^([A-Z-]+)[^:\n]*:(.*)$', ve.replace('\r', ''), re.M))
            st = f.get('DTSTART', '')
            m = re.match(r'(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})\d{0,2})?(Z?)', st)
            if not m or not f.get('SUMMARY'):
                continue
            d0 = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0))
            if m.group(6) == 'Z':  # UTC → hora de Madrid (aprox. con el horario de verano europeo)
                d0 += dt.timedelta(hours=2 if 3 < d0.month < 11 else 1)
            out.append(ev(src, f['SUMMARY'].strip(), d0.date().isoformat(), hora=f'{d0:%H:%M}' if m.group(4) else None,
                          lugar=src.get('venue') or (f.get('LOCATION') or '').strip() or None, url=f.get('URL', '').strip() or None))
    return out


# --------------------------------------------------------------------------- schema.org Event
def jsonld(src):
    page = get(src['url'])
    if not page:
        return None
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            ty = o.get('@type')
            if isinstance(ty, str) and (ty.endswith('Event') or ty == 'Festival') and o.get('name') and o.get('startDate'):
                ini, fin = str(o['startDate'])[:10], str(o.get('endDate') or o['startDate'])[:10]
                hora = str(o['startDate'])[11:16] if len(str(o['startDate'])) > 15 and str(o['startDate'])[11:16] != '00:00' else None
                if (dt.date.fromisoformat(fin) - dt.date.fromisoformat(ini)).days > 200:
                    return  # abonos y «programación de la temporada»: no son eventos
                out.append(ev(src, html.unescape(str(o['name'])).strip(), ini, fin, hora=hora, url=o.get('url'),
                              descripcion=html.unescape(re.sub(r'<[^>]+>', ' ', str(o.get('description') or '')))[:420]))
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)
    for b in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            walk(json.loads(b.strip()))
        except ValueError:
            pass
    return out


# --------------------------------------------------------------------------- estrenos de cine
def estrenos(src):
    page = get(src['url'])
    if not page:
        return None
    out = []
    for fecha, bloque in re.findall(r'<h3 class="mlist-subtit"><span>([^<]+)</span></h3>(.*?)(?=<h3 class="mlist-subtit"|<h2 |$)', page, re.S):
        m = re.search(r'(\d{1,2}) de ([a-z]+) de (\d{4})', html.unescape(fecha).lower())
        if not m or m.group(2) not in MESES:
            continue
        dia = dt.date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
        for item in re.findall(r'<div class="mlist-item".*?</div>\s*</div>|<div class="mlist-item".*?title="[^"]+">[^<]+</a></div>', bloque, re.S):
            if 'source cinema' not in item:
                continue  # solo cines, no plataformas
            t = re.search(r'<a href="([^"]+)" title="([^"]+)"', item)
            if t:
                out.append(ev(src, 'Estreno en cines: ' + html.unescape(t.group(2)), dia.isoformat(),
                              (dia + dt.timedelta(days=6)).isoformat(), url=t.group(1), lugar='Cines de Madrid', direccion=None))
    return out


# --------------------------------------------------------------------------- páginas de agenda leídas con IA
TOOL = {'name': 'guardar_eventos', 'description': 'Guarda los eventos de la página.',
        'input_schema': {'type': 'object', 'required': ['eventos'], 'properties': {'eventos': {'type': 'array', 'items': {
            'type': 'object', 'required': ['titulo', 'inicio'],
            'properties': {
                'titulo': {'type': 'string'},
                'inicio': {'type': 'string', 'description': 'YYYY-MM-DD'},
                'fin': {'type': ['string', 'null'], 'description': 'YYYY-MM-DD si dura varios días'},
                'hora': {'type': ['string', 'null'], 'description': 'HH:MM'},
                'lugar': {'type': ['string', 'null'], 'description': 'Sala o espacio, si la página lo dice'},
                'ciudad': {'type': ['string', 'null']},
                'precio': {'type': ['string', 'null']},
                'url': {'type': ['string', 'null'], 'description': 'Enlace a la ficha del evento, tal como aparece entre corchetes'},
                'descripcion': {'type': ['string', 'null'], 'description': 'Una frase'}}}}}}}


def llm(src, cache):
    page = get(src['url'])
    if not page:
        c = cache.get(src['name'])
        return c['items'] if c else None
    text = text_of(page)[:14000]
    h = hashlib.md5(('v1' + text).encode()).hexdigest()
    c = cache.get(src['name'])
    if c and c.get('hash') == h:
        items = c['items']
    elif not AKEY or (SPENT['in'] * 0.10 + SPENT['out'] * 0.50) / 1e6 >= MAX_USD:
        items = c['items'] if c else []
    else:
        system = (f'Hoy es {HOY.isoformat()}. Extraes los eventos con fecha de la página de agenda de {src["name"]} (Madrid). '
                  'Solo eventos concretos con fecha (no abonos, ni la temporada entera, ni noticias). Si una fecha no trae año, '
                  'es la próxima en el futuro. No inventes nada que no esté en el texto. Los enlaces aparecen entre corchetes.')
        body = {'model': MODEL, 'max_tokens': 6000, 'system': system, 'tools': [TOOL],
                'tool_choice': {'type': 'tool', 'name': 'guardar_eventos'},
                'messages': [{'role': 'user', 'content': f'Dirección: {src["url"]}\n\n{text}'}]}
        items = c['items'] if c else []
        for attempt in range(2):
            req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
            for k, v in (('x-api-key', AKEY), ('anthropic-version', '2023-06-01'), ('content-type', 'application/json')):
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    res = json.loads(r.read().decode())
                SPENT['in'] += res['usage'].get('input_tokens', 0)
                SPENT['out'] += res['usage'].get('output_tokens', 0)
                blk = [b for b in res['content'] if b.get('type') == 'tool_use']
                if blk:
                    items = blk[0]['input'].get('eventos') or []
                    break
            except urllib.error.HTTPError as e:
                print('  API', e.code, e.read().decode()[:150])
                time.sleep(10)
        print(f'  IA: {src["name"]} → {len(items)} eventos')
    cache[src['name']] = {'hash': h, 'items': items}
    base = re.match(r'https?://[^/]+', src['url']).group(0)
    out = []
    for it in items:
        if src.get('only_city') and (it.get('ciudad') or '').strip().lower() != src['only_city'].lower():
            continue
        if not re.match(r'\d{4}-\d{2}-\d{2}$', it.get('inicio') or ''):
            continue
        u = it.get('url') or ''
        u = u if u.startswith('http') else (base + u if u.startswith('/') else src['url'])
        out.append(ev(src, it['titulo'].strip(), it['inicio'], it.get('fin') if re.match(r'\d{4}-\d{2}-\d{2}$', it.get('fin') or '') else None,
                      hora=it.get('hora'), lugar=(src.get('venue') or it.get('lugar')), url=u, precio=it.get('precio'),
                      gratis=bool(re.search(r'gratu|gratis|libre', (it.get('precio') or '').lower())),
                      descripcion=(it.get('descripcion') or '')[:420]))
    return out


def main():
    os.makedirs(RAW, exist_ok=True)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    prev = json.load(open(OUT)) if os.path.exists(OUT) else []
    todos = []
    for src in CONF:
        fn = {'ical_links': ical_links, 'jsonld': jsonld, 'estrenos': estrenos}.get(src['kind'])
        try:
            got = (llm(src, cache) if src['kind'] == 'llm' else agendacultural(src, cache) if src['kind'] == 'agendacultural'
                   else fn(src))
        except Exception as ex:  # una fuente rota no para las demás
            print('  error en', src['name'], ex, file=sys.stderr)
            got = None
        if got is None:  # sin respuesta: se conserva lo de la última vez
            got = [e for e in prev if e['fuente'] == src['name']]
            print(f'{src["name"]}: sin respuesta, se reutilizan {len(got)}')
        else:
            print(f'{src["name"]}: {len(got)} eventos')
        todos += got
    json.dump(cache, open(CACHE, 'w'), ensure_ascii=False)
    json.dump(todos, open(OUT, 'w'), ensure_ascii=False)
    print(f'{len(todos)} eventos de fuentes adicionales. Coste IA aproximado: {(SPENT["in"] * 0.10 + SPENT["out"] * 0.50) / 1e6:.3f} USD')


if __name__ == '__main__':
    main()
