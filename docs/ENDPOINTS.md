# Endpoints

Catálogo de todos los endpoints que usa la CLI, cómo se extrajeron y a qué
llamada del código corresponden. **Ninguna llamada necesita navegador**:
todas son HTTP plano vía `wallapoplib/http.py` (`http_request`), con el token
de sesión como `Authorization: Bearer <access_token>`.

## Convenciones

- Base de la API: `https://api.wallapop.com` (`API_BASE = /api/v3`).
- Los endpoints `BFF` (Next.js de la web) no llevan `/api/v3`: viven bajo
  `/bff/...` directamente en la raíz del host.
- `Auth` = requiere `Authorization: Bearer`. La CLI añade además
  `X-DeviceID` si hay cookie `device_id`, y marca la app con
  `Origin: https://es.wallapop.com`, `X-Product: wallapop`,
  `X-AppVersion: 826560`, `X-DeviceOS: 0`.
- Métodos del cliente (en `wallapoplib/client.py`): `_request()` (anónimo) y
  `_authed()` (con auth + refresh automático de la sesión web si `401`);
  `_bff()` hace lo mismo pero bajo la raíz sin `/api/v3`.

### Identificadores de app (constantes de `config.py`)

Definidas en `wallapoplib/config.py`. **No son secretos**: la web las expone en
su bundle JS y las usa cualquier navegador, por eso se pueden versionar.

- `API_BASE` (`https://api.wallapop.com`) y `API_ROOT` (raíz sin `/api/v3`,
  para los endpoints BFF de Next.js).
- `WEB_BASE` (`https://es.wallapop.com`): origen web; va en `Origin`/`Referer`.
- `PUB_KEY` (`pub-c-...`) y `SUB_KEY` (`sub-c-...`): llaves de *publicar* y
  *suscribir* de la app de PubNub de Wallapop (su chat en tiempo real).
  Identifican el espacio de canales, no autentican. Que el publish REST con la
  `pub key` sea abierto es lo que permite mandar el mensaje de chat sin
  navegador.
- `PUB_ORIGIN` (`ps7.pndsn.com`): el broker de PubNub al que se publica.
- `PNSDK` (`PubNub-JS-Web/10.2.6`): cadena de auto-identificación del SDK en
  cada petición.
- `WEB_APP_VERSION` (`8.2656.0`): la build de la web que se simula; el API la
  usa para identificar al cliente (`X-AppVersion: 826560`).

Lo que sí es sensible (tokens y cookies) no vive en el repo: está en
`~/.config/wallapop_cli/{credentials,session}.json`, fuera de git.

**Extracción de endpoints (historias de origen):**

- Los endpoints de búsqueda actuales (`/search/components` + `/search/section`)
  se descubrieron inspeccionando las peticiones XHR de la web en el navegador y
  cruzando con el bundle JS. `/general/search` es el flujo antiguo que la CLI
  mantiene como fallback funcional.
- Los endpoints de **ofertas** se recuperaron del bundle de la app web:
  `https://web-static.wallapop.com/angular/es/chunk-*.js` (los hashes rotan
  con la versión de la app `8.2656.0`). En concretos:
  - `chunk-WF7BPLHU.js` — el servicio de ofertas define las rutas:
    `R="bff/delivery/offer-details"`, `h="api/v3/delivery/buyer/offers"`,
    `P="api/v3/delivery/offers"`, `U = .../{id}/status`,
    `v = .../{id}/offer-price`.
  - `chunk-THOUZVB2.js` — el componente del modal OTS define
    `submitOfferResponse` (mapea a `ACCEPTED`/`DECLINED` sobre `{id}/status`)
    y `submitCounterOffer` (sobre `{id}/offer-price`).
  Se validaron en vivo: un `PATCH status` sobre una oferta ya cerrada devuelve
  `409 invalid offer status` (es decir, el endpoint existe y valida de verdad).
- Los endpoints `bff/messaging/*` son de la SPA Next.js; el inbox devuelve los
  mensajes más recientes **primero** (hay que ordenar por `timestamp`).
- El envío de chat por REST PubNub se extrajo observando a la web: el mensaje
  se publica a `ps7.pndsn.com` con `auth` = token de
  `/instant-messaging/token`, `pnsdk=PubNub-JS-Web/10.2.6`. Acknowledgment
  `[1,"Sent",<timetoken>]`.

---

## Autenticación

### POST `/api/v3/access/login`

Login email+password de la app.

- Body: `{"email": ..., "password": ..., "client_id": "wallapop"}`
- No requiere token previo.
- Respuesta: `data.access_token`, `data.refresh_token`, `data.user`.
- Código: `Wallapop.login()`.

### POST `/api/v3/access/refresh`

Renovación de token.

- Body: `{"refresh_token": ..., "client_id": "wallapop"}`
- Respuesta: `data.access_token` (+ nuevo `refresh_token`).
- Código: `Wallapop.try_refresh()`.

### GET `https://es.wallapop.com/api/auth/session` [cookie]

Convierte la sesión del navegador (NextAuth) en un JWT usable.

- Necesita la cookie `__Secure-next-auth.session-token` (junto a
  `device_id`, `device_fgp`, `accessToken` si las hay).
- Respuesta: JSON con el campo `token` (JWT de la API). Es `accessToken`.
- Código: `refresh_web_session()`.

---

## Búsqueda

### GET `/api/v3/search/components`

Bootstrap de una búsqueda.

- Params: `keywords`, `source=search_box`, `order_by`
  (`most_relevance` | `newest` | `price_low_to_high` | `price_high_to_low` |
  `closest`), `filters_source=search_box`, opcional `category_id`,
  `subcategory_ids`, `condition`, `min_sale_price`, `max_sale_price`,
  `is_shippable=true`, `latitude`, `longitude`.
- De su respuesta se extrae `search_id` (clave para paginar).
- Código: `Wallapop.search()`.

### GET `/api/v3/search/section`

Página de resultados (lo que la web monta tras el bootstrap).

- Params: los del paso anterior + `search_id`, `section_type=organic_search_results`,
  `source=deep_link`; para página 2+ añadir `next_page` (cursor que devuelve
  la propia API en el campo `next_page`).
- Respuesta: `...items[]` con los anuncios sin normalizar.
- Código: `Wallapop._search_sections()`.

### GET `/api/v3/general/search` (legacy)

Alternativa antigua si `/search/components` no devuelve `search_id`.

- Params: `keywords`, `order_by`, `filters_source`, `source`, opcional
  `category_ids` (plural), `subcategory_ids`, `condition`, `min_sale_price`,
  `max_sale_price`, `is_shippable=true`, `latitude`, `longitude`,
  `distance=1000000`.
- Respuesta: `search_objects[]`.
- Código: `Wallapop._search_legacy()`.

---

## Anuncios

### GET `/api/v3/items/{id}`

Detalle público de un anuncio (sin auth).

- Respuesta: JSON plano del item (precio con su estructura, `user`,
  `web_slug`, etc.).
- Código: `Wallapop.item()` (también se usa para averiguar la moneda al
  mandar una oferta).

### GET `https://es.wallapop.com/item/{slug}`  [página]

Resolución de referencia → id interno. La página Next.js embebe el primer
payload en `<script id="__NEXT_DATA__" type="application/json">`.

- Extrae `data.props.pageProps.item.id` (el slug literal puede contener un
  sufijo que no coincide con el id).
- Código: `resolve_internal_id()` (aplica también si ya recibe un url o un
  hash de 12 caracteres alfanuméricos).

---

## Mensajería

### POST `/api/v3/instant-messaging/conversation`

Crea (o devuelve) la conversación de un anuncio. Idempotente.

- Body: `{"item_hash_id": <item hash>}`
- De la respuesta se extrae `conversation_id`.
- Código: `Wallapop.ensure_conversation()`.

### GET `/bff/messaging/conversation/{conversation_id}`  [BFF]

Hilo completo de una conversación.

- Respuesta: `with_user`, `item`, `channel`, `sale`, y
  `messages.messages[]` (texto o payload de "delivery" para ofertas).
- Nota: los mensajes llegan newest-first; hay que ordenar por `timestamp`.
- Código: `Wallapop.conversation_thread()`.

### GET `/bff/messaging/inbox?page_size=30&max_messages=30`  [BFF]

Inbox; por conversación incluye sus N últimos mensajes (newest-first) y el
`item` asociado.

- Código: `Wallapop.inbox()`.

### GET `/api/v3/instant-messaging/messages/unread`

Contador de no leídos (`unread_counter`).

- Código: `Wallapop.unread_count()`.

### GET `/api/v3/instant-messaging/token`

Token de PubNub (se usa como `auth` en el publish).

- Respuesta: `data.token`.
- Código: `Wallapop.im_token()`.

### GET `https://ps7.pndsn.com/publish/{pub}/.../{channel}/0/{msg}`  [PubNub REST]

Entrega real de un mensaje de chat (así lo hace la web).

- Ruta: `publish/{PUB_KEY}/{SUB_KEY}/0/{channel}/0/{json_url_encoded}`.
- Params: `meta` (JSON con tipo de mensaje, `to_user_hash`,
  `from_user_hash` [= `hashed_sub` del JWT], `conversation_hash`,
  platform), `uuid` (= hash de usuario), `requestid`, `pnsdk`, `auth`.
- Ack de éxito: `[1,"Sent",<timetoken>]`.
- Código: `Wallapop.pubnub_publish()`.

---

## Ofertas

### POST `/api/v3/delivery/buyer/offers`

Enviar una oferta (flujo actual de la web; el viejo `/items/{id}/offers` está
muerto: 404).

- Body: `{"offer_id": <uuid generado por el cliente>, "offer_price_amount":
  <float>, "offer_price_currency": "EUR", "item_ids": [<item id>],
  "message": "..." (opcional)}`.
- Si manda falla `400/422` y había mensaje, se reintenta sin él.
- Código: `Wallapop.send_offer()`.

### PATCH `/api/v3/delivery/offers/{offer_id}/status`

Aceptar o rechazar una oferta (p. ej. la contraoferta de un vendedor).

- Body: `{"status": "ACCEPTED" | "DECLINED"}`.
- Comportamiento real verificado: sobre una oferta ya cerrada devuelve
  `409 invalid offer status` (validación del servicio, no bloqueo anti-bot).
- Tras aceptar, el hilo recibe *"Oferta aceptada, ¡cómpralo ahora! Esta
  oferta caduca en 24h."*; al rechazar, *"Has rechazado la oferta."*.
- Código: `Wallapop.offer_response()`.

### PATCH `/api/v3/delivery/offers/{offer_id}/offer-price`

Contraofertar un precio sobre una oferta abierta (flujo "hacer una nueva
oferta" de la web).

- Body: `{"offer_price_amount": <float>, "offer_price_currency": "EUR"}`.
- Código: `Wallapop.counter_offer()`.

### GET `/bff/delivery/offer-details?offer_id=`  [BFF]

Payload del modal "Offer Tracking Status".

- Solo tiene sentido cuando **nosotros somos el que debe responder** (el
  payload se genera para el respondiente actual).
- ⚠️ **Bloqueado por anti-bot para `urllib`**: devuelve 302 →
  `/notAvailable`, y con cabeceras `Sec-Fetch-*`/`mpid`/`trackinguserid`
  pasa a 200 pero con el shell HTML de la SPA (no JSON). En navegador
  (Playwright + cookies de sesión) sí devuelve el JSON.
- La CLI no depende de él: reconstruye el estado desde el hilo de chat
  (`parse_offer_events`). Se documenta por integridad y por si la entrega
  cambia.
- Código: `Wallapop.offer_details()` (actualmente solo diagnóstico).

### GET `/bff/delivery/make-an-offer?item_id=`  [BFF]

Config del editor de ofertas.

- Respuesta: `item.price.amount`, y dentro de `restriction`:
  `max_offer_percentage` (límite de descuento) y
  `remaining_offers_per_day`.
- Código: `Wallapop.make_offer_info()`.

---

## Modelo del ledger de ofertas (sin API extra)

El estado de una negociación se deriva **offline** de los mensajes del hilo:

- El mensaje de oferta llega como `type=delivery_generic` con un `payload` cuyo
  `text` (o `buttons[].action` con `app/chat/offer/<uuid>`) identifica el
  evento; tipos: `our-offer`, `seller-counter`, `our-counter`,
  `seller-accepted`, `accepted-buy`, `seller-declined`, `our-declined`,
  `expired`.
- Los mensajes terminales (aceptado/rechazado/caducado) no traen importe:
  se hace *forward-fill* del `amount`/`offer_id` desde el último evento
  anterior con valor.
- Código: `parse_offer_events()` + `offer_state_summary()` (`models.py`).