// Limón · IA en el servidor: perfil en lenguaje natural y recomendación razonada.
// Solo para usuarios con sesión. Tope de llamadas por persona y día y de gasto diario total.
// Secretos: ANTHROPIC_API_KEY (lo añade el dueño en Supabase → Edge Functions → Secrets).
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';

const URL = Deno.env.get('SUPABASE_URL')!;
const SECRET = (() => {
  try { return JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}').default; } catch { return undefined; }
})() || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const AKEY = Deno.env.get('ANTHROPIC_API_KEY');
const MODEL = Deno.env.get('LIMON_AI_MODEL') || 'claude-haiku-5-5';
const PRICE_IN = 0.10 / 1e6, PRICE_OUT = 0.50 / 1e6;
const MAX_CALLS_USER_DAY = 30;
const MAX_USD_DAY = Number(Deno.env.get('LIMON_AI_MAX_USD_DAY') || '1');
const cityOf = (c: unknown) => (typeof c === 'string' && /^[a-z0-9-]{2,40}$/.test(c) ? c : 'madrid');

const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
const fold = (s: string) => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

async function claude(system: string, user: string, tool: Record<string, unknown>, maxTokens = 2000) {
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': AKEY!, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
    body: JSON.stringify({
      model: MODEL, max_tokens: maxTokens,
      system: [{ type: 'text', text: system, cache_control: { type: 'ephemeral' } }],
      tools: [tool], tool_choice: { type: 'tool', name: tool.name },
      messages: [{ role: 'user', content: user }],
    }),
  });
  const res = await r.json();
  if (!r.ok) throw new Error('IA ' + r.status + ': ' + JSON.stringify(res).slice(0, 200));
  const u = res.usage || {};
  const usd = ((u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) * 1.25 + (u.cache_read_input_tokens || 0) * 0.1) * PRICE_IN
    + (u.output_tokens || 0) * PRICE_OUT;
  const blk = (res.content || []).find((b: { type: string }) => b.type === 'tool_use');
  return { out: blk ? blk.input : null, usd };
}

async function guard(uid: string) {
  const day = new Date().toISOString().slice(0, 10);
  const [{ data: mine }, { data: all }] = await Promise.all([
    admin.from('ai_usage').select('calls').eq('day', day).eq('user_id', uid).maybeSingle(),
    admin.from('ai_usage').select('usd').eq('day', day),
  ]);
  if (mine && mine.calls >= MAX_CALLS_USER_DAY) return 'Has llegado al límite de consultas de hoy. Mañana más.';
  const total = (all || []).reduce((s: number, r: { usd: number }) => s + Number(r.usd), 0);
  if (total >= MAX_USD_DAY) return 'La IA está en pausa hasta mañana por el tope de gasto diario.';
  return null;
}

// ---------------------------------------------------------------- perfil en lenguaje natural
const TASTE_TOOL = {
  name: 'guardar_perfil',
  description: 'Guarda el perfil de gustos interpretado.',
  input_schema: {
    type: 'object', required: ['resumen', 'categorias', 'temas', 'evitar'],
    properties: {
      resumen: { type: 'string', description: 'Dos o tres frases en segunda persona: lo que has entendido de sus gustos, concreto.' },
      categorias: {
        type: 'array', description: 'Categorías de la taxonomía que encajan, con peso 1 (algo), 2 (bastante) o 3 (mucho).',
        items: { type: 'object', required: ['id', 'peso'], properties: { id: { type: 'string' }, peso: { type: 'integer', enum: [1, 2, 3] } } },
      },
      temas: {
        type: 'array', items: { type: 'string' },
        description: 'Intereses concretos tal como aparecerían en el título o la descripción de un evento: personas, obras, épocas, lugares, estilos ("Hitchcock", "Antiguo Egipto", "jazz manouche"). Máximo 15.',
      },
      evitar: { type: 'array', items: { type: 'string' }, description: 'Ids de categorías que no quiere ver.' },
      pregunta: { type: ['string', 'null'], description: 'Una pregunta corta para afinar, solo si algo importante queda ambiguo.' },
    },
  },
};

async function taste(uid: string, text: string) {
  const { data: cats } = await admin.from('categories').select('id,label,group_id').eq('is_generic', false);
  const ids = new Set((cats || []).map((c: { id: string }) => c.id));
  const list = (cats || []).map((c: { id: string; label: string; group_id: string }) => `${c.id} = ${c.label} (${c.group_id})`).join('\n');
  const system = 'Eres el asistente de Limón, una app que recomienda planes en tu ciudad. Conviertes lo que alguien cuenta de sus gustos ' +
    'en un perfil: categorías de esta taxonomía con peso, temas concretos que buscar en los eventos y categorías que evitar. ' +
    'No inventes gustos que no se deduzcan del texto. Usa solo ids de la lista. Si dice que algo no le gusta o no lo quiere ver ' +
    '(«nada de reguetón», «odio las discotecas»), pon esas categorías en evitar. En temas pon solo nombres concretos que aparecerían ' +
    'tal cual en un evento (personas, obras, épocas, civilizaciones, estilos); nunca frases genéricas como «conferencias de historia» ' +
    'o «planes con perro», que ya cubren las categorías.\n\nTaxonomía (id = nombre (grupo)):\n' + list;
  const { out, usd } = await claude(system, text.slice(0, 2000), TASTE_TOOL, 1500);
  if (!out) throw new Error('La IA no devolvió el perfil');
  const t = {
    resumen: String(out.resumen || '').slice(0, 600),
    categorias: (out.categorias || []).filter((c: { id: string }) => ids.has(c.id)).slice(0, 40),
    temas: (out.temas || []).map((x: string) => String(x).slice(0, 60)).slice(0, 15),
    evitar: (out.evitar || []).filter((x: string) => ids.has(x)).slice(0, 30),
    pregunta: out.pregunta ? String(out.pregunta).slice(0, 200) : null,
    at: new Date().toISOString(),
  };
  await admin.from('profiles').update({ taste_text: text.slice(0, 2000), taste: t }).eq('id', uid);
  await admin.from('reco_cache').delete().eq('user_id', uid);
  return { taste: t, usd };
}

// ---------------------------------------------------------------- recomendación
type Ev = { id: number; title: string; description: string | null; starts_on: string; ends_on: string; time_text: string | null;
  district: string | null; topics: string[] | null; series: string | null; is_unique: boolean; ai_unique: boolean; is_free: boolean;
  venues: { name: string } | null; event_sessions: { on_date: string }[]; event_categories: { category_id: string }[] };

const FAMILY = new Set(['infantil', 'cuentacuentos', 'titeres', 'talleres_familia']);

function windowOf(when: string): [string, string] {
  const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Europe/Madrid' }));
  now.setHours(0, 0, 0, 0);
  const add = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const ymd = (d: Date) => d.toISOString().slice(0, 10);
  const dow = (now.getDay() + 6) % 7;
  if (when === 'finde') { const sat = dow >= 5 ? (dow === 6 ? add(now, -1) : now) : add(now, 5 - dow); return [ymd(dow >= 5 ? now : sat), ymd(add(sat, 1))]; }
  if (when === 'mes') return [ymd(now), ymd(add(now, 30))];
  return [ymd(now), ymd(add(now, 6 - dow))];
}

const RECO_TOOL = {
  name: 'elegir_planes',
  description: 'Devuelve los planes elegidos con el motivo.',
  input_schema: {
    type: 'object', required: ['planes'],
    properties: {
      planes: {
        type: 'array', items: {
          type: 'object', required: ['id', 'por_que'],
          properties: {
            id: { type: 'integer' },
            por_que: { type: 'string', description: 'Una frase de menos de 110 caracteres, en segunda persona, que conecte el plan con algo concreto del perfil.' },
          },
        },
      },
    },
  },
};

async function recommend(uid: string, when: string, force: boolean, city = 'madrid') {
  const [from, to] = windowOf(when);
  const { data: prof } = await admin.from('profiles').select('taste,learned,with_kids,with_dog').eq('id', uid).maybeSingle();
  const [{ data: ints }, { data: dis }] = await Promise.all([
    admin.from('user_interests').select('category_id,value').eq('user_id', uid),
    admin.from('dismissed_events').select('event_id').eq('user_id', uid),
  ]);
  const taste = (prof && prof.taste) || null;
  const learned: Record<string, number> = (prof && prof.learned) || {};
  const key = (city === 'madrid' ? '' : city + ':') + when + ':' + from;
  const sig = JSON.stringify([taste && taste.at, ints, learned, (dis || []).length]);
  if (!force) {
    const { data: c } = await admin.from('reco_cache').select('items,created_at').eq('user_id', uid).eq('key', key).maybeSingle();
    if (c && c.items && c.items.sig === sig && Date.now() - new Date(c.created_at).getTime() < 12 * 3600e3) return { ...c.items, cached: true, usd: 0 };
  }
  const W: Record<string, number> = {}, avoid = new Set<string>(taste ? taste.evitar || [] : []);
  for (const c of (taste && taste.categorias) || []) W[c.id] = (W[c.id] || 0) + [0, 2, 3.5, 5][c.peso];
  for (const r of ints || []) { if (r.value === 1) W[r.category_id] = (W[r.category_id] || 0) + 3; if (r.value === 2) avoid.add(r.category_id); }
  const temas = ((taste && taste.temas) || []).map((t: string) => ({ t, re: new RegExp('(^|[^a-z0-9])' + fold(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '($|[^a-z0-9])') }));
  const gone = new Set((dis || []).map((r: { event_id: number }) => r.event_id));
  if (!Object.keys(W).length && !temas.length && !Object.values(learned).some((v) => v > 0)) return { planes: [], vacio: true, usd: 0 };

  const cols = 'id,title,description,starts_on,ends_on,time_text,district,topics,series,is_unique,ai_unique,is_free,venues(name),event_sessions(on_date),event_categories(category_id)';
  const evs: Ev[] = [];
  for (let off = 0; ; off += 1000) {
    const { data, error } = await admin.from('events').select(cols).eq('city_id', city).gte('ends_on', from).lte('starts_on', to).order('id').range(off, off + 999);
    if (error) throw error;
    evs.push(...(data as unknown as Ev[]));
    if (!data || data.length < 1000) break;
  }
  const scored: { e: Ev; s: number; why: string[] }[] = [];
  for (const e of evs) {
    if (gone.has(e.id)) continue;
    const sess = (e.event_sessions || []).map((x) => x.on_date);
    if (sess.length > 1 && !sess.some((d) => d >= from && d <= to)) continue;
    const cats = (e.event_categories || []).map((x) => x.category_id);
    if (cats.some((c) => avoid.has(c))) continue;
    if (!(prof && prof.with_kids) && cats.some((c) => FAMILY.has(c))) continue;
    let s = 0; const why: string[] = [];
    for (const c of cats) { if (W[c]) { s += W[c]; why.push(c); } s += (learned['cat:' + c] || 0) * 0.6; }
    for (const t of e.topics || []) s += learned['tema:' + t] || 0;
    if (e.series) s += learned['serie:' + e.series] || 0;
    const hay = fold([e.title, e.description || '', (e.topics || []).join(' '), e.series || ''].join(' '));
    for (const t of temas) if (t.re.test(hay)) { s += 4; why.push(t.t); }
    if (s <= 0) continue;
    if (e.is_unique || e.ai_unique) s += 1;
    scored.push({ e, s, why });
  }
  scored.sort((a, b) => b.s - a.s);
  const seen = new Set<string>(), cand: typeof scored = [];
  for (const x of scored) {
    const k = x.e.series || 't:' + fold(x.e.title).slice(0, 40);
    if (seen.has(k)) continue;
    seen.add(k); cand.push(x);
    if (cand.length >= 60) break;
  }
  if (!cand.length) return { planes: [], usd: 0 };
  const perfil = [taste && taste.resumen, taste && taste.temas && taste.temas.length ? 'Temas: ' + taste.temas.join(', ') : '',
    Object.keys(W).length ? 'Categorías que le gustan: ' + Object.keys(W).join(', ') : '',
    Object.entries(learned).filter(([, v]) => v >= 1).map(([k]) => k.replace(/^(cat|tema|serie):/, '')).slice(0, 20).join(', ')
      ? 'Ha guardado o ido a planes de: ' + Object.entries(learned).filter(([, v]) => v >= 1).map(([k]) => k.replace(/^(cat|tema|serie):/, '')).slice(0, 20).join(', ') : '',
    avoid.size ? 'Evita: ' + [...avoid].join(', ') : ''].filter(Boolean).join('\n');
  const lines = cand.map(({ e }) => {
    const sess = (e.event_sessions || []).map((x) => x.on_date).filter((d) => d >= from && d <= to).slice(0, 3);
    const when = sess.length ? sess.join(',') : (e.starts_on === e.ends_on ? e.starts_on : `${e.starts_on}→${e.ends_on}`);
    return `${e.id} | ${when}${e.time_text && e.time_text.length < 30 ? ' ' + e.time_text : ''} | ${e.title.slice(0, 110)} | ${e.venues ? e.venues.name.slice(0, 50) : ''} | ${(e.event_categories || []).map((c) => c.category_id).join(',')} | ${(e.topics || []).join(', ')}${e.description ? ' | ' + e.description.slice(0, 160).replace(/\s+/g, ' ') : ''}`;
  }).join('\n');
  const system = 'Eres el recomendador de Limón, una app de planes en la ciudad. Eliges entre 6 y 10 planes para una persona a partir de su perfil. ' +
    'Prioriza lo que conecta con un interés concreto suyo sobre lo genérico; varía tipos de plan y días; no repitas un mismo ciclo; ' +
    'descarta lo que choque con lo que evita. El motivo, de menos de 100 caracteres y sin repetir el título ni la fecha, debe citar ' +
    'el interés concreto (no "porque te gusta la cultura").';
  const user = `Perfil:\n${perfil}\n\nCandidatos (id | cuándo | título | lugar | categorías | temas | descripción):\n${lines}`;
  const { out, usd } = await claude(system, user, RECO_TOOL, 1500);
  const valid = new Set(cand.map((x) => x.e.id));
  const planes = ((out && out.planes) || []).filter((p: { id: number }) => valid.has(p.id)).slice(0, 10)
    .map((p: { id: number; por_que: string }) => ({ id: p.id, por_que: String(p.por_que || '').slice(0, 160) }));
  const items = { planes, sig, when, from, to, candidatos: cand.length };
  await admin.from('reco_cache').upsert({ user_id: uid, key, items, created_at: new Date().toISOString() });
  return { ...items, usd };
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    const { data: u } = token ? await admin.auth.getUser(token) : { data: { user: null } };
    if (!u || !u.user) return json({ error: 'Entra con tu cuenta para usar la IA.' }, 401);
    if (!AKEY) return json({ error: 'La IA aún no está configurada en el servidor.' }, 503);
    const uid = u.user.id;
    const body = await req.json().catch(() => ({}));
    const stop = await guard(uid);
    if (stop) return json({ error: stop }, 429);
    let res: { usd: number } & Record<string, unknown>;
    if (body.action === 'taste') {
      const text = String(body.text || '').trim();
      if (text.length < 8) return json({ error: 'Cuéntame un poco más.' }, 400);
      res = await taste(uid, text);
    } else if (body.action === 'recommend') {
      res = await recommend(uid, ['semana', 'finde', 'mes'].includes(body.when) ? body.when : 'semana', !!body.force, cityOf(body.city));
    } else return json({ error: 'Acción desconocida' }, 400);
    if (res.usd > 0) await admin.rpc('ai_usage_add', { p_user: uid, p_usd: res.usd });
    const { usd: _u, ...out } = res;
    return json(out);
  } catch (e) {
    console.error(e);
    return json({ error: 'Algo ha fallado en el servidor. Prueba otra vez en un rato.' }, 500);
  }
});
