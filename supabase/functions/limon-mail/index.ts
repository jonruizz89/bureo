// Limón · correos.
//  - flush:  envía lo que hay en la cola (decisiones sobre organizadores, quedadas, amigos, ideas y avisos para admins). Lo puede pedir cualquiera:
//            solo envía lo que ya está en cola.
//  - daily:  cortes de calle de hoy y mañana junto a tus planes guardados o en tus calles, y lluvia en tus planes al aire libre
//            (requiere la clave secreta).
//  - weekly: resumen de la semana según tus gustos, lo que sigues y tus amigos (requiere la clave secreta).
//  - unsub:  darse de baja de un tipo de aviso con el enlace del correo (también One-Click, RFC 8058).
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';
import { pushTo } from '../_shared/push.ts';

const URL = Deno.env.get('SUPABASE_URL')!;
const KEYS: string[] = (() => {
  try { return Object.values(JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}')) as string[]; } catch { return []; }
})();
const SECRET = KEYS[0] || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const RESEND = Deno.env.get('RESEND_API_KEY');
const APP = Deno.env.get('LIMON_APP_URL') || 'https://limonplanes.com/';
const FROM = 'Limón <hola@limonplanes.com>';
const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const fold = (s: string) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9ñ ]+/g, ' ').replace(/\s+/g, ' ').trim();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const KINDS = ['semanal', 'calle', 'lluvia', 'organizador', 'admin', 'quedadas', 'amigos', 'ideas'];

// ---------- fechas en Madrid
function madridToday() {
  const s = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  return new Date(s + 'T12:00:00Z');
}
const ymd = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (d: Date, n: number) => new Date(d.getTime() + n * 864e5);
const isoDow = (d: Date) => ((d.getUTCDay() + 6) % 7) + 1;
const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const human = (s: string) => { const d = new Date(s + 'T12:00:00Z'); return `${DIAS[d.getUTCDay()]} ${d.getUTCDate()} de ${MESES[d.getUTCMonth()]}`; };

// ---------- plantilla
type Item = { title: string; meta: string; url: string; note?: string };
function layout(o: { title: string; intro: string; items?: Item[]; sections?: { h: string; items: Item[] }[]; cta?: { label: string; url: string }; footer: string }) {
  const it = (x: Item) => `<tr><td style="padding:12px 0;border-top:1px solid #F1EBD3">
      <a href="${esc(x.url)}" style="color:#231D02;font-weight:700;font-size:16px;text-decoration:none">${esc(x.title)}</a>
      <div style="color:#6B6142;font-size:14px;margin-top:3px">${esc(x.meta)}</div>
      ${x.note ? `<div style="color:#7A5900;font-size:13px;margin-top:4px">${esc(x.note)}</div>` : ''}</td></tr>`;
  const secs = (o.sections || (o.items ? [{ h: '', items: o.items }] : [])).filter((s) => s.items.length);
  return `<!doctype html><html lang="es"><body style="margin:0;background:#FFF9E0">
<div style="background:#FFF9E0;padding:28px 12px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#231D02">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px 24px">
    <table role="presentation" style="border-collapse:collapse"><tr>
      <td><img src="https://limonplanes.com/limon-180.png" width="36" height="36" alt="" style="display:block"></td>
      <td style="padding-left:10px;font-size:18px;font-weight:700">Limón</td></tr></table>
    <h1 style="font-size:21px;line-height:1.3;margin:20px 0 8px">${esc(o.title)}</h1>
    <p style="font-size:15px;line-height:1.5;margin:0 0 8px;color:#3D3517">${o.intro}</p>
    ${secs.map((s) => `${s.h ? `<h2 style="font-size:15px;margin:22px 0 4px;color:#7A5900">${esc(s.h)}</h2>` : ''}
      <table role="presentation" style="border-collapse:collapse;width:100%">${s.items.map(it).join('')}</table>`).join('')}
    ${o.cta ? `<p style="margin:24px 0 0"><a href="${esc(o.cta.url)}" style="display:inline-block;background:#FFD43B;color:#231D02;text-decoration:none;font-weight:700;font-size:15px;padding:12px 24px;border-radius:999px">${esc(o.cta.label)}</a></p>` : ''}
  </div>
  <p style="max-width:520px;margin:14px auto 0;text-align:center;font-size:12px;line-height:1.5;color:#9A8F66">${o.footer}</p>
</div></body></html>`;
}
const textOf = (html: string) => html.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<a [^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g, '$2 ($1)')
  .replace(/<(br|\/p|\/tr|\/h1|\/h2|\/div)>/g, '\n').replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n\n').trim();

type Prof = { id: string; email: string; notify: Record<string, boolean> | null; mail_token: string; role?: string };
const unsubUrl = (p: Prof, k: string) => `${APP}?baja=${p.mail_token}&k=${k}`;
const oneClick = (p: Prof, k: string) => `${URL}/functions/v1/limon-mail?action=unsub&t=${p.mail_token}&k=${k}`;
const footer = (p: Prof, k: string, why: string) =>
  `${why} <a href="${esc(unsubUrl(p, k))}" style="color:#9A8F66">Dejar de recibir estos correos</a> · <a href="${esc(APP)}privacidad.html" style="color:#9A8F66">Privacidad</a>`;
const evUrl = (id: number | string) => `${APP}?ev=e${id}`;

async function send(p: Prof, subject: string, html: string, k: string) {
  if (!RESEND) throw new Error('Falta RESEND_API_KEY');
  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${RESEND}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: FROM, to: [p.email], subject, html, text: textOf(html),
      headers: { 'List-Unsubscribe': `<${oneClick(p, k)}>`, 'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click' },
    }),
  });
  await sleep(550);  // Resend: 2 envíos por segundo
  if (!r.ok) throw new Error(`Resend ${r.status}: ${(await r.text()).slice(0, 200)}`);
}
// resumen, cortes y lluvia solo si se activan; organizador, admin, quedadas, amigos e ideas salvo que se desactiven
const OPT_IN = ['semanal', 'calle', 'lluvia'];
const wants = (p: Prof, k: string) => !!p.email && (OPT_IN.includes(k) ? (p.notify || {})[k] === true : (p.notify || {})[k] !== false);
// avisos en el móvil: solo los tipos que la persona activa en «En este móvil» (notify.push)
const wantsPush = (p: Prof, k: string) => !!((p.notify || {}) as Record<string, unknown>).push && ((p.notify as unknown as { push: Record<string, boolean> }).push[k] === true);
// el aviso push sale del mismo correo: asunto, primera frase y botón
function pushOf(subject: string, html: string, tag: string) {
  const intro = (html.match(/color:#3D3517">([\s\S]*?)<\/p>/) || [])[1] || '';
  const url = ((html.match(/<p style="margin:24px 0 0"><a href="([^"]+)"/) || [])[1] || APP).replace(/&amp;/g, '&');
  return { title: subject, body: textOf(intro).replace(/\s+/g, ' ').slice(0, 200), url, tag };
}

// ---------- cola: organizadores, quedadas, amigos, ideas y admins
async function flush() {
  const { data: rows } = await admin.from('mail_outbox').select('*').is('sent_at', null).is('error', null).order('created_at').limit(50);
  let sent = 0, skipped = 0;
  for (const m of rows || []) {
    const { data: p } = await admin.from('profiles').select('id,email,notify,mail_token,role').eq('id', m.user_id).maybeSingle();
    const k = m.kind.startsWith('admin') ? 'admin' : m.kind === 'rsvp' ? 'quedadas' : m.kind.startsWith('follow') ? 'amigos' : m.kind === 'idea_status' ? 'ideas' : 'organizador';
    let mail: { subject: string; html: string } | null = null;
    const pl = m.payload || {};
    if (p && (wants(p, k) || wantsPush(p, k))) {
      if (m.kind === 'org_editor') {
        mail = pl.status === 'aprobado'
          ? { subject: 'Ya puedes publicar tus eventos en Limón', html: layout({ title: '¡Bienvenido a Limón como organizador!', intro: 'Hemos aprobado tu solicitud. Desde <b>Perfil → Mis eventos</b> puedes crear tus eventos: la IA los revisa, los sitúa en el mapa y salen en Limón con enlace a tu web.', cta: { label: 'Crear mi primer evento', url: APP + '?tab=intereses' }, footer: footer(p, k, 'Te escribimos por tu solicitud de organizador.') }) }
          : { subject: 'Tu solicitud de organizador en Limón', html: layout({ title: 'No hemos podido aprobar tu solicitud', intro: 'De momento no podemos darte acceso de organizador. Si crees que es un error o quieres darnos más detalles, responde a este correo o vuelve a enviar la solicitud desde tu perfil.', footer: footer(p, k, 'Te escribimos por tu solicitud de organizador.') }) };
      } else if (m.kind === 'org_event') {
        const ok = pl.status === 'publicado';
        let link = APP;
        if (ok) { const { data: s } = await admin.from('submitted_events').select('event_id').eq('id', pl.id).maybeSingle(); if (s && s.event_id) link = evUrl(s.event_id); }
        mail = { subject: ok ? `Publicado en Limón: ${pl.title}` : `No hemos publicado «${pl.title}»`,
          html: layout({ title: ok ? 'Tu evento ya está en Limón' : 'Tu evento no se ha publicado',
            intro: ok ? `<b>${esc(pl.title)}</b> ya aparece en Limón para quien busque planes así.` : `Hemos revisado <b>${esc(pl.title)}</b> y no lo hemos publicado.${pl.nota ? ' ' + esc(pl.nota) : ' Puedes editarlo desde tu perfil y volver a enviarlo.'}`,
            cta: { label: ok ? 'Verlo en Limón' : 'Ir a mis eventos', url: ok ? link : APP + '?tab=intereses' }, footer: footer(p, k, 'Te escribimos por un evento que publicaste.') }) };
      } else if (m.kind === 'rsvp') {
        const { data: ev } = await admin.from('events').select('id,title').eq('id', pl.event_id).maybeSingle();
        const { data: all } = await admin.from('plan_rsvps').select('name,status').eq('invite_id', pl.invite);
        const VERB: Record<string, string> = { voy: 'se apunta', quizas: 'quizá se apunta', no: 'no puede ir' };
        const list = (all || []).map((r) => `${r.name}: ${({ voy: 'va', quizas: 'quizá', no: 'no puede' } as Record<string, string>)[r.status]}`).join(' · ');
        if (ev) mail = { subject: `${pl.name} ${VERB[pl.status] || 'ha respondido'} a «${ev.title}»`,
          html: layout({ title: `${pl.name} ${VERB[pl.status] || 'ha respondido'}`, intro: `A tu propuesta de ir a <b>${esc(ev.title)}</b>.${list ? '<br><br>Respuestas: ' + esc(list) : ''}`,
            cta: { label: 'Ver el plan', url: evUrl(ev.id) }, footer: footer(p, k, 'Te escribimos porque propusiste este plan en Limón.') }) };
      } else if (m.kind === 'follow' || m.kind === 'follow_ok') {
        const { data: who } = await admin.from('profiles').select('username,display_name').eq('username', pl.from).maybeSingle();
        if (who) {
          const name = who.display_name || who.username, link = `${APP}?u=${who.username}`;
          mail = m.kind === 'follow_ok'
            ? { subject: `${name} ha aceptado tu solicitud en Limón`, html: layout({ title: `${name} te deja ver sus planes`, intro: `<b>${esc(name)}</b> (@${esc(who.username)}) ha aceptado tu solicitud. Ya ves a qué planes va y lo que ha guardado, y Limón lo tendrá en cuenta para recomendarte.`, cta: { label: 'Ver su perfil', url: link }, footer: footer(p, k, 'Te escribimos por tus amigos en Limón.') }) }
            : pl.status === 'pending'
              ? { subject: `${name} quiere seguirte en Limón`, html: layout({ title: `${name} quiere seguirte`, intro: `<b>${esc(name)}</b> (@${esc(who.username)}) ha pedido ver tus planes. Tu perfil es privado: solo podrá verlos si aceptas.`, cta: { label: 'Ver la solicitud', url: APP + '?tab=intereses' }, footer: footer(p, k, 'Te escribimos por tus amigos en Limón.') }) }
              : { subject: `${name} ha empezado a seguirte en Limón`, html: layout({ title: `${name} te sigue`, intro: `<b>${esc(name)}</b> (@${esc(who.username)}) ha empezado a seguirte.`, cta: { label: 'Ver su perfil', url: link }, footer: footer(p, k, 'Te escribimos por tus amigos en Limón.') }) };
        }
      } else if (m.kind === 'idea_status') {
        const T: Record<string, string> = { estudiando: 'La estamos mirando', planificada: 'La hemos planificado', hecha: '¡Ya está hecha!', descartada: 'De momento no la haremos' };
        mail = { subject: `Tu idea en Limón: ${T[pl.status] || pl.status}`, html: layout({ title: T[pl.status] || 'Tu idea ha cambiado', intro: `Sobre tu idea <b>${esc(pl.title)}</b>.${pl.nota ? '<br><br>' + esc(pl.nota) : ''}<br><br>¡Gracias por ayudarnos a mejorar Limón!`, cta: { label: 'Ver el tablón de ideas', url: APP + '?ideas' }, footer: footer(p, k, 'Te escribimos porque propusiste esta idea.') }) };
      } else if (m.kind === 'admin_idea') {
        const KN: Record<string, string> = { idea: 'Idea', fallo: 'Fallo', fuente: 'Falta un sitio o evento' };
        mail = { subject: `${KN[pl.kind] || 'Idea'}: ${pl.title}`, html: layout({ title: 'Nueva propuesta en el tablón', intro: `<b>${esc(pl.title)}</b> (${esc(KN[pl.kind] || pl.kind)})`, cta: { label: 'Ver el tablón', url: APP + '?ideas' }, footer: footer(p, k, 'Eres administrador de Limón.') }) };
      } else if (m.kind === 'admin_editor') {
        mail = { subject: `Solicitud de organizador: ${pl.nombre || 'sin nombre'}`, html: layout({ title: 'Nueva solicitud de organizador', intro: `<b>${esc(pl.nombre)}</b>${pl.web ? ' · ' + esc(pl.web) : ''}<br>${esc(pl.que || '')}`, cta: { label: 'Revisar', url: APP + '?tab=intereses' }, footer: footer(p, k, 'Eres administrador de Limón.') }) };
      } else if (m.kind === 'admin_event') {
        const { data: s } = await admin.from('submitted_events').select('status,title,starts_on,venue_name,ai_review').eq('id', pl.id).maybeSingle();
        if (s && s.status === 'revision') {
          const ia = s.ai_review ? (s.ai_review.ok ? 'la IA no ve problemas' : 'la IA tiene dudas: ' + (s.ai_review.motivo || '')) + (s.ai_review.sin_ubicacion ? '; sin ubicar' : '') : '';
          mail = { subject: `Evento para revisar: ${s.title}`, html: layout({ title: 'Hay un evento esperando revisión', intro: `<b>${esc(s.title)}</b> · ${esc(s.starts_on)} · ${esc(s.venue_name || '')}${ia ? '<br>' + esc(ia) : ''}`, cta: { label: 'Revisar', url: APP + '?tab=intereses' }, footer: footer(p, k, 'Eres administrador de Limón.') }) };
        }
      }
    }
    if (!mail || !p) { await admin.from('mail_outbox').update({ sent_at: new Date().toISOString(), error: 'omitido' }).eq('id', m.id); skipped++; continue; }
    const viaPush = wantsPush(p, k) ? await pushTo(admin, p.id, pushOf(mail.subject, mail.html, m.kind)).catch(() => 0) : 0;
    if (!wants(p, k)) { await admin.from('mail_outbox').update({ sent_at: new Date().toISOString(), error: viaPush ? 'solo push' : 'omitido' }).eq('id', m.id); viaPush ? sent++ : skipped++; continue; }
    try { await send(p, mail.subject, mail.html, k); await admin.from('mail_outbox').update({ sent_at: new Date().toISOString() }).eq('id', m.id); sent++; }
    catch (e) { await admin.from('mail_outbox').update({ error: String(e).slice(0, 300) }).eq('id', m.id); }
  }
  return { sent, skipped };
}

// ---------- cortes
type Range = { from?: string; to?: string; start?: string; end?: string; weekdays?: number[]; last_sunday?: boolean };
type Closure = { id: number; title: string; kind: string; streets: string[] | null; ranges: Range[] | null; geom: { segs?: { l: number[][]; n: string }[]; pts?: { p: number[]; n: string }[] } | null };
function activeOn(c: Closure, d: Date): Range | null {
  const ds = ymd(d);
  for (const r of c.ranges || []) {
    if (!r.from || !r.to || ds < r.from || ds > r.to) continue;
    if (r.weekdays && r.weekdays.length && !r.weekdays.includes(isoDow(d))) continue;
    if (r.last_sunday && !(isoDow(d) === 7 && addDays(d, 7).getUTCMonth() !== d.getUTCMonth())) continue;
    return r;
  }
  return null;
}
function cutDistance(o: number[], c: Closure) {
  const mxy = (p: number[]) => [(p[1] - o[1]) * 84800, (p[0] - o[0]) * 111320];
  let best = 1e9;
  for (const s of c.geom?.segs || []) {
    const l = s.l;
    for (let i = 0; i < l.length; i++) {
      const a = mxy(l[i]);
      if (i === 0) best = Math.min(best, Math.hypot(a[0], a[1]));
      if (i + 1 < l.length) {
        const b = mxy(l[i + 1]), dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
        const t = L ? Math.max(0, Math.min(1, -(a[0] * dx + a[1] * dy) / L)) : 0;
        best = Math.min(best, Math.hypot(a[0] + t * dx, a[1] + t * dy));
      }
    }
  }
  for (const p of c.geom?.pts || []) { const a = mxy(p.p); best = Math.min(best, Math.hypot(a[0], a[1])); }
  return best;
}
const TRANS = ['metro', 'cercanias', 'metro_ligero'];
const hours = (r: Range) => r.start ? `de ${r.start}${r.end ? ' a ' + r.end : ''}` : 'todo el día';
const streetKey = (s: string) => fold(s).replace(/^(calle|avenida|paseo|plaza|glorieta|ronda|camino|carretera|travesia|costanilla|cuesta|puerta|pasaje|via) (de la |de los |de las |del |de )?/, '');

// ---------- lluvia (Open-Meteo, previsión para el centro de Madrid)
const OUTDOOR = new Set(['aire_libre', 'mercadillos', 'fiestas', 'desfiles', 'religiosas', 'calle', 'rutas', 'carreras', 'maratones', 'trail', 'bici', 'triatlon',
  'natacion', 'tematicas', 'solidarias', 'foodtrucks', 'carreras_perro', 'quedadas_perro', 'aves', 'excursiones', 'huertos', 'itinerarios', 'jardines', 'senderismo', 'g:naturaleza', 'beneficos']);
const isOutdoor = (cats: string[], text: string) => cats.some((c) => OUTDOOR.has(c)) || /al aire libre/i.test(text || '');
type Rain = { daily: Record<string, number>; hourly: Record<string, number[]> };
type CityRow = { id: string; name: string; lat: number; lng: number };
async function loadCities(): Promise<Record<string, CityRow>> {
  const { data } = await admin.from('cities').select('id,name,lat,lng');
  return Object.fromEntries((data || []).map((c: CityRow) => [c.id, c]));
}
const rainCache: Record<string, Promise<Rain | null>> = {};
const rainFor = (c: CityRow | undefined) => { const k = c ? c.id : 'madrid'; return (rainCache[k] ||= rainForecast(c ? c.lat : 40.4168, c ? c.lng : -3.7038)); };
async function rainForecast(lat: number, lng: number): Promise<Rain | null> {
  try {
    const r = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&hourly=precipitation_probability&daily=precipitation_probability_max&timezone=Europe%2FMadrid&forecast_days=8`);
    if (!r.ok) return null;
    const j = await r.json(), out: Rain = { daily: {}, hourly: {} };
    (j.daily.time as string[]).forEach((d, i) => { out.daily[d] = j.daily.precipitation_probability_max[i] ?? 0; });
    (j.hourly.time as string[]).forEach((t, i) => { const d = t.slice(0, 10); (out.hourly[d] ||= [])[+t.slice(11, 13)] = j.hourly.precipitation_probability[i] ?? 0; });
    return out;
  } catch { return null; }
}
function rainAt(r: Rain, d: string, time: string | null) {
  const m = String(time || '').match(/(\d{1,2})[:.h](\d{2})/);
  if (m && r.hourly[d]) { const h = +m[1]; return Math.max(...r.hourly[d].slice(h, h + 3).map((x) => x || 0)); }
  return r.daily[d] ?? 0;
}

async function daily() {
  const today = madridToday(), days = [today, addDays(today, 1)];
  // el registro de envíos se guarda 12 meses como mucho (política de privacidad)
  await admin.from('mail_outbox').delete().lt('created_at', new Date(Date.now() - 365 * 864e5).toISOString());
  const { data: profs } = await admin.from('profiles').select('id,email,notify,mail_token,my_streets,city_id').or('notify->>calle.eq.true,notify->>lluvia.eq.true,notify->push->>calle.eq.true,notify->push->>lluvia.eq.true');
  if (!profs || !profs.length) return { users: 0, sent: 0 };
  const cities = await loadCities();
  const { data: cl } = await admin.from('closures').select('id,city_id,title,kind,streets,ranges,geom');
  const allClosures = (cl || []) as (Closure & { city_id: string })[];
  let sent = 0;
  for (const p of profs as (Prof & { my_streets: string[] | null; city_id: string | null })[]) {
    if (!p.email) continue;
    const cityId = p.city_id || 'madrid', closures = allClosures.filter((c) => c.city_id === cityId), rain = await rainFor(cities[cityId]);
    const sections: { h: string; items: Item[] }[] = [];
    const wantCalle = (p.notify || {}).calle === true || wantsPush(p, 'calle'), wantRain = (p.notify || {}).lluvia === true || wantsPush(p, 'lluvia');
    // tus planes guardados con sesión hoy o mañana
    const { data: sv } = await admin.from('saved_events').select('event_id, events(id,title,description,lat,lng,time_text,venues(name), event_sessions(on_date), event_categories(category_id))').eq('user_id', p.id);
    const planItems: Item[] = [], rainItems: Item[] = [];
    for (const row of sv || []) {
      const e = row.events as unknown as { id: number; title: string; description: string | null; lat: number; lng: number; time_text: string; venues: { name: string } | null; event_sessions: { on_date: string }[]; event_categories: { category_id: string }[] } | null;
      if (!e) continue;
      if (wantRain && rain && isOutdoor((e.event_categories || []).map((c) => c.category_id), e.title + ' ' + (e.description || ''))) {
        for (const d of days) {
          if (!(e.event_sessions || []).some((s) => s.on_date === ymd(d))) continue;
          const pr = rainAt(rain, ymd(d), e.time_text);
          if (pr >= 60) rainItems.push({ title: e.title, meta: `${d === today ? 'Hoy' : 'Mañana'}${e.time_text ? ' · ' + e.time_text : ''}${e.venues?.name ? ' · ' + e.venues.name : ''}`, url: evUrl(e.id), note: `☔ ${pr} % de probabilidad de lluvia${/\d/.test(e.time_text || '') ? ' a esa hora' : ' ese día'}` });
        }
      }
      if (!wantCalle || !e.lat) continue;
      for (const d of days) {
        if (!(e.event_sessions || []).some((s) => s.on_date === ymd(d))) continue;
        const near: { m: number; t: string }[] = [];
        for (const c of closures) {
          if (!c.geom) continue;
          const r = activeOn(c, d); if (!r) continue;
          const trans = TRANS.includes(c.kind), m = cutDistance([e.lat, e.lng], c);
          if (m > (trans ? 700 : 350)) continue;
          const where = trans ? c.title : 'Corte en ' + ((c.geom.segs || [])[0]?.n || c.title);
          near.push({ m, t: `${where}${m > 60 ? ` (a ${Math.round(m / 50) * 50} m)` : ''}, ${hours(r)}` });
        }
        if (!near.length) continue;
        near.sort((a, b) => a.m - b.m);
        const seen = new Set<string>();
        const uniq = near.filter((x) => { const k = x.t.replace(/ \(a .*$|, .*$/, ''); if (seen.has(k)) return false; seen.add(k); return true; });
        planItems.push({ title: e.title, meta: `${d === today ? 'Hoy' : 'Mañana'}${e.time_text ? ' · ' + e.time_text : ''}${e.venues?.name ? ' · ' + e.venues.name : ''}`, url: evUrl(e.id), note: '⚠ ' + uniq.slice(0, 2).map((x) => x.t).join(' · ') });
      }
    }
    if (planItems.length) sections.push({ h: 'Cortes junto a tus planes', items: planItems });
    if (rainItems.length) sections.push({ h: 'Puede llover en tus planes al aire libre', items: rainItems });
    // tus calles
    const mine = new Set((wantCalle ? p.my_streets || [] : []).map(streetKey).filter(Boolean));
    if (mine.size) {
      const streetItems: Item[] = [];
      for (const c of closures) {
        const hit = (c.streets || []).filter((s) => mine.has(streetKey(s)));
        if (!hit.length) continue;
        for (const d of days) { const r = activeOn(c, d); if (r) { streetItems.push({ title: c.title, meta: `${d === today ? 'Hoy' : 'Mañana'}, ${hours(r)} · ${hit.slice(0, 3).join(', ')}`, url: APP + '?tab=calle' }); break; } }
      }
      if (streetItems.length) sections.push({ h: 'Tus calles', items: streetItems.slice(0, 8) });
    }
    if (!sections.length) continue;
    const { data: ins } = await admin.from('mail_outbox').insert({ user_id: p.id, kind: 'calle', ref: ymd(today), payload: { n: sections.reduce((a, s) => a + s.items.length, 0) } }).select('id');
    if (!ins || !ins.length) continue;  // ya se envió hoy
    const onlyRain = !planItems.length && sections.length === 1 && rainItems.length > 0, kk = onlyRain ? 'lluvia' : 'calle';
    const html = layout({ title: onlyRain ? 'Puede llover en tus planes' : 'Avisos para hoy y mañana', intro: onlyRain ? `Según la previsión de Open-Meteo para ${(cities[cityId] || { name: 'tu ciudad' }).name}, ${human(ymd(today))} y el día siguiente.` : `Lo que hemos visto en los avisos municipales y en la previsión del tiempo para el ${human(ymd(today))} y el día siguiente.`, sections,
      cta: onlyRain ? { label: 'Ver mis planes', url: APP + '?tab=agenda' } : { label: 'Ver el mapa de cortes', url: APP + '?tab=calle' },
      footer: footer(p, kk, `Recibes este aviso porque activaste los avisos de ${onlyRain ? 'lluvia' : 'cortes de calle'} en Limón. Puedes cambiar qué avisos recibes en tu perfil.`) });
    const subject = planItems.length ? `Cortes cerca de tus planes: ${planItems[0].title}` : rainItems.length ? `Puede llover: ${rainItems[0].title}` : 'Cortes en tus calles hoy y mañana';
    if (wantsPush(p, kk)) {
      const first = sections[0].items[0];
      await pushTo(admin, p.id, { title: subject, body: [first.meta, first.note].filter(Boolean).join(' · '), url: onlyRain ? APP + '?tab=agenda' : APP + '?tab=calle', tag: kk }).catch(() => 0);
    }
    if (!wants(p, kk)) { await admin.from('mail_outbox').update({ sent_at: new Date().toISOString(), error: 'solo push' }).eq('id', ins[0].id); continue; }
    try { await send(p, subject, html, kk); await admin.from('mail_outbox').update({ sent_at: new Date().toISOString() }).eq('id', ins[0].id); sent++; }
    catch (e) { await admin.from('mail_outbox').update({ error: String(e).slice(0, 300) }).eq('id', ins[0].id); }
  }
  return { users: profs.length, sent };
}

// ---------- resumen semanal
type Ev = { id: number; title: string; starts_on: string; ends_on: string; time_text: string | null; is_free: boolean; price_text: string | null; district: string | null;
  is_unique: boolean; is_featured: boolean; topics: string[] | null; series: string | null; description: string | null; venues: { name: string } | null;
  event_categories: { category_id: string }[]; event_sessions: { on_date: string }[] };
async function weekly(only?: string) {
  const today = madridToday(), from = ymd(today), to = ymd(addDays(today, 7));
  let q = admin.from('profiles').select('id,email,notify,mail_token,taste,learned,with_kids,city_id').eq('notify->>semanal', 'true');
  if (only) q = q.eq('id', only);
  const { data: profs } = await q;
  if (!profs || !profs.length) return { users: 0, sent: 0 };
  const cities = await loadCities();
  const evCache: Record<string, Ev[]> = {};
  const eventsOf = async (cityId: string) => {
    if (evCache[cityId]) return evCache[cityId];
    const evs: Ev[] = [];
    for (let off = 0; ; off += 1000) {
      const { data, error } = await admin.from('events')
        .select('id,title,starts_on,ends_on,time_text,is_free,price_text,district,is_unique,is_featured,topics,series,description,venues(name),event_categories(category_id),event_sessions(on_date)')
        .eq('city_id', cityId).lte('starts_on', to).gte('ends_on', from).order('id').range(off, off + 999);
      if (error) throw error;
      evs.push(...((data || []) as unknown as Ev[]));
      if (!data || data.length < 1000) break;
    }
    return (evCache[cityId] = evs);
  };
  const { data: cats } = await admin.from('categories').select('id,label,group_id');
  const GROUP: Record<string, string> = {}, LABEL: Record<string, string> = {};
  for (const c of cats || []) { GROUP[c.id] = c.group_id; LABEL[c.id] = c.label; }
  const nextDate = (e: Ev) => {
    const ss = (e.event_sessions || []).map((s) => s.on_date).filter((d) => d >= from && d <= to).sort();
    return ss[0] || (e.starts_on > from ? e.starts_on : null);
  };
  let sent = 0;
  for (const p of profs as (Prof & { taste: any; learned: Record<string, number> | null; with_kids: boolean | null; city_id: string | null })[]) {
    if (!p.email) continue;
    const cityId = p.city_id || 'madrid', evs = await eventsOf(cityId), cityName = (cities[cityId] || { name: 'tu ciudad' }).name;
    const [ui, dis, sv, tf, fr] = await Promise.all([
      admin.from('user_interests').select('category_id,value').eq('user_id', p.id),
      admin.from('dismissed_events').select('event_id').eq('user_id', p.id),
      admin.from('saved_events').select('event_id').eq('user_id', p.id),
      admin.from('topic_follows').select('kind,value').eq('user_id', p.id),
      admin.from('follows').select('followee').eq('follower', p.id).eq('status', 'accepted')]);
    const prefs: Record<string, number> = {}; for (const r of ui.data || []) prefs[r.category_id] = r.value;
    const dismissed = new Set((dis.data || []).map((r) => r.event_id)), saved = new Set((sv.data || []).map((r) => r.event_id));
    const taste = p.taste || {}, learned = p.learned || {};
    const likedGroups = new Set(Object.keys(prefs).filter((k) => prefs[k] === 1).map((k) => GROUP[k]));
    const scored: { e: Ev; s: number; why: string[]; d: string | null }[] = [];
    for (const e of evs) {
      if (dismissed.has(e.id) || saved.has(e.id)) continue;
      const cs = (e.event_categories || []).map((c) => c.category_id);
      if (cs.some((c) => (taste.evitar || []).includes(c) || prefs[c] === 2)) continue;
      if (cs.some((c) => ['infantil', 'cuentacuentos', 'titeres', 'talleres_familia'].includes(c)) && !p.with_kids) continue;
      let s = 0; const why: string[] = [];
      for (const c of taste.categorias || []) if (cs.includes(c.id)) { s += [0, 1.5, 2.5, 3.5][c.peso] || 0; why.push(LABEL[c.id] || c.id); }
      if (taste.temas && taste.temas.length) {
        const hay = ' ' + fold([e.title, e.description || '', (e.topics || []).join(' '), e.series || ''].join(' ')) + ' ';
        for (const t of taste.temas) { const k = fold(t); if (k.length >= 3 && hay.includes(' ' + k + ' ')) { s += 3; why.push(t); } }
      }
      for (const c of cs) { const w = learned['cat:' + c]; if (w) s += w * 0.6; if (prefs[c] === 1) { s += 3; why.push(LABEL[c] || c); } else if (likedGroups.has(GROUP[c])) s += 0.4; }
      for (const t of e.topics || []) { const w = learned['tema:' + t]; if (w) s += w; }
      if (e.series && (learned['serie:' + e.series] || 0) >= 1) s += learned['serie:' + e.series];
      const d = nextDate(e);
      const noTaste = !Object.keys(prefs).length && !(taste.categorias || []).length && !Object.keys(learned).length;
      if (noTaste) s = (e.is_unique ? 2 : 0) + (e.is_featured ? 1.5 : 0);
      if (s <= 0) continue;
      if (e.is_unique) s += 0.8;
      if (e.is_featured) s += 0.4;
      if (d) s += 0.6; else s -= 0.8;  // mejor lo que tiene fecha esta semana que lo que lleva meses abierto
      scored.push({ e, s, why: [...new Set(why)].slice(0, 2), d });
    }
    scored.sort((a, b) => b.s - a.s);
    const pick: typeof scored = []; const seenSerie = new Set<string>(), seenVenue = new Map<string, number>();
    for (const x of scored) {
      if (pick.length >= 8) break;
      if (x.e.series && seenSerie.has(x.e.series)) continue;
      const v = x.e.venues?.name || ''; if (v && (seenVenue.get(v) || 0) >= 2) continue;
      if (x.e.series) seenSerie.add(x.e.series); if (v) seenVenue.set(v, (seenVenue.get(v) || 0) + 1);
      pick.push(x);
    }
    pick.sort((a, b) => (a.d || '9999').localeCompare(b.d || '9999'));
    const fmt = (x: { e: Ev; d: string | null; why?: string[] }): Item => ({
      title: x.e.title,
      meta: [x.d ? human(x.d) : 'Toda la semana', x.e.time_text, x.e.venues?.name, x.e.is_free ? 'Gratis' : x.e.price_text].filter(Boolean).join(' · '),
      url: evUrl(x.e.id), note: x.why && x.why.length ? 'Por: ' + x.why.join(', ') : undefined,
    });
    // de lo que sigues (salas, ciclos, temas, artistas)
    const topics = (tf.data || []) as { kind: string; value: string }[];
    const hitTopic = (e: Ev) => {
      for (const t of topics) {
        const v = fold(t.value);
        if (t.kind === 'sala' && e.venues?.name && fold(e.venues.name) === v) return t.value;
        if (t.kind === 'ciclo' && e.series && fold(e.series) === v) return t.value;
        if (t.kind === 'tema' && (e.topics || []).some((x) => fold(x) === v)) return t.value;
        if (t.kind === 'artista' && v.length >= 3 && (' ' + fold(e.title + ' ' + (e.description || '')) + ' ').includes(' ' + v + ' ')) return t.value;
      }
      return null;
    };
    const picked = new Set(pick.map((x) => x.e.id));
    const followItems = topics.length ? evs.filter((e) => !saved.has(e.id) && !dismissed.has(e.id) && !picked.has(e.id) && nextDate(e) && hitTopic(e))
      .slice(0, 6).map((e) => ({ ...fmt({ e, d: nextDate(e) }), note: 'Sigues: ' + hitTopic(e) })) : [];
    followItems.forEach((x) => picked.add(Number(x.url.split('=e')[1])));
    // a qué van las personas que sigues
    const friendIds = (fr.data || []).map((x: { followee: string }) => x.followee);
    let friendItems: Item[] = [];
    if (friendIds.length) {
      const [{ data: fs }, { data: fp }] = await Promise.all([
        admin.from('saved_events').select('event_id,user_id').in('user_id', friendIds.slice(0, 200)),
        admin.from('profiles').select('id,username,display_name').in('id', friendIds.slice(0, 200))]);
      const names: Record<string, string> = {};
      (fp || []).forEach((x: { id: string; username: string; display_name: string }) => { names[x.id] = x.display_name || x.username || 'Alguien'; });
      const byEv: Record<number, string[]> = {};
      (fs || []).forEach((x: { event_id: number; user_id: string }) => { (byEv[x.event_id] ||= []).push(names[x.user_id]); });
      friendItems = evs.filter((e) => byEv[e.id] && !saved.has(e.id) && !dismissed.has(e.id) && !picked.has(e.id) && nextDate(e))
        .sort((a, b) => byEv[b.id].length - byEv[a.id].length).slice(0, 5)
        .map((e) => ({ ...fmt({ e, d: nextDate(e) }), note: '👥 ' + byEv[e.id].slice(0, 3).join(', ') + (byEv[e.id].length > 3 ? ' y más' : '') }));
    }
    const savedItems = evs.filter((e) => saved.has(e.id)).map((e) => ({ e, d: nextDate(e) })).sort((a, b) => (a.d || '9999').localeCompare(b.d || '9999')).slice(0, 6).map(fmt);
    if (!pick.length && !savedItems.length && !followItems.length && !friendItems.length) continue;
    const ref = only ? 'prueba:' + Date.now() : 'semana:' + from;
    const { data: ins } = await admin.from('mail_outbox').insert({ user_id: p.id, kind: 'semanal', ref, payload: { n: pick.length } }).select('id');
    if (!ins || !ins.length) continue;
    const html = layout({ title: `Tu semana en ${cityName}`, intro: `Planes del ${human(from)} al ${human(to)} elegidos según lo que te gusta.${pick.length && !Object.keys(prefs).length && !(taste.categorias || []).length ? ' Cuéntanos tus gustos en tu perfil y afinaremos más.' : ''}`,
      sections: [{ h: 'Tus planes guardados', items: savedItems }, { h: 'Tus amigos van a', items: friendItems }, { h: 'De lo que sigues', items: followItems },
        { h: savedItems.length || friendItems.length || followItems.length ? 'Te pueden gustar' : '', items: pick.map(fmt) }],
      cta: { label: 'Ver más planes', url: APP }, footer: footer(p, 'semanal', 'Recibes el resumen semanal porque lo activaste en Limón.') });
    const total = pick.length + savedItems.length + followItems.length + friendItems.length;
    try { await send(p, pick.length ? `Tu semana: ${pick[0].e.title} y ${total - 1} planes más` : 'Tu semana en Limón', html, 'semanal'); await admin.from('mail_outbox').update({ sent_at: new Date().toISOString() }).eq('id', ins[0].id); sent++; }
    catch (e) { await admin.from('mail_outbox').update({ error: String(e).slice(0, 300) }).eq('id', ins[0].id); }
  }
  return { users: profs.length, sent };
}

async function unsub(t: string, k: string) {
  if (!/^[0-9a-f-]{36}$/i.test(t || '') || !KINDS.includes(k)) return { ok: false };
  const { data: p } = await admin.from('profiles').select('id,notify').eq('mail_token', t).maybeSingle();
  if (!p) return { ok: false };
  await admin.from('profiles').update({ notify: { ...(p.notify || {}), [k]: false } }).eq('id', p.id);
  return { ok: true, k };
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const u = new globalThis.URL(req.url);
    const body = req.method === 'POST' && (req.headers.get('content-type') || '').includes('json') ? await req.json().catch(() => ({})) : {};
    const action = body.action || u.searchParams.get('action');
    if (action === 'unsub') return json(await unsub(body.t || u.searchParams.get('t'), body.k || u.searchParams.get('k')));
    if (action === 'flush') return json(await flush());
    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    const isService = !!token && (KEYS.includes(token) || token === Deno.env.get('SUPABASE_SERVICE_ROLE_KEY'));
    if (action === 'weekly' || action === 'daily') {
      if (isService) {
        const out: Record<string, unknown> = { flush: await flush() };
        out[action] = action === 'weekly' ? await weekly() : await daily();
        return json(out);
      }
      // un usuario puede pedirse una prueba de su propio resumen
      const { data: who } = token ? await admin.auth.getUser(token) : { data: { user: null } };
      if (action === 'weekly' && who && who.user) {
        const { data: last } = await admin.from('mail_outbox').select('created_at').eq('user_id', who.user.id).like('ref', 'prueba:%').order('created_at', { ascending: false }).limit(1);
        if (last && last.length && Date.now() - new Date(last[0].created_at).getTime() < 10 * 60e3) return json({ error: 'Espera unos minutos antes de pedir otra prueba.' }, 429);
        return json(await weekly(who.user.id));
      }
      return json({ error: 'No autorizado.' }, 401);
    }
    return json({ error: 'Acción desconocida.' }, 400);
  } catch (e) {
    console.error(e);
    return json({ error: 'Algo ha fallado en el servidor.' }, 500);
  }
});
