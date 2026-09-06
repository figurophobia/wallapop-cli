# Subir múltiples fotos por API pura

Fecha: 2026-09-03. Objetivo: publicar artículos con **varias fotos** por API pura
(`Wallapop.publish()`), replicando el flujo del navegador.

- ✅ **RESUELTO (2026-09-03 tarde)**: `Wallapop.publish(photo_paths=[...])` ya
  publica con **N fotos** por API pura, sin navegador, vía `picture2`.
  Ver sección "✅ RESUELTO" más abajo.

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

## Lo que NO funciona (y por qué — resuelto en la sección RESUELTO)
- `POST /api/v3/items/<id>/picture2` daba **400 SIEMPRE** por HTTP plano porque el
  body no era el correcto: faltaba el campo `order` (o la imagen iba vacía =
  `INVALID_PICTURE_SIZE`). Con `image` completa + `order` + headers de la app →
  **200**. (Los 400 previos de `fetch`/urllib eran por body/headers incompletos,
  no por CORS/preflight.)
- El **draft-upload path** (steps init+title + `_upload_photo` a
  `/api/v3/upload/<id>/pictures` con field `image`, 204) crea el item con **1
  sola imagen** — el draft **no** arrastra las fotos al anuncio. Las extras hay
  que enlazarlas con `picture2` después de crear el item. (Ya no se usa.)

## Items de prueba (creados, el usuario los borró a mano)
nz08e9myy36o, 8z887v1351z3, 9jdwyd73v96k, e65qnmyxdg6o, wzyrkend3x65,
ejkmy2998qzx, pzpk0812y5j3 (browser, 3 fotos), 9jdwydreql6k (API: 1→2→3 pics),
w67v08lrv86x (API: 1→2 pics sin draft), 36e5y9ro4q6d (API integrada: 3 pics),
w67v21ey386x (browser capture).

## Recomendación operativa
- `Wallapop.publish(photo_paths=[...])` por API pura ya publica con **N fotos**
  (cover en POST /items + picture2 por extra). Es el modo recomendado.
- El wizard del navegador sigue disponible con `--mode browser`, pero ya no es
  necesario para multi-foto.

---

## ✅ RESUELTO (2026-09-03 tarde): MULTI-FOTO funciona por API pura con `picture2`

**Hallazgo clave**: el endpoint `POST /api/v3/items/<id>/picture2` SÍ se puede
replicar por HTTP plano con el cuerpo **correcto**:

- **body**: `multipart/form-data` con dos campos:
  - `image` = **bytes JPEG completos** de la foto extra (NO vacío).
  - `order` = **string** con la posición (p.ej. `"2"`, `"3"`).
- **headers**: las de la app (`Authorization: Bearer <token>`,
  `Origin: https://es.wallapop.com`, `x-deviceos: 0`, `x-deviceid`, `x-appversion`,
  `Accept: application/json, ...`, `Content-Type: multipart/form-data; boundary=...`).
- **respuesta 200** `text/plain` con un **mini-id** de estilo `k3jfhh2122d`
  (p.ej. `4z4ykv5508vz`, `ejkxr4yyedq6`).
- Tras cada picture2 200, `GET /items/<id>` confirma que el anuncio **gana una
  imagen más** (1 → 2 → 3 ...).

### Flujo completo que funciona
1. `POST /api/v3/items` (multipart `image` cover + `item` JSON) → 200, item con 1 foto.
2. Por **cada foto extra**, `POST /api/v3/items/<id>/picture2` (image completa +
   `order` progresivo) → 200. Repetir N-1 veces.

### Por qué fallaban los intentos previos
- Variante "Chromium 291 B" (imagen **vacía** + `order`) → **400
  `INVALID_PICTURE_SIZE`**: la imagen debe ir **completa** (bytes reales).
- Imagen completa **sin** `order` → 400. `order` es **obligatorio**.
- Solo `order` sin image → 500.
- Hay que usar un **token fresco** (si caduca da `401 ACCESS_TOKEN_EXPIRED`;
  refrescar con `refresh_web()` / `refresh_web_session()` antes).
- Los headers de la app (`x-deviceos`, etc.) los añade ya `_headers(auth=True)`.

### Verificación (item real, borrado a mano por el usuario)
`9jdwydreql6k`: tras POST /items → 1 imagen; tras picture2 order=2 → 2 imágenes;
tras picture2 order=3 → 3 imágenes. ids devueltos: `4z4ykv5508vz`, `ejkxr4yyedq6`.

### Integración (HECHA, 2026-09-03)
`Wallapop.publish(photo_paths=[...])` crea el item (POST /items con la cover) y
luego llama `_add_picture()` (picture2) por cada foto extra. Verificado en vivo:
`36e5y9ro4q6d` salió con 3 imágenes. picture2 funciona **sin** subir las extras
al draft (el paso `_upload_photo` al draft ya no se usa y se eliminó).

## ⚠️ Verificado (2026-09-03 tarde): POST /items exige al menos `color`
- `POST /api/v3/items` con `brand=""`,`model=""`,`color=""` → **400
  `INVALID_ATTRIBUTE`** (verificado en cat. 10088 y 12463).
- No depende del título: el backend **no** autocompleta marca/modelo/color del
  título en el POST final (el "suggested-item-data" que deduce esos valores del
  título lo hace el **frontend** de la web antes de mandar el POST).
- Basta con que **`color` no esté vacío** para que dé 200 (brand/model pueden ir
  vacíos). Con `color` vacío falla aunque brand/model estén rellenos.
- **El valor de `color` va en inglés** en el POST (`blue`), aunque la web lo
  muestre en español ("Azul"). `azul` → 400, `blue` → 200 (verificado 2026-09-06).
  Los valores válidos son los del form de la web.
- Implicación: `Wallapop.publish()` con los tres vacíos siempre fallará a día de
  hoy; la web no lo nota porque su wizard rellena `color` por ti. Para usar la
  API pura hay que pasar un `color` (o `--color` en el CLI).

## ✅ Editar anuncio publicado y gestionar fotos (verificado 2026-09-06)
- Editar cualquier atributo (incl. cambiar fotos): `PUT /api/v3/items/<id>`, headers
  con `Accept: application/vnd.upload-v2+json`, body completo con `pictures`.
  Todos los atributos prueban 204. Receta completa en `docs/ENDPOINTS.md`.
- Añadir fotos: `picture2` (misma receta que arriba). Cambiar la principal:
  reordenar `pictures` en el PUT (`order: 0` = portada).

## Código en el repo
- `wallapoplib/client.py` → `Wallapop.publish(photo_paths)`, `_add_picture`
  (picture2: image completa + `order`), `_cost_configuration_id()`, `_request`, `_authed`.
- `wallapoplib/publish.py` → publish browser (Playwright, N fotos OK; ya no es
  necesario para multi-foto).
- `wallapoplib/cli/commands.py` → `_pick_photos` (hasta 10), `publish` (API por
  defecto).
- `wallapoplib/http.py` → `http_request_raw()` (bodies binarios multipart).
- `tests/test_client.py` → tests de publish incl. multi-foto (POST /items +
  picture2).

## Evidencias crudas (research/)
- `publish_INSTRUMENTADO.py` — publish.py instrumentado (log a /tmp/opencode/flow.log).
- `pic2_test.py` — prueba picture2 vía fetch JS en navegador.
- `diag.py` — publica item y prueba picture2 + estructura.
- `upload_body.bin` — body real del upload al draft (cover, 119748 B).
- `flow_captured.log` — flujo capturado del publish browser multi-foto.
- `README.md` — resumen de investigación (para reanudar en otro chat).
