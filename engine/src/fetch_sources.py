"""Descarga las fuentes rápidas de Madrid en raw/:
agenda municipal (2 conjuntos de datos abiertos), recintos con schema.org Event y el sitemap de esMadrid."""
import json, os, re, sys, html, subprocess

RAW = os.environ.get('BUREO_RAW', 'raw')
os.makedirs(RAW, exist_ok=True)


def curl(url, out):
    r = subprocess.run(['curl', '-sSL', '-m', '120', '--retry', '3', '-A', 'BureoBot/0.1 (+https://github.com/jonruizz89/bureo)',
                        '-o', os.path.join(RAW, out), '-w', '%{http_code}', url], capture_output=True, text=True)
    print(out, r.stdout, r.stderr.strip()[:200])
    if r.stdout != '200':
        raise SystemExit(f'fallo al descargar {url}')


def jsonld_events(path):
    t = open(path, encoding='utf-8', errors='replace').read()
    blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', t, re.S | re.I)
    evs = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            ty = o.get('@type')
            tys = ty if isinstance(ty, list) else [ty]
            if any(isinstance(x, str) and (x.endswith('Event') or x == 'Festival') for x in tys):
                evs.append(o)
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)
    for b in blocks:
        for cand in (b.strip(), html.unescape(b.strip())):
            try:
                walk(json.loads(cand))
                break
            except Exception:
                continue
    seen, out = set(), []
    for e in evs:
        loc = e.get('location')
        loc = loc.get('name') if isinstance(loc, dict) else loc
        k = (e.get('name'), e.get('startDate'))
        if k in seen:
            continue
        seen.add(k)
        out.append({'titulo': html.unescape(str(e.get('name'))), 'inicio': e.get('startDate'),
                    'fin': e.get('endDate'), 'lugar': loc, 'url': e.get('url')})
    return out


base = 'https://datos.madrid.es/egob/catalogo/'
curl(base + '300107-0-agenda-actividades-eventos.json', '300107-0-agenda-actividades-eventos.json')
curl(base + '206974-0-agenda-eventos-culturales-100.json', '206974-0-agenda-eventos-culturales-100.json')
curl('https://www.esmadrid.com/sitemap.xml', 'sm.xml')
for name, url in (('ifema', 'https://www.ifema.es/'), ('rjb', 'https://rjb.csic.es/agenda/')):
    try:
        curl(url, name + '.html')
        evs = jsonld_events(os.path.join(RAW, name + '.html'))
        json.dump(evs, open(os.path.join(RAW, name + '.json'), 'w'), ensure_ascii=False)
        print(name, len(evs), 'eventos')
    except SystemExit as e:
        print('aviso:', e, file=sys.stderr)  # un recinto caído no para el resto
