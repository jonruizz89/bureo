// Limón · calendario suscribible (iCal) de cada usuario: sus planes guardados y, si quiere, los cortes de sus calles y líneas.
// Se accede con un enlace secreto por persona (profiles.ical_token); no necesita sesión porque lo piden las apps de calendario.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4';

const URL = Deno.env.get('SUPABASE_URL')!;
const SECRET = (() => {
  try { return JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}').default; } catch { return undefined; }
})() || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const admin = createClient(URL, SECRET, { auth: { persistSession: false } });
const WEB = 'https://limonplanes.com/';

const esc = (t: string) => String(t || '').replace(/\\/g, '\\\\').replace(/;/g, '\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');
const fold = (line: string) => {  // líneas de 75 octetos como pide el estándar
  const out: string[] = []; let cur = '';
  for (const ch of line) {
    if (new TextEncoder().encode(cur + ch).length > 73) { out.push(cur); cur = ' ' + ch; } else cur += ch;
  }
  return out.concat(cur).join('\r\n');
};
const ymd = (d: string) => d.replace(/-/g, '');
const nextDay = (d: string) => { const x = new Date(d + 'T12:00:00Z'); x.setUTCDate(x.getUTCDate() + 1); return x.toISOString().slice(0, 10); };
const key = (t: string) => (t || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9 ]/g, ' ')
  .replace(/\s+/g, ' ').trim().replace(/^(calle|c|avenida|av|avda|paseo|plaza|pza|glorieta|ronda|carretera|camino|travesia|costanilla|cuesta)( de(l| la| los| las)?)? /, '').replace(/^(de|del|la|el) /, '');
const lineKey = (t: string) => { const m = String(t || '').toUpperCase().replace(/L[IÍ]NEA/g, 'L').replace(/\s+/g, '').match(/^(L|ML|C|R|N)?(\d{0,3}[A-Z]?)$/); return m ? (m[1] || '') + m[2] : ''; };

function timed(t: string | null) {
  const m = String(t || '').match(/(\d{1,2})[:.h](\d{2})/);
  return m ? `${m[1].padStart(2, '0')}${m[2]}00` : null;
}

Deno.serve(async (req) => {
  const u = new globalThis.URL(req.url);
  const t = u.searchParams.get('t') || '';
  if (!/^[0-9a-f-]{36}$/.test(t)) return new Response('Enlace no válido', { status: 404 });
  const { data: p } = await admin.from('profiles').select('id,my_streets,city_id').eq('ical_token', t).maybeSingle();
  if (!p) return new Response('Enlace no válido', { status: 404 });
  const today = new Date().toISOString().slice(0, 10);
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Limón//Planes//ES', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
    'X-WR-CALNAME:Limón · mis planes', 'X-WR-TIMEZONE:Europe/Madrid', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H', 'X-PUBLISHED-TTL:PT6H'];
  const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15) + 'Z';

  // planes guardados
  const { data: sv } = await admin.from('saved_events').select('event_id').eq('user_id', p.id);
  const ids = (sv || []).map((r: { event_id: number }) => r.event_id);
  if (ids.length) {
    const { data: evs } = await admin.from('events')
      .select('id,title,description,starts_on,ends_on,time_text,venues(name),event_sessions(on_date),event_sources(url)')
      .in('id', ids).gte('ends_on', new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10));
    for (const e of evs || []) {
      const sess = ((e.event_sessions || []) as { on_date: string }[]).map((x) => x.on_date).sort();
      const days = sess.length > 1 ? sess.filter((d) => d >= today).slice(0, 1) : [e.starts_on];
      const url = (((e.event_sources || []) as { url: string }[])[0] || {}).url || WEB;
      const venue = (e.venues as unknown as { name: string } | null)?.name || '';
      for (const d of days.length ? days : [e.starts_on]) {
        const h = timed(e.time_text);
        const single = sess.length > 1 || e.starts_on === e.ends_on;
        lines.push('BEGIN:VEVENT', `UID:limon-e${e.id}-${d}@limon`, `DTSTAMP:${stamp}`);
        const long = !single && (Date.parse(e.ends_on) - Date.parse(e.starts_on)) > 7 * 864e5;
        if (h && single) lines.push(`DTSTART;TZID=Europe/Madrid:${ymd(d)}T${h}`, 'DURATION:PT2H');
        else if (long) lines.push(`DTSTART;VALUE=DATE:${ymd(e.ends_on)}`, `DTEND;VALUE=DATE:${ymd(nextDay(e.ends_on))}`, 'TRANSP:TRANSPARENT');
        else lines.push(`DTSTART;VALUE=DATE:${ymd(single ? d : e.starts_on)}`, `DTEND;VALUE=DATE:${ymd(nextDay(single ? d : e.ends_on))}`);
        lines.push(fold('SUMMARY:' + esc((long ? 'Último día: ' : '') + e.title)), fold('LOCATION:' + esc(venue)),
          fold('DESCRIPTION:' + esc(((e.time_text ? e.time_text + '\n' : '') + (e.description || '')).slice(0, 600) + '\n' + url)),
          fold('URL:' + url), 'END:VEVENT');
      }
    }
  }

  // cortes en mis calles y líneas (próximas 3 semanas)
  const mine = (p.my_streets || []) as string[];
  if (mine.length && u.searchParams.get('cortes') !== '0') {
    const { data: cs } = await admin.from('closures').select('id,title,kind,summary,streets,lines,stations,ranges,source_url').eq('city_id', p.city_id || 'madrid');
    const sk = mine.map(key).filter((k) => k.length >= 3), lk = mine.map(lineKey).filter(Boolean);
    const end = new Date(Date.now() + 21 * 864e5).toISOString().slice(0, 10);
    for (const c of cs || []) {
      const hit = (c.streets || []).some((s: string) => { const k = key(s); return k.length >= 3 && sk.some((q) => k.includes(q) || q.includes(k)); })
        || (c.stations || []).some((s: string) => sk.includes(key(s)))
        || (c.lines || []).some((l: string) => { const k = lineKey(l); return k && (lk.includes(k) || lk.map((x) => 'L' + x).includes(k)); });
      if (!hit) continue;
      for (const r of (c.ranges || []) as { from: string; to: string; weekdays?: number[]; start?: string | null; end?: string | null; last_sunday?: boolean }[]) {
        let d = r.from > today ? r.from : today; let n = 0;
        while (d <= r.to && d <= end && n < 25) {
          const wd = ((new Date(d + 'T12:00:00Z').getUTCDay() + 6) % 7) + 1;
          const lastSun = wd === 7 && new Date(new Date(d + 'T12:00:00Z').getTime() + 7 * 864e5).getUTCMonth() !== new Date(d + 'T12:00:00Z').getUTCMonth();
          if ((!r.weekdays || !r.weekdays.length || r.weekdays.includes(wd)) && (!r.last_sunday || lastSun)) {
            lines.push('BEGIN:VEVENT', `UID:limon-c${c.id}-${d}@limon`, `DTSTAMP:${stamp}`);
            if (r.start && /^\d{1,2}:\d{2}$/.test(r.start)) {
              lines.push(`DTSTART;TZID=Europe/Madrid:${ymd(d)}T${r.start.replace(':', '').padStart(4, '0')}00`);
              if (r.end && /^\d{1,2}:\d{2}$/.test(r.end) && r.end > r.start) lines.push(`DTEND;TZID=Europe/Madrid:${ymd(d)}T${r.end.replace(':', '').padStart(4, '0')}00`);
              else lines.push('DURATION:PT1H');
            } else lines.push(`DTSTART;VALUE=DATE:${ymd(d)}`, `DTEND;VALUE=DATE:${ymd(nextDay(d))}`);
            lines.push(fold('SUMMARY:' + esc('⚠ ' + c.title)), fold('DESCRIPTION:' + esc((c.summary || '') + '\n' + (c.source_url || ''))), 'TRANSP:TRANSPARENT', 'END:VEVENT');
            n++;
          }
          d = nextDay(d);
        }
      }
    }
  }
  lines.push('END:VCALENDAR');
  return new Response(lines.join('\r\n') + '\r\n', {
    headers: { 'Content-Type': 'text/calendar; charset=utf-8', 'Cache-Control': 'public, max-age=1800', 'Access-Control-Allow-Origin': '*' },
  });
});
