# Subir múltiples fotos por API pura: estado de la investigación

Fecha: 2026-09-03. Objetivo: publicar artículos con **varias fotos** por API pura
(`Wallapop.publish()`), replicando el flujo del navegador. estados:

- ✅ El **navegador** (Playwright → wizard) publica con N fotos sin problema.
- ⚠️ La **API pura** (`requests`/`urllib`) publica el item con **1 sola foto**.
  La asociación multi-foto por API pura **aún NO está resuelta**.

Esto es una nota viva: los hallazgos van fechados; la evidencias crudas (bodies
capturados, scripts) viven en este directorio cuando se mencionan.

## Cómo publica el navegador una foto por API pura (resuelto)
- `Wallapop.publish(photo_paths=[...], ...)` → `POST /api/v3/items` multipart
  (`image` + JSON `item`), headers `Accept: application/vnd.upload-v2+json`,
  `x-deviceos: 0`, `Origin/Referer: https://es.wallapop.com`.
  Respuesta 200 `{"id":"<item id>","flags":{}}`.
- Ref: `docs/ENDPOINTS.md` y `wallapoplib/client.py`.

## Flujo REAL del navegador para MULTI-foto (capturado)
Secuencia observada en un publish con 3 fotos (Chromium Playwright,
`set_input_files([...])` de 3 archivos):
1. `POST /api/v3/steps`  init → `{"mode":{"action":"upload","id":"<upload_id>"}}`
2. `POST /api/v3/steps`  title → `current_step:"title", draft:{"summary":"..."}`
3. `POST /api/v3/upload/<upload_id>/pictures` → sube la **cover** (1 foto
   procesada, ~119 KB, field `image`), respuesta **204**
4. `POST /api/v3/steps`  photo → category → loading
5. `POST /api/v3/items/upload/components` ×2
6. `POST /api/v3/items` → crea el item (la cover ya viene del draft upload)
7. `POST /api/v3/items/<id>/picture2` ×(N-1) → enlaza las fotos restantes

Evidencia: `research/flow_captured.log`, `research/upload_body.bin`.

## El endpoint `picture2` (clave, y CONFUSO)
Encarga de enlazar/subir las fotos adicionales al item ya creado. Comportamiento
observado dependiente del cliente:
- **Chromium / Playwright** (`set_input_files`) → body de **291 B**:
  `image` **VACÍO** + `order:"2"` → 200.
- **Firefox / HAR manual** → body con la `image` **COMPLETA** (bytes JPEG) +
  filename → 200, devuelve un id de foto (text/plain, p.ej. `pj9key24382z`).

Hay DOS interpretaciones posibles (referencia del `order` ya subido vs subida
directa). En ambos casos 200.

## Lo que NO funciona (bloqueo actual)
- `POST /api/v3/items/<id>/picture2` por HTTP plano (`requests`/`urllib`) → **400
  SIEMPRE**, replicando headers exactas de Firefox (Accept, Accept-Language,
  Origin, Referer, User-Agent FF, Content-Type multipart) e incluso la imagen
  completa. También 400 desde `fetch` JS con `FormData` dentro del navegador
  (`page.evaluate`). Solo responde bien dentro del flujo real de la app web
  (preflight CORS + auth/device implícitos).
- El **draft-upload path** (steps init+title + `_upload_photo` a
  `/api/v3/upload/<id>/pictures` con field `image`, 204) crea el item con **1
  sola imagen** — **no** asocia las fotos extra aunque se suban al draft.

## Items usados en alas (creados, el usuario los borró a mano)
nz08e9myy36o, 8z887v1351z3, 9jdwyd73v96k, e65qnmyxdg6o, wzyrkend3x65,
ejkmy2998qzx, pzpk0812y5j3 (éste, browser, con 3 fotos → 3 imágenes reales).

## Recomendación operativa para hoy
- Para publicaciones que requieran más de 1 foto **sin reiventar la rueda**: usar
  el wizard del navegador (`wallapop publish` con Playwright), que ya funciona
  con N fotos.
- `Wallapop.publish()` por API pura sigue siendo válido para artículos de **1
  foto** y sirve de base para seguir investigando el multi-foto.

## Pistas para el próximo intento
1. Capturar el body EXACTO del `POST /api/v3/items` del navegador con N fotos
   (por ahora solo se capturó para 1 foto, en ENDPOINTS.md) y el body del
   `picture2` REAL de la app (no el de 291 B de Chromium sino el completo de
   Firefox). La diferencia Firefox vs Chromium puede ser la clave.
2. Verificar si `picture2` requiere el preflight OPTIONS CORS con
   `Access-Control-Request-Headers: authorization` y algún header de sesión/
   device (`x-deviceid`, cookie JSESSIONID, etc.) aún no replicado.
3. En el draft upload, la cover va al `upload_id`. Probar `POST /api/v3/items`
   **sin** el campo `image` cuando el upload_id del draft ya contiene las fotos
   — tal vez el POST /items del navegador no re-sube la cover sino que solo la
   referencia, y nuestra réplica con `image` siempre pisa el draft.

## Código en el repo
- `wallapoplib/client.py` → `Wallapop.publish(photo_paths)`, `_upload_photo`
  (draft path, field `image`), `_cost_configuration_id()`, `_request`, `_authed`.
- `wallapoplib/publish.py` → publish browser (Playwright, N fotos OK).
- `wallapoplib/cli/commands.py` → `_pick_photos` (hasta 10), `publish` (API por
  defecto).
- `wallapoplib/http.py` → `http_request_raw()` (bodies binarios multipart).
- `tests/test_client.py` → tests de publish incl. multi-foto (steps + upload +
  POST /items).

## Evidencias crudas (research/)
- `publish_INSTRUMENTADO.py` — publish.py instrumentado (log a /tmp/opencode/flow.log).
- `pic2_test.py` — prueba picture2 vía fetch JS en navegador.
- `diag.py` — publica item y prueba picture2 + estructura.
- `upload_body.bin` — body real del upload al draft (cover, 119748 B).
- `flow_captured.log` — flujo capturado del publish browser multi-foto.
- `README.md` — resumen de investigación (para reanudar en otro chat).
