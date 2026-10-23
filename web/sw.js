// Limón · service worker: abre al instante y sin conexión, y recibe los avisos push.
// Estrategia: la página y los datos, primero la red y si falla la copia; los ficheros propios, la copia y se renueva en segundo plano;
// las librerías externas (con versión fija), la copia. Los mapas, el tiempo y las funciones del servidor no se guardan.
const VERSION = 'limon-v3';  // v3: ficheros con ?v= de versión; se descartan las copias antiguas
const SHELL = ['./', 'index.html', 'data.js', 'config.js', 'limon.svg', 'limon-180.png', 'limon-192.png', 'manifest.webmanifest'];
const CDN = /^https:\/\/(cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com|fonts\.googleapis\.com|fonts\.gstatic\.com)\//;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

async function networkFirst(req, fallbackUrl) {
  const c = await caches.open(VERSION);
  try {
    const r = await fetch(req);
    if (r && r.ok) c.put(req, r.clone());
    return r;
  } catch (e) {
    return (await c.match(req)) || (fallbackUrl && (await c.match(fallbackUrl))) || Response.error();
  }
}
async function staleWhileRevalidate(req) {
  const c = await caches.open(VERSION), hit = await c.match(req);
  const fresh = fetch(req).then((r) => { if (r && r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
  return hit || fresh;
}
async function cacheFirst(req) {
  const c = await caches.open(VERSION), hit = await c.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r && r.ok) c.put(req, r.clone());  // solo respuestas verificables (CORS), nunca opacas
  return r;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (req.mode === 'navigate') { e.respondWith(networkFirst(req, 'index.html')); return; }
  if (u.origin === self.location.origin) {
    // código (html, js): siempre la última versión si hay red, para que la página y su código no se desfasen
    e.respondWith(/\.(js|html|webmanifest)$|\/$/.test(u.pathname) ? networkFirst(req) : staleWhileRevalidate(req)); return;
  }
  // datos de Supabase (eventos, tu agenda…): para poder abrir la app sin cobertura con lo último que se cargó
  if (/\.supabase\.co$/.test(u.hostname) && u.pathname.startsWith('/rest/v1/')) { e.respondWith(networkFirst(req)); return; }
  if (CDN.test(req.url)) { e.respondWith(cacheFirst(req)); return; }
});

// ---------- avisos push
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { body: e.data && e.data.text() }; }
  const title = d.title || 'Limón';
  const opts = { body: d.body || '', icon: 'limon-192.png', badge: 'limon-badge-96.png', tag: d.tag || undefined, renotify: !!d.tag, data: { url: d.url || './' }, lang: 'es' };
  e.waitUntil(Promise.all([
    self.registration.showNotification(title, opts),
    self.navigator && self.navigator.setAppBadge ? self.navigator.setAppBadge().catch(() => { }) : Promise.resolve()
  ]));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || './', self.location.origin).href;
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if (new URL(c.url).origin === self.location.origin) { await c.focus(); if ('navigate' in c) await c.navigate(url).catch(() => { }); return; }
    }
    await self.clients.openWindow(url);
  })());
});
// si el navegador renueva la suscripción, la app la vuelve a registrar al abrirse
self.addEventListener('pushsubscriptionchange', () => { });
