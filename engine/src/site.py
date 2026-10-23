"""Páginas estáticas para buscadores: una por zona (ciudad, provincia, municipio, distrito), por zona y tipo de plan, y por evento.

La app es una página que se pinta con JavaScript y los buscadores apenas la ven. Estas páginas son HTML normal, con los datos
del día, datos estructurados de evento (schema.org/Event, los que usa Google para mostrar eventos) y enlaces a la app.
Se generan dentro de la carpeta de la web antes de publicarla (pages.yml); no se guardan en el repositorio.

Uso: python3 src/site.py ../web   (lee la URL y la clave pública de web/config.js)
"""
import datetime as dt, html, json, math, os, re, sys, urllib.parse, urllib.request
from collections import defaultdict

WEB = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '../web')
BASE = 'https://limonplanes.com'
cfg = open(os.path.join(WEB, 'config.js'), encoding='utf-8').read()
SB_URL = re.search(r"supabaseUrl:\s*'([^']+)'", cfg).group(1)
SB_KEY = re.search(r"supabaseKey:\s*'([^']+)'", cfg).group(1)
HOY = dt.date.today()
HASTA_LISTA = HOY + dt.timedelta(days=45)    # lo que sale en las páginas de zona
HASTA_FICHA = HOY + dt.timedelta(days=180)   # eventos con página propia
DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
ABIERTAS = re.compile(r'CC BY|Open Data|datos abiertos|dominio público', re.I)  # fuentes cuya descripción se puede reproducir entera


def get(path):
    out, off = [], 0
    while True:
        req = urllib.request.Request(f'{SB_URL}/rest/v1/{path}&offset={off}&limit=1000', headers={'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY})
        with urllib.request.urlopen(req, timeout=120) as r:
            part = json.loads(r.read().decode())
        out += part
        if len(part) < 1000:
            return out
        off += 1000


def slug(t):
    t = t.lower().translate(str.maketrans('áàäâéèëêíìïîóòöôúùüûñç', 'aaaaeeeeiiiioooouuuunc'))
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', t)).strip('-')[:70] or 'x'


esc = lambda t: html.escape(str(t or ''), quote=True)
d = lambda s: dt.date.fromisoformat(s)


def fecha(x, dia=True):
    return (DIAS[x.weekday()] + ' ' if dia else '') + f'{x.day} de {MESES[x.month - 1]}'


def cuando(e):
    a, b = d(e['starts_on']), d(e['ends_on'])
    if a == b:
        return fecha(a) + (f', {e["time_text"]}' if e.get('time_text') and len(e['time_text']) < 30 else '')
    if a <= HOY:
        return 'Hasta el ' + fecha(b, False)
    return f'Del {fecha(a, False)} al {fecha(b, False)}'


def km(a, b):
    r = math.pi / 180
    x = (b[1] - a[1]) * r * math.cos((a[0] + b[0]) / 2 * r)
    return math.hypot(x, (b[0] - a[0]) * r) * 6371


# ---------- datos
cities = get('cities?select=id,name,kind,parent,status,districts,munis,lat,lng&status=eq.publicada&order=position')
groups = {g['id']: g['name'] for g in get('category_groups?select=id,name&order=position') if g['id'] != 'otros'}
cats = {c['id']: c for c in get('categories?select=id,group_id,label&order=position')}
cols = ('id,city_id,title,description,details,starts_on,ends_on,time_text,district,price_text,is_free,image_url,organizer_url,lat,lng,is_unique,ai_unique,'
        'venues(name),event_categories(category_id),event_sources(url,sources(name,license))')
ids = ','.join(c['id'] for c in cities)
evs = get(f'events?select={cols}&city_id=in.({ids})&ends_on=gte.{HOY.isoformat()}&starts_on=lte.{HASTA_FICHA.isoformat()}&order=starts_on,id')
print(len(evs), 'eventos;', len(cities), 'zonas publicadas')
byc = {c['id']: c for c in cities}
for e in evs:
    e['grupos'] = sorted({cats[c['category_id']]['group_id'] for c in e.get('event_categories') or [] if c['category_id'] in cats} & set(groups), key=list(groups).index)
    e['lugar'] = (e.get('venues') or {}).get('name')
    e['url'] = f"/evento/{e['id']}-{slug(e['title'])}/"
    e['largo'] = (d(e['ends_on']) - d(e['starts_on'])).days > 6

# ---------- zonas: ciudades, provincias, municipios de cada provincia y distritos de las ciudades
zonas = []  # {path, name, titulo, evs, parent, center, kids}
used = set()


def zona(path, name, lista, **kw):
    z = dict(path=path, name=name, evs=lista, kids=[], **kw)
    zonas.append(z)
    used.add(path)
    return z


for c in cities:
    if c['parent'] and c['parent'] in byc:
        continue  # las ciudades dentro de una provincia van con ella
    kids = [k for k in cities if k['parent'] == c['id']]
    if c['kind'] == 'provincia':
        allids = {c['id']} | {k['id'] for k in kids}
        prov = zona(c['id'], c['name'], [e for e in evs if e['city_id'] in allids], center=[c['lat'], c['lng']], provincia=True)
        munis = c.get('munis') or {}
        for k in kids:
            prov['kids'].append(zona(k['id'], k['name'], [e for e in evs if e['city_id'] == k['id']], center=[k['lat'], k['lng']], parent=prov))
        for m in sorted({e['district'] for e in evs if e['city_id'] == c['id'] and e.get('district')}, key=slug):
            p = slug(m) if slug(m) not in used else f"{c['id']}-{slug(m)}"
            prov['kids'].append(zona(p, m, [e for e in evs if e['city_id'] == c['id'] and e.get('district') == m], center=munis.get(m), parent=prov))
    else:
        city = zona(c['id'], c['name'], [e for e in evs if e['city_id'] == c['id']], center=[c['lat'], c['lng']])
        for dist in sorted(c.get('districts') or [], key=slug):
            lista = [e for e in evs if e['city_id'] == c['id'] and e.get('district') == dist]
            if lista:
                city['kids'].append(zona(f"{c['id']}/{slug(dist)}", dist, lista, center=None, parent=city, distrito=True))
zpath = {}
for z in zonas:
    for e in z['evs']:
        if not z.get('provincia') and not z['kids']:  # la zona más concreta de cada evento
            zpath[e['id']] = z
        else:
            zpath.setdefault(e['id'], z)

# ---------- HTML
CSS = """:root{--bg:#F6F7F9;--s:#fff;--ink:#12182B;--q:#4B5366;--l:#DDE1E8;--a:#FFD43B;--ai:#231D02;--at:#7A5900}
@media (prefers-color-scheme:dark){:root{--bg:#0F1320;--s:#181E2E;--ink:#E8EBF2;--q:#A3ABBD;--l:#2A3247;--at:#FFDF6E}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 'IBM Plex Sans',system-ui,-apple-system,sans-serif}
a{color:inherit}main,header,footer{max-width:760px;margin:0 auto;padding:0 16px}
header{display:flex;align-items:center;justify-content:space-between;padding-top:14px;padding-bottom:14px}
.brand{display:flex;align-items:center;gap:8px;text-decoration:none;font:italic 700 24px 'Space Grotesk',system-ui,sans-serif}
.cta{display:inline-block;background:var(--a);color:var(--ai);font-weight:600;text-decoration:none;border-radius:10px;padding:10px 16px}
h1{font:700 30px/1.2 'Space Grotesk',system-ui,sans-serif;margin:10px 0 8px}h2{font:600 20px 'Space Grotesk',system-ui,sans-serif;margin:28px 0 8px}
.lead,.q{color:var(--q)}.crumbs{font-size:14px;color:var(--q);margin-top:6px}.crumbs a{color:var(--q)}
ul.ev{list-style:none;padding:0;margin:0}ul.ev li{background:var(--s);border:1px solid var(--l);border-radius:12px;padding:12px 14px;margin:8px 0}
ul.ev a{font-weight:600;text-decoration:none}ul.ev a:hover{text-decoration:underline}.m{font-size:14px;color:var(--q)}
.share{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.share a{border:1px solid var(--l);background:var(--s);border-radius:10px;padding:8px 14px;text-decoration:none;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.chips a{background:var(--s);border:1px solid var(--l);border-radius:999px;padding:5px 12px;font-size:14px;text-decoration:none}
.card{background:var(--s);border:1px solid var(--l);border-radius:14px;padding:16px;margin:12px 0}.card p{margin:8px 0}
img.hero{width:100%;max-height:340px;object-fit:cover;border-radius:14px;margin:8px 0}
footer{color:var(--q);font-size:13px;padding-top:30px;padding-bottom:40px}"""
CSP = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; script-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'")


def page(path, title, desc, body, crumbs=(), ld=None, app='/'):
    bc = ' › '.join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in crumbs)
    ldj = ''.join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False).replace("</", "<\\/")}</script>' for x in (ld or []))
    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title>
<meta name="description" content="{esc(desc[:300])}"><link rel="canonical" href="{BASE}{path}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc[:300])}"><meta property="og:url" content="{BASE}{path}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Limón"><meta property="og:locale" content="es_ES">
<meta property="og:image" content="{BASE}/og.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/limon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/limon-180.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>{ldj}</head><body>
<header><a class="brand" href="/"><img src="/limon.svg" alt="" width="30" height="30">limón</a><a class="cta" href="{esc(app)}">Abrir la app</a></header>
<main>{f'<nav class="crumbs" aria-label="Estás en">{bc}</nav>' if bc else ''}{body}</main>
<footer>Limón reúne los planes de tu zona de agendas públicas y webs de organizadores; comprueba siempre la información en la fuente.
Datos de la agenda municipal de Madrid (datos.madrid.es) de Kulturklik (Open Data Euskadi, CC BY 4.0) y de la agenda del Ayuntamiento de Zaragoza, entre otros.
<br><a href="/planes/">Todas las zonas</a> · <a href="/privacidad.html">Privacidad</a> · <a href="/condiciones.html">Condiciones</a></footer>
</body></html>"""
    out = os.path.join(WEB, path.strip('/'), 'index.html')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'w', encoding='utf-8').write(doc)
    PAGES.append(path)


PAGES = []


def app_link(e):
    c = e['city_id'] if not byc[e['city_id']].get('parent') else byc[e['city_id']]['parent']
    return f"/?c={c}&ev=e{e['id']}"


def zone_app(z):
    top = z
    while top.get('parent'):
        top = top['parent']
    if z is top or z['path'] in byc:
        return f"/?c={z['path']}"
    return f"/?c={top['path']}&m={urllib.parse.quote(z['name'])}" if not z.get('distrito') else f"/?c={top['path']}"


def li(e, z=None):
    sitio = e['lugar'] or ''
    zz = zpath.get(e['id'])
    if zz and z is not zz and zz['name'] not in sitio:
        sitio = (sitio + ' · ' if sitio else '') + zz['name']
    g = ', '.join(groups[x] for x in e['grupos'][:2])
    return f'<li><a href="{e["url"]}">{esc(e["title"])}</a><div class="m">{esc(cuando(e))}{" · " + esc(sitio) if sitio else ""}{" · Gratis" if e.get("is_free") else ""}{" · " + esc(g) if g else ""}</div></li>'


def finde():
    w = HOY.weekday()
    vie = HOY + dt.timedelta(days=(4 - w) % 7) if w < 4 else HOY
    return vie, HOY + dt.timedelta(days=6 - w)


def secciones(lista, z):
    vie, dom = finde()
    con = [e for e in lista if not e['largo'] and d(e['starts_on']) <= HASTA_LISTA]
    s1 = [e for e in con if d(e['starts_on']) <= dom and d(e['ends_on']) >= vie]
    s2 = [e for e in con if e not in s1 and d(e['starts_on']) <= HOY + dt.timedelta(days=14)]
    s3 = [e for e in con if e not in s1 and e not in s2]
    largos = [e for e in lista if e['largo']]
    out = ''
    for t, l, n in (('Este fin de semana', s1, 60), ('Próximos días', s2, 60), ('Más adelante', s3, 40), ('En curso: exposiciones, ciclos y cursos', largos, 40)):
        if l:
            out += f'<h2>{t}</h2><ul class="ev">' + ''.join(li(e, z) for e in l[:n]) + '</ul>' + (f'<p class="q">Y {len(l) - n} más en la app.</p>' if len(l) > n else '')
    return out


def crumbs_of(z):
    c, p = [], z.get('parent')
    while p:
        c.insert(0, (p['name'], f"/planes/{p['path']}/"))
        p = p.get('parent')
    return [('Planes', '/planes/')] + c


def vecinos(z):
    if not z.get('center') or not z.get('parent'):
        return []
    sib = [k for k in z['parent']['kids'] if k is not z and k.get('center')]
    return sorted(sib, key=lambda k: km(z['center'], k['center']))[:8]


# zonas
for z in zonas:
    lista = z['evs']
    if not lista:
        continue
    n = len(lista)
    gr = defaultdict(list)
    for e in lista:
        for g in e['grupos']:
            gr[g].append(e)
    gl = [(g, gr[g]) for g in groups if len(gr[g]) >= 3]
    tit = f"Planes en {z['name']}: agenda de eventos, conciertos y actividades"
    desc = f"Qué hacer en {z['name']} esta semana y este fin de semana: {n} planes entre conciertos, teatro, exposiciones, actividades infantiles y más. Agenda actualizada cada día."
    body = f"<h1>Planes en {esc(z['name'])}</h1><p class='lead'>{n} planes y eventos en {esc(z['name'])} en las próximas semanas, actualizados cada día.</p>"
    body += f'<p><a class="cta" href="{esc(zone_app(z))}">Ver en Limón con mapa y filtros</a></p>'
    vie0, dom0 = finde()
    if sum(1 for e in lista if d(e['starts_on']) <= dom0 and d(e['ends_on']) >= vie0) >= 3:
        body += f'<p><a href="/planes/{z["path"]}/este-finde/"><b>Qué hacer este fin de semana en {esc(z["name"])} ›</b></a></p>'
    if gl:
        body += '<div class="chips">' + ''.join(f'<a href="/planes/{z["path"]}/{g}/">{esc(groups[g])} ({len(l)})</a>' for g, l in gl) + '</div>'
    if z['kids']:
        body += '<h2>Por zona</h2><div class="chips">' + ''.join(f'<a href="/planes/{k["path"]}/">{esc(k["name"])}</a>' for k in sorted(z['kids'], key=lambda k: -len(k['evs'])) if k['evs']) + '</div>'
    body += secciones(lista, z)
    vz = vecinos(z)
    if vz:
        body += '<h2>Cerca</h2><div class="chips">' + ''.join(f'<a href="/planes/{k["path"]}/">{esc(k["name"])}</a>' for k in vz if k['evs']) + '</div>'
    page(f"/planes/{z['path']}/", tit, desc, body, crumbs_of(z), app=zone_app(z),
         ld=[{'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
             {'@type': 'ListItem', 'position': i + 1, 'name': t, 'item': BASE + u} for i, (t, u) in enumerate(crumbs_of(z) + [(z['name'], f"/planes/{z['path']}/")])]}])
    for g, l in gl:
        gn = groups[g]
        page(f"/planes/{z['path']}/{g}/", f"{gn} en {z['name']}: agenda y próximos planes",
             f"{gn} en {z['name']}: {len(l)} planes en las próximas semanas. Fechas, horarios, lugares y precios, actualizados cada día.",
             f"<h1>{esc(gn)} en {esc(z['name'])}</h1><p class='lead'>{len(l)} planes de {esc(gn.lower())} en {esc(z['name'])}.</p>"
             f'<p><a class="cta" href="{esc(zone_app(z))}">Ver en Limón</a></p>' + secciones(l, z),
             crumbs_of(z) + [(z['name'], f"/planes/{z['path']}/")], app=zone_app(z))

# ---------- «Este finde en …»: los mejores planes del fin de semana, para compartir por WhatsApp o Telegram
def picks(lista, vie, dom, n=12):
    cand = [e for e in lista if d(e['starts_on']) <= dom and d(e['ends_on']) >= vie]
    def score(e):
        return (3 if e.get('is_unique') or e.get('ai_unique') else 0) + (0 if e['largo'] else 1.5) + (0.7 if e.get('is_free') else 0) + \
               (0.5 if e.get('image_url') else 0) + (0.5 if e.get('description') or e.get('details') else 0) + 0.3 * len(e.get('event_sources') or [])
    out, por_grupo = [], defaultdict(int)
    for e in sorted(cand, key=lambda e: (-score(e), e['starts_on'], e['id'])):
        g = (e['grupos'] or ['otros'])[0]
        if por_grupo[g] >= 3:  # variedad: como mucho 3 del mismo tipo
            continue
        por_grupo[g] += 1
        out.append(e)
        if len(out) >= n:
            break
    return sorted(out, key=lambda e: (e['largo'], max(e['starts_on'], vie.isoformat()), e.get('time_text') or '', e['id']))


def recorta(t, n):
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def sitio_corto(t):
    t = re.split(r' - | \(|, ', t)[0].strip()
    return recorta(t, 35)


def corto(e, vie):
    a = d(e['starts_on'])
    dia = DIAS[max(a, vie).weekday()][:3] if not e['largo'] else 'todo el finde'
    h = e.get('time_text') if e.get('time_text') and len(e['time_text']) < 12 and not e['largo'] else ''
    return f"{dia}{' ' + h if h else ''}"


vie, dom = finde()
FINDE = {}
for z in zonas:
    sel = picks(z['evs'], vie, dom)
    if len(sel) < 3:
        continue
    path = f"/planes/{z['path']}/este-finde/"
    FINDE[z['path']] = path
    msg = f"🍋 Planes este finde en {z['name']}:\n" + '\n'.join(f"• {corto(e, vie)} · {recorta(e['title'], 70)}" + (f" ({sitio_corto(e['lugar'])})" if e['lugar'] else '') for e in sel[:8]) + \
          f"\n\nTodos: {BASE}{path}"
    wa = 'https://wa.me/?text=' + urllib.parse.quote(msg)
    tg = 'https://t.me/share/url?' + urllib.parse.urlencode({'url': BASE + path, 'text': msg.rsplit('\n\n', 1)[0]})
    rango = f"del {vie.day} al {fecha(dom, False)}" if vie.month == dom.month else f"del {fecha(vie, False)} al {fecha(dom, False)}"
    body = (f"<h1>Este finde en {esc(z['name'])}</h1><p class='lead'>Los {len(sel)} planes que no te puedes perder {rango}: variados, con fecha y lugar.</p>"
            f'<div class="share"><a href="{esc(wa)}" rel="noopener" target="_blank">Compartir por WhatsApp</a><a href="{esc(tg)}" rel="noopener" target="_blank">Compartir por Telegram</a></div>'
            '<ul class="ev">' + ''.join(li(e, z) for e in sel) + '</ul>'
            f'<p><a href="/planes/{z["path"]}/">Toda la agenda de {esc(z["name"])}</a> · <a class="cta" href="{esc(zone_app(z))}">Ver en Limón</a></p>')
    page(path, f"Qué hacer este fin de semana en {z['name']} ({rango}) · Limón",
         f"Planes para este fin de semana en {z['name']}, {rango}: " + ', '.join(e['title'] for e in sel[:4]) + ' y más.',
         body, crumbs_of(z) + [(z['name'], f"/planes/{z['path']}/")], app=zone_app(z))
json.dump(FINDE, open(os.path.join(WEB, 'planes', 'finde.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print(len(FINDE), 'páginas de «este finde»')

# índice de zonas
tops = [z for z in zonas if not z.get('parent') and z['evs']]
body = "<h1>Planes por zona</h1><p class='lead'>Agenda de eventos, conciertos, teatro, exposiciones y actividades, actualizada cada día.</p>"
for z in tops:
    body += f'<h2><a href="/planes/{z["path"]}/">{esc(z["name"])}</a></h2>'
    if z['kids']:
        body += '<div class="chips">' + ''.join(f'<a href="/planes/{k["path"]}/">{esc(k["name"])}</a>' for k in sorted(z['kids'], key=lambda k: slug(k['name'])) if k['evs']) + '</div>'
page('/planes/', 'Planes por zona · Limón', 'Qué hacer en Madrid, Zaragoza, Bilbao, Donostia, Vitoria-Gasteiz y toda Euskadi: agenda de eventos, conciertos, teatro y actividades, actualizada cada día.', body)

# eventos
for e in evs:
    z = zpath.get(e['id'])
    srcs = [s for s in e.get('event_sources') or [] if s.get('url')]
    abierta = any(ABIERTAS.search(((s.get('sources') or {}).get('license') or '')) for s in srcs)
    txt = e.get('description') or ''
    if not txt and e.get('details'):  # texto leído de la web del evento: solo un extracto, la ficha completa está en su web
        t = e['details'].split('\n\n')[0]
        txt = t if abierta or len(t) <= 320 else t[:300].rsplit(' ', 1)[0] + '…'
    sitio = ', '.join(x for x in (e['lugar'], z['name'] if z and z['name'] not in (e['lugar'] or '') else None) if x)
    precio = 'Gratis' if e.get('is_free') else (e.get('price_text') or '')
    gs = ' · '.join(groups[g] for g in e['grupos'])
    links = ''.join(f'<p><a href="{esc(s["url"])}" rel="nofollow noopener" target="_blank">Más información en {esc((s.get("sources") or {}).get("name") or "la web del evento")} ↗</a></p>' for s in srcs[:2])
    if e.get('organizer_url') and not any(s['url'] == e['organizer_url'] for s in srcs):
        links += f'<p><a href="{esc(e["organizer_url"])}" rel="nofollow noopener" target="_blank">Web del organizador ↗</a></p>'
    body = f"<h1>{esc(e['title'])}</h1><p class='lead'>{esc(cuando(e))}{' · ' + esc(sitio) if sitio else ''}</p>"
    if e.get('image_url') and e['image_url'].startswith('https://'):
        body += f'<img class="hero" src="{esc(e["image_url"])}" alt="" loading="lazy">'
    body += '<div class="card">' + (f'<p>{esc(txt)}</p>' if txt else '') + \
        (f'<p class="m">Precio: {esc(precio)}</p>' if precio else '') + (f'<p class="m">{esc(gs)}</p>' if gs else '') + links + \
        f'<p><a class="cta" href="{esc(app_link(e))}">Guardar en Limón y ver en el mapa</a></p></div>'
    if z:
        mas = [x for x in z['evs'] if x is not e and not x['largo'] and d(x['starts_on']) >= HOY][:6]
        if mas:
            body += f'<h2>Más planes en {esc(z["name"])}</h2><ul class="ev">' + ''.join(li(x, z) for x in mas) + f'</ul><p><a href="/planes/{z["path"]}/">Toda la agenda de {esc(z["name"])}</a></p>'
    loc = {'@type': 'Place', 'name': e['lugar'] or (z['name'] if z else ''), 'address': {'@type': 'PostalAddress', 'addressLocality': z['name'] if z else '', 'addressCountry': 'ES'}}
    if e.get('lat'):
        loc['geo'] = {'@type': 'GeoCoordinates', 'latitude': e['lat'], 'longitude': e['lng']}
    ld = {'@context': 'https://schema.org', '@type': 'Event', 'name': e['title'], 'startDate': e['starts_on'], 'endDate': e['ends_on'],
          'eventStatus': 'https://schema.org/EventScheduled', 'eventAttendanceMode': 'https://schema.org/OfflineEventAttendanceMode', 'location': loc,
          'url': BASE + e['url']}
    if txt:
        ld['description'] = txt[:500]
    if e.get('image_url') and e['image_url'].startswith('https://'):
        ld['image'] = [e['image_url']]
    # entradas y organizador: solo lo que sabemos (no se inventa disponibilidad ni fecha de venta)
    web = e.get('organizer_url') or (srcs[0]['url'] if srcs else None)
    m = re.search(r'(\d+(?:[.,]\d{1,2})?)\s*(?:€|euros?)', e.get('price_text') or '', re.I) or re.search(r'(?:€|euros?)\s*(\d+(?:[.,]\d{1,2})?)', e.get('price_text') or '', re.I)
    if e.get('is_free'):
        ld['isAccessibleForFree'] = True
        ld['offers'] = {'@type': 'Offer', 'price': 0, 'priceCurrency': 'EUR', 'url': web or BASE + e['url']}
    elif m:
        ld['offers'] = {'@type': 'Offer', 'price': float(m.group(1).replace(',', '.')), 'priceCurrency': 'EUR', 'url': web or BASE + e['url']}
    quien = e['lugar'] or ((srcs[0].get('sources') or {}).get('name') if srcs else None)
    if quien:
        ld['organizer'] = {'@type': 'Organization', 'name': quien}
        if web:
            ld['organizer']['url'] = web
    desc = f"{cuando(e)}{' en ' + sitio if sitio else ''}. " + (txt or f"{e['title']}: fecha, horario, lugar y precio.")
    cr = (crumbs_of(z) + [(z['name'], f"/planes/{z['path']}/")]) if z else [('Planes', '/planes/')]
    page(e['url'], f"{e['title']} · {cuando(e)}{' · ' + z['name'] if z else ''}", desc, body, cr, ld=[ld], app=app_link(e))

# sitemap
hoy = HOY.isoformat()
sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
     f'<url><loc>{BASE}/</loc><lastmod>{hoy}</lastmod><changefreq>daily</changefreq></url>\n' + \
     ''.join(f'<url><loc>{BASE}{p}</loc><lastmod>{hoy}</lastmod>{"<changefreq>daily</changefreq>" if p.startswith("/planes/") else ""}</url>\n' for p in PAGES) + '</urlset>\n'
open(os.path.join(WEB, 'sitemap.xml'), 'w', encoding='utf-8').write(sm)
print(len(PAGES), 'páginas:', sum(p.startswith('/planes/') for p in PAGES), 'de zonas y', sum(p.startswith('/evento/') for p in PAGES), 'de eventos')
