# Limón (antes Bureo)

Los planes de tu ciudad que van contigo. Limón (limonplanes.com) reúne la agenda de Madrid (Ayuntamiento, esMadrid y grandes recintos),
une los duplicados, clasifica cada evento en 152 categorías y te enseña lo que encaja con tus intereses.

Nació en 2013 como **Everent**, un proyecto fin de carrera.

## Partes

- `web/`: la aplicación web (HTML y JavaScript, sin compilación). Lee los eventos de Supabase y guarda
  intereses y agenda en la cuenta de cada usuario. Se publica con GitHub Pages.
- `engine/`: el motor de datos en Python, sin dependencias externas.
  1. `src/fetch_sources.py` descarga la agenda municipal, el sitemap de esMadrid e IFEMA y el Real Jardín Botánico.
  2. `src/fetch_esmadrid.py` descarga solo las fichas de esMadrid nuevas o cambiadas, a una cada 10 s.
  3. `src/pipeline.py` lo unifica, junta sesiones, une duplicados entre fuentes, categoriza y marca los planes únicos.
  4. `src/load_supabase.py` lo carga en Supabase (actualiza por clave, se puede repetir sin duplicar).
- `.github/workflows/nightly.yml`: ejecuta el motor cada noche.

## Secretos del repositorio

En *Settings → Secrets and variables → Actions*:

- `SUPABASE_URL`: la URL del proyecto.
- `SUPABASE_SECRET`: una clave secreta de Supabase (`sb_secret_…`) solo para el motor.
- `SUPABASE_ACCESS_TOKEN`: token personal de Supabase para desplegar funciones y leer los avisos de seguridad.
- `SUPABASE_DB_PASSWORD` (o `SUPABASE_DB_URL`) y `BACKUP_PASSPHRASE`: para la copia de seguridad semanal (ver «Copias de seguridad»).

La clave publicable de `web/config.js` es pública por diseño: las políticas de seguridad por filas solo permiten
leer el catálogo y que cada usuario gestione sus propios datos.

## Licencias de los datos

Contiene información de la agenda del Ayuntamiento de Madrid (datos.madrid.es, CC BY 4.0). De esMadrid y de los
recintos se guarda lo mínimo para mostrar el evento y siempre se enlaza a la ficha original.

## Pruebas antes de publicar

- **`pruebas`** → https://pruebas.limonplanes.com (Cloudflare Pages, carpeta `web`, sin compilación). Lleva una franja roja «PRUEBAS», no aparece en buscadores (`web/_headers`) y no cuenta estadísticas de uso.
- **`main`** → https://limonplanes.com (GitHub Pages). Solo se pasa a `main` lo que se ha probado en `pruebas`.
- Base de datos: la misma para las dos webs (los datos, los correos y los avisos son reales). Los cambios de base de datos solo añaden (tablas, columnas, funciones nuevas), nunca quitan ni renombran, para que la versión publicada siga funcionando.
- Funciones del servidor: lo que se sube a `pruebas` se publica como `limon-…-pruebas`; la web de pruebas las usa si existen y, si no, usa las normales.

## Seguridad

- **Revisión semanal** (`security.yml`): pide a Supabase sus avisos de seguridad y falla si aparece uno nuevo. Los revisados y aceptados están en `.github/security/aceptados.txt`, cada uno con el porqué.
- **Captcha** (Cloudflare Turnstile): la clave pública va en `web/config.js` (`turnstile`); la secreta solo en Supabase → Authentication → Attack Protection. Si se activa en Supabase sin clave en la web, nadie podrá entrar.
- **Web**: política de contenido (CSP) en cada página y huella (SRI) en las librerías externas. Al cambiar de versión una librería hay que recalcular su huella: `npm pack <paquete>@<versión>` y `openssl dgst -sha384 -binary <fichero> | base64`.

## Copias de seguridad

Cada domingo `backup.yml` guarda una copia cifrada de la base de datos durante 90 días (pestaña Actions → «Copia de seguridad» → artefacto). Para restaurar:

```sh
gpg -d limon-AAAA-MM-DD.tgz.gpg > limon.tgz   # pide la frase BACKUP_PASSPHRASE
tar xzf limon.tgz                             # app.dump (esquema public) y cuentas.dump (usuarios)
pg_restore -d "$SUPABASE_DB_URL" --no-owner --data-only cuentas.dump   # primero las cuentas
pg_restore -d "$SUPABASE_DB_URL" --no-owner --clean --if-exists app.dump
```
