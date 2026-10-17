"""Auditoría semanal de cobertura.

Para cada grupo de categorías, Claude busca en la web eventos de Madrid de las dos próximas semanas (herramienta
web_search del API). Cada evento encontrado se compara con la base; los que faltan se guardan como huecos con su
origen, y los dominios de origen nuevos se acumulan como fuentes candidatas. Se mide el % de huecos por grupo.

Variables: SUPABASE_URL, SUPABASE_SECRET, ANTHROPIC_API_KEY, BUREO_AUDIT_MAX_USD (1.0), BUREO_AUDIT_MODEL.
Coste orientativo: unas 3 búsquedas por grupo (10 $ por 1.000 búsquedas) + tokens → 0,5–1 $ por ejecución.
"""
import datetime as dt, difflib, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

URL = os.environ['SUPABASE_URL'].rstrip('/')
SKEY = os.environ['SUPABASE_SECRET']
AKEY = os.environ['ANTHROPIC_API_KEY']
MODEL = os.environ.get('BUREO_AUDIT_MODEL', 'claude-haiku-5-5')
MAX_USD = float(os.environ.get('BUREO_AUDIT_MAX_USD', '1.0'))
CITY = 'madrid'
HOY = dt.date.today()
FIN = HOY + dt.timedelta(days=14)
COST = {'in': 0, 'out': 0, 'search': 0}
GAP_ALERT = 10.0  # % de huecos a partir del cual se pide un descubrimiento dirigido
AGREGADORES = ('eventbrite.', 'meetup.', 'timeout.', 'feverup.', 'fever.', 'ticketmaster.', 'entradas.com', 'correrjuntos.',
               'instagram.', 'facebook.', 'x.com', 'twitter.', 'tiktok.', 'youtube.', 'google.', 'wikipedia.')


def usd():
    return (COST['in'] * 0.10 + COST['out'] * 0.50) / 1e6 + COST['search'] * 0.01


def sb(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + '/rest/v1/' + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    for k, v in (('apikey', SKEY), ('Authorization', 'Bearer ' + SKEY), ('Content-Type', 'application/json')):
        req.add_header(k, v)
    if prefer:
        req.add_header('Prefer', prefer)
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def fold(t):
    t = unicodedata.normalize('NFD', str(t or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9ñ ]', ' ', ''.join(c for c in t if unicodedata.category(c) != 'Mn'))).strip()


STOP = set('de del la las el los y en a con para por un una al lo sus su madrid 2026 2027 edicion festival concierto exposicion'.split())


def toks(t):
    return {w for w in fold(t).split() if len(w) > 2 and w not in STOP}


def domain(u):
    try:
        h = urllib.parse.urlparse(u).hostname or ''
    except ValueError:
        return ''
    return re.sub(r'^www\d?\.', '', h.lower())


TOOL = {'name': 'guardar_encontrados', 'description': 'Guarda los eventos encontrados.',
        'input_schema': {'type': 'object', 'required': ['eventos'], 'properties': {'eventos': {'type': 'array', 'items': {
            'type': 'object', 'required': ['titulo', 'fecha', 'url'],
            'properties': {
                'titulo': {'type': 'string'},
                'fecha': {'type': 'string', 'description': 'YYYY-MM-DD (el primer día dentro del periodo)'},
                'lugar': {'type': ['string', 'null']},
                'url': {'type': 'string', 'description': 'La página donde lo has visto'},
                'organizador': {'type': ['string', 'null']},
                'origen': {'type': ['string', 'null'], 'description': 'Web del organizador o de quien lo publica primero, si la conoces'},
                'categoria_nueva': {'type': ['string', 'null'], 'description': 'Solo si no encaja en ninguna categoría del grupo: nombre de la categoría que faltaría'}}}}}}}


def search_group(g, labels):
    system = (f'Hoy es {HOY.isoformat()}. Eres el auditor de cobertura de Limón, una app de planes en Madrid. Busca en la web eventos '
              f'presenciales en el municipio de Madrid entre el {HOY.isoformat()} y el {FIN.isoformat()} del grupo «{g["name"]}» '
              f'({", ".join(labels)}). Busca sobre todo lo que una agenda oficial podría no tener: organizadores privados, '
              'asociaciones, federaciones, salas pequeñas, librerías, embajadas e institutos culturales. Haz como mucho 3 búsquedas. '
              'Después llama a guardar_encontrados con hasta 15 eventos concretos con fecha dentro del periodo; no inventes nada: '
              'cada evento debe venir de una página que hayas visto.')
    body = {'model': MODEL, 'max_tokens': 4000, 'system': system,
            'tools': [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3,
                       'user_location': {'type': 'approximate', 'city': 'Madrid', 'region': 'Madrid', 'country': 'ES', 'timezone': 'Europe/Madrid'}},
                      TOOL],
            'messages': [{'role': 'user', 'content': f'Grupo: {g["name"]}'}]}
    for attempt in range(2):
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST', data=json.dumps(body).encode())
        for k, v in (('x-api-key', AKEY), ('anthropic-version', '2023-06-01'), ('content-type', 'application/json')):
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print('  API', e.code, e.read().decode()[:300])
            time.sleep(15)
            continue
        u = res.get('usage') or {}
        COST['in'] += u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0)
        COST['out'] += u.get('output_tokens', 0)
        COST['search'] += (u.get('server_tool_use') or {}).get('web_search_requests', 0)
        blk = [b for b in res.get('content', []) if b.get('type') == 'tool_use' and b.get('name') == 'guardar_encontrados']
        kinds = [b.get('type') for b in res.get('content', [])]
        if os.environ.get('BUREO_AUDIT_DEBUG'):
            print('  ', res.get('stop_reason'), kinds, json.dumps(blk[0]['input'], ensure_ascii=False)[:600] if blk else
                  ' '.join(b.get('text', '') for b in res.get('content', []) if b.get('type') == 'text')[:400])
        if blk:
            return blk[0]['input'].get('eventos') or []
        if res.get('stop_reason') == 'pause_turn':  # la búsqueda sigue en curso: se reanuda tal cual
            body['messages'] += [{'role': 'assistant', 'content': res.get('content', [])}]
            continue
        # si no llamó a la herramienta, se le pide que lo haga con lo que ya ha visto
        body['messages'] += [{'role': 'assistant', 'content': res.get('content', [])},
                             {'role': 'user', 'content': 'Guarda ahora lo encontrado con guardar_encontrados.'}]
        body['tool_choice'] = {'type': 'tool', 'name': 'guardar_encontrados'}
    return []


def main():
    groups = [g for g in sb('GET', 'category_groups?select=id,name&order=position') if g['id'] != 'otros']
    only = [x for x in os.environ.get('BUREO_AUDIT_GROUPS', '').split(',') if x]
    if only:
        groups = [g for g in groups if g['id'] in only]
    cats = sb('GET', 'categories?select=id,label,group_id&is_generic=eq.false')
    evs = []
    for off in range(0, 20000, 1000):
        part = sb('GET', f'events?select=title,starts_on,ends_on,venues(name),event_sessions(on_date)&city_id=eq.{CITY}'
                         f'&ends_on=gte.{HOY.isoformat()}&starts_on=lte.{(FIN + dt.timedelta(days=2)).isoformat()}&order=id&offset={off}&limit=1000')
        evs += part
        if len(part) < 1000:
            break
    base = [(toks(e['title']), fold(e['title']), dt.date.fromisoformat(e['starts_on']), dt.date.fromisoformat(e['ends_on'])) for e in evs]
    known = {domain(s['url']) for s in sb('GET', f'sources?select=url&city_id=eq.{CITY}')} | {'esmadrid.com', 'madrid.es', 'datos.madrid.es'}
    print(f'{len(evs)} eventos en la base para {HOY}–{FIN}; {len(groups)} grupos')

    def present(item):
        try:
            f = dt.date.fromisoformat(item['fecha'][:10])
        except ValueError:
            return True  # sin fecha válida no se cuenta
        t, ft = toks(item['titulo']), fold(item['titulo'])
        for bt, bf, a, b in base:
            if not (a - dt.timedelta(days=1) <= f <= b + dt.timedelta(days=1)):
                continue
            if t and bt and (len(t & bt) / max(1, min(len(t), len(bt))) >= 0.6 or difflib.SequenceMatcher(None, ft, bf).ratio() >= 0.75):
                return True
        return False

    run_at = dt.datetime.now(dt.timezone.utc).isoformat()
    cands = {c['domain']: c for c in sb('GET', f'source_candidates?select=*&city_id=eq.{CITY}')}
    runs, gaps = [], []
    for g in groups:
        if usd() >= MAX_USD:
            print('tope de gasto alcanzado; el resto de grupos queda para la próxima semana')
            break
        labels = [c['label'] for c in cats if c['group_id'] == g['id']]
        raw = search_group(g, labels)
        found = [x for x in raw if x.get('titulo') and HOY.isoformat() <= (x.get('fecha') or '')[:10] <= FIN.isoformat()]
        if len(raw) != len(found):
            print(f'  {len(raw) - len(found)} sin fecha dentro del periodo:', [x.get('fecha') for x in raw if x not in found][:6])
        miss = [x for x in found if not present(x)]
        pct = round(100 * len(miss) / len(found), 1) if found else 0.0
        print(f'{g["name"]}: {len(found)} encontrados, {len(miss)} faltan ({pct} %)' + ('  ← descubrimiento dirigido' if pct > GAP_ALERT else ''))
        runs.append({'city_id': CITY, 'run_at': run_at, 'group_id': g['id'], 'found': len(found), 'missing': len(miss), 'pct_missing': pct})
        for x in miss:
            origin = x.get('origen') or x.get('url')
            gaps.append({'city_id': CITY, 'run_at': run_at, 'group_id': g['id'], 'title': x['titulo'][:300], 'on_date': x['fecha'][:10],
                         'venue': (x.get('lugar') or '')[:200] or None, 'url': x.get('url'), 'origin': origin,
                         'organizer': (x.get('organizador') or '')[:200] or None, 'new_category': x.get('categoria_nueva')})
            d = domain(origin)
            if not d or d in known or any(a in d for a in AGREGADORES):
                d2 = domain(x.get('url'))  # si el origen es un agregador, la pista es la página vista
                if not d2 or d2 in known:
                    continue
                d = d2
            c = cands.get(d) or {'city_id': CITY, 'domain': d, 'hits': 0, 'groups': [], 'first_seen': HOY.isoformat(), 'status': 'nueva'}
            c['hits'] += 1
            c['groups'] = sorted(set(c['groups']) | {g['id']})
            c['last_seen'] = HOY.isoformat()
            c['example_url'] = x.get('url')
            c['example_title'] = x['titulo'][:300]
            c['organizer'] = x.get('organizador') or c.get('organizer')
            cands[d] = c
    if runs:
        sb('POST', 'coverage_runs', runs, 'return=minimal')
    if gaps:
        sb('POST', 'coverage_gaps', gaps, 'return=minimal')
    keys = ('city_id', 'domain', 'hits', 'groups', 'example_url', 'example_title', 'organizer', 'first_seen', 'last_seen', 'status')
    rows = [{k: c.get(k) for k in keys} for c in cands.values()]
    if rows:
        sb('POST', 'source_candidates?on_conflict=city_id,domain', rows, 'resolution=merge-duplicates,return=minimal')
    tot_f = sum(r['found'] for r in runs)
    tot_m = sum(r['missing'] for r in runs)
    print(f'Total: {tot_f} encontrados, {tot_m} faltan ({round(100 * tot_m / max(1, tot_f), 1)} %). '
          f'{len([c for c in cands.values() if c["status"] == "nueva"])} fuentes candidatas. Coste aproximado: {usd():.3f} USD')


if __name__ == '__main__':
    main()
