"""Pestaña «Calle» en ciudades del motor genérico: cortes, obras y eventos en la vía pública.

Fuentes: Tráfico Euskadi (api.euskadi.eus/traffic), que publica las incidencias de cada ayuntamiento con fechas y coordenadas
(Bilbao = 5, Vitoria-Gasteiz = 6, Donostia = 7), la web del Ayuntamiento de Gijón (cortes de tráfico y cambios de autobús)
y los avisos de Renfe Cercanías de cada núcleo (Bilbao, San Sebastián, Asturias). Se queda con lo vigente o próximo (30 días) que afecta a la calle y reemplaza las filas
de `closures` de esa ciudad. Las ocupaciones menores (contenedores, andamios, aleros, toldos…) se descartan.
"""
import datetime as dt, html, json, re, sys
import os
from lib import sb, fetch, load_city, fold, UA_API, tool_call, usd

API = 'https://api.euskadi.eus/traffic/v1.0'
SOURCES = {'bilbao': ('5', 'Ayuntamiento de Bilbao'), 'vitoria': ('6', 'Ayuntamiento de Vitoria-Gasteiz'), 'donostia': ('7', 'Ayuntamiento de Donostia / San Sebastián')}
PORTAL = 'https://www.trafikoa.eus/'
MINOR = re.compile(r'contenedor|andamio|alero|toldo|fachada|cubierta|tejado|canal[oó]n|plataforma elevadora|mudanza|camion grua|'
                   r'cami[oó]n pluma|marquesina|terraza|mesas y sillas|carga y descarga|vado|cata|sondeo', re.I)
TRAFFIC = re.compile(r'cort|cierre|cerrad|desv[ií]|carril|calzada|circulaci|tr[aá]fico|paso alternativo|sentido|ocupaci[oó]n total', re.I)
EVENT = re.compile(r'carrera|marcha|prueba deportiva|cicl|maraton|evento|fiesta|feria|concierto|partido|desfile|procesi|cabalgata|'
                   r'manifestaci|concentraci|rodaje', re.I)
STREET = re.compile(r"\b(calle|c/|avenida|avda\.?|av\.|plaza|pza\.?|alameda|paseo|camino|carretera|traves[ií]a|ronda|muelle|"
                    r"puente|glorieta|kalea|zumarkalea|etorbidea|hiribidea|pasealekua|enparantza)\s+([^,.;()]+?)(?=\s*(?:,|\.|;|\(|$|\s+n[º°o]?\s*\d|"
                    r"\s+\d|\s+y\s|\s+esquina|\s+entre|\s+desde|\s+hasta|\s+a la altura|\s+frente))", re.I)
SMALL = {'de', 'del', 'la', 'las', 'el', 'los', 'y', 'e', 'a', 'en'}


def nice(s):
    s = html.unescape(s or '').strip()
    if s.upper() == s:  # TODO EN MAYÚSCULAS → Frase normal
        s = s.lower()
        s = s[:1].upper() + s[1:]
    return re.sub(r'\s+', ' ', s)


def titlecase(s):
    w = s.lower().split()
    return ' '.join(x if (i and x in SMALL) else x[:1].upper() + x[1:] for i, x in enumerate(w))


def streets_of(text):
    out = []
    for kind, name in STREET.findall(html.unescape(text)):
        k = kind.lower().rstrip('.')
        k = {'c/': 'calle', 'avda': 'avenida', 'av': 'avenida', 'pza': 'plaza'}.get(k, k)
        n = titlecase(name.strip())
        if len(n) >= 3:
            s = f'{k[:1].upper() + k[1:]} {n}' if k not in ('kalea', 'zumarkalea', 'etorbidea', 'hiribidea', 'pasealekua', 'enparantza') else f'{n} {k}'
            if s not in out:
                out.append(s)
    return out[:4]


def day_time(v):
    if not v:
        return None, None
    d, _, t = v.partition('T')
    t = t[:5] if t and t[:5] != '00:00' else None
    return d, t


TOOL = {'name': 'guardar_avisos', 'description': 'Guarda los avisos revisados.', 'input_schema': {'type': 'object', 'required': ['avisos'], 'properties': {
    'avisos': {'type': 'array', 'items': {'type': 'object', 'required': ['n', 'afecta'], 'properties': {
        'n': {'type': 'integer'},
        'afecta': {'type': 'boolean', 'description': 'true si corta, estrecha o desvía el tráfico o el paso de peatones de forma notable (obras en calzada, cortes, eventos en la calle); false si es una ocupación menor sin efecto para quien pasa (descarga de material, grúa en acera, limpieza de fachada, permisos de entrada)'},
        'titulo': {'type': 'string', 'description': 'Título corto y claro en castellano, con mayúsculas normales (p. ej. «Obras de urbanización en la avenida Madariaga»)'},
        'tipo': {'type': 'string', 'enum': ['obras', 'evento', 'otro']},
        'calles': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Calles afectadas con su nombre habitual (p. ej. «Gran Vía», «Calle Iparraguirre»)'}}}}}}}


def polish(rows, city):
    out = []
    for k in range(0, len(rows), 40):
        part = rows[k:k + 40]
        lista = '\n'.join(f"{n}. {r['summary']}" for n, r in enumerate(part))
        res = tool_call(f'Revisas los avisos de tráfico del ayuntamiento de {city["name"]} para la pestaña «Calle» de Limón, que avisa a la gente '
                        'antes de salir. Para cada aviso decide si afecta de verdad a quien pasa por la calle y, si es así, dale un título claro, '
                        'el tipo y las calles. Llama a guardar_avisos con todos.', lista, TOOL, max_tokens=6000) or {}
        got = {a.get('n'): a for a in res.get('avisos') or []}
        for n, r in enumerate(part):
            a = got.get(n)
            if not a:
                out.append(r); continue  # sin respuesta: se queda como estaba
            if not a.get('afecta'):
                continue
            if a.get('titulo'):
                r['title'] = a['titulo'][:120]
            if a.get('tipo'):
                r['kind'] = a['tipo']
            if a.get('calles'):
                r['streets'] = a['calles'][:4]
                if r.get('geom') and r['geom'].get('pts'):
                    r['geom']['pts'][0]['n'] = a['calles'][0]
            out.append(r)
    return out


GIJON = 'https://drupal.gijon.es/es/jsonapi/node/incidencias_de_trafico'


def gijon_rows(cid):
    """Gijón/Xixón: cortes de tráfico y cambios de autobús que publica el Ayuntamiento en su web (Drupal JSON:API)."""
    import urllib.parse
    today = dt.date.today()
    until = (today + dt.timedelta(days=30)).isoformat()
    q = {'filter[f][condition][path]': 'field_fecha_fin_corte', 'filter[f][condition][operator]': '>=', 'filter[f][condition][value]': today.isoformat(),
         'page[limit]': '50'}
    url, rows = f'{GIJON}?{urllib.parse.urlencode(q)}', []
    while url:
        st, _, txt, _ = fetch(url, accept='application/vnd.api+json', ua=UA_API)
        if st != 200:
            print('Gijón', st); break
        d = json.loads(txt)
        for e in d.get('data') or []:
            a = e.get('attributes') or {}
            ini, h1 = day_time(a.get('field_fecha_inicio_corte'))
            fin, h2 = day_time(a.get('field_fecha_fin_corte'))
            fin = fin or ini
            if not ini or fin < today.isoformat() or ini > until:
                continue
            name = nice(a.get('title') or '')
            body = re.sub(r'<[^>]+>', ' ', html.unescape(((a.get('body') or {}).get('value') if isinstance(a.get('body'), dict) else '') or ''))
            body = re.sub(r'\s+', ' ', body).strip()
            lines = [x for x in a.get('field_lineas_de_autobus') or []]
            kind = 'autobus' if lines and not TRAFFIC.search(name) else 'evento' if EVENT.search(name + ' ' + body[:200]) else 'obras' if re.search(r'obra', name + body[:200], re.I) else 'otro'
            if h2 == '23:59':
                h2 = None
            alias = ((a.get('path') or {}).get('alias') or '').lstrip('/')
            dist = a.get('field_distrito') or []
            rows.append({'city_id': cid, 'title': name[:120], 'kind': kind, 'summary': (body or name)[:600], 'streets': streets_of(name + '. ' + body)[:4],
                         'lines': lines[:12], 'stations': [], 'district': dist[0] if len(dist) == 1 else None,
                         'ranges': [{'from': ini, 'to': fin, 'start': h1, 'end': h2, 'holidays': True, 'weekdays': []}],
                         'source_url': f'https://www.gijon.es/es/{alias}' if alias else 'https://www.gijon.es/', 'source_name': 'Ayuntamiento de Gijón/Xixón', 'geom': None})
        url = ((d.get('links') or {}).get('next') or {}).get('href')
    print(f'{len(rows)} avisos vigentes o próximos')
    return rows


def main(cid):
    city = load_city(cid)
    if cid == 'gijon':
        return save(cid, gijon_rows(cid), city)
    if cid not in SOURCES:
        print('Sin fuente de calle para', cid); return
    src, src_name = SOURCES[cid]
    today = dt.date.today()
    until = (today + dt.timedelta(days=30)).isoformat()
    rows, seen, kept, minor = [], set(), 0, 0
    for page in range(1, 21):
        for _try in range(3):
            st, _, txt, _ = fetch(f'{API}/incidences/bySource/{src}?_page={page}', accept='application/json', ua=UA_API)
            if st == 200:
                break
        if st != 200:
            print('página', page, st); break
        items = (json.loads(txt) or {}).get('incidences') or []
        if not items:
            break
        recent = 0
        for i in items:
            ini, h1 = day_time(i.get('startDate'))
            fin, h2 = day_time(i.get('endDate'))
            fin = fin or ini
            if not ini or fin < today.isoformat():
                continue
            recent += 1
            if ini > until:
                continue
            name = nice(i.get('incidenceName') or i.get('cause') or '')
            if not name or name.lower() in seen:
                continue
            if MINOR.search(fold(name)) and not TRAFFIC.search(name):
                minor += 1; continue
            seen.add(name.lower())
            ev = EVENT.search(name)
            kind = 'evento' if ev else 'obras' if i.get('incidenceType') in ('Obra', 'Obras', 'Mantenimiento') else 'otro'
            sts = streets_of(name)
            try:
                lat, lng = float(i.get('latitude')), float(i.get('longitude'))
                geom = {'pts': [{'p': [round(lat, 6), round(lng, 6)], 'n': sts[0] if sts else name[:60]}], 'segs': []}
            except (TypeError, ValueError):
                geom = None
            title = name if len(name) <= 110 else name[:107].rsplit(' ', 1)[0] + '…'
            rows.append({'city_id': cid, 'title': title, 'kind': kind, 'summary': name, 'streets': sts, 'lines': [], 'stations': [],
                         'district': None, 'ranges': [{'from': ini, 'to': fin, 'start': h1, 'end': h2, 'holidays': True, 'weekdays': []}],
                         'source_url': PORTAL, 'source_name': f'{src_name} · Tráfico Euskadi', 'geom': geom})
            kept += 1
        if recent == 0 and page > 3:  # páginas ya solo con incidencias pasadas
            break
    print(f'{kept} avisos vigentes o próximos · {minor} ocupaciones menores descartadas')
    save(cid, rows, city)


# Avisos de Renfe Cercanías (GTFS-RT público y sin clave). Madrid ya los recibe por el Consorcio (closures.py).
RENFE = 'https://gtfsrt.renfe.com/alerts.json'
NUCLEO = {'bilbao': '60', 'donostia': '61', 'gijon': '20'}
SOLO_ACCESIBILIDAD = re.compile(r'ascensor|escaleras? mec[aá]nica', re.I)


def renfe_rows(cid):
    nuc = NUCLEO.get(cid)
    if not nuc:
        return []
    st, _, txt, _ = fetch(RENFE, accept='application/json', ua=UA_API)
    if st != 200:
        print('Renfe', st); return []
    today = dt.date.today()
    out, vistos = [], set()
    for e in (json.loads(txt) or {}).get('entity') or []:
        a = e.get('alert') or {}
        rutas = [x.get('routeId') or '' for x in a.get('informedEntity') or []]
        if not any(r.startswith(nuc + 'T') for r in rutas):
            continue
        tr = ((a.get('descriptionText') or {}).get('translation') or [{}])
        t = next((x['text'] for x in tr if x.get('language') == 'es'), tr[0].get('text', ''))
        t = t.split('//')[-1].strip() if '//' in t else t  # en Euskadi viene «euskera // castellano»
        t = re.sub(r'#\w+\s*', '', t).strip()
        if not t or SOLO_ACCESIBILIDAD.search(t) and not re.search(r'interrump|suprim|cerrad|no efect|sin servicio|autob[uú]s', t, re.I):
            continue  # un ascensor o una escalera fuera de servicio: útil para pocos y llenaría la lista
        p = (a.get('activePeriod') or [{}])[0]
        ini = dt.datetime.fromtimestamp(int(p['start'])).date() if p.get('start') else today
        fin = dt.datetime.fromtimestamp(int(p['end'])).date() if p.get('end') else today + dt.timedelta(days=7)
        if fin < today:
            continue
        lineas = sorted({m.group(1) for r in rutas if r.startswith(nuc + 'T') for m in [re.search(r'(C\d+[a-z]?)$', r)] if m})
        if len(lineas) >= 4:
            lineas = []  # el aviso va a todas las líneas del núcleo: es de una estación o general, no de una línea
        m = re.match(r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ \-/]{2,40})[:.]\s*(.*)', t)
        estacion, cuerpo = (titlecase(m.group(1).strip()), m.group(2)) if m else (None, t)
        if estacion:
            estacion = re.sub(r'-(\w)', lambda x: '-' + x.group(1).upper(), estacion)
        else:
            m2 = re.search(r'estaci[oó]n de ([A-ZÁÉÍÓÚÑ][\w\-]+(?: [A-ZÁÉÍÓÚÑ][\w\-]+)?)', t)
            estacion = m2.group(1) if m2 else None
        frase = re.split(r'(?<=[.;])\s', cuerpo, 1)[0].rstrip('.;')
        titulo = ('Cercanías ' + '/'.join(lineas[:3]) + ' · ' if lineas else 'Cercanías · ') + (estacion + ': ' if estacion and estacion not in frase else '') + frase
        titulo = titulo if len(titulo) <= 110 else titulo[:107].rsplit(' ', 1)[0] + '…'
        if titulo.lower() in vistos:
            continue
        vistos.add(titulo.lower())
        out.append({'city_id': cid, 'title': titulo, 'kind': 'cercanias', 'summary': t[:600], 'streets': [], 'lines': lineas,
                    'stations': [estacion] if estacion else [], 'district': None,
                    'ranges': [{'from': max(ini, today).isoformat(), 'to': fin.isoformat(), 'start': None, 'end': None, 'holidays': True, 'weekdays': []}],
                    'source_url': 'https://www.renfe.com/es/es/cercanias', 'source_name': 'Renfe Cercanías', 'geom': None})
    print(f'{len(out)} avisos de Renfe Cercanías')
    return out


def save(cid, rows, city):
    if os.environ.get('ANTHROPIC_API_KEY') and rows:
        rows = polish(rows, city)
        print(f'{len(rows)} tras revisarlos con IA · {round(usd(), 3)} $')
    try:
        rows = rows + renfe_rows(cid)
    except Exception as e:  # noqa: BLE001  — si Renfe falla, los cortes de calle se guardan igual
        print('Renfe no disponible:', str(e)[:200])
    if '--dry' in sys.argv:
        for r in rows[:15]:
            print(' ·', r['kind'], r['ranges'][0]['from'], '→', r['ranges'][0]['to'], '|', r['title'][:90], '|', r['streets'])
        return
    sb('DELETE', f'closures?city_id=eq.{cid}', None, 'return=minimal')
    for k in range(0, len(rows), 200):
        sb('POST', 'closures', rows[k:k + 200], 'return=minimal')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'bilbao')
