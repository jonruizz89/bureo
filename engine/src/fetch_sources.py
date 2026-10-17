"""Descarga las fuentes rápidas de Madrid en raw/:
agenda municipal (2 conjuntos de datos abiertos), recintos con schema.org Event y el sitemap de esMadrid."""
import json, os, re, sys, html, subprocess, time

RAW = os.environ.get('BUREO_RAW', 'raw')
os.makedirs(RAW, exist_ok=True)


UAS = ['BureoBot/0.1 (+https://github.com/jonruizz89/bureo)', 'curl/8.5.0',
       'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36']


def curl(url, out):
    """Descarga con reintentos. Si falla, se queda la copia anterior (si la hay) y se avisa sin parar el resto."""
    dest = os.path.join(RAW, out)
    for i in range(3):  # menos reintentos: si una web no responde, se usa la copia anterior
        tmp = dest + '.part'
        r = subprocess.run(['curl', '-sSL', '-m', '180', '-A', UAS[i % len(UAS)], '-o', tmp, '-w', '%{http_code}', url],
                           capture_output=True, text=True)
        if r.stdout == '200' and os.path.getsize(tmp) > 0:
            os.replace(tmp, dest)
            print(out, 'ok')
            return True
        print(out, 'intento', i + 1, 'respuesta', r.stdout or r.stderr.strip()[:120], flush=True)
        time.sleep(15 * (i + 1))
    if os.path.exists(dest):
        print('aviso: no se pudo actualizar', out, '- se usa la copia anterior', file=sys.stderr)
        return True
    print('aviso: no se pudo descargar', out, file=sys.stderr)
    return False


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
if not curl('https://www.esmadrid.com/sitemap.xml', 'sm.xml'):
    raise SystemExit('sin sitemap de esMadrid no se puede seguir')
for name, url in (('ifema', 'https://www.ifema.es/'), ('rjb', 'https://rjb.csic.es/agenda/')):
    if curl(url, name + '.html'):
        evs = jsonld_events(os.path.join(RAW, name + '.html'))
        if evs:
            json.dump(evs, open(os.path.join(RAW, name + '.json'), 'w'), ensure_ascii=False)
        print(name, len(evs), 'eventos')
