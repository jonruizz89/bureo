// Convierte las diapositivas HTML de /redes/<cuenta>/ (generadas por site.py) en PNG de 1080×1350
// y escribe una página sin indexar por cuenta para descargar las imágenes y copiar el texto del post.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const WEB = path.resolve(process.argv[2] || '../web');
const DIR = path.join(WEB, 'redes');
const lista = JSON.parse(fs.readFileSync(path.join(DIR, 'redes.json'), 'utf8'));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1080, height: 1350 } });
for (const r of lista) {
  const dir = path.join(DIR, r.cuenta);
  for (let i = 0; i < r.slides; i++) {
    const html = path.join(dir, `s${i}.html`);
    await page.goto(pathToFileURL(html).href, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(dir, `s${i}.png`) });
    if (i === 0) {  // en el reel la portada no dice «Desliza»: pasa sola
      await page.evaluate(() => { const n = document.querySelector('.n'); if (n) n.style.visibility = 'hidden'; });
      await page.screenshot({ path: path.join(dir, 'p0.png') });
    }
    fs.unlinkSync(html);
  }
  // reel 9:16: las mismas tarjetas con un margen del color de fondo arriba y abajo (zona segura de la interfaz de Reels)
  let reel = false;
  try {
    const rp = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
    const lista_f = [];
    for (let i = 0; i < r.slides; i++) {
      const bg = (i === 0 || i === r.slides - 1) ? '#FFD43B' : '#FFF8DC';
      const png = fs.readFileSync(path.join(dir, i === 0 ? 'p0.png' : `s${i}.png`)).toString('base64');
      await rp.setContent(`<html><body style="margin:0;width:1080px;height:1920px;background:${bg};display:flex;align-items:center;justify-content:center"><img src="data:image/png;base64,${png}" style="width:1080px;height:1350px;display:block"></body></html>`);
      const f = path.join(dir, `r${i}.png`);
      await rp.screenshot({ path: f });
      lista_f.push(`file '${f}'\nduration ${i === 0 ? 2.5 : i === r.slides - 1 ? 3 : 2.2}`);
    }
    await rp.close();
    const txt = path.join(dir, 'reel.txt');
    fs.writeFileSync(txt, lista_f.join('\n') + `\nfile '${path.join(dir, `r${r.slides - 1}.png`)}'\n`);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', txt, '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-movflags', '+faststart', path.join(dir, 'reel.mp4')]);
    for (let i = 0; i < r.slides; i++) fs.unlinkSync(path.join(dir, `r${i}.png`));
    fs.unlinkSync(path.join(dir, 'p0.png'));
    fs.unlinkSync(txt);
    reel = true;
  } catch (e) { console.log(`${r.cuenta}: sin reel (${e.message.slice(0, 120)})`); }
  const imgs = Array.from({ length: r.slides }, (_, i) =>
    `<a href="s${i}.png" download="limon-${r.cuenta}-${i + 1}.png"><img src="s${i}.png" alt="Imagen ${i + 1}" loading="lazy"></a>`).join('');
  fs.writeFileSync(path.join(dir, 'index.html'), `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Post de ${esc(r.nombre)} · ${esc(r.rango)}</title>
<style>body{font-family:system-ui,sans-serif;margin:0 auto;max-width:720px;padding:16px;background:#FFF8DC;color:#231D02}
h1{font-size:22px}.g{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:8px}
.g a{flex:0 0 78%;scroll-snap-align:start}.g img{width:100%;border-radius:12px;display:block}
textarea{width:100%;box-sizing:border-box;height:340px;font:14px/1.4 system-ui;border-radius:10px;padding:10px}
button{font:600 16px system-ui;background:#231D02;color:#FFD43B;border:0;border-radius:999px;padding:12px 20px;margin:10px 0}p{font-size:14px}</style></head>
<body><h1>🍋 Este finde en ${esc(r.nombre)} · ${esc(r.rango)}</h1>
<p>Toca cada imagen para descargarla (en el móvil: mantén pulsado → Guardar imagen). Súbelas en este orden como carrusel.</p>
<div class="g">${imgs}</div>
${reel ? `<h2>Reel</h2><p>Vídeo vertical con las mismas tarjetas. Al subirlo, añade una canción desde la biblioteca de Instagram y usa el mismo texto.</p><video src="reel.mp4" controls playsinline muted style="width:60%;max-width:300px;border-radius:12px;display:block"></video><p><a href="reel.mp4" download="limon-${r.cuenta}-reel.mp4">Descargar el reel</a></p>` : ''}
<h2>Texto del post</h2><textarea id="t" readonly>${esc(r.texto)}</textarea>
<button id="c">Copiar texto</button>
<p>Enlace para la bio o las historias: <a href="${esc(r.enlace)}">${esc(r.enlace)}</a></p>
<script>document.getElementById('c').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('t').value)}catch(e){document.getElementById('t').select();document.execCommand('copy')}document.getElementById('c').textContent='¡Copiado!'}</script>
</body></html>`);
  console.log(`${r.cuenta}: ${r.slides} imágenes`);
}
const links = lista.map(r => `<li><a href="${r.cuenta}/">${esc(r.nombre)}</a> · ${esc(r.rango)}</li>`).join('');
fs.writeFileSync(path.join(DIR, 'index.html'), `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Posts de redes · Limón</title><style>body{font-family:system-ui,sans-serif;max-width:720px;margin:auto;padding:16px;background:#FFF8DC;color:#231D02;font-size:18px}li{margin:10px 0}</style></head><body><h1>🍋 Posts de esta semana</h1><ul>${links}</ul></body></html>`);
fs.unlinkSync(path.join(DIR, 'redes.json'));
await browser.close();
