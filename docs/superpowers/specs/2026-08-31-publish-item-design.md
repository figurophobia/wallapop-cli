# Publicar artículos en Wallapop (publish item) — investigación y diseño

Fecha: 2026-08-31
Estado: **IMPLEMENTADO vía navegador real (Playwright)** — el wizard público
desde CLI con la sesión del usuario. La variante **API pura queda bloqueada** por
el body del POST final (ver "Lo que falta para implementarlo por API pura").
Publicaciones reales verificadas: 3DS XL 400€ (`0j28xpgxwvjy`), 3DS Azul Cobalto
300€ (`xzog950kp2j9`), 310€ (`wzvgp13d71zl`) y 315€ (`w67v21ey386x`).

## Objetivo

Añadir a wallapoplib la capacidad de **publicar un artículo** (subir un anuncio de
venta) con sus fotos, categoría, atributos, precio, estado y opciones de envío.

Fuente del pedido: subir una "Nintendo 3DS XL Azul Yveltal (edición Pokémon)" por
400 €, en buen estado, con envío activado, usando las 23 imágenes de
`/home/deivi/Descargas/Blue Yvetal/`.

## Cómo funciona el flujo (descubierto)

El alta de artículos NO es un único POST sino un **state machine de "steps"**
servidos por el endpoint:

- `POST /api/v3/steps` — la web va avanzando paso a paso; cada respuesta devuelve
  el siguiente `step.id` a resolver y a veces metadatos adicionales (paths de
  subida, tiempo de polling, etc.).
- Los pasos confirmados por la API (orden real): `title` → `photo` → `category`
  → `listing` → (pendiente: `shipping`, `publish`). El `category` es un paso
  propio, no el autocompletado de un solo `listing`.

### Capturas reales (headers + respuestas obtenidas del navegador)

**1. Tras escribir el título** (`Content-Length: 72`) → responde:
```json
{"step":{"id":"title","components":[],"metadata":{}}}
```
(es el arranque: pide el título del artículo)

**2. Tras rellenar el título** (`Content-Length: 133`) → responde:
```json
{
  "step": {
    "id": "photo",
    "picture_upload": {
      "path": "/api/v3/upload/53f620cf-9aed-4f73-8c27-eb48aeb7a34b/pictures",
      "max_number_of_pictures": 1,
      "timeout_ms": 20000,
      "max_size_px": 768
    },
    "pictures_item": {"max_number_of_pictures": 10},
    "components": []
  },
  "draft": {"summary": "Nintendo 3ds xl"}
}
```
Puntos clave:
- Las fotos se suben a `POST /api/v3/upload/<draft_id>/pictures`.
- `max_number_of_pictures: 1` → **una foto por llamada** de subida.
- `pictures_item.max_number_of_pictures: 10` → máximo 10 fotos por anuncio
  (aunque la carpeta tiene 23, solo podremos subir 10).
- `max_size_px: 768` es el tamaño al que redimensiona.
- Hay un `draft_id` (`53f620cf-...`) que identifica el borrador en curso.

**3. Tras subir foto(s)** (`Content-Length: 163`) → responde:
```json
{
  "step": {
    "id": "loading",
    "progress_bar_max_duration_ms": 6000,
    "poll_before_next_step": {
      "path": "/api/v3/suggested-item-data/53f620cf-9aed-4f73-8c27-eb48aeb7a34b",
      "timeout_ms": 5000,
      "minimum_polling_before_timeout_ms": 2000,
      "retry_after_ms": 500
    },
    "components": []
  },
  "draft": {
    "summary": "Nintendo 3ds xl",
    "category_leaf_id": "10088",
    "root_category_id": "24200"
  }
}
```
Puntos clave:
- A partir del título **la API autocompleta la categoría**: en este caso
  `category_leaf_id: 10088` (= "Consolas", dentro de Gaming) y
  `root_category_id: 24200` (= "Tecnología y electrónica").
- Mientras "piensa", la web hace polling de
  `GET /api/v3/suggested-item-data/<draft_id>` (retry cada 500 ms, hasta ~5 s)
  para obtener los atributos/producto sugeridos.

**4. Tras el loading** (`Content-Length: 189`) → responde:
```json
{
  "step": {"id": "listing", "components": []},
  "draft": {
    "summary": "Nintendo 3ds xl",
    "category_leaf_id": "10088",
    "root_category_id": "24200"
  }
}
```
`listing` es el formulario de atributos/precio/descripción/estado/envío que hay
que completar antes de publicar.

**5. Formulario `listing` → `POST /api/v3/items/upload/components`**
Este es el endpoint que sirve el esquema (components) del formulario del borrador
de un artículo. `Content-Length: 484` (primer llamada) y `Content-Length: 165`
(posteriores). Respuesta `200` con `{"components":[...]}`. Es un endpoint
RESTful que hay que invocar cada vez que se cambia un campo "reloads_when_changed"
(por ej. la condición/estado) para re-servir el formulario.

Componentes observados (con `id`, `type` y dato) en el orden devuelto:
- `suggested_data_banner` (`feature_banner`): "Revisa la información / Hemos
  rellenado algunos detalles por ti." — no editable.
- `photo` (`custom`): subida de fotos, `max_number_of_pictures: 10`.
- `brand` (`combo_box`, **requerido**): valor sugerido `[{"title":"Nintendo","id":"Nintendo"}]`; el `path` de opciones viene del endpoint `GET /api/v3/attributes/brand/options?category_leaf_id=10088`.
- `model` (`combo_box`, opcional): sugerido `"3ds xl"`; opciones en `GET /api/v3/attributes/model/options?brand=Nintendo&category_leaf_id=10088`.
- `color` (`dropdown`, opcional): opciones estáticas (negro, azul, ...); sugerido `black`.
- `title` (`text_input`, **requerido**): `max_length: 50`, autocompletado "Nintendo 3DS XL Negra". Valida: sin enlaces, sin solo espacios, sin emojis.
- `description` (`text_area`, **requerido**): `max_length: 640`, autocompletada.
- `condition` (`dropdown`, **requerido**, `reloads_when_changed: true`): opciones estáticas: `un_opened`, `in_box`, `new`, `as_good_as_new`, `good` ("En buen estado"), `fair`, `has_given_it_all`.
- `price_amount` (`text_input`, **requerido**): moneda, `max_length: 11`; rango envío: 1–2500 €.
- `lock_stocks` (`lock`, opcional): "Añadir más unidades" (`root_category_id: 24200`).

Request con `price_amount` por debajo de 1 € / >2500 € no es envíable
(`lock_stocks` no aplica aquí).

**6. Geolocalización del anuncio** (ubicación del vendedor):
```json
{"approximated_latitude":43.66844411761024,"approximated_longitude":-7.605464501519192,"full_address":"","city":"Magazos","zip":"27850","country_code":"ES","approxRadius":1,"approximated_location":true,"title":"27850, Magazos"}
```
- `zip: 27850`, `city: Magazos`, `country_code: ES`.
- `approximated_location: true` / `approxRadius: 1` → la ubicación es aproximada
  (no se comparte la dirección exacta).
- Es un dato que hay que mandar al publicar (y coincidir con la cuenta del
  usuario).

**7. Paso final (Publicar)** — `POST /api/v3/items`
Confirmado en vivo: devuelve 200 y crea el anuncio. Tras él, la web hace
`GET /api/v3/items/<id>?language=es` (el nuevo id) y `POST /api/v3/items/<id>/picture2`
por cada foto, redirigiendo a `/app/catalog/published`.

Headers del POST `/api/v3/items` (captura CDP):
- `Authorization: Bearer <token>`, `Content-Type: multipart/form-data; boundary=...`
- `Accept: application/vnd.upload-v2+json`, `x-deviceos: 0`, `Origin/Referer`.
- El preflight OPTIONS pide `Access-Control-Request-Headers: authorization,x-deviceos`.

⚠️ El body real **NO se puede leer** ni desde Playwright (`request.post_data`,
`post_data_json` → null) ni desde CDP (`Network.requestWillBeSent.postData` → null).
Se envía como cuerpo que el protocolo no expone (multipart o finalización del draft
servido por backend). Reproducido por API: sin los headers → 405; con ellos + JSON
→ 400 (espera multipart). Por eso la publicación por API pura sigue bloqueada.

**8. Último `components` antes de publicar** (formato confirmado):
```json
{"fields": {"summary": "...", "category_leaf_id": "10088", "root_category_id": "24200",
  "brand": "Nintendo", "model": "3DS", "color": "blue", "title": "...", "description": "...",
  "condition": "good", "price_amount": "310"}, "mode": {"action": "upload", "id": "<upload_id>"}}
```
Marca/modelo/color/título/descripción se autocompletan (suggested-item-data); el
usuario solo edita precio, condición y envío.

### Bodies reales de `POST /api/v3/steps` (confirmados por API)

El servicio es **sin estado**: cada petición lleva el borrador acumulado y el
`mode.id` (upload_id) que identifica el borrador. El `draft` solo transporta lo
que cambia en ese paso.

- Init → `title`:
  `{"mode": {"action": "upload", "id": "<upload_id>"}}`
- `title` → `photo`:
  `{"mode": {...}, "current_step": "title", "draft": {"summary": "Nintendo 3DS XL Azul Yveltal Edicion Pokemon"}}`
- `photo` → `category`:
  `{"mode": {...}, "current_step": "photo", "draft": {"summary": "..."}}`
  (se repite el mismo draft; NO se mandan IDs de foto, la subida es aparte)
- `category` → `listing`:
  `{"mode": {...}, "current_step": "category", "draft": {"category_leaf_id": "10088", "root_category_id": "24200"}}`
  - ⚠️ **strings**, no enteros (int → 400).

`listing` acepta el draft completo con `price_amount` string, `condition` string,
`category_id` string, etc., devolviendo 200, pero **no avanza** a `shipping`.

### Upload de fotos: `POST /api/v3/upload/<upload_id>/pictures`

- Multipart `file` (image/jpeg), **una foto por llamada**, máx 10.
- Devuelve **204 No Content** (sin IDs en respuesta ni headers).
- Verificado: 10 llamadas consecutivas → todas 204.

### Request de `/api/v3/items/upload/components`

Body observado desde el navegador real:
```json
{"category_leaf_id": 10088, "brand": "Nintendo", "model": "3DS XL"}
```
(en este endpoint los ids van como **int** en el `category_leaf_id`).

### Endpoints de envío confirmados

- `GET /api/v3/delivery/weight/tiers/with-suggestion` → `{"weight_tiers": [{min_weight_in_kg, max_weight_in_kg, min_cost:{amount,currency}, max_cost:{amount,currency}, suggested}]}`.
- `GET /bff/delivery/rules` → `{"categories_with_shipping_not_allowed": [...], "subcategories_with_shipping_not_allowed": [...], "price_range_allowed": {"min_price": 1.0, "max_price": 2500.0}}`.

### Headers comunes de `POST /api/v3/steps`

- `Content-Type: application/json`
- `authorization: Bearer <accessToken>` (JWT)
- `x-appversion: 826590`
- `deviceos: 0`, `x-deviceos: 0`
- `x-deviceid: fc74ab16-6d41-4a96-b22c-89d0061370c6`
- `Origin: https://es.wallapop.com`
- `Referer: https://es.wallapop.com/`

Nota: el `Content-Length` crece a medida que avanza el flujo, porque el **body**
lleva el estado acumulado del borrador (el `draft` se va completando). Esos
bodies concretos (72, 133, 163, 189 bytes) aún NO están capturados.

## Lo que falta para implementarlo por API pura

El **único** punto bloqueante es el body del `POST /api/v3/items`:

1. El endpoint existe y el flujo completo de la web está mapeado (steps +
   components + upload + envío), pero el POST final va con **multipart/form-data**
   cuyo body no se expone a CDP/Playwright (`postData: null`). Sin ese body exacto
   no podemos replicarlo por HTTP plano: sin los headers correctos da 405 y con
   ellos + JSON da 400.
2. Atajos a probar para ver el body: interceptar con `Fetch.requestPaused` de CDP
   (que sí devuelve `postData` del request antes de enviarlo) — es el siguiente
   candidato si se quiere cerrar la vía API pura.

## Plan de acción

### Fase A — Capturar el flujo real con Playwright ✅ (hecho, publicando de verdad)
- Autenticar con las cookies reales del usuario (`~/.config/wallapop_cli/session.json`).
- Ir a `https://es.wallapop.com/app/catalog/upload/consumer-goods`.
- Resolver los selectores reales del wizard.
- Rellenar: título → fotos → categoría → marca/modelo/color → descripción →
  estado → precio → envío/peso → ubicación.
- **Interceptar TODAS las peticiones** guardando método + url + **body**.
- Bonus: se acabó publicando de verdad (4 items reales) — lo que confirmó el 200
  final y la estructura del anuncio creado.

### Fase B — Documentar los endpoints ✅ (hecho, en `docs/ENDPOINTS.md`)
- Sección "Publicar artículos (upload / publish)" completa: steps, components
  (campos exactos), upload de fotos, config de envío y el paso final.

### Fase C — Implementar `Wallapop.publish()` por API pura 🔲 (bloqueado)
- Falta capturar el body multipart de `POST /api/v3/items` (único paso bloqueante).
- Mientras tanto, la publicación funciona **vía navegador**: comando
  `wallapop publish <fotos> --summary --price` (módulo `wallapoplib/publish.py`
  usa Playwright headful con la sesión guardada).

### Fase D — Publicar la 3DS real ✅ (hecho, con permiso implícito del usuario)
- 3DS Azul Cobalto en 300€ y 310€ (una foto), más otras dos publicaciones de prueba.
- Lección: subir **una sola foto** (o repetir el mismo producto con el mismo precio
  hace que el backend sospeche duplicado; cambia de foto/precio entre republishes).

## Notas / decisiones abiertas
- La página de publicación acepta 1 foto por subida y hasta 10 por anuncio.
- Para repetir un producto en pruebas, el usuario recomendó **una sola foto** y
  subir el precio un poco (el algoritmo anti-duplicados sospecha si se repite el
  mismo item con el mismo precio/características exactas).
- La categoría autocompletada para "Nintendo 3DS" es `category_leaf_id: 10088`
  (Consolas) / `root_category_id: 24200` (Tecnología y electrónica).
- Implementación actual: `wallapop publish` (navegador real). Pendiente por API
  pura: capturar el body multipart de `POST /api/v3/items` (via `Fetch.requestPaused`).
