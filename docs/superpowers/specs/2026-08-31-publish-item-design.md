# Publicar artículos en Wallapop (publish item) — investigación y diseño

Fecha: 2026-08-31
Estado: EN INVESTIGACIÓN — pendiente de capturar el flujo completo y de implementar.

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
- Los pasos observados hasta ahora (en orden): `title` → `photo` → `loading` →
  `listing` → (pendiente: probablemente `category`, `attributes`, `price`,
  `shipping`, `review`, `publish`).

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

1. **El body exacto de cada `POST /api/v3/steps`** (los 72/133/163/189 bytes).
   Lo más fiable: capturarlos con Playwright interceptando la red mientras se
   completa el formulario real (una vez resueltos los selectores del wizard).
2. **Cómo se crea el `draft_id`** (¿`POST /api/v3/steps` inicial? ¿otro endpoint?
   Aparece `53f620cf-...` en paths de upload y suggested-item-data).
3. **El formato del upload** de cada foto a `/api/v3/upload/<draft>/pictures`
   (multipart con `file`? ¿`max_number_of_pictures: 1` por llamada?).
4. **El cuerpo del `suggested-item-data`** (atributos sugeridos).
5. **El formulario `listing`** completo y su esquema de envío/peso/ubicación
   (headers observados: `GET /api/v3/delivery/weight/tiers/with-suggestion`,
   `GET /api.wallapop.com/bff/delivery/rules`).
6. **El paso final de publicación** (último `POST /api/v3/steps` o POST de item).

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
