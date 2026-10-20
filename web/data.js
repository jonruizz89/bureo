// Limón (antes Bureo) · carga de datos desde Supabase y sincronización de la cuenta
(function () {
  const C = window.BUREO_CONFIG;
  const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
  });
  const $ = (id) => document.getElementById(id);
  // web de pruebas (pruebas.limonplanes.com): misma base de datos; usa las funciones «-pruebas» si existen y no cuenta estadísticas
  const STAGING = /^pruebas\.|\.pages\.dev$|\.workers\.dev$/.test(location.hostname);
  window.LIMON_STAGING = STAGING;
  window.limonFn = async function (name, opts) {
    if (STAGING) {
      const r = await sb.functions.invoke(name + '-pruebas', opts);
      const st = r.error && r.error.context && r.error.context.status;
      // si no hay copia «-pruebas» de esa función, Supabase responde 404 (o falla la petición previa del navegador): se usa la normal
      if (!(r.error && (st === 404 || r.error.name === 'FunctionsFetchError' || /not found|404/i.test(String(r.error.message))))) return r;
    }
    return sb.functions.invoke(name, opts);
  };
  // enlace de «He olvidado mi contraseña»: se detecta antes de que la app termine de cargar
  let recovery = /type=recovery/.test(location.hash);
  sb.auth.onAuthStateChange((evt) => { if (evt === 'PASSWORD_RECOVERY') { recovery = true; if (window.__limonPw) window.__limonPw(); } });
  const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

  async function loadTaxonomy() {
    const [g, c] = await Promise.all([
      sb.from('category_groups').select('id,name,position').order('position'),
      sb.from('categories').select('id,group_id,label,position').eq('is_generic', false).order('position')
    ]);
    if (g.error) throw g.error;
    if (c.error) throw c.error;
    return g.data.filter((x) => x.id !== 'otros').map((x) => ({
      id: x.id, name: x.name,
      items: c.data.filter((k) => k.group_id === x.id).map((k) => ({ id: k.id, label: k.label }))
    }));
  }

  async function loadEvents() {
    const today = ymd(new Date());
    const cols = 'id,city_id,title,description,starts_on,ends_on,time_text,district,price_text,is_free,image_url,organizer_url,' +
      'is_unique,is_featured,ai_unique,topics,series,lat,lng,venues(name),event_sessions(on_date),event_categories(category_id),event_sources(url,sources(name))';
    const out = [];
    for (let from = 0; ; from += 1000) {
      // zona: una ciudad, una provincia entera (sus eventos + los de sus ciudades) o un municipio de la provincia
      let q = sb.from('events').select(cols).gte('ends_on', today);
      q = C.muni ? q.eq('city_id', C.city).eq('district', C.muni) : q.in('city_id', C.scope || [C.city]);
      const r = await q.order('starts_on').order('id').range(from, from + 999);
      if (r.error) throw r.error;
      out.push(...r.data);
      if (r.data.length < 1000) break;
    }
    return out.map((e) => ({
      id: 'e' + e.id,
      titulo: e.title,
      descripcion: e.description || '',
      inicio: e.starts_on,
      fin: e.ends_on,
      hora: e.time_text,
      lugar: e.venues ? e.venues.name : null,
      // en la provincia, el «distrito» es el municipio: los planes de Bilbao cuentan como «Bilbao»
      distrito: e.city_id !== C.city && C.names ? C.names[e.city_id] : e.district,
      lat: e.lat, lng: e.lng,
      precio: e.price_text,
      gratis: e.is_free,
      imagen: e.image_url,
      web_organizador: e.organizer_url,
      unico: e.is_unique || e.ai_unique,
      temas: e.topics || [],
      serie: e.series,
      destacado: e.is_featured,
      sesiones: (e.event_sessions || []).map((s) => s.on_date).sort(),
      cats: (e.event_categories || []).map((c) => c.category_id),
      fuentes: (e.event_sources || []).map((s) => ({ nombre: s.sources ? s.sources.name : 'Fuente', url: s.url }))
    })).map((e) => { if (!e.sesiones.length) e.sesiones = [e.inicio]; return e; });
  }

  async function loadClosures() {
    if (C.muni) return [];
    const r = await sb.from('closures').select('title,kind,summary,streets,lines,stations,district,ranges,source_url,source_name,geom').in('city_id', C.scope || [C.city]);
    if (r.error) { console.warn('cortes', r.error); return []; }
    return r.data;
  }

  async function loadAudit() {
    const r = await sb.from('coverage_runs').select('run_at,group_id,found,missing,pct_missing').eq('city_id', C.city).order('run_at', { ascending: false }).limit(40);
    if (r.error || !r.data.length) return null;
    const last = r.data[0].run_at;
    const c = await sb.from('source_candidates').select('domain,hits,example_url,example_title,status').eq('city_id', C.city).eq('status', 'nueva').order('hits', { ascending: false }).limit(12);
    return { runs: r.data.filter((x) => x.run_at === last), cands: c.data || [] };
  }

  async function loadSources() {
    const r = await sb.from('sources').select('name,url,method,license,last_run_at').in('city_id', C.scope || [C.city]).eq('status', 'activa').order('id');
    if (r.error) throw r.error;
    return r.data;
  }

  const toDbId = (id) => Number(String(id).replace(/^e/, ''));
  // ---------- estadísticas de uso: solo con permiso (bureo.analytics = '1'), sin datos de contacto
  const TQ = []; let tTimer = null;
  const consent = () => { try { return localStorage.getItem('bureo.analytics'); } catch (e) { return null; } };
  function anonId() {
    // identificador aleatorio de este navegador, distinto del de «Quedar»: no se puede cruzar con nada más
    try { let d = localStorage.getItem('bureo.anon'); if (!d) { d = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).slice(0, 36); localStorage.setItem('bureo.anon', d); } return d; } catch (e) { return 'sin-almacen'; }
  }
  async function flushTrack() {
    if (!TQ.length || !sb) return;
    const rows = TQ.splice(0, 100).map((r) => (user ? Object.assign(r, { user_id: user.id }) : Object.assign(r, { device: anonId() })));
    const { error } = await sb.from('activity').insert(rows);
    if (error) console.warn('estadísticas', error.message);
  }
  window.limonTrack = function (kind, eventId, ctx) {
    if (consent() !== '1' || !sb || STAGING) return;
    const id = eventId ? toDbId(eventId) : null;
    TQ.push({ kind, event_id: id || null, city_id: C.city, ctx: ctx || null });
    clearTimeout(tTimer); tTimer = setTimeout(flushTrack, TQ.length >= 20 ? 0 : 4000);
  };
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushTrack(); });
  window.limonConsent = {
    get: consent,
    async set(on) {
      try { localStorage.setItem('bureo.analytics', on ? '1' : '0'); } catch (e) { }
      if (!on) TQ.length = 0;
      if (user) await sb.from('profiles').update({ analytics: !!on }).eq('id', user.id);
    }
  };

  // ---------- cuenta
  let app = null, user = null, timers = {};

  async function pushKey(k, v) {
    if (!user) return;
    const uid = user.id;
    if (k === 'prefs') {
      const ids = Object.keys(v);
      let del = sb.from('user_interests').delete().eq('user_id', uid);
      if (ids.length) del = del.not('category_id', 'in', '(' + ids.join(',') + ')');
      await del;
      if (ids.length) await sb.from('user_interests').upsert(ids.map((c) => ({ user_id: uid, category_id: c, value: v[c], updated_at: new Date().toISOString() })));
    } else if (k === 'saved') {
      const ids = v.map(toDbId).filter(Boolean);
      let del = sb.from('saved_events').delete().eq('user_id', uid);
      if (ids.length) del = del.not('event_id', 'in', '(' + ids.join(',') + ')');
      await del;
      if (ids.length) await sb.from('saved_events').upsert(ids.map((id) => ({ user_id: uid, event_id: id })), { ignoreDuplicates: true });
    } else if (k === 'dismissed' || k === 'went' || k === 'nofui') {
      // attendance guarda «fui» (went = true) y «no pude ir» (went = false, la respuesta a «¿Al final fuiste?»)
      const table = k === 'dismissed' ? 'dismissed_events' : 'attendance';
      const ids = v.map(toDbId).filter(Boolean);
      let del = sb.from(table).delete().eq('user_id', uid);
      if (k !== 'dismissed') del = del.eq('went', k === 'went');
      if (ids.length) del = del.not('event_id', 'in', '(' + ids.join(',') + ')');
      await del;
      if (ids.length) {
        if (k === 'dismissed') await sb.from(table).upsert(ids.map((id) => ({ user_id: uid, event_id: id })), { ignoreDuplicates: true });
        else if (k === 'went') await sb.from(table).upsert(ids.map((id) => ({ user_id: uid, event_id: id, went: true })), { onConflict: 'user_id,event_id' });
        else await sb.from(table).upsert(ids.map((id) => ({ user_id: uid, event_id: id, went: false })), { onConflict: 'user_id,event_id', ignoreDuplicates: true });
      }
    } else if (k === 'taste') {
      await sb.from('profiles').update({ taste: v }).eq('id', uid);
    } else if (k === 'learned') {
      await sb.from('profiles').update({ learned: v }).eq('id', uid);
    } else if (k === 'streets') {
      await sb.from('profiles').update({ my_streets: v }).eq('id', uid);
    } else if (k === 'opts') {
      await sb.from('profiles').update({ with_kids: !!v.ninos, with_dog: !!v.perro }).eq('id', uid);
    }
  }
  function sync(k, v) {
    if (!user || !['prefs', 'saved', 'opts', 'streets', 'dismissed', 'went', 'nofui', 'learned', 'taste'].includes(k)) return;
    clearTimeout(timers[k]);
    timers[k] = setTimeout(() => pushKey(k, v).catch((e) => console.warn('sync', k, e)), 400);
  }

  async function pullAccount() {
    const uid = user.id;
    const [i, s, p, d, w] = await Promise.all([
      sb.from('user_interests').select('category_id,value').eq('user_id', uid),
      sb.from('saved_events').select('event_id').eq('user_id', uid),
      sb.from('profiles').select('with_kids,with_dog,my_streets,learned,taste,taste_text,city_id,analytics').eq('id', uid).maybeSingle(),
      sb.from('dismissed_events').select('event_id').eq('user_id', uid),
      sb.from('attendance').select('event_id,went').eq('user_id', uid)
    ]);
    const remote = {
      prefs: Object.fromEntries((i.data || []).map((r) => [r.category_id, r.value])),
      saved: (s.data || []).map((r) => 'e' + r.event_id),
      opts: { ninos: !!(p.data && p.data.with_kids), perro: !!(p.data && p.data.with_dog) },
      streets: (p.data && p.data.my_streets) || [],
      dismissed: (d.data || []).map((r) => 'e' + r.event_id),
      went: (w.data || []).filter((r) => r.went).map((r) => 'e' + r.event_id),
      nofui: (w.data || []).filter((r) => !r.went).map((r) => 'e' + r.event_id),
      learned: (p.data && p.data.learned) || {},
      taste: (p.data && p.data.taste) || null,
      taste_text: (p.data && p.data.taste_text) || ''
    };
    // el permiso de estadísticas va con la cuenta; si se dio antes de entrar, se guarda en ella
    const pa = p.data ? p.data.analytics : null;
    if (pa === true || pa === false) { try { localStorage.setItem('bureo.analytics', pa ? '1' : '0'); } catch (e) { } }
    else if (consent() === '1' || consent() === '0') sb.from('profiles').update({ analytics: consent() === '1' }).eq('id', uid);
    // en un navegador nuevo, se abre la ciudad que la persona tiene en su cuenta
    const pc = p.data && p.data.city_id;
    let chosen = null; try { chosen = localStorage.getItem('bureo.city'); } catch (e) { }
    if (pc && !chosen && !new URLSearchParams(location.search).get('c') && pc !== C.city && CITIES.some((c) => c.id === pc && c.status === 'publicada')) { window.limonCity.set(pc); return; }
    if (!pc || (chosen && chosen !== pc)) sb.from('profiles').update({ city_id: C.city }).eq('id', uid);
    const local = app.get();
    const remoteEmpty = !Object.keys(remote.prefs).length && !remote.saved.length && !remote.streets.length && !remote.dismissed.length && !Object.keys(remote.learned).length;
    if (remoteEmpty && (Object.keys(local.prefs).length || local.saved.length)) {
      // primera vez en este dispositivo con cuenta nueva: subimos lo que ya tenías
      await pushKey('prefs', local.prefs); await pushKey('saved', local.saved); await pushKey('opts', local.opts); await pushKey('streets', local.streets || []);
      await pushKey('dismissed', local.dismissed || []); await pushKey('went', local.went || []); await pushKey('nofui', local.nofui || []); await pushKey('learned', local.learned || {});
    } else {
      app.set(remote);
    }
    if (remote.taste || remote.taste_text) app.set({ taste: remote.taste, taste_text: remote.taste_text });
  }

  function renderAccount() {
    $('acc-out').hidden = !!user;
    $('acc-in').hidden = !user;
    if (user) $('acc-who').textContent = user.email || 'tu cuenta';
  }

  function wireAccount() {
    const back = location.origin + location.pathname;
    const ES = (m) => {
      m = String(m || '');
      if (/invalid login credentials/i.test(m)) return 'Correo o contraseña incorrectos. Si nunca pusiste contraseña, entra con un enlace o pulsa «He olvidado mi contraseña».';
      if (/email not confirmed/i.test(m)) return 'Aún no has confirmado tu correo: abre el enlace que te enviamos al crear la cuenta.';
      if (/already registered|already exists/i.test(m)) return 'Ya hay una cuenta con ese correo: entra o recupera la contraseña.';
      if (/password should be|at least|weak/i.test(m)) return 'La contraseña es demasiado débil: usa al menos 8 caracteres y mezcla letras y números.';
      if (/rate limit|too many|seconds/i.test(m)) return 'Demasiados intentos seguidos. Espera un minuto y vuelve a probar.';
      if (/same.*password|different from the old/i.test(m)) return 'Esa ya es tu contraseña actual.';
      return m;
    };
    $('acc-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const mode = (ev.submitter && ev.submitter.dataset.mode) || 'login';
      const email = $('acc-email').value.trim(), password = $('acc-pass').value;
      const msg = (t) => { $('acc-msg').textContent = t; };
      if (!email) { msg('Escribe tu correo.'); return; }
      if ((mode === 'login' || mode === 'signup') && password.length < 8) { msg(mode === 'signup' ? 'Elige una contraseña de al menos 8 caracteres.' : 'Escribe tu contraseña (o pide un enlace para entrar sin ella).'); $('acc-pass').focus(); return; }
      msg(mode === 'login' ? 'Entrando…' : 'Enviando…');
      let r;
      if (mode === 'login') r = await sb.auth.signInWithPassword({ email, password });
      else if (mode === 'signup') r = await sb.auth.signUp({ email, password, options: { emailRedirectTo: back } });
      else if (mode === 'reset') r = await sb.auth.resetPasswordForEmail(email, { redirectTo: back });
      else r = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: back } });
      if (r.error) { msg(ES(r.error.message)); return; }
      $('acc-pass').value = '';
      if (mode === 'login') msg('');
      else if (mode === 'signup') {
        // si el correo ya tiene cuenta, Supabase no envía nada y devuelve un usuario sin identidades
        const dup = r.data && r.data.user && Array.isArray(r.data.user.identities) && r.data.user.identities.length === 0;
        msg(r.data && r.data.session ? '' : dup ? 'Ya tienes una cuenta con ' + email + '. Pulsa «He olvidado mi contraseña» para ponerle una, o pide un enlace para entrar sin ella.'
          : 'Te hemos enviado un correo a ' + email + ' para confirmar la cuenta. Ábrelo y ya estarás dentro.');
      }
      else if (mode === 'reset') msg('Si hay una cuenta con ' + email + ', te llegará un enlace para poner una contraseña nueva.');
      else msg('Te hemos enviado un enlace a ' + email + '. Ábrelo en este dispositivo para entrar.');
    });
    if ((window.BUREO_CONFIG || {}).google) {
      $('acc-google').hidden = false;
      $('acc-google').addEventListener('click', async () => {
        const { error } = await sb.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: back } });
        if (error) $('acc-msg').textContent = 'No se ha podido abrir Google: ' + ES(error.message);
      });
    }
    // ventana directa para poner la contraseña nueva al volver del correo
    window.__limonPw = () => {
      const d = $('pw-dlg');
      if (d && !d.open) { if (d.showModal) d.showModal(); else d.setAttribute('open', ''); setTimeout(() => $('pw-dlg-1').focus(), 50); }
    };
    if (recovery) window.__limonPw();
    $('pw-dlg-show').addEventListener('change', (ev) => { ['pw-dlg-1', 'pw-dlg-2'].forEach((id) => { $(id).type = ev.target.checked ? 'text' : 'password'; }); });
    $('pw-dlg-later').addEventListener('click', () => $('pw-dlg').close());
    $('pw-dlg-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const a = $('pw-dlg-1').value, b = $('pw-dlg-2').value, m = $('pw-dlg-msg');
      if (a.length < 8) { m.textContent = 'Mínimo 8 caracteres.'; return; }
      if (a !== b) { m.textContent = 'Las dos contraseñas no coinciden.'; $('pw-dlg-2').focus(); return; }
      $('pw-dlg-ok').disabled = true; m.textContent = 'Guardando…';
      const { error } = await sb.auth.updateUser({ password: a });
      $('pw-dlg-ok').disabled = false;
      if (error) { m.textContent = ES(error.message); return; }
      m.textContent = '¡Listo! Ya puedes entrar con tu correo y esta contraseña.';
      $('pw-dlg-1').value = $('pw-dlg-2').value = '';
      setTimeout(() => $('pw-dlg').close(), 1600);
    });
    $('acc-pw-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const password = $('acc-pw-new').value;
      if (password.length < 8) { $('acc-pw-msg').textContent = 'Mínimo 8 caracteres.'; return; }
      $('acc-pw-msg').textContent = 'Guardando…';
      const { error } = await sb.auth.updateUser({ password });
      $('acc-pw-msg').textContent = error ? ES(error.message) : 'Contraseña guardada. Ya puedes entrar con ella en cualquier dispositivo.';
      if (!error) $('acc-pw-new').value = '';
    });
    $('acc-logout').addEventListener('click', async () => { await sb.auth.signOut(); });
    $('acc-export').addEventListener('click', async () => {
      if (!user) return;
      const uid = user.id, q = (t, col) => sb.from(t).select('*').eq(col || 'user_id', uid);
      const [p, i, sv, at, di, se, inv, rs] = await Promise.all([q('profiles', 'id'), q('user_interests'), q('saved_events'), q('attendance'), q('dismissed_events'), q('submitted_events', 'owner'),
        sb.from('plan_invites').select('*, plan_rsvps(name,status,created_at)').eq('owner', uid), q('plan_rsvps')]);
      const [fw, tf, ls, lf] = await Promise.all([sb.from('follows').select('*'), q('topic_follows'), sb.from('user_lists').select('*, list_items(event_id,note,added_at)').eq('owner', uid), q('list_follows')]);
      const [idd, idv, act] = await Promise.all([q('ideas', 'author'), q('idea_votes'), q('activity').order('created_at', { ascending: false }).limit(5000)]);
      const out = { exportado: new Date().toISOString(), email: user.email, perfil: p.data, intereses: i.data, guardados: sv.data,
        asistencias: at.data, descartados: di.data, eventos_publicados: se.data, planes_propuestos: inv.data, mis_respuestas_a_planes: rs.data,
        seguidores_y_seguidos: fw.data, lo_que_sigues: tf.data, mis_listas: ls.data, listas_que_sigo: lf.data, mis_ideas: idd.data, mis_votos: idv.data, estadisticas_de_uso: act.data };
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' }));
      a.download = 'limon-mis-datos.json'; document.body.appendChild(a); a.click(); a.remove();
    });
    $('acc-del-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if ($('acc-del').value.trim() !== 'BORRAR') { $('acc-del-msg').textContent = 'Escribe BORRAR en mayúsculas para confirmar.'; return; }
      $('acc-del-msg').textContent = 'Borrando…';
      const res = await window.limonDB.fn('limon-account', { action: 'delete', confirm: 'BORRAR' });
      if (!res || res.error) { $('acc-del-msg').textContent = (res && res.error) || 'No se ha podido borrar.'; return; }
      try { Object.keys(localStorage).filter((k) => k.startsWith('bureo.')).forEach((k) => localStorage.removeItem(k)); } catch (e) { }
      await sb.auth.signOut();
      alert('Tu cuenta y tus datos se han borrado.');
      location.reload();
    });
    sb.auth.onAuthStateChange((evt, session) => {
      const was = user && user.id;
      user = session ? session.user : null;
      renderAccount();

      if (app && app.setUser) app.setUser(!!user);
      if (user && user.id !== was) pullAccount().catch((e) => console.warn('pull', e));
    });
  }

  // acceso directo para las secciones de cuenta (organizadores, admin, calendario)
  window.limonDB = { sb, user: () => user, fn: async (name, body) => {
    if (!user) return { error: 'Entra con tu cuenta.' };
    const { data, error } = await window.limonFn(name, { body });
    if (error) { try { const b = await error.context.json(); return { error: b.error || 'Error del servidor' }; } catch (e) { return { error: 'No se ha podido contactar con el servidor.' }; } }
    return data;
  } };

  // IA en el servidor (función limon-ai): solo con sesión
  window.limonAI = async function (payload) {
    if (!user) return { error: 'Entra con tu cuenta para usar la IA.' };
    const { data, error } = await window.limonFn('limon-ai', { body: Object.assign({ city: C.city }, payload) });
    if (error) {
      try { const b = await error.context.json(); return { error: b.error || 'No se ha podido contactar con la IA.' }; }
      catch (e) { return { error: 'No se ha podido contactar con la IA.' }; }
    }
    return data;
  };

  // ciudades: la elegida en este navegador, la de ?c= (vista previa, también de ciudades en borrador) o Madrid
  let CITIES = [], CITY = null;
  async function loadCities() {
    const r = await sb.from('cities').select('id,name,region,lat,lng,bbox,districts,stadiums,features,status,position,kind,parent').order('position');
    CITIES = r.data || [];
    const qp = new URLSearchParams(location.search);
    let saved = null; try { saved = localStorage.getItem('bureo.city'); } catch (e) { }
    const [sid, smuni] = (saved || '').split('|');
    const pick = (id) => CITIES.find((c) => c.id === id);
    let base = null, muni = null;
    if (qp.get('c') && pick(qp.get('c'))) { base = pick(qp.get('c')); muni = qp.get('m'); }
    else if (sid && pick(sid) && pick(sid).status === 'publicada') { base = pick(sid); muni = smuni || null; }
    base = base || pick(C.city) || CITIES[0] || { id: C.city, name: 'Madrid', lat: 40.4168, lng: -3.7038, districts: [], stadiums: [], features: { calle: true } };
    const kids = CITIES.filter((c) => c.parent === base.id);
    if (base.kind !== 'provincia' || !(base.districts || []).includes(muni) || kids.some((k) => k.name === muni)) muni = null;
    C.city = base.id; C.muni = muni; C.scope = [base.id].concat(kids.map((k) => k.id));
    C.names = Object.fromEntries(kids.map((k) => [k.id, k.name]));
    if (muni) CITY = Object.assign({}, base, { name: muni, muni, province: base.name, districts: [], features: {}, stadiums: [] });
    else if (base.kind === 'provincia') CITY = Object.assign({}, base, { districts: [...new Set((base.districts || []).concat(kids.map((k) => k.name)))].sort((a, b) => a.localeCompare(b, 'es')) });
    else CITY = base;
  }
  // centro de un municipio sin fila propia: la media de sus planes situados
  function centerOn(evs) {
    const pts = evs.filter((e) => e.lat && e.lng);
    if (!CITY.muni || !pts.length) return;
    CITY.lat = pts.reduce((a, e) => a + e.lat, 0) / pts.length; CITY.lng = pts.reduce((a, e) => a + e.lng, 0) / pts.length;
  }
  window.limonCity = {
    list: () => CITIES, current: () => CITY,
    // id = «madrid», «bizkaia» (toda la provincia) o «bizkaia|Getxo» (un municipio de la provincia)
    async set(id) {
      const [base, muni] = String(id).split('|'), c = CITIES.find((x) => x.id === base);
      // una zona en borrador solo se ve en vista previa (?c=), sin guardarla como propia
      if (c && c.status !== 'publicada') { location.href = location.pathname + '?c=' + encodeURIComponent(base) + (muni ? '&m=' + encodeURIComponent(muni) : ''); return; }
      try { localStorage.setItem('bureo.city', id); } catch (e) { }
      if (user) await sb.from('profiles').update({ city_id: base }).eq('id', user.id);
      location.href = location.pathname;
    },
    zone: () => (C.muni ? C.city + '|' + C.muni : C.city)
  };

  window.bootBureo = async function (startApp) {
    $('pt-h').textContent = 'Cargando planes…';
    try {
      await loadCities();
      const [tax, evs, srcs, cuts, audit] = await Promise.all([loadTaxonomy(), loadEvents(), loadSources(), loadClosures(), loadAudit().catch(() => null)]);
      centerOn(evs);
      const last = srcs.map((s) => s.last_run_at).filter(Boolean).sort().pop();
      const meta = {
        eventos: evs.length,
        fuentes: srcs,
        multifuente: evs.filter((e) => e.fuentes.length > 1).length,
        sin_categoria: evs.filter((e) => !e.cats.length || (e.cats.length === 1 && e.cats[0] === 'sin_categoria')).length,
        generado: last ? new Date(last).toLocaleString('es-ES', { dateStyle: 'long', timeStyle: 'short' }) : 'sin datos'
      };
      app = startApp({ eventos: evs, meta, closures: cuts, audit, city: CITY, cities: CITIES }, tax, sync);
      wireAccount();
    } catch (e) {
      console.error(e);
      $('pt-h').textContent = 'No se han podido cargar los planes';
      $('pt-lead').textContent = 'Revisa tu conexión y recarga la página.';
    }
  };
})();
