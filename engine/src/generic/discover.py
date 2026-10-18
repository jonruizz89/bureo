"""Paso 1 · Descubrir fuentes de eventos de una ciudad con búsqueda web (Claude + web_search).

Para cada ámbito (agenda municipal, música, teatro, museos…) Claude busca en la web y devuelve las páginas de agenda
de quien organiza o publica eventos en el municipio. Se guardan en `sources` como «candidata» para que probe.py las pruebe.
Los agregadores (Eventbrite, Fever, Time Out…) se descartan: como mucho sirven de pista.
"""
import datetime as dt, sys
from lib import sb, tool_call, load_city, domain, is_aggregator, usd, over_budget
import connectors

AMBITOS = [
    ('municipal', 'la agenda oficial del ayuntamiento y de sus áreas de cultura, juventud, deportes y fiestas, y la agenda de turismo de la ciudad'),
    ('datos', 'portales de datos abiertos (municipal, autonómico o estatal) con conjuntos de datos de agenda, eventos o actividades culturales, y agendas culturales de la comunidad autónoma o la diputación'),
    ('musica', 'salas de conciertos, auditorios, festivales de música y sociedades filarmónicas'),
    ('escena', 'teatros, compañías, salas de artes escénicas, danza, circo y monólogos'),
    ('arte', 'museos, centros de arte, salas de exposiciones y galerías'),
    ('cine', 'filmotecas, cines con ciclos o cineclubs, y festivales de cine'),
    ('letras', 'bibliotecas, librerías con presentaciones, ateneos y centros culturales o cívicos con programación de actividades'),
    ('ciudad', 'fiestas populares, ferias, mercados, carreras populares y eventos deportivos, actividades de naturaleza y visitas guiadas'),
]

TOOL = {'name': 'guardar_fuentes', 'description': 'Guarda las fuentes de eventos encontradas.',
        'input_schema': {'type': 'object', 'required': ['fuentes'], 'properties': {'fuentes': {'type': 'array', 'items': {
            'type': 'object', 'required': ['nombre', 'url'],
            'properties': {
                'nombre': {'type': 'string', 'description': 'Quién publica (ej.: Teatro Arriaga)'},
                'url': {'type': 'string', 'description': 'La página que lista sus próximos eventos (agenda, programación, calendario) o el conjunto de datos abiertos; no la portada si hay una página de agenda'},
                'tipo': {'type': 'string', 'enum': ['ayuntamiento', 'datos_abiertos', 'agenda_autonomica', 'recinto', 'organizador', 'agregador', 'otro']},
                'notas': {'type': ['string', 'null'], 'description': 'Qué tipo de eventos publica, en pocas palabras'}}}}}}}


def main(cid):
    city = load_city(cid)
    name, region = city['name'], city.get('region') or ''
    known = {(s.get('url') or '').rstrip('/') for s in sb('GET', f'sources?select=url&city_id=eq.{cid}')}
    known_dom = {}
    for s in sb('GET', f'sources?select=url,name&city_id=eq.{cid}'):
        known_dom.setdefault(domain(s.get('url') or ''), 0)
        known_dom[domain(s.get('url') or '')] += 1
    found, rows = 0, []
    for r in connectors.suggest(city):  # APIs públicas conocidas del territorio (p. ej. Kulturklik en Euskadi)
        if r['url'].rstrip('/') not in known:
            known.add(r['url'].rstrip('/')); rows.append(r); found += 1
            print('conector:', r['name'])
    for key, que in AMBITOS:
        if over_budget():
            print('Presupuesto agotado:', round(usd(), 2), '$'); break
        system = (f'Hoy es {dt.date.today().isoformat()}. Ayudas a montar Limón, una agenda de planes, en {name} ({region}, España). '
                  f'Busca en la web las fuentes que publican eventos presenciales en el municipio de {name}: {que}. '
                  'Quiero la página concreta donde cada sitio lista sus próximas actividades, no noticias sueltas. Prioriza a quien organiza '
                  'los eventos (ayuntamiento, recintos, asociaciones) y los datos abiertos. Los agregadores comerciales (Eventbrite, Fever, '
                  'Time Out, ticketeras) márcalos como «agregador». Haz como mucho 3 búsquedas y después llama a guardar_fuentes con hasta 12 '
                  'fuentes; no inventes URLs: cada una debe salir de lo que hayas visto.')
        web = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3,
               'user_location': {'type': 'approximate', 'city': name, 'region': region, 'country': 'ES', 'timezone': 'Europe/Madrid'}}
        out = tool_call(system, f'Ámbito: {que}', TOOL, max_tokens=12000, extra_tools=[web]) or {}
        for f in out.get('fuentes') or []:
            url = (f.get('url') or '').strip()
            if not url.startswith('http') or url.rstrip('/') in known:
                continue
            known.add(url.rstrip('/'))
            agg = f.get('tipo') == 'agregador' or is_aggregator(url)
            d = domain(url)
            if not agg and known_dom.get(d, 0) >= 3:  # ya hay varias páginas de ese sitio
                continue
            known_dom[d] = known_dom.get(d, 0) + 1
            layer = 'agregador' if agg else {'ayuntamiento': 'municipal', 'datos_abiertos': 'municipal', 'agenda_autonomica': 'regional',
                                              'recinto': 'recinto'}.get(f.get('tipo'), 'organizador')
            rows.append({'city_id': cid, 'name': (f.get('nombre') or d)[:120], 'url': url[:500], 'layer': layer, 'method': None,
                         'license': 'Enlace a la ficha original', 'active': False,
                         'status': 'descartada' if agg else 'candidata',
                         'notes': ('Agregador: solo como pista. ' if agg else '') + (f.get('notas') or '')[:300],
                         'config': {'descubierta': dt.date.today().isoformat(), 'ambito': key, 'tipo': f.get('tipo')}})
            found += 1
        print(f'{key}: {len(out.get("fuentes") or [])} propuestas · {round(usd(), 3)} $')
    if rows:
        sb('POST', 'sources?on_conflict=city_id,name', rows, 'resolution=ignore-duplicates,return=minimal')
    print(f'{found} fuentes nuevas para {name} · coste {round(usd(), 3)} $')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'bilbao')
