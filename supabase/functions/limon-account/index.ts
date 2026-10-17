// Limón · baja de la cuenta: borra el usuario y, en cascada, todos sus datos personales.
// Los eventos que hubiera publicado como organizador dejan de estar vigentes (trigger submitted_retract).
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';

const URL = Deno.env.get('SUPABASE_URL')!;
const SECRET = (() => {
  try { return JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}').default; } catch { return undefined; }
})() || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    const { data: u } = token ? await admin.auth.getUser(token) : { data: { user: null } };
    if (!u || !u.user) return json({ error: 'Entra con tu cuenta.' }, 401);
    const body = await req.json().catch(() => ({}));
    if (body.action !== 'delete' || body.confirm !== 'BORRAR') return json({ error: 'Falta la confirmación.' }, 400);
    const { data: me } = await admin.from('profiles').select('role').eq('id', u.user.id).maybeSingle();
    if (me && me.role === 'admin') {
      const { count } = await admin.from('profiles').select('id', { count: 'exact', head: true }).eq('role', 'admin');
      if ((count || 0) <= 1) return json({ error: 'Eres el único administrador: nombra antes a otro para poder darte de baja.' }, 409);
    }
    // retirar sus eventos publicados antes de borrar (el borrado en cascada también lo hace, esto es por si acaso)
    await admin.from('submitted_events').update({ status: 'borrador' }).eq('owner', u.user.id).not('event_id', 'is', null);
    const { error } = await admin.auth.admin.deleteUser(u.user.id);
    if (error) throw error;
    return json({ ok: true });
  } catch (e) {
    console.error(e);
    return json({ error: 'No se ha podido borrar la cuenta. Prueba otra vez en un rato.' }, 500);
  }
});
