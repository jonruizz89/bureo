"""Paso 2 · Probar cada fuente candidata y decidir cómo leerla («receta»).

Orden de preferencia, de más fiable a menos:
  1. iCal (calendario)            2. datos abiertos (CSV/JSON)     3. schema.org/Event en la propia página
  4. schema.org/Event en las fichas enlazadas desde el listado     5. RSS/Atom leído con IA     6. página leída con IA
La receta se guarda en sources.config y la usa extract.py cada noche. Si no sale ningún evento, la fuente se descarta.
"""
import datetime as dt, re, sys, urllib.parse
from lib import sb, fetch, parse_page, load_city, domain, usd, over_budget, fold
import formats as F

HOY = dt.date.today()
HASTA = HOY + dt.timedelta(days=120)
NO_FICHA = re.compile(r'(aviso-legal|privacidad|cookies|contacto|accesibilidad|login|registro|mapa-web|facebook|twitter|instagram|'
                      r'youtube|linkedin|whatsapp|mailto:|tel:|\.pdf$|\.jpg$|\.png$|/tag/|/categoria/|/category/|#)', re.I)


def vivos(evs):
    return [e for e in evs if e.get('inicio') and (e.get('fin') or e['inicio']) >= HOY.isoformat() and e['inicio'] <= HASTA.isoformat()]


def try_ical(url):
    st, ct, txt, _ = fetch(url)
    if st == 200 and 'BEGIN:VCALENDAR' in txt[:2000]:
        return vivos(F.ical_events(txt, url))
    return []


def try_datos(url, txt=None, ct=''):
    if txt is None:
        st, ct, txt, _ = fetch(url, limit=20_000_000)
        if st != 200:
            return [], None
    rows, kind = F.table_rows(txt, ct)
    if not rows:
        return [], None
    sample = rows[:3] if isinstance(rows, list) else rows
    m = F.guess_mapping(sample if kind == 'csv' else (rows if isinstance(rows, dict) else rows[:3]), kind) or {}
    if not m.get('es_agenda'):
        return [], None
    data = F.dig(rows, m.get('ruta')) if kind == 'json' and isinstance(rows, dict) else rows
    return vivos(F.mapped_events(data, m, url)), m


def detail_links(page, url, limit=60):
    host = urllib.parse.urlparse(url).netloc
    base_path = urllib.parse.urlparse(url).path.rstrip('/')
    seen, out = set(), []
    for t, h in page['links']:
        p = urllib.parse.urlparse(h)
        if p.netloc != host or NO_FICHA.search(h) or h.rstrip('/') == url.rstrip('/'):
            continue
        h = h.split('#')[0]
        if h in seen or len(p.path.strip('/').split('/')) < 2:
            continue
        seen.add(h)
        out.append(h)
    # primero los que cuelgan de la ruta del listado (/agenda/…)
    out.sort(key=lambda h: (not urllib.parse.urlparse(h).path.startswith(base_path + '/'), -len(h)))
    return out[:limit]


def common_prefix(urls):
    if not urls:
        return None
    parts = [urllib.parse.urlparse(u).path.strip('/').split('/') for u in urls]
    pre = []
    for seg in zip(*parts):
        if len(set(seg)) == 1:
            pre.append(seg[0])
        else:
            break
    p = urllib.parse.urlparse(urls[0])
    return f'{p.scheme}://{p.netloc}/' + '/'.join(pre) + ('/' if pre else '')


def probe(src, city):
    url = src['url']
    st, ct, txt, final = fetch(url)
    if st == 'robots':
        return None, 'robots.txt no permite leerla', []
    if st != 200 or not txt:
        return None, f'No se puede abrir ({st})', []
    # 1) la propia URL es un calendario o un fichero de datos
    if 'BEGIN:VCALENDAR' in txt[:2000]:
        ev = vivos(F.ical_events(txt, final))
        return ({'method': 'ical', 'feed': final}, None, ev) if ev else (None, 'Calendario sin eventos próximos', [])
    if 'json' in ct or 'csv' in ct or re.search(r'\.(csv|json)(\?|$)', final, re.I):
        ev, m = try_datos(final, txt, ct)
        return ({'method': 'datos', 'feed': final, 'mapping': m}, None, ev) if ev else (None, 'Los datos no parecen una agenda con fechas próximas', [])
    page = parse_page(txt, final)
    # 2) enlaces a calendarios o a datos descargables
    for t, h in page['links']:
        if re.search(r'(\.ics(\?|$)|^webcal:|format=ical|ical=1|/ical/?$|\?ical)', h, re.I):
            ev = try_ical(h.replace('webcal://', 'https://'))
            if len(ev) >= 2:
                return {'method': 'ical', 'feed': h.replace('webcal://', 'https://')}, None, ev
    for t, h in page['links'][:400]:
        if re.search(r'\.(csv|json)(\?|$)|format=(csv|json)', h, re.I) and not over_budget():
            ev, m = try_datos(h)
            if len(ev) >= 2:
                return {'method': 'datos', 'feed': h, 'mapping': m}, None, ev
    # 3) schema.org/Event en la página
    ev = vivos(F.jsonld_events(page['ld'], final))
    if len(ev) >= 2:
        return {'method': 'jsonld', 'url': final}, None, ev
    # 4) schema.org/Event en las fichas
    cands = detail_links(page, final, 12)
    hits = []
    for h in cands[:6]:
        s2, _, t2, f2 = fetch(h)
        if s2 == 200 and F.jsonld_events(parse_page(t2, f2)['ld'], f2):
            hits.append(h)
        if len(hits) >= 3:
            break
    if len(hits) >= 2:
        pre = common_prefix(hits)
        evs = []
        for h in [l for l in detail_links(page, final, 40) if l.startswith(pre)][:25]:
            s2, _, t2, f2 = fetch(h)
            if s2 == 200:
                evs += F.jsonld_events(parse_page(t2, f2)['ld'], f2)
        ev = vivos(evs)
        if ev:
            return {'method': 'jsonld_fichas', 'url': final, 'prefijo': pre, 'max': 40}, None, ev
    if over_budget():
        return None, 'Sin presupuesto para leer con IA', []
    # 5) RSS/Atom
    for typ, h in page['alts']:
        if 'rss' in typ or 'atom' in typ:
            s2, _, t2, _ = fetch(h)
            items = F.feed_items(t2) if s2 == 200 else []
            if items:
                ev = vivos(F.llm_events('\n\n'.join(items[:40]), h, city['name'], HOY.isoformat())[0])
                if len(ev) >= 3:
                    return {'method': 'rss', 'feed': h}, None, ev
    # 6) la página leída con IA
    if len(re.findall(r'\b\d{1,2}[/.-]\d{1,2}|\b\d{4}-\d{2}-\d{2}|\b\d{1,2} (de )?(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|urt|ots|mar|api|mai|eka|uzt|abu|ira|urr|aza|abe)', page['text'], re.I)) >= 3:
        ev, nxt = F.llm_events(page['text'], final, city['name'], HOY.isoformat())
        ev = vivos(ev)
        if len(ev) >= 3:
            return {'method': 'ia', 'url': final, 'paginas': 2 if nxt else 1}, None, ev
    return None, 'No he encontrado eventos con fecha en esta página', []


LABEL = {'ical': 'Calendario iCal', 'datos': 'Datos abiertos', 'jsonld': 'schema.org en la página', 'jsonld_fichas': 'schema.org en cada ficha',
         'rss': 'RSS leído con IA', 'ia': 'Página leída con IA'}


def main(cid, retry=False):
    city = load_city(cid)
    st = 'status=in.(candidata,rota)' if retry else 'status=eq.candidata'
    srcs = sb('GET', f'sources?select=id,name,url,status,config&city_id=eq.{cid}&{st}&order=id')
    ok = 0
    for s in srcs:
        if over_budget():
            print('Presupuesto agotado:', round(usd(), 2), '$'); break
        try:
            recipe, why, ev = probe(s, city)
        except Exception as ex:
            recipe, why, ev = None, f'Error al probar: {type(ex).__name__}', []
        cfg = dict(s.get('config') or {})
        if recipe:
            cfg.update(recipe); ok += 1
            sb('PATCH', f'sources?id=eq.{s["id"]}', {'status': 'activa', 'active': True, 'method': LABEL[recipe['method']], 'config': cfg,
                                                     'events_last': len(ev), 'notes': None}, 'return=minimal')
            print(f'✓ {s["name"]}: {LABEL[recipe["method"]]} · {len(ev)} eventos · ej.: {ev[0]["titulo"][:60]} ({ev[0]["inicio"]})')
        else:
            sb('PATCH', f'sources?id=eq.{s["id"]}', {'status': 'descartada', 'active': False, 'notes': why, 'config': cfg}, 'return=minimal')
            print(f'✗ {s["name"]}: {why}')
    print(f'{ok} de {len(srcs)} fuentes con receta · coste {round(usd(), 3)} $')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'bilbao', '--retry' in sys.argv)
