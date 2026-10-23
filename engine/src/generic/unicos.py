"""Paso 6 · «Únicos en <ciudad>»: citas de ciudad que pasan como mucho una vez al año (fiestas, festivales anuales, grandes ferias…).

En Madrid se marcan con reglas propias (pipeline.py); en las demás ciudades lo decide la IA con la lista de eventos ya clasificados.
Coste: una llamada por ciudad y noche (unos céntimos).
"""
import datetime as dt, re, sys
from lib import sb, tool_call, load_city, usd

TOOL = {'name': 'marcar_unicos', 'description': 'Guarda qué eventos son planes únicos de la ciudad.',
        'input_schema': {'type': 'object', 'required': ['ids'], 'properties': {
            'ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Ids de los eventos que son planes únicos'}}}}


# Lo que la IA a veces cuela y nunca es un plan único: funciones sueltas de un festival («Festival X 2026: obra»), ciclos
# comerciales que se repiten en muchas ciudades, clubes de lectura, sesiones de cine…
SESION = re.compile(r'^[^:]{3,80}\b20\d\d\b[^:]*:\s*\S|^[^:]{3,60}\(\d{1,2}/\d{1,2}\)|candlelight|club de lectura|cine-?club|irakurle|'
                    r'entrega de dorsales|visitas? guiadas?|mercado de trueque|^\W*(concierto|recital)\b', re.I)
MAX_UNICOS = 40


def sesion(t):
    return bool(SESION.search(t or ''))


def main(cid):
    city = load_city(cid)
    hoy = dt.date.today().isoformat()
    evs = []
    for off in range(0, 20000, 1000):
        part = sb('GET', f'events?select=id,title,starts_on,ends_on,venues(name),event_categories(category_id)&city_id=eq.{cid}'
                         f'&ends_on=gte.{hoy}&order=id&offset={off}&limit=1000') or []
        evs += part
        if len(part) < 1000:
            break
    cand = [e for e in evs if (dt.date.fromisoformat(e['ends_on']) - dt.date.fromisoformat(e['starts_on'])).days <= 45]
    if not cand:
        print('sin eventos'); return
    lines = '\n'.join(f"{e['id']}|{e['title'][:90]}|{e['starts_on']}{'→' + e['ends_on'] if e['ends_on'] != e['starts_on'] else ''}|"
                      f"{(e.get('venues') or {}).get('name', '')[:40]}|{','.join(c['category_id'] for c in e.get('event_categories') or [])}" for e in cand)
    system = (f'Eres el editor de Limón, una agenda de planes en {city["name"]}. Marca los «planes únicos en {city["name"]}»: citas de la ciudad '
              'que pasan como mucho una vez al año y que alguien de allí lamentaría perderse: fiestas populares y patronales, festivales anuales '
              '(música, cine, teatro, danza), grandes ferias y mercados de temporada, carreras populares conocidas, tradiciones, cabalgatas, '
              'celebraciones de Navidad o de una fecha concreta. NO marques: conciertos o funciones sueltas, obras en gira, exposiciones, talleres, '
              'cursos, conferencias, ciclos semanales, sesiones de cine, cuentacuentos, clubes de lectura, visitas guiadas, conciertos de bandas '
              'o escuelas de música, ni una función suelta aunque forme parte de un festival (si el título es «Festival X 2026: obra», NO es único), ni ciclos comerciales que se repiten en muchas ciudades (Candlelight, galas de ballet en gira, tributos). Si un festival tiene varias actividades sueltas, marca la principal '
              '(la que lleva el nombre del festival), no cada actividad. Sé exigente: normalmente son pocos (de 5 a 40). '
              + ('Es una provincia entera: marca solo fiestas patronales y populares, festivales anuales con nombre propio, ferias y grandes citas '
                 'de cada pueblo. ' if city.get('kind') == 'provincia' else '') +
              'Responde con marcar_unicos y los ids.')
    out = tool_call(system, 'id|título|fechas|lugar|categorías\n' + lines, TOOL, max_tokens=4000) or {}
    valid = {e['id']: e for e in cand}
    ids = sorted({int(i) for i in out.get('ids') or [] if int(i) in valid and not sesion(valid[int(i)]['title'])})
    ids = ids[:MAX_UNICOS]
    if out.get('ids') is None:
        print('La IA no ha respondido; se deja como estaba'); return
    on = ','.join(map(str, ids))
    if ids:
        sb('PATCH', f'events?city_id=eq.{cid}&id=in.({on})', {'is_unique': True}, 'return=minimal')
    sb('PATCH', f'events?city_id=eq.{cid}&ends_on=gte.{hoy}&is_unique=is.true' + (f'&id=not.in.({on})' if ids else ''), {'is_unique': False}, 'return=minimal')
    titles = {e['id']: e['title'] for e in cand}
    print(f'{len(ids)} únicos de {len(cand)} candidatos · {round(usd(), 3)} $')
    for i in ids:
        print(' ·', titles[i][:80])


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'bilbao')
