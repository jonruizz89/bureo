"""Bureo · motor de datos (fase 0) para Madrid.

Lee las fuentes descargadas en raw/, las lleva a un esquema común, une duplicados,
asigna categorías de la taxonomía y marca los planes únicos. Escribe out/events.json.

Fuentes:
  - Agenda municipal (datos.madrid.es, 2 conjuntos, CC BY 4.0)
  - esMadrid (portal de turismo; fichas con schema.org Event + campos de la ficha)
  - Recintos con schema.org Event: IFEMA, Real Jardín Botánico

En producción, la categorización por reglas se sustituye por un modelo pequeño (Haiku)
que lee título + descripción; aquí se usan reglas para poder ejecutarlo sin clave de API.
"""
import json, re, os, unicodedata, html, difflib, datetime as dt, collections

RAW = os.environ.get('BUREO_RAW', 'raw')
OUT = os.environ.get('BUREO_OUT', 'out')
HOY = dt.datetime.now(dt.timezone(dt.timedelta(hours=1))).date()  # hora peninsular aproximada
HASTA = HOY + dt.timedelta(days=120)
TAX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'taxonomy.json')))
ITEM_GROUP = {it['id']: g['id'] for g in TAX for it in g['items']}


def norm(t):
    t = unicodedata.normalize('NFD', (t or '').lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9 ]+', ' ', t).strip()


def clean(t):
    if isinstance(t, list):
        t = ', '.join(str(x) for x in t if x)
    t = html.unescape(str(t or ''))
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def d(s):
    if not s:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(s))
    return dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None


# --------------------------------------------------------------------------- categorías
MADRID_TIPO = {
    'CursosTalleres': ['talleres'], 'TeatroPerformance': ['teatro'], 'ConferenciasColoquios': ['conf'],
    'Musica': ['g:musica'], 'ExcursionesItinerariosVisitas': ['visitas'],
    'CineActividadesAudiovisuales': ['g:cine'], 'Exposiciones': ['g:arte'],
    'CuentacuentosTiteresMarionetas': ['cuentacuentos'], 'RecitalesPresentacionesActosLiterarios': ['libros'],
    'DanzaBaile': ['danza'], 'ClubesLectura': ['clubes'], 'ActividadesCalleArteUrbano': ['calle'],
    'ItinerariosOtrasActividadesAmbientales': ['itinerarios'], 'ActividadesDeportivas': ['deporte_municipal'],
    'Historia': ['historia'], 'Fiestas': ['fiestas'], 'CircoMagia': ['circo'], 'JazzSoulFunkySwingReagge': ['jazz'],
    'Flamenco': ['flamenco'], 'CoroGospel': ['coros'], 'Idiomas': ['idiomas'], 'ComemoracionesHomenajes': ['homenajes'],
    'ConcursosCertamenes': ['concursos'], 'CarrerasMaratones': ['carreras'], 'FolcloreEtnica': ['mundo'],
    'CantautorFolkCountry': ['cantautor'], 'Literatura': ['libros'], 'Arte': ['g:arte'],
    'CapacitacionDigital': ['digital'], 'Campamentos': ['infantil'], 'Ciclismo': ['bici'],
    'LatinaEspanola': ['latina'], 'CineFiccion': ['g:cine'], 'Clasica': ['clasica'], 'Skateboarding': ['skate'],
    'Ferias': ['ferias'], 'Zarzuela': ['zarzuela'], 'RockPop': ['rock', 'pop'], 'EnLinea': ['online'],
    'ComediaMonologo': ['monologos'], 'Golf': ['golf'], 'LGTBI': ['lgtbi'], 'ActosSolidarios': ['solidarios'],
}
# términos del campo "Tipo" de las fichas de esMadrid
ESM_TIPO = {
    'teatro y danza': ['g:escena'], 'teatro': ['teatro'], 'danza': ['danza'], 'flamenco': ['flamenco'],
    'musicales': ['musicales'], 'musical': ['musicales'], 'opera': ['opera'], 'zarzuela': ['zarzuela'],
    'ballet': ['ballet'], 'circo': ['circo'], 'magia': ['circo'], 'humor': ['monologos'], 'comedia': ['monologos'],
    'microteatro': ['microteatro'], 'performance': ['performance'],
    'musica': ['g:musica'], 'conciertos': ['g:musica'], 'clasica': ['clasica'], 'musica clasica': ['clasica'],
    'jazz': ['jazz'], 'pop rock': ['rock', 'pop'], 'rock': ['rock'], 'pop': ['pop'], 'indie': ['indie'],
    'electronica': ['electronica'], 'hip hop': ['hiphop'], 'rap': ['hiphop'], 'latina': ['latina'],
    'musica antigua': ['antigua'], 'world music': ['mundo'], 'festivales': ['festivales_musica'],
    'exposiciones': ['g:arte'], 'arte': ['g:arte'], 'pintura': ['pintura'], 'fotografia': ['fotografia'],
    'escultura': ['escultura'], 'arte contemporaneo': ['contemporaneo'], 'arquitectura': ['diseno'],
    'diseno': ['diseno'], 'ilustracion': ['ilustracion'], 'comic': ['ilustracion'],
    'cine': ['g:cine'], 'festivales de cine': ['festivales_cine'],
    'deportes': ['g:deporte'], 'deporte': ['g:deporte'], 'futbol': ['futbol'], 'baloncesto': ['baloncesto'],
    'tenis': ['tenis'], 'carreras populares': ['carreras'], 'atletismo': ['carreras'], 'hipica': ['hipica'],
    'motor': ['motor'], 'golf': ['golf'], 'ciclismo': ['bici'], 'padel': ['padel'],
    'ferias': ['ferias'], 'ferias y congresos': ['ferias'], 'mercadillos': ['mercadillos'], 'mercados': ['mercadillos'],
    'gastronomia': ['g:gastro'], 'fiestas': ['fiestas'], 'fiestas populares': ['fiestas'], 'tradiciones': ['tradiciones'],
    'navidad': ['fiestas'], 'infantil': ['infantil'], 'ninos': ['infantil'], 'familia': ['talleres_familia'],
    'conferencias': ['conf'], 'literatura': ['libros'], 'visitas guiadas': ['visitas'], 'rutas': ['rutas'],
    'lgtbi': ['lgtbi'], 'naturaleza': ['g:naturaleza'], 'moda': ['moda'], 'videojuegos': ['videojuegos'],
    'drama': ['teatro'], 'tragicomedia': ['teatro'], 'jazz blues': ['jazz'], 'danza moderna': ['danza'],
    'danza clasica': ['ballet'], 'inmersivo': ['g:arte'], 'antiguedades y artesania': ['artesania', 'coleccionismo'],
    'dibujo': ['ilustracion'], 'instalaciones': ['contemporaneo'], 'arqueologia': ['arqueo_cat', 'expo_hist'],
    'historia': ['historia'], 'folk': ['cantautor'], 'cantautor': ['cantautor'], 'funk': ['jazz'],
    'reggae': ['mundo'], 'ciencias': ['divulgacion'], 'experimental': ['contemporaneo'], 'video': ['contemporaneo'],
    'grabado': ['pintura'], 'artes decorativas': ['diseno'], 'moda y complementos': ['moda'], 'talleres': ['talleres'],
    'alimentacion': ['g:gastro'], 'waterpolo': ['otros_partidos'], 'automocion': ['salones_motor'],
    'melodica': ['pop'], 'compras': ['mercadillos'],
}
# palabras clave en título/descripción → categoría fina
KW = [
    (r'\b(perro|perros|canin|mascota|perroton)', ['quedadas_perro']),
    (r'\bperroton\b|carrera.{0,40}(perro|canin)', ['carreras_perro', 'solidarias']),
    (r'adopci[o]n.{0,30}(animal|perro|gato|mascota)|protectora', ['adopcion']),
    (r'\b(10 ?k|5 ?k|carrera popular|carrera solidaria|san silvestre|run\b|running)', ['carreras']),
    (r'media marat|maraton', ['maratones']),
    (r'\btrail\b', ['trail']),
    (r'solidari|benefic', ['solidarios']),
    (r'\bpintur|pintor|oleo|acuarela|retrato', ['pintura']),
    (r'fotograf', ['fotografia']),
    (r'escultur', ['escultura']),
    (r'contemporane', ['contemporaneo']),
    (r'ilustraci|comic|tebeo|manga', ['ilustracion']),
    (r'arquitectur|diseno', ['diseno']),
    (r'arqueolog|prehistori|antiguo egipto|romano|romana|medieval|faraon|neolitic|bronce', ['arqueo_cat', 'expo_hist']),
    (r'historia', ['historia']),
    (r'\bciencias?\b|cientific|csic|\bfisica|\bquimica|biolog|astronom', ['divulgacion']),
    (r'astronom|estrellas|telescop|eclipse', ['astronomia']),
    (r'inteligencia artificial|\bia\b|tecnolog|robot', ['tecnologia']),
    (r'botanic|jardin|plantas|arbol', ['jardines']),
    (r'\baves\b|ornitolog|pajaro', ['aves']),
    (r'senderismo|ruta a pie|caminata', ['senderismo']),
    (r'documental', ['documental']),
    (r'cortometraje|\bcortos\b', ['cortos']),
    (r'animaci[o]n', ['animacion']),
    (r'ciclo de cine|clasico del cine|cine clasico|filmoteca|cineteca|restaurad|hitchcock|kubrick|bergman|fellini|cine mudo', ['cine_clasico']),
    (r'\bv\.?o\.?s\.?e?\b|version original', ['vose']),
    (r'preestreno|coloquio con', ['preestrenos']),
    (r'festival de cine|semana de cine|muestra de cine', ['festivales_cine']),
    (r'presentaci[o]n del libro|presenta su (libro|novela)|presentaci[o]n de la novela', ['libros']),
    (r'firma de libros|firmara', ['firmas']),
    (r'poesia|poetic|poema|recital', ['poesia']),
    (r'feria del libro', ['ferias_libro']),
    (r'debate|mesa redonda', ['debates']),
    (r'opera\b', ['opera']), (r'(?<!hipodromo de la )zarzuela', ['zarzuela']), (r'ballet', ['ballet']),
    (r'musical\b', ['musicales']), (r'microteatro', ['microteatro']),
    (r'monologo|stand ?up|comedia', ['monologos']), (r'impro', ['improv']), (r'circo|magia|mago', ['circo']),
    (r'orquesta|sinfonic|filarmon|cuarteto|piano|violin|camara|barroc|bach|mozart|beethoven', ['clasica']),
    (r'barroc|renacent|musica antigua', ['antigua']),
    (r'banda sinfonica', ['bandas']),
    (r'\bjazz|swing|soul|funk|blues', ['jazz']),
    (r'flamenc|cante|bailaor', ['flamenco']),
    (r'\brock\b', ['rock']), (r'\bindie\b', ['indie']), (r'\bmetal\b|punk', ['metal']),
    (r'electronic|\bdj\b|techno', ['electronica']), (r'hip ?hop|\brap\b|trap', ['hiphop']),
    (r'reggaeton|regueton', ['reggaeton']), (r'cantautor|folk', ['cantautor']),
    (r'coro|gospel|coral', ['coros']), (r'festival', []),
    (r'\bvino|cata|enolog|bodega', ['catas']), (r'cerveza', ['cerveza']), (r'\bcafe\b', ['cafe']),
    (r'gastronom|tapa|cocina|chef|culinar|comida|food', ['g:gastro']),
    (r'ruta de la tapa|ruta de tapas|tapeo', ['tapas']), (r'curso de cocina|taller de cocina', ['cocina']),
    (r'mercado gastronom|mercado de productores|mercado agroecolog', ['mercados_gastro']),
    (r'food ?truck', ['foodtrucks']),
    (r'mercadillo|rastro|mercado de artesan|mercado navide', ['mercadillos']),
    (r'artesan', ['artesania']),
    (r'trashumancia|romeria|verbena|tradiciones populares', ['tradiciones']),
    (r'cabalgata|desfile', ['desfiles']), (r'procesi[o]n', ['religiosas']),
    (r'fiestas? de|fiestas del barrio|san isidro|la paloma|navidad', ['fiestas']),
    (r'halloween|dia de muertos|todos los santos', ['fiestas']),
    (r'cuentacuentos|cuento', ['cuentacuentos']), (r'titere|marioneta', ['titeres']),
    (r'en familia|para familias|familiar', ['talleres_familia']),
    (r'infantil|ninos|ninas|peques|bebes', ['infantil']),
    (r'adolescente|juvenil|jovenes', ['jovenes']),
    (r'mayores|tercera edad', ['mayores']),
    (r'voluntari', ['voluntariado']),
    (r'idioma|intercambio de|english|ingles', ['idiomas']),
    (r'escritura creativa|taller de escritura', ['escritura']),
    (r'manualidad|ganchillo|costura|bordado|ceramica|diy', ['manualidades']),
    (r'yoga|meditaci|mindfulness|pilates|bienestar', ['bienestar']),
    (r'\bfutbol|liga|champions|real madrid|atletico de madrid|rayo vallecano|getafe', ['futbol']),
    (r'baloncesto|euroliga|acb', ['baloncesto']), (r'\btenis\b', ['tenis']), (r'padel', ['padel']),
    (r'hipodromo|carreras de caballos|hipica', ['hipica']), (r'motor|automovil|coches', ['motor']),
    (r'ciclis|bicicleta|\bbici', ['bici']), (r'triatl', ['triatlon']), (r'esports|gaming', ['esports']),
    (r'videojuego', ['videojuegos']), (r'coleccionis', ['coleccionismo']), (r'moda|pasarela', ['moda']),
    (r'salon (del|de la)|feria (del|de la)', ['ferias_consumo']),
    (r'congreso|jornadas', ['congresos']),
    (r'lgtb|orgullo', ['lgtbi']),
    (r'visita guiada|visitas guiadas|visita comentada', ['visitas']),
    (r'ruta por|paseo por|itinerario', ['rutas']),
    (r'museo', ['museos']),
    (r'monumento|palacio|puertas abiertas|patrimonio', ['monumentos']),
    (r'galeria', ['galerias']), (r'feria de arte|arco\b|estampa', ['ferias_arte']),
    (r'grafiti|graffiti|arte urbano|mural', ['urbano']),
    (r'accesib|lengua de signos|audiodescri', ['accesibles']),
]
KW = [(re.compile(p), c) for p, c in KW]


# categorías cuyas palabras clave son ambiguas en una descripción larga: solo se miran en el título
SOLO_TITULO = {'historia', 'fiestas', 'museos', 'rutas', 'cine_clasico', 'monologos', 'coros', 'clasica', 'catas',
               'cafe', 'infantil', 'jovenes', 'mayores', 'tradiciones', 'talleres_familia', 'congresos', 'monumentos',
               'visitas', 'bienestar', 'idiomas', 'jardines', 'motor', 'moda', 'coleccionismo', 'g:gastro',
               'tecnologia', 'solidarios', 'carreras', 'futbol', 'baloncesto', 'ferias_consumo', 'galerias',
               'debates', 'zarzuela', 'poesia', 'cuentacuentos', 'manualidades', 'accesibles', 'diseno', 'rock', 'antigua'}


ARTE_FINAS = {'pintura', 'fotografia', 'escultura', 'ilustracion', 'contemporaneo', 'diseno', 'urbano', 'astronomia'}


def categorias(base, titulo, desc='', publico=''):
    cats = list(base)
    tt, td = norm(titulo), norm(desc[:500])
    es_arte = any(c == 'g:arte' or ITEM_GROUP.get(c) in ('arte', 'patrimonio') for c in base)
    for rx, cs in KW:
        solo_titulo = set(cs) <= SOLO_TITULO or (set(cs) <= ARTE_FINAS and not es_arte)
        if rx.search(tt) or (td and cs and not solo_titulo and rx.search(td)):
            cats += cs
    p = norm(publico)
    if 'ninos' in p:
        cats.append('infantil')
    if 'familias' in p:
        cats.append('talleres_familia')
    if 'mayores' in p:
        cats.append('mayores')
    # exposiciones: si hay categoría fina de arte, quita la genérica
    out = []
    for c in cats:
        if c not in out:
            out.append(c)
    finas = {ITEM_GROUP.get(c) for c in out if not c.startswith('g:')}
    out = [c for c in out if not (c.startswith('g:') and c[2:] in finas)]
    # "expo de historia" solo tiene sentido en exposiciones
    if 'expo_hist' in out and not any(c in ('g:arte',) or ITEM_GROUP.get(c) == 'arte' for c in base):
        out.remove('expo_hist')
    return out


# --------------------------------------------------------------------------- lectores
def madrid():
    evs = []
    for fn in ('300107-0-agenda-actividades-eventos.json', '206974-0-agenda-eventos-culturales-100.json'):
        for e in json.load(open(os.path.join(RAW, fn)))['@graph']:
            tipo = e.get('@type', '').split('/')[-1]
            base = MADRID_TIPO.get(tipo, [])
            org = e.get('organization') or {}
            loc = e.get('location') or {}
            dist = ((e.get('address') or {}).get('district') or {}).get('@id', '').split('/')[-1]
            desc = clean(e.get('description'))
            evs.append({
                'fuente': 'Agenda municipal de Madrid', 'fuente_id': 'madrid:' + e['id'],
                'titulo': clean(e['title']), 'descripcion': desc,
                'inicio': d(e['dtstart']), 'fin': d(e['dtend']) or d(e['dtstart']),
                'hora': (e.get('time') or '').strip() or None,
                'lugar': clean(e.get('event-location')) or org.get('organization-name'),
                'distrito': re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', dist) or None,
                'lat': loc.get('latitude'), 'lng': loc.get('longitude'),
                'precio': clean(e.get('price')) or None, 'gratis': e.get('free') == 1,
                'url': e.get('link'), 'publico': e.get('audience') or '',
                'cats': categorias(base, e['title'], desc, e.get('audience') or ''),
                'destacado': tipo in ('ProgramacionDestacadaAgendaCultura', '1ciudad21distritos'),
                'tipo_origen': tipo,
            })
    return evs


def ld_events(blocks):
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            ty = o.get('@type')
            tys = ty if isinstance(ty, list) else [ty]
            if any(isinstance(x, str) and (x.endswith('Event') or x == 'Festival') for x in tys):
                out.append(o)
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
    return out


def esmadrid():
    evs = []
    p = os.path.join(RAW, 'esmadrid.jsonl')
    if not os.path.exists(p):
        return evs
    for l in open(p):
        r = json.loads(l)
        c = r.get('campos') or {}
        for e in ld_events(r['ld']):
            loc = e.get('location') or {}
            if isinstance(loc, list):
                loc = loc[0] if loc and isinstance(loc[0], dict) else {}
            tipos = [norm(x) for x in (c.get('tipo') or '').split(',') if x.strip()]
            base = []
            for t in tipos:
                base += ESM_TIPO.get(t, [])
            desc = clean(e.get('description'))
            precio = c.get('precio')
            if precio:
                precio = re.split(r'\s*Horarios', precio)[0].strip() or None
            gratis = bool(precio and re.search(r'gratu|gratis|libre', norm(precio)))
            hora = c.get('horario')
            evs.append({
                'fuente': 'esMadrid', 'fuente_id': 'esm:' + r['url'].rsplit('/', 1)[-1],
                'titulo': clean(e.get('name')), 'descripcion': desc,
                'inicio': d(e.get('startDate')), 'fin': d(e.get('endDate')) or d(e.get('startDate')),
                'hora': hora[:90] if hora else None,
                'lugar': clean(loc.get('name')) if isinstance(loc, dict) else None, 'distrito': None,
                'lat': None, 'lng': None, 'precio': precio, 'gratis': gratis,
                'url': r['url'], 'web_organizador': c.get('web'), 'imagen': c.get('imagen'),
                'publico': '', 'cats': categorias(base, e.get('name') or '', desc),
                'destacado': False, 'tipo_origen': c.get('tipo'), 'evento_ciudad': 'eventos de ciudad' in tipos,
            })
    return evs


def recinto(fn, nombre, base_cats):
    evs = []
    p = os.path.join(RAW, fn)
    if not os.path.exists(p):
        return evs
    for e in json.load(open(p)):
        evs.append({
            'fuente': nombre, 'fuente_id': nombre + ':' + (e.get('url') or e['titulo']),
            'titulo': clean(e['titulo']).replace('Exposición: ', ''), 'descripcion': '',
            'inicio': d(e['inicio']), 'fin': d(e.get('fin')) or d(e['inicio']), 'hora': None,
            'lugar': {'IFEMA': 'IFEMA Madrid', 'RJB': 'Real Jardín Botánico'}.get(nombre, nombre),
            'distrito': None, 'lat': None, 'lng': None, 'precio': None, 'gratis': False,
            'url': e.get('url'), 'publico': '',
            'cats': categorias(base_cats, e['titulo']), 'destacado': False, 'tipo_origen': None,
        })
    return evs


# --------------------------------------------------------------------------- unión de duplicados
def clave_titulo(t):
    t = norm(t)
    t = re.sub(r'\b(exposicion|concierto|conferencia|taller|visita|ciclo|presentacion|el|la|los|las|de|del|y|en)\b', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def solapan(a, b):
    return a['inicio'] <= b['fin'] and b['inicio'] <= a['fin']


GENERICOS = {'visitas guiadas', 'visita guiada', 'talleres', 'concierto', 'cuentacuentos', 'cine', 'teatro',
             'exposiciones', 'actividades', 'programacion', 'mercado productores'}


def mismo_lugar(a, b):
    la = set(norm(a.get('lugar') or '').split()) - {'de', 'del', 'la', 'el', 'centro', 'cultural', 'madrid', 'sala'}
    lb = set(norm(b.get('lugar') or '').split()) - {'de', 'del', 'la', 'el', 'centro', 'cultural', 'madrid', 'sala'}
    return bool(la & lb)


def parecidos(a, b):
    ka, kb = clave_titulo(a['titulo']), clave_titulo(b['titulo'])
    if not ka or not kb:
        return False
    if (ka in GENERICOS or kb in GENERICOS or min(len(ka), len(kb)) < 8) and not mismo_lugar(a, b):
        return False
    if ka == kb:
        return True
    if min(len(ka), len(kb)) >= 12 and (ka in kb or kb in ka):
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.86


def unir(evs):
    # 1) sesiones del mismo evento en la misma fuente: mismo título + mismo lugar
    grupos = collections.OrderedDict()
    for e in evs:
        k = (e['fuente'], clave_titulo(e['titulo']), norm(e['lugar'] or ''))
        grupos.setdefault(k, []).append(e)
    base = []
    for g in grupos.values():
        g.sort(key=lambda x: x['inicio'])
        e = dict(g[0])
        e['sesiones'] = sorted({x['inicio'].isoformat() for x in g})
        e['inicio'] = min(x['inicio'] for x in g)
        e['fin'] = max(x['fin'] for x in g)
        e['cats'] = list(dict.fromkeys(c for x in g for c in x['cats']))
        e['destacado'] = any(x['destacado'] for x in g)
        e['fuentes'] = [{'nombre': e['fuente'], 'url': e['url']}]
        base.append(e)
    # 2) el mismo evento en distintas fuentes
    out = []
    unidos = 0
    for e in base:
        dup = None
        for o in out:
            if o['fuente'] != e['fuente'] and e['fuente'] not in [f['nombre'] for f in o['fuentes']] \
                    and solapan(o, e) and parecidos(o, e):
                dup = o
                break
        if dup:
            unidos += 1
            dup['fuentes'].append({'nombre': e['fuente'], 'url': e['url']})
            dup['cats'] = list(dict.fromkeys(dup['cats'] + e['cats']))
            for k in ('imagen', 'web_organizador', 'lat', 'lng', 'distrito', 'precio', 'hora'):
                if not dup.get(k) and e.get(k):
                    dup[k] = e[k]
            if len(e.get('descripcion') or '') > len(dup.get('descripcion') or ''):
                dup['descripcion'] = e['descripcion']
            dup['sesiones'] = sorted(set(dup['sesiones']) | set(e['sesiones']))
            dup['destacado'] = dup['destacado'] or e['destacado']
            dup['evento_ciudad'] = dup.get('evento_ciudad') or e.get('evento_ciudad')
        else:
            out.append(e)
    return out, unidos


# --------------------------------------------------------------------------- planes únicos
UNICO_RX = re.compile(r'trashumancia|perroton|san silvestre|cabalgata|fiestas del orgullo|madrid orgullo|'
                      r'desfile.{0,20}hispanidad|noche de los libros|noche en blanco|semana de la ciencia|'
                      r'carrera de la ciencia|mercado medieval|open house|maraton de madrid|media maraton|jazzmadrid|suma flamenca|'
                      r'festival de otono|feria del libro|mercado de navidad|belen de|dia de muertos')
NO_UNICO = re.compile(r'^(taller|inscripciones|alertante|curso|visita)|mercado (municipal )?de productores|sabados de el rastro|dia de mercado|mercado de las ranas|semana de la ciencia .|. semana de la ciencia')


def es_unico(e):
    """Planes únicos: citas de ciudad que se repiten como mucho una vez al año.
    Se marcan por nombre (lista que crece con la auditoría) o por ser de esMadrid y de tipo tradición/fiesta/carrera."""
    dur = (e['fin'] - e['inicio']).days
    if dur > 45:
        return False
    if NO_UNICO.search(e['titulo'].lower()) or NO_UNICO.search(norm(e['titulo'])):
        return False
    if UNICO_RX.search(norm(e['titulo'])):
        return True
    if e.get('evento_ciudad') and dur <= 10:
        return True
    de_turismo = any(f['nombre'] == 'esMadrid' for f in e['fuentes'])
    if de_turismo and dur <= 4 and any(c in ('tradiciones', 'desfiles', 'fiestas', 'carreras', 'maratones',
                                             'carreras_perro', 'festivales_musica') for c in e['cats']):
        return True
    return False


def por_lugar(e):
    """Último recurso: deducir el grupo por el recinto o por pistas de la descripción."""
    l, t = norm(e.get('lugar') or ''), norm(e.get('descripcion') or '')
    if re.search(r'cineteca|filmoteca|cine ', l) or re.search(r'dirigida por|director|\d+ ?min|\d{4} \d+', t):
        return ['g:cine']
    if re.search(r'teatro|sala', l) or re.search(r'compania|dramaturg|escena', t):
        return ['g:escena']
    if re.search(r'auditorio|musica', l):
        return ['g:musica']
    if re.search(r'museo|centrocentro|matadero|conde duque', l):
        return ['g:arte']
    return ['sin_categoria']


# --------------------------------------------------------------------------- main
def main():
    fuentes = {
        'Agenda municipal de Madrid': madrid(),
        'esMadrid': esmadrid(),
        'IFEMA': recinto('ifema.json', 'IFEMA', ['ferias_consumo']),
        'RJB': recinto('rjb.json', 'RJB', ['jardines']),
    }
    todos = [e for v in fuentes.values() for e in v]
    leidos = {k: len(v) for k, v in fuentes.items()}
    vivos = [e for e in todos if e['inicio'] and e['fin'] and e['fin'] >= HOY and e['inicio'] <= HASTA]
    eventos, unidos = unir(vivos)
    for i, e in enumerate(sorted(eventos, key=lambda x: (x['inicio'], x['titulo']))):
        e['id'] = 'e%04d' % i
        e['unico'] = es_unico(e)
        if not e['cats']:
            e['cats'] = por_lugar(e)
        e['descripcion'] = (e.get('descripcion') or '')[:420]
        if e['fin'] < e['inicio']:
            e['fin'] = e['inicio']
        e['inicio'] = e['inicio'].isoformat()
        e['fin'] = e['fin'].isoformat()
        e['sesiones'] = e['sesiones'][:40]
        for k in ('fuente', 'fuente_id', 'url', 'publico', 'tipo_origen', 'evento_ciudad'):
            e.pop(k, None)
    eventos.sort(key=lambda x: x['id'])
    os.makedirs(OUT, exist_ok=True)
    meta = {
        'generado': dt.datetime.now().isoformat(timespec='minutes'), 'ventana': [HOY.isoformat(), HASTA.isoformat()],
        'registros_leidos': leidos, 'en_ventana': len(vivos), 'eventos': len(eventos),
        'duplicados_entre_fuentes': unidos,
        'multifuente': sum(1 for e in eventos if len(e['fuentes']) > 1),
        'sin_categoria': sum(1 for e in eventos if e['cats'] == ['sin_categoria']),
        'unicos': sum(1 for e in eventos if e['unico']),
    }
    json.dump({'meta': meta, 'eventos': eventos}, open(os.path.join(OUT, 'events.json'), 'w'), ensure_ascii=False)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
