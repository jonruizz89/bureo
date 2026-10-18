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


def kk_match(city):
    if fold(city.get('region')) not in KK_REGIONS:
        return None
    for prov in ('48', '20', '01'):
        st, _, txt, _ = fetch(f'{KK}/municipalities/byProvince/{prov}?_elements=400', accept=JSON, ua=UA_API)
        if st != 200:
            continue
        data = json.loads(txt)
        for m in data if isinstance(data, list) else data.get('items', []):
            if fold(m.get('nameEs')) == fold(city['name']) or fold(m.get('nameEu')) == fold(city['name']):
                return {'method': 'kulturklik', 'province': prov, 'municipality': m['municipalityId']}
    return None


def kk_read(cfg, max_pages=30):
    out, page, pages = [], 1, 1
    while page <= min(pages, max_pages):
        q = urllib.parse.urlencode({'provinceNoraCode': cfg['province'], 'municipalityNoraCode': cfg['municipality'],
                                    '_elements': 50, '_page': page})
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
            })
        page += 1
    return out


CONNECTORS = {
    'kulturklik': {'name': 'Kulturklik · Agenda cultural de Euskadi', 'url': 'https://www.kulturklik.euskadi.eus/', 'layer': 'regional',
                   'license': 'Open Data Euskadi (CC BY 4.0)', 'match': kk_match, 'read': kk_read},
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
