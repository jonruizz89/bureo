"""Paso 3 · Leer cada noche las fuentes activas de una ciudad con su receta y dejar out/<ciudad>/events.json.

Después: load_supabase.py (BUREO_CITY=<ciudad>, BUREO_OUT=out/<ciudad>) y classify.py ponen las categorías con IA.
Solo se quedan los eventos del municipio (por el municipio que indica la fuente o por las coordenadas) de los próximos 120 días.
"""
import datetime as dt, json, os, re, sys, time, urllib.parse, urllib.request
from lib import sb, fetch, parse_page, load_city, fold, usd, over_budget, UA
import formats as F
import connectors

HOY = dt.date.today()
HASTA = HOY + dt.timedelta(days=120)
RAW = os.environ.get('BUREO_RAW', 'raw')
MAX_GEO = int(os.environ.get('BUREO_GEOCODE_MAX', '400'))


def read_source(s, city):
    c = s.get('config') or {}
    m = c.get('method')
    if m in connectors.CONNECTORS:
        return connectors.read(c)
    if m == 'ical':
        st, _, txt, f = fetch(c['feed'])
        return F.ical_events(txt, f) if st == 200 else None
    if m == 'datos':
        st, ct, txt, f = fetch(c['feed'], limit=20_000_000)
        if st != 200:
            return None
        rows, kind = F.table_rows(txt, ct)
        mp = c.get('mapping') or {}
        data = F.dig(rows, mp.get('ruta')) if kind == 'json' and isinstance(rows, dict) else rows
        return F.mapped_events(data, mp, f)
    if m == 'jsonld':
        st, _, txt, f = fetch(c['url'])
        return F.jsonld_events(parse_page(txt, f)['ld'], f) if st == 200 else None
    if m == 'jsonld_fichas':
        st, _, txt, f = fetch(c['url'])
        if st != 200:
            return None
        from probe import detail_links
        out = []
        for h in [l for l in detail_links(parse_page(txt, f), f, 120) if l.startswith(c.get('prefijo') or '')][:c.get('max', 40)]:
            s2, _, t2, f2 = fetch(h)
            if s2 == 200:
                out += F.jsonld_events(parse_page(t2, f2)['ld'], f2)
        return out
    if m == 'rss':
        st, _, txt, _ = fetch(c['feed'])
        items = F.feed_items(txt) if st == 200 else []
        return F.llm_events('\n\n'.join(items[:60]), c['feed'], city['name'], HOY.isoformat())[0] if items else None
    if m == 'ia':
        out, url = [], c['url']
        for _ in range(int(c.get('paginas') or 1)):
            if not url or over_budget():
                break
            st, _, txt, f = fetch(url)
            if st != 200:
                return out or None
            evs, url = F.llm_events(parse_page(txt, f)['text'], f, city['name'], HOY.isoformat())
            out += evs
        return out
    return None


STOPW = {'de', 'del', 'la', 'el', 'los', 'las', 'y', 'sala', 'teatro', 'antzokia', 'museo', 'museoa', 'bilbao', 'bilboko', 'aretoa'}


def in_city(e, city):
    name = fold(city['name'])
    if city.get('kind') == 'provincia':  # toda la provincia, menos las ciudades que tienen su propia zona (Bilbao)
        if not e.get('municipio') or fold(e['municipio']) in city.get('_children', set()):
            return False
    elif e.get('municipio'):
        mu = fold(e['municipio'])
        if name not in mu and mu not in name:
            return False
    bb = city.get('bbox')
    if bb and e.get('lat') and e.get('lng'):
        m = 0.01
        if not (bb[0] - m <= e['lng'] <= bb[2] + m and bb[1] - m <= e['lat'] <= bb[3] + m):
            return False
    return True


TSTOP = set('programa festival fest de del la las el los en y a con bilbao bilbo edicion ciclo the and of 2025 2026 2027 2028'.split())


def tkeys(t):
    return {w for w in fold(t).split() if len(w) > 1 and w not in TSTOP and not w.isdigit()}


def fuzzy_merge(eventos):
    by_day = {}
    for e in eventos:
        by_day.setdefault(e['inicio'], []).append(e)
    gone = set()
    for lst in by_day.values():
        for i, a in enumerate(lst):
            if id(a) in gone:
                continue
            ta = tkeys(a['titulo'])
            for b in lst[i + 1:]:
                if id(b) in gone or a.get('fin') != b.get('fin'):
                    continue
                tb = tkeys(b['titulo'])
                if len(ta) < 2 or len(tb) < 2:
                    continue
                inter = len(ta & tb)
                if not (inter / len(ta | tb) >= 0.6 or (inter >= 2 and (ta <= tb or tb <= ta))):
                    continue
                la, lb = fold(a.get('lugar') or ''), fold(b.get('lugar') or '')
                if la and lb and not (la in lb or lb in la or la.split()[:2] == lb.split()[:2]):
                    continue
                for k, v in b.items():
                    if not a.get(k) and v:
                        a[k] = v
                if len(b.get('descripcion') or '') > len(a.get('descripcion') or ''):
                    a['descripcion'] = b['descripcion']
                names = {f['nombre'] for f in a['fuentes']}
                a['fuentes'] += [f for f in b['fuentes'] if f['nombre'] not in names]
                a['sesiones'] = sorted(set(a.get('sesiones') or []) | set(b.get('sesiones') or []))[:40]
                gone.add(id(b))
    return [e for e in eventos if id(e) not in gone]


def main(cid):
    city = load_city(cid)
    prov = city.get('kind') == 'provincia'
    if prov:
        # las ciudades con zona propia dentro de la provincia (Bilbao, Donostia…) no se repiten en ella; features.aliases = otros nombres
        kids = sb('GET', f'cities?select=name,features&parent=eq.{cid}') or []
        city['_children'] = {fold(n) for c in kids for n in [c['name']] + list((c.get('features') or {}).get('aliases') or [])}
    srcs = sb('GET', f'sources?select=id,name,url,layer,config&city_id=eq.{cid}&status=eq.activa&order=id')
    raw, per = [], {}
    for s in srcs:
        if not (s.get('config') or {}).get('method'):
            continue
        try:
            evs = read_source(s, city)
        except Exception as ex:
            print(f'✗ {s["name"]}: {type(ex).__name__}: {ex}'); evs = None
        if evs is None:
            sb('PATCH', f'sources?id=eq.{s["id"]}', {'notes': f'Fallo de lectura el {HOY.isoformat()}'}, 'return=minimal')
            print(f'✗ {s["name"]}: no se ha podido leer'); continue
        keep = []
        for e in evs:
            if not e.get('inicio') or (e.get('fin') or e['inicio']) < HOY.isoformat() or e['inicio'] > HASTA.isoformat():
                continue
            if e['inicio'] < (HOY - dt.timedelta(days=730)).isoformat():
                continue  # «eventos» que empezaron hace años (programas permanentes, secciones de la web): no son planes
            if s.get('layer') == 'regional' and not e.get('municipio') and not (e.get('lat') and e.get('lng')):
                continue  # agendas autonómicas: solo si sabemos que es en la ciudad
            if not in_city(e, city):
                continue
            e['fuentes'] = [{'nombre': s['name'], 'url': e.get('url') or s['url']}]
            keep.append(e)
        per[s['name']] = len(keep)
        raw += keep
        sb('PATCH', f'sources?id=eq.{s["id"]}', {'events_last': len(keep), 'notes': None}, 'return=minimal')
        print(f'✓ {s["name"]}: {len(keep)} eventos')

    if prov:  # en una provincia el «distrito» es el municipio, y el recinto lleva su pueblo («Kultur Etxea (Getxo)»)
        for e in raw:
            mu = e.get('municipio')
            e['distrito'] = mu
            if e.get('lugar') and fold(mu) not in fold(e['lugar']):
                e['lugar'] = f"{e['lugar']} ({mu})"
            elif not e.get('lugar'):
                e['lugar'] = mu

    # el mismo evento en varias fuentes o con varias sesiones → una sola tarjeta
    def same_place(a, b):
        ta, tb = set(a.split()) - STOPW, set(b.split()) - STOPW
        return bool(ta and tb) and (ta <= tb or tb <= ta or a.split()[:2] == b.split()[:2])
    by_title = {}
    for e in raw:
        by_title.setdefault(fold(e['titulo'])[:70], []).append(e)
    for lst in by_title.values():  # «Teatro Campos Elíseos» y «Teatro Campos Elíseos Antzokia» son el mismo sitio
        names = sorted({fold(x.get('lugar') or '') for x in lst if x.get('lugar')}, key=len, reverse=True)
        for x in lst:
            v = fold(x.get('lugar') or '')
            if not v and len({next((n for n in names if same_place(m, n)), m) for m in names}) == 1:
                v = names[0]  # sin lugar en una fuente, pero el título solo aparece en un sitio: es el mismo evento
            x['_lugar'] = next((n for n in names if v and same_place(v, n)), v)
            if not x.get('lugar') and v:
                x['lugar'] = next(y['lugar'] for y in lst if fold(y.get('lugar') or '') == v)
    groups = {}
    for e in raw:
        k = (fold(e['titulo'])[:70], e.pop('_lugar')[:40])
        groups.setdefault(k, []).append(e)
    eventos = []
    for (_, _), lst in groups.items():
        lst.sort(key=lambda x: x['inicio'])
        singles = [x for x in lst if x['inicio'] == (x.get('fin') or x['inicio'])]
        ranges = [x for x in lst if x not in singles]
        bundles = ([singles] if singles else []) + [[x] for x in ranges]
        for b in bundles:
            base = dict(b[0])
            for x in b[1:]:
                for k, v in x.items():
                    if not base.get(k) and v:
                        base[k] = v
            fuentes = {}
            for x in b:
                for f in x['fuentes']:
                    fuentes.setdefault(f['nombre'], f)
            dates = sorted({x['inicio'] for x in b})
            base['inicio'], base['fin'] = dates[0], max(x.get('fin') or x['inicio'] for x in b)
            base['sesiones'] = dates[:40] if len(b) > 1 else [base['inicio']] if base['inicio'] == base['fin'] else []
            base['fuentes'] = list(fuentes.values())
            eventos.append(base)

    # el mismo evento con títulos algo distintos según la fuente («Basque Dok Festival 2026» / «BASQUE DOK FESTIVAL»)
    eventos = fuzzy_merge(eventos)

    # recintos ya situados (también los corregidos a mano) antes que preguntar a OpenStreetMap
    from geo import Geocoder, norm as vnorm
    known = {}
    for off in range(0, 20000, 1000):
        part = sb('GET', f'venues?select=name_key,lat,lng,district&city_id=eq.{cid}&lat=not.is.null&order=id&offset={off}&limit=1000') or []
        known.update({v['name_key']: [v['lat'], v['lng'], v['district']] for v in part})
        if len(part) < 1000:
            break
    geo = Geocoder(city, known, MAX_GEO)
    placed, mll = 0, {}
    for e in eventos:
        if not (e.get('lat') and e.get('lng')) and e.get('lugar'):
            g = geo.find(e['lugar'], e.get('direccion') if e.get('direccion') != e.get('lugar') else None)
            if g:
                e['lat'], e['lng'] = g[0], g[1]; placed += 1
                if not prov:
                    e['distrito'] = g[2]
            elif prov and e.get('muni_ll'):  # sin el sitio exacto: en el centro del pueblo
                e['lat'], e['lng'] = e['muni_ll']
        elif e.get('lat') and not e.get('distrito') and e.get('lugar') and vnorm(e['lugar']) in known:
            e['distrito'] = known[vnorm(e['lugar'])][2]
        e.setdefault('distrito', None)
        e['cats'] = []
        e['descripcion'] = (e.get('descripcion') or '')[:420]
        if prov and e.get('municipio') and e.get('muni_ll'):
            mll.setdefault(e['municipio'], [round(x, 4) for x in e['muni_ll']])
        for k in ('municipio', 'url', 'direccion', 'tipo', 'muni_ll'):
            e.pop(k, None)
    geo.save()
    if prov:  # el selector y el filtro «municipio» muestran los pueblos con planes
        munis = sorted({e['distrito'] for e in eventos if e.get('distrito')}, key=fold)
        # y el mapa de todos los municipios con su centro (para «alrededores» en los pueblos sin planes): solo se añaden
        todos = dict(city.get('munis') or {})
        nuevos = {k: v for k, v in mll.items() if k not in todos and fold(k) not in city.get('_children', set())}
        todos.update(nuevos)
        sb('PATCH', f'cities?id=eq.{cid}', {'districts': munis, 'munis': todos}, 'return=minimal')
    out = os.path.join(os.environ.get('BUREO_OUT', 'out'), cid)
    os.makedirs(out, exist_ok=True)
    meta = {'ciudad': city['name'], 'generado': dt.datetime.now().isoformat(timespec='minutes'), 'por_fuente': per,
            'eventos': len(eventos), 'situados_ahora': placed, 'coste_ia_usd': round(usd(), 3)}
    json.dump({'meta': meta, 'eventos': eventos}, open(os.path.join(out, 'events.json'), 'w'), ensure_ascii=False)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'bilbao')
