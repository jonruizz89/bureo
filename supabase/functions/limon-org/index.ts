// Limón · revisión de eventos enviados por organizadores.
// Sitúa la dirección en el mapa, la IA comprueba que es un evento real en la ciudad del evento y le pone categorías.
// Si el organizador está aprobado y la IA no ve problemas, se publica; si no, queda para revisión del admin.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';

const URL = Deno.env.get('SUPABASE_URL')!;
const SECRET = (() => {
  try { return JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}').default; } catch { return undefined; }
})() || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const AKEY = Deno.env.get('ANTHROPIC_API_KEY');
const MODEL = Deno.env.get('LIMON_AI_MODEL') || 'claude-haiku-5-5';
const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
type City = { id: string; name: string; lat: number; lng: number; districts: string[] };
const fold = (s: string) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z ]/g, ' ').trim();

async function geocode(q: string, city: City) {
  const pick = (d: string) => (city.districts || []).find((x) => fold(x) === fold(d) || fold(d).startsWith(fold(x).split(' ')[0])) || null;
  const same = (c?: string) => !!c && fold(c) === fold(city.name);
  try {  // Photon (OpenStreetMap)
    const r = await fetch('https://photon.komoot.io/api/?' + new URLSearchParams({ q: q + ' ' + city.name, lat: String(city.lat), lon: String(city.lng), limit: '5' }));
    if (r.ok) {
      const f = ((await r.json()).features || []).find((x: { properties: { city?: string } }) => same(x.properties.city));
      if (f) return { lat: f.geometry.coordinates[1], lng: f.geometry.coordinates[0], district: pick(f.properties.district || '') };
    }
  } catch (_e) { /* siguiente servicio */ }
  try {  // Nominatim (OpenStreetMap)
    const r = await fetch('https://nominatim.openstreetmap.org/search?' + new URLSearchParams({
      q: q + ', ' + city.name, format: 'jsonv2', limit: '5', addressdetails: '1', countrycodes: 'es' }),
      { headers: { 'User-Agent': 'BureoBot/0.1 (+https://github.com/jonruizz89/bureo)' } });
    if (r.ok) {
      const x = (await r.json()).find((y: { address?: { city?: string; town?: string } }) => same((y.address || {}).city || (y.address || {}).town));
      if (x) return { lat: Number(x.lat), lng: Number(x.lon), district: pick(x.address.city_district || x.address.suburb || '') };
    }
  } catch (_e) { /* sin ubicación */ }
  return null;
}

const TOOL = {
  name: 'revisar_evento',
  description: 'Devuelve la revisión del evento.',
  input_schema: {
    type: 'object', required: ['ok', 'motivo', 'categorias'],
    properties: {
      ok: { type: 'boolean', description: 'true si es un evento presencial y público en el municipio indicado, con datos coherentes y sin contenido ofensivo, engañoso o publicitario sin evento' },
      motivo: { type: 'string', description: 'Si ok es false, qué falla, en una frase para el organizador. Si es true, vacío.' },
      categorias: { type: 'array', items: { type: 'string' }, description: 'De 1 a 3 ids de la taxonomía' },
    },
  },
};

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    const { data: u } = token ? await admin.auth.getUser(token) : { data: { user: null } };
    if (!u || !u.user) return json({ error: 'Entra con tu cuenta.' }, 401);
    const body = await req.json().catch(() => ({}));
    const { data: me } = await admin.from('profiles').select('role,editor_status').eq('id', u.user.id).maybeSingle();
    const { data: s } = await admin.from('submitted_events').select('*').eq('id', Number(body.id)).maybeSingle();
    if (!s) return json({ error: 'No existe ese evento.' }, 404);
    if (s.owner !== u.user.id && !(me && me.role === 'admin')) return json({ error: 'No es tuyo.' }, 403);

    // 1) dónde
    const { data: cityRow } = await admin.from('cities').select('id,name,lat,lng,districts').eq('id', s.city_id || 'madrid').maybeSingle();
    const city: City = cityRow || { id: 'madrid', name: 'Madrid', lat: 40.4168, lng: -3.7038, districts: [] };
    let geo = null;
    for (const q of [s.address, s.venue_name && s.address ? s.venue_name + ', ' + s.address : null, s.venue_name]) {
      if (!geo && q) geo = await geocode(q, city);
    }

    // 2) qué es (IA)
    let review = { ok: true, motivo: '', categorias: [] as string[] };
    if (AKEY) {
      const { data: cats } = await admin.from('categories').select('id,label').eq('is_generic', false);
      const ids = new Set((cats || []).map((c: { id: string }) => c.id));
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'x-api-key': AKEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
        body: JSON.stringify({
          model: MODEL, max_tokens: 3000,
          system: [{ type: 'text', cache_control: { type: 'ephemeral' }, text: 'Revisas eventos que los organizadores publican en Limón, una agenda de planes por ciudades. ' +
            `Este evento es para ${city.name}. Acepta cualquier evento presencial abierto al público en el municipio de ${city.name}, aunque sea pequeño o de pago. ` +
            `Rechaza solo lo que no sea un evento (anuncios, ofertas, spam), lo que esté fuera de ${city.name}, lo ofensivo o lo incoherente. ` +
            'Asigna de 1 a 3 categorías de esta lista (id = nombre):\n' + (cats || []).map((c: { id: string; label: string }) => `${c.id} = ${c.label}`).join('\n') }],
          tools: [TOOL], tool_choice: { type: 'tool', name: 'revisar_evento' },
          messages: [{ role: 'user', content: JSON.stringify({ titulo: s.title, descripcion: s.description, desde: s.starts_on, hasta: s.ends_on,
            hora: s.time_text, lugar: s.venue_name, direccion: s.address, precio: s.is_free ? 'gratis' : s.price_text, web: s.url }) }],
        }),
      });
      const res = await r.json();
      const blk = (res.content || []).find((b: { type: string }) => b.type === 'tool_use');
      if (blk) review = { ok: !!blk.input.ok, motivo: String(blk.input.motivo || ''), categorias: (blk.input.categorias || []).filter((c: string) => ids.has(c)).slice(0, 3) };
      const us = res.usage || {};
      const usd = ((us.input_tokens || 0) + (us.cache_creation_input_tokens || 0) * 1.25 + (us.cache_read_input_tokens || 0) * 0.1) * 0.10 / 1e6 + (us.output_tokens || 0) * 0.50 / 1e6;
      if (usd > 0) await admin.rpc('ai_usage_add', { p_user: u.user.id, p_usd: usd });
    }
    const fueraDeCiudad = (s.address || s.venue_name) && !geo;
    const trusted = me && (me.role === 'admin' || (me.role === 'editor' && me.editor_status === 'aprobado'));
    const publish = review.ok && trusted && !fueraDeCiudad;
    await admin.from('submitted_events').update({
      lat: geo ? geo.lat : null, lng: geo ? geo.lng : null, district: geo ? geo.district : null,
      categories: review.categorias, status: publish ? 'revision' : (review.ok ? 'revision' : 'rechazado'),
      ai_review: { ...review, sin_ubicacion: !!fueraDeCiudad, at: new Date().toISOString() },
    }).eq('id', s.id);
    if (!review.ok && s.event_id) await admin.rpc('publish_submission', { p_id: s.id, p_publish: false });
    if (publish) {
      if (s.event_id) {  // al volver a publicar se rehacen fechas y categorías
        await admin.from('event_sessions').delete().eq('event_id', s.event_id);
        await admin.from('event_categories').delete().eq('event_id', s.event_id);
      }
      const { error } = await admin.rpc('publish_submission', { p_id: s.id, p_publish: true });
      if (error) throw error;
    }
    return json({
      estado: publish ? 'publicado' : review.ok ? 'revision' : 'rechazado',
      motivo: !review.ok ? review.motivo : fueraDeCiudad ? `No encuentro esa dirección en ${city.name}: revísala o la revisará el equipo.` : !trusted ? 'Lo revisará el equipo de Limón antes de publicarlo.' : '',
      categorias: review.categorias, distrito: geo && geo.district,
    });
  } catch (e) {
    console.error(e);
    return json({ error: 'Algo ha fallado en el servidor. Prueba otra vez en un rato.' }, 500);
  }
});
