// Limón · avisos push en el móvil.
//  - key:  clave pública VAPID para suscribirse (pública).
//  - test: manda un aviso de prueba a los dispositivos de quien lo pide (con sesión; como mucho 1 por minuto).
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';
import { vapid, pushTo } from '../_shared/push.ts';

const URL = Deno.env.get('SUPABASE_URL')!;
const KEYS: string[] = (() => {
  try { return Object.values(JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}')) as string[]; } catch { return []; }
})();
const SECRET = KEYS[0] || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const APP = Deno.env.get('LIMON_APP_URL') || 'https://limonplanes.com/';
const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
const lastTest = new Map<string, number>();

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const u = new globalThis.URL(req.url);
    const body = req.method === 'POST' ? await req.json().catch(() => ({})) : {};
    const action = body.action || u.searchParams.get('action');
    if (action === 'key') return json({ key: (await vapid(admin)).publicKey });
    if (action === 'test') {
      const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
      const { data } = token ? await admin.auth.getUser(token) : { data: { user: null } };
      const user = data && data.user;
      if (!user) return json({ error: 'Entra con tu cuenta.' }, 401);
      if (Date.now() - (lastTest.get(user.id) || 0) < 60e3) return json({ error: 'Espera un minuto antes de pedir otra prueba.' }, 429);
      lastTest.set(user.id, Date.now());
      const n = await pushTo(admin, user.id, { title: 'Limón', body: '¡Funciona! Así te llegarán los avisos de tus planes.', url: APP + '?tab=parati', tag: 'prueba' });
      return json({ sent: n });
    }
    return json({ error: 'Acción desconocida.' }, 400);
  } catch (e) {
    console.error(e);
    return json({ error: 'Algo ha fallado en el servidor.' }, 500);
  }
});
