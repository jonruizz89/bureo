// Limón (antes Bureo) · carga de datos desde Supabase y sincronización de la cuenta
(function () {
  const C = window.BUREO_CONFIG;
  const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
  });
  const $ = (id) => document.getElementById(id);
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
    const cols = 'id,title,description,starts_on,ends_on,time_text,district,price_text,is_free,image_url,organizer_url,' +
      'is_unique,is_featured,ai_unique,topics,series,lat,lng,venues(name),event_sessions(on_date),event_categories(category_id),event_sources(url,sources(name))';
    const out = [];
    for (let from = 0; ; from += 1000) {
      const r = await sb.from('events').select(cols).eq('city_id', C.city).gte('ends_on', today)
        .order('starts_on').order('id').range(from, from + 999);
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
      distrito: e.district,
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
    const r = await sb.from('closures').select('title,kind,summary,streets,lines,stations,district,ranges,source_url,source_name,geom').eq('city_id', C.city);
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
    const r = await sb.from('sources').select('name,url,method,license,last_run_at').eq('city_id', C.city).order('id');
    if (r.error) throw r.error;
    return r.data;
  }

  // ---------- cuenta
  let app = null, user = null, timers = {};
  const toDbId = (id) => Number(String(id).replace(/^e/, ''));

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
    } else if (k === 'dismissed' || k === 'went') {
      const table = k === 'dismissed' ? 'dismissed_events' : 'attendance';
      const ids = v.map(toDbId).filter(Boolean);
      let del = sb.from(table).delete().eq('user_id', uid);
      if (ids.length) del = del.not('event_id', 'in', '(' + ids.join(',') + ')');
      await del;
      if (ids.length) await sb.from(table).upsert(ids.map((id) => k === 'went' ? { user_id: uid, event_id: id, went: true } : { user_id: uid, event_id: id }), { ignoreDuplicates: true });
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
    if (!user || !['prefs', 'saved', 'opts', 'streets', 'dismissed', 'went', 'learned', 'taste'].includes(k)) return;
    clearTimeout(timers[k]);
    timers[k] = setTimeout(() => pushKey(k, v).catch((e) => console.warn('sync', k, e)), 400);
  }

  async function pullAccount() {
    const uid = user.id;
    const [i, s, p, d, w] = await Promise.all([
      sb.from('user_interests').select('category_id,value').eq('user_id', uid),
      sb.from('saved_events').select('event_id').eq('user_id', uid),
      sb.from('profiles').select('with_kids,with_dog,my_streets,learned,taste,taste_text').eq('id', uid).maybeSingle(),
      sb.from('dismissed_events').select('event_id').eq('user_id', uid),
      sb.from('attendance').select('event_id').eq('user_id', uid).eq('went', true)
    ]);
    const remote = {
      prefs: Object.fromEntries((i.data || []).map((r) => [r.category_id, r.value])),
      saved: (s.data || []).map((r) => 'e' + r.event_id),
      opts: { ninos: !!(p.data && p.data.with_kids), perro: !!(p.data && p.data.with_dog) },
      streets: (p.data && p.data.my_streets) || [],
      dismissed: (d.data || []).map((r) => 'e' + r.event_id),
      went: (w.data || []).map((r) => 'e' + r.event_id),
      learned: (p.data && p.data.learned) || {},
      taste: (p.data && p.data.taste) || null,
      taste_text: (p.data && p.data.taste_text) || ''
    };
    const local = app.get();
    const remoteEmpty = !Object.keys(remote.prefs).length && !remote.saved.length && !remote.streets.length && !remote.dismissed.length && !Object.keys(remote.learned).length;
    if (remoteEmpty && (Object.keys(local.prefs).length || local.saved.length)) {
      // primera vez en este dispositivo con cuenta nueva: subimos lo que ya tenías
      await pushKey('prefs', local.prefs); await pushKey('saved', local.saved); await pushKey('opts', local.opts); await pushKey('streets', local.streets || []);
      await pushKey('dismissed', local.dismissed || []); await pushKey('went', local.went || []); await pushKey('learned', local.learned || {});
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
      const out = { exportado: new Date().toISOString(), email: user.email, perfil: p.data, intereses: i.data, guardados: sv.data,
        asistencias: at.data, descartados: di.data, eventos_publicados: se.data, planes_propuestos: inv.data, mis_respuestas_a_planes: rs.data };
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
      if (evt === 'PASSWORD_RECOVERY') {
        // viene del correo «He olvidado mi contraseña»: abrir el formulario para poner una nueva
        setTimeout(() => {
          const t = document.querySelector('[data-goto="intereses"], #t-intereses'); if (t) t.click();
          $('acc-pw').open = true; $('acc-pw-msg').textContent = 'Escribe tu nueva contraseña.';
          $('acc-pw').scrollIntoView({ behavior: 'smooth', block: 'center' }); $('acc-pw-new').focus();
        }, 400);
      }
      if (app && app.setUser) app.setUser(!!user);
      if (user && user.id !== was) pullAccount().catch((e) => console.warn('pull', e));
    });
  }

  // acceso directo para las secciones de cuenta (organizadores, admin, calendario)
  window.limonDB = { sb, user: () => user, fn: async (name, body) => {
    if (!user) return { error: 'Entra con tu cuenta.' };
    const { data, error } = await sb.functions.invoke(name, { body });
    if (error) { try { const b = await error.context.json(); return { error: b.error || 'Error del servidor' }; } catch (e) { return { error: 'No se ha podido contactar con el servidor.' }; } }
    return data;
  } };

  // IA en el servidor (función limon-ai): solo con sesión
  window.limonAI = async function (payload) {
    if (!user) return { error: 'Entra con tu cuenta para usar la IA.' };
    const { data, error } = await sb.functions.invoke('limon-ai', { body: payload });
    if (error) {
      try { const b = await error.context.json(); return { error: b.error || 'No se ha podido contactar con la IA.' }; }
      catch (e) { return { error: 'No se ha podido contactar con la IA.' }; }
    }
    return data;
  };

  window.bootBureo = async function (startApp) {
    $('pt-h').textContent = 'Cargando planes…';
    try {
      const [tax, evs, srcs, cuts, audit] = await Promise.all([loadTaxonomy(), loadEvents(), loadSources(), loadClosures(), loadAudit().catch(() => null)]);
      const last = srcs.map((s) => s.last_run_at).filter(Boolean).sort().pop();
      const meta = {
        eventos: evs.length,
        fuentes: srcs,
        multifuente: evs.filter((e) => e.fuentes.length > 1).length,
        sin_categoria: evs.filter((e) => !e.cats.length || (e.cats.length === 1 && e.cats[0] === 'sin_categoria')).length,
        generado: last ? new Date(last).toLocaleString('es-ES', { dateStyle: 'long', timeStyle: 'short' }) : 'sin datos'
      };
      app = startApp({ eventos: evs, meta, closures: cuts, audit }, tax, sync);
      wireAccount();
    } catch (e) {
      console.error(e);
      $('pt-h').textContent = 'No se han podido cargar los planes';
      $('pt-lead').textContent = 'Revisa tu conexión y recarga la página.';
    }
  };
})();
