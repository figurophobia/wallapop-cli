# Publicar artículos en Wallapop (publish item) — investigación y diseño

Fecha: 2026-08-31
Estado: EN INVESTIGACIÓN — flujo API confirmado hasta `listing`; pendiente de
capturar `shipping` + el POST final y de implementar.

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

**7. Falta el paso final (Publicar)**
El usuario tiene TODO listo en el wizard y solo le queda pulsar "Publicar". Ese
último paso (el POST que crea el anuncio definitivo, su URL y su body) aún no
está capturado. Es la pieza que cierra el flujo para la implementación por API.

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

1. **El paso final de "Publicar"** — el POST que crea el anuncio definitivo.
   El builder exacto (`POST /api/v3/items`, multipart, con `attributes`,
   `category_leaf_id`, `location`, `delivery`, `upload_id`) se extrajo del JS
   (`chunk-B5DKKJDO.js`, fn `ce`), pero el body real que el navegador manda aún
   NO está capturado (el endpoint `/items/{id}/publish` da 404; el real es
   `POST /api/v3/items`).
2. **Los bodies de `shipping` y `listing→publish` de `/steps`** — las variantes
   probadas por API devuelven **400 con body vacío** (`price` como objeto 400,
   `condition:"used_good"` 400, `shipping:{enabled,size,weight}` 400). Falta capturar
   el cuerpo exacto desde el navegador real pulsando "Publicar".
3. **`/api/v3/items/upload/components`** — el request observado da 400 por API con
   el body que sí acepta el navegador; el esquema exacto que responde (200) no se
   ha podido volcar porque la respuesta contiene BDUI y falta replicar el estado
   completo del draft al invocarlo.
4. **El flujo UI en headless** queda bloqueado por los widgets custom de Wallapop:
   el CTA "Continuar/Publicar" es un web component `walla-button` cuyo texto vive
   en shadow DOM, y el form de categorías/sidebar interfiere con el avance.

El draft de `listing` correcto (200, no avanza) es:
```json
{
  "summary": "Nintendo 3DS XL Azul Yveltal Edicion Pokemon",
  "description": "Nintendo 3DS XL en buen estado. Edicion Pokemon Yveltal azul. Con cargador. Envio disponible.",
  "price_amount": "400",
  "category_id": "10088",
  "brand": "Nintendo",
  "model": "Nintendo 3DS XL",
  "condition": "good",
  "pictures": []
}
```

## Plan de acción

### Fase A — Capturar el flujo real con Playwright (sin publicar todavía)
- Autenticar con las cookies reales del usuario (`~/.config/wallapop_cli/session.json`).
- Ir a `https://es.wallapop.com/app/catalog/upload/consumer-goods`.
- Resolver los selectores reales del wizard (el clic por texto en "Algo que ya
  no necesito" no respondió; hay que inspeccionar el DOM/radio/label correcto).
- Rellenar: título → fotos → categoría → marca/modelo/color → descripción →
  estado → precio → envío/peso → ubicación.
- **Interceptar TODAS las peticiones** (`POST /api/v3/steps`, upload de fotos,
  suggested-item-data, y el paso final) guardando método + url + **body**.
- Detener JUSTO antes de pulsar "Publicar" para no subir el anuncio real aún.

### Fase B — Documentar los endpoints
- Volcar a `docs/ENDPOINTS.md` la sección "Publicar artículos" con cada llamada,
  su body y su respuesta (el flujo de steps completo).

### Fase C — Implementar `Wallapop.publish()` por API pura
- Método que recorra los steps construyendo el borrador, suba las fotos y haga
  el alta final, siguiendo el patrón `_request()` de la lib.
- Subcomando CLI `publish` con los campos como flags.

### Fase D — (opcional, con permiso explícito) Publicar la 3DS real
- Usar las 23 imágenes de `/home/deivi/Descargas/Blue Yvetal/` (solo 10 por
  anuncio según `pictures_item.max_number_of_pictures: 10`).
- Precio 400 €, buen estado, envío activado.
- Esto es una acción **irreversible** sobre la cuenta; requiere confirmación
  explícita del usuario en el momento de hacerlo.

## Notas / decisiones abiertas
- Carpeta de fotos: `/home/deivi/Descargas/Blue Yvetal/` (23 JPG, ~1.2-1.9 MB).
- El anuncio admite máx. 10 fotos → habrá que elegir 10 o las "mejores".
- El usuario autorizó (mensajes previos): investigar los endpoints Y publicar el
  item vía Playwright. Antes de la publicación definitiva conviene confirmar el
  contenido exacto (título, descripción, atributos) a subir.
- La categoría sugerida por el API para "Nintendo 3DS XL" es
  `category_leaf_id: 10088` / `root_category_id: 24200`.
