"""Descarga incremental de las fichas de agenda de esMadrid.

Lee el sitemap (raw/sm.xml) y la caché (raw/esmadrid.jsonl). Solo vuelve a pedir una ficha si es nueva,
si su fecha de modificación en el sitemap ha cambiado, o si no se pudo leer bien la última vez.
Las fichas que ya no están en el sitemap o cuyo evento terminó hace más de 30 días salen de la caché.
Respeta el Crawl-delay de 10 s de su robots.txt. Uso: python3 src/fetch_esmadrid.py [máx. de fichas]
"""
import json, os, re, sys, time, subprocess, html as H, datetime as dt

RAW = os.environ.get('BUREO_RAW', 'raw')
CACHE = os.path.join(RAW, 'esmadrid.jsonl')
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
DEADLINE = time.time() + float(os.environ.get('BUREO_FETCH_MINUTES', '240')) * 60

sm = open(os.path.join(RAW, 'sm.xml'), encoding='utf-8').read()
lastmod = dict(re.findall(r'<url><loc>(https://www\.esmadrid\.com/agenda/[^<]+)</loc>(?:<lastmod>([^<]*)</lastmod>)?', sm))
cache = {}
if os.path.exists(CACHE):
    for l in open(CACHE, encoding='utf-8'):
        try:
            r = json.loads(l)
            cache[r['url']] = r
        except Exception:
            pass

hace30 = (dt.date.today() - dt.timedelta(days=30)).isoformat()


def ended_long_ago(r):
    ends = re.findall(r'"endDate":\s*"(\d{4}-\d{2}-\d{2})', ' '.join(r.get('ld') or []))
    return bool(ends) and max(ends) < hace30


cache = {u: r for u, r in cache.items() if u in lastmod and not ended_long_ago(r)}
todo = [u for u in lastmod if u not in cache or cache[u].get('lastmod') != lastmod[u] or not cache[u].get('ld')]
todo.sort(key=lambda u: lastmod[u] or '', reverse=True)
print(f'{len(lastmod)} fichas en el sitemap, {len(cache)} en caché, {len(todo)} por descargar')


def campo(s, name):
    m = re.search(r'field-name-field-' + name + r'[^>]*>(.*?)</div>\s*</div>', s, re.S)
    if not m:
        return None
    t = re.sub(r'<div class="label-above field-label"\s*>[^<]*</div>', '', m.group(1))
    t = re.sub(r'<[^>]+>', ' ', t)
    return H.unescape(re.sub(r'\s+', ' ', t)).strip() or None


def save():
    tmp = CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in cache.values():
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, CACHE)


n = 0
for u in todo[:LIMIT]:
    if time.time() > DEADLINE:
        print('tiempo agotado: el resto queda para la próxima noche')
        break
    try:
        s = subprocess.run(['curl', '-sSL', '-m', '30', '-A', 'BureoBot/0.1 (+https://github.com/jonruizz89/bureo)', u],
                           capture_output=True, text=True).stdout
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', s, re.S)
        web = re.search(r'field-name-field-web.*?<a href="([^"]+)"', s, re.S)
        og = re.search(r'og:image" content="([^"]+)"', s)
        cache[u] = {'url': u, 'lastmod': lastmod[u], 'ld': blocks, 'campos': {
            'tipo': campo(s, 'categoria'), 'precio': campo(s, 'price'),
            'horario': campo(s, 'resumen-fechas-y-horarios'), 'metro': campo(s, 'metro'),
            'web': web.group(1) if web else None, 'imagen': og.group(1) if og else None}}
        n += 1
        if n % 25 == 0:
            save()
    except Exception as e:
        print('error', u, e, flush=True)
    time.sleep(10)
save()
print(f'{n} fichas descargadas; caché con {len(cache)}')
