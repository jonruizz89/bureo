"""Pestaña «Calle» en ciudades del motor genérico: cortes, obras y eventos en la vía pública.

Fuente: Tráfico Euskadi (api.euskadi.eus/traffic), que publica las incidencias de cada ayuntamiento con fechas y coordenadas
(Bilbao = fuente 5, Vitoria-Gasteiz = 6). Se queda con lo vigente o próximo (30 días) que afecta a la calle y reemplaza las filas
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


def main(cid):
    city = load_city(cid)
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
    if os.environ.get('ANTHROPIC_API_KEY') and rows:
        rows = polish(rows, city)
        print(f'{len(rows)} tras revisarlos con IA · {round(usd(), 3)} $')
    if '--dry' in sys.argv:
        for r in rows[:15]:
            print(' ·', r['kind'], r['ranges'][0]['from'], '→', r['ranges'][0]['to'], '|', r['title'][:90], '|', r['streets'])
        return
    sb('DELETE', f'closures?city_id=eq.{cid}', None, 'return=minimal')
    for k in range(0, len(rows), 200):
        sb('POST', 'closures', rows[k:k + 200], 'return=minimal')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'bilbao')
