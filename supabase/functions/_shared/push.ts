// Limón · avisos push (Web Push con VAPID). Las claves se crean en el servidor la primera vez y se guardan en app_keys.
import webpush from 'npm:web-push@3.6.7';
import type { SupabaseClient } from 'npm:@supabase/supabase-js@2.45.4';

type Keys = { publicKey: string; privateKey: string };
let cached: Keys | null = null;

export async function vapid(admin: SupabaseClient): Promise<Keys> {
  if (cached) return cached;
  const { data } = await admin.from('app_keys').select('value').eq('name', 'vapid').maybeSingle();
  if (data && data.value && data.value.publicKey) { cached = data.value as Keys; }
  else {
    const k = webpush.generateVAPIDKeys();
    await admin.from('app_keys').upsert({ name: 'vapid', value: k }, { onConflict: 'name', ignoreDuplicates: true });
    const { data: again } = await admin.from('app_keys').select('value').eq('name', 'vapid').maybeSingle();
    cached = (again && again.value) as Keys || k;  // si dos peticiones la crean a la vez, gana la que se guardó
  }
  webpush.setVapidDetails('mailto:hola@limonplanes.com', cached.publicKey, cached.privateKey);
  return cached;
}

export type PushMsg = { title: string; body: string; url: string; tag?: string };

/** Envía el aviso a todos los dispositivos de la persona. Devuelve cuántos lo han recibido. Borra las suscripciones caducadas. */
export async function pushTo(admin: SupabaseClient, userId: string, msg: PushMsg): Promise<number> {
  const { data: subs } = await admin.from('push_subscriptions').select('id,endpoint,p256dh,auth,fails').eq('user_id', userId);
  if (!subs || !subs.length) return 0;
  await vapid(admin);
  const payload = JSON.stringify({ title: msg.title.slice(0, 120), body: msg.body.slice(0, 240), url: msg.url, tag: msg.tag || null });
  let ok = 0;
  for (const s of subs) {
    try {
      await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, payload, { TTL: 12 * 3600, urgency: 'normal' });
      await admin.from('push_subscriptions').update({ last_ok: new Date().toISOString(), fails: 0 }).eq('id', s.id);
      ok++;
    } catch (e) {
      const code = (e as { statusCode?: number }).statusCode;
      if (code === 404 || code === 410 || (s.fails || 0) >= 5) await admin.from('push_subscriptions').delete().eq('id', s.id);  // el navegador ya no la acepta
      else await admin.from('push_subscriptions').update({ fails: (s.fails || 0) + 1 }).eq('id', s.id);
      console.warn('push', code, String(e).slice(0, 160));
    }
  }
  return ok;
}
