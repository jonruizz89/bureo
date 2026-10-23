"""Conectores para APIs públicas de agenda que una página no deja ver (documentación interactiva, paginación…).

Cada conector sabe 1) si cubre una ciudad (`match`) y con qué parámetros, y 2) leer sus eventos (`read`).
discover.py los da de alta solos cuando la ciudad está en su territorio; probe.py y extract.py los leen con `read`.
"""
import datetime as dt, html, json, re, time, urllib.parse
from lib import fetch, fold, UA_API

# ---------- Kulturklik · agenda cultural del Gobierno Vasco (opendata.euskadi.eus, licencia CC BY 4.0)
KK = 'https://api.euskadi.eus/culture/events/v1.0'
JSON = 'application/json'
KK_REGIONS = {'bizkaia', 'gipuzkoa', 'araba', 'alava', 'araba alava', 'euskadi', 'pais vasco'}


def _clean(t):
    t = re.sub(r'<[^>]+>', ' ', html.unescape(t or ''))
    return re.sub(r'\s+', ' ', t).strip()


KK_PROV = {'bizkaia': '48', 'vizcaya': '48', 'gipuzkoa': '20', 'guipuzcoa': '20', 'araba': '01', 'alava': '01', 'araba alava': '01'}


def kk_match(city):
    if fold(city.get('region')) not in KK_REGIONS:
        return None
    if city.get('kind') == 'provincia':  # toda la provincia: todos sus municipios
        prov = KK_PROV.get(fold(city['name'])) or KK_PROV.get(fold(city.get('region')))
        return {'method': 'kulturklik', 'province': prov, 'municipality': None} if prov else None
    for prov in ('48', '20', '01'):
        st, _, txt, _ = fetch(f'{KK}/municipalities/byProvince/{prov}?_elements=400', accept=JSON, ua=UA_API)
        if st != 200:
            continue
        data = json.loads(txt)
        for m in data if isinstance(data, list) else data.get('items', []):
            if fold(m.get('nameEs')) == fold(city['name']) or fold(m.get('nameEu')) == fold(city['name']):
                return {'method': 'kulturklik', 'province': prov, 'municipality': m['municipalityId']}
    return None


def kk_read(cfg, max_pages=None):
    max_pages = max_pages or (30 if cfg.get('municipality') else 90)
    out, page, pages = [], 1, 1
    while page <= min(pages, max_pages):
        params = {'provinceNoraCode': cfg['province'], '_elements': 50, '_page': page}
        if cfg.get('municipality'):
            params['municipalityNoraCode'] = cfg['municipality']
        q = urllib.parse.urlencode(params)
        for intento in range(3):
            st, _, txt, _ = fetch(f'{KK}/events/upcoming?{q}', accept=JSON, ua=UA_API)
            if st == 200:
                break
            time.sleep(5 * (intento + 1))
        if st != 200:
            print(f'  Kulturklik: página {page} sin respuesta ({st})')
            if not out:
                return None
            page += 1
            continue
        d = json.loads(txt)
        pages = d.get('totalPages') or 1
        for i in d.get('items') or []:
            name = _clean(i.get('nameEs') or i.get('nameEu'))
            if not name or re.match(r'^\(?\s*(suspendid|cancelad|aplazad)', fold(name)) or i.get('online') in (True, 'true', 'Si', 'Sí'):
                continue
            price = _clean(i.get('priceEs'))
            ini, fin = (i.get('startDate') or '')[:10], (i.get('endDate') or i.get('startDate') or '')[:10]
            if not ini:
                continue
            web = i.get('urlEventEs') or i.get('sourceUrlEs') or i.get('purchaseUrlEs')
            out.append({
                'titulo': name[:300], 'descripcion': _clean(i.get('descriptionEs'))[:600], 'inicio': ini, 'fin': max(ini, fin),
                'hora': _clean(i.get('openingHoursEs'))[:60] or None,
                'lugar': _clean(i.get('establishmentEs') or i.get('placeEs')) or None,
                'direccion': _clean(i.get('placeEs')) or None,
                'precio': price or None, 'gratis': bool(re.search(r'\b(gratis|gratuit|doan)', fold(price))),
                'imagen': ((i.get('images') or [{}])[0] or {}).get('imageUrl'),
                'web_organizador': web, 'url': i.get('sourceUrlEs') or web,
                'municipio': i.get('municipalityEs'), 'tipo': i.get('typeEs'),
                'muni_ll': [float(i['municipalityLatitude']), float(i['municipalityLongitude'])] if i.get('municipalityLatitude') else None,
            })
        page += 1
    return out


# ---------- Agenda de Zaragoza · datos abiertos del Ayuntamiento (API de eventos, coordenadas en WGS84 con srsname)
ZGZ = 'https://www.zaragoza.es/sede/servicio/cultura/evento/list.json'


def zgz_match(city):
    return {'method': 'zaragoza'} if fold(city.get('name')) == 'zaragoza' else None


def _zgz_precio(e):
    ps = [p for p in e.get('price') or [] if isinstance(p, dict)]
    vals = [float(p.get('hasCurrencyValue') or 0) for p in ps]
    if ps and all(v == 0 for v in vals):
        return 'Gratis', True
    if vals:
        m = min(v for v in vals if v > 0)
        return (f'Desde {m:g} €' if len(set(vals)) > 1 else f'{m:g} €'), False
    return _clean(e.get('priceComment')) or None, False


def zgz_read(cfg, max_pages=20):
    out, start, total = [], 0, None
    while (total is None or start < total) and start < max_pages * 500:
        for intento in range(3):
            st, _, txt, _ = fetch(f'{ZGZ}?rows=500&start={start}&srsname=wgs84', accept=JSON, ua=UA_API)
            if st == 200:
                break
            time.sleep(5 * (intento + 1))
        if st != 200:
            print(f'  Zaragoza: página {start // 500 + 1} sin respuesta ({st})')
            return out or None
        d = json.loads(txt)
        total = d.get('totalCount') or 0
        for e in d.get('result') or []:
            name = _clean(e.get('title'))
            if not name or e.get('visible') == 'N':
                continue
            subs = [s for s in e.get('subEvent') or [] if isinstance(s, dict)]
            loc = next((s.get('location') for s in subs if s.get('location')), None) or e.get('location') or {}
            ini, fin = (e.get('startDate') or '')[:10], (e.get('endDate') or e.get('startDate') or '')[:10]
            if not ini:
                continue
            horas = [h.get('startTime') for s in subs for h in s.get('openingHours') or [] if h.get('startTime')]
            g = (loc.get('geometry') or {}).get('coordinates')
            img = e.get('image') or ''
            precio, gratis = _zgz_precio(e)
            junta = re.sub(r'^Junta (Municipal|Vecinal) (de )?', '', loc.get('junta') or '').strip() or None
            web = e.get('url') or e.get('alt')
            out.append({
                'titulo': name[:300], 'descripcion': _clean(e.get('description'))[:600], 'inicio': ini, 'fin': max(ini, fin),
                'hora': (horas[0] if ini == fin and len(set(horas)) == 1 else None),
                'lugar': _clean(loc.get('title')) or None, 'direccion': _clean(loc.get('streetAddress')) or None,
                'lat': g[1] if g and len(g) == 2 and abs(g[1]) <= 90 else None, 'lng': g[0] if g and len(g) == 2 and abs(g[0]) <= 180 else None,
                'distrito': junta, 'precio': precio, 'gratis': gratis,
                'imagen': ('https:' + img if img.startswith('//') else img) if img and '/icon/' not in img else None,
                'web_organizador': web, 'url': e.get('alt') or web,
            })
        start += 500
    return out


# ---------- Gijón/Xixón · contenidos del Ayuntamiento (Drupal JSON:API: eventos con fechas, distrito y la ficha del lugar)
GIJ = 'https://drupal.gijon.es/es/jsonapi/node/eventos'


def gij_match(city):
    return {'method': 'gijon'} if fold(city.get('name')).startswith('gijon') or fold(city.get('name')).startswith('xixon') else None


def gij_read(cfg, max_pages=40):
    hoy = dt.date.today().isoformat()
    q = {'filter[f][condition][path]': 'field_rango_de_fechas.end_value', 'filter[f][condition][operator]': '>=', 'filter[f][condition][value]': hoy,
         'page[limit]': '50', 'include': 'field_localizacion_en_el_directo'}
    url, out, pages = f'{GIJ}?{urllib.parse.urlencode(q)}', [], 0
    while url and pages < max_pages:
        for intento in range(3):
            st, _, txt, _ = fetch(url, accept='application/vnd.api+json', ua=UA_API)
            if st == 200:
                break
            time.sleep(5 * (intento + 1))
        if st != 200:
            print(f'  Gijón: página {pages + 1} sin respuesta ({st})')
            return out or None
        d = json.loads(txt)
        pages += 1
        lugares = {i['id']: i.get('attributes') or {} for i in d.get('included') or []}
        for e in d.get('data') or []:
            a = e.get('attributes') or {}
            name = _clean(a.get('title'))
            fechas = [f for f in a.get('field_rango_de_fechas') or [] if f.get('value')]
            if not name or not fechas or a.get('status') is False:
                continue
            ini = min(f['value'][:10] for f in fechas)
            fin = max((f.get('end_value') or f['value'])[:10] for f in fechas)
            if fin < hoy:
                continue
            horas = {f['value'][11:16] for f in fechas if f['value'][11:16] not in ('', '00:00')}
            rel = ((e.get('relationships') or {}).get('field_localizacion_en_el_directo') or {}).get('data') or []
            lug = lugares.get(rel[0]['id'], {}) if rel else {}
            lat = lng = None
            try:
                lat, lng = float(lug.get('field_latitud')), float(lug.get('field_longitud'))
            except (TypeError, ValueError):
                lo = (lug.get('field_lo') or [{}])[0] if isinstance(lug.get('field_lo'), list) else {}
                try:
                    lat, lng = float(lo.get('lat')), float(lo.get('lon'))
                except (TypeError, ValueError):
                    pass
            dist = a.get('field_distrito') or []
            alias = ((a.get('path') or {}).get('alias') or '').lstrip('/')
            web = f'https://www.gijon.es/es/eventos/{alias}' if alias else 'https://www.gijon.es/es/eventos'
            body = _clean((a.get('body') or {}).get('value') if isinstance(a.get('body'), dict) else a.get('body'))
            out.append({
                'titulo': name[:300], 'descripcion': body[:600], 'inicio': ini, 'fin': max(ini, fin),
                'hora': next(iter(horas)) if ini == fin and len(horas) == 1 else None,
                'lugar': _clean(lug.get('title')) or None, 'direccion': _clean(lug.get('field_direccion')) or None,
                'lat': lat, 'lng': lng, 'distrito': dist[0] if len(dist) == 1 else None,
                'precio': None, 'gratis': bool(re.search(r'\b(gratis|gratuit|entrada libre)', fold(body))),
                'imagen': None, 'web_organizador': web, 'url': web,
            })
        url = ((d.get('links') or {}).get('next') or {}).get('href')
    return out


CONNECTORS = {
    'kulturklik': {'name': 'Kulturklik · Agenda cultural de Euskadi', 'url': 'https://www.kulturklik.euskadi.eus/', 'layer': 'regional',
                   'license': 'Open Data Euskadi (CC BY 4.0)', 'match': kk_match, 'read': kk_read},
    'zaragoza': {'name': 'Agenda de Zaragoza (datos abiertos del Ayuntamiento)', 'url': 'https://www.zaragoza.es/sede/servicio/cultura/evento/',
                 'layer': 'municipal', 'license': 'Datos abiertos del Ayuntamiento de Zaragoza (CC BY 4.0)', 'match': zgz_match, 'read': zgz_read},
    'gijon': {'name': 'Agenda del Ayuntamiento de Gijón/Xixón', 'url': 'https://www.gijon.es/es/eventos', 'layer': 'municipal',
              'license': 'Enlace a la ficha original', 'match': gij_match, 'read': gij_read},
}


def suggest(city):
    """Fuentes de conector que cubren esta ciudad, listas para insertar en `sources`."""
    rows = []
    for key, c in CONNECTORS.items():
        try:
            cfg = c['match'](city)
        except Exception as ex:
            print(f'  conector {key}: {type(ex).__name__}'); cfg = None
        if cfg:
            rows.append({'city_id': city['id'], 'name': c['name'], 'url': c['url'], 'layer': c['layer'], 'method': None,
                         'license': c['license'], 'active': False, 'status': 'candidata', 'notes': 'API pública conocida',
                         'config': dict(cfg, descubierta=dt.date.today().isoformat(), ambito='conector')})
    return rows


def read(cfg):
    c = CONNECTORS.get(cfg.get('method'))
    return c['read'](cfg) if c else None
