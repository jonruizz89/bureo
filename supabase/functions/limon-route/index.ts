// Limón · ruta en coche entre dos puntos de una misma ciudad (OSRM), para comprobar cortes en el camino.
// El origen se redondea a ~100 m y la petición sale desde el servidor: OSRM no ve la IP ni la posición exacta del usuario.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
// cualquier punto de España (península, Baleares y Canarias); además, origen y destino a menos de ~60 km
const inSpain = (p: unknown) => Array.isArray(p) && p.length === 2 && p.every((x) => typeof x === 'number' && isFinite(x)) &&
  p[0] > 27.5 && p[0] < 44 && p[1] > -18.5 && p[1] < 4.5;
const near = (a: number[], b: number[]) => Math.abs(a[0] - b[0]) < 0.55 && Math.abs(a[1] - b[1]) < 0.75;

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  try {
    const { from, to } = await req.json();
    if (!inSpain(from) || !inSpain(to) || !near(from, to)) return json({ error: 'Solo calculo rutas dentro de la misma ciudad.' }, 400);
    const f = [Math.round(from[0] * 1000) / 1000, Math.round(from[1] * 1000) / 1000];
    const u = `https://router.project-osrm.org/route/v1/driving/${f[1]},${f[0]};${to[1]},${to[0]}?overview=full&geometries=geojson`;
    const r = await fetch(u, { headers: { 'User-Agent': 'LimonPlanes/1.0 (+https://limonplanes.com)' } });
    if (!r.ok) return json({ error: 'El servicio de rutas no responde.' }, 502);
    const j = await r.json();
    const rt = j.routes && j.routes[0];
    if (!rt) return json({ error: 'No encuentro una ruta.' }, 404);
    return json({ distance: rt.distance, duration: rt.duration, line: rt.geometry.coordinates.map((c: number[]) => [c[1], c[0]]) });
  } catch (_e) {
    return json({ error: 'No he podido calcular la ruta.' }, 500);
  }
});
