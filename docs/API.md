# wallapoplib — API pública (contrato v1)

Biblioteca de comunicación con Wallapop por HTTP plano (sin navegador, solo
stdlib), con la CLI incluida (`wallapop`). Instalación local de desarrollo:

```
pip install -e '.[test]'
```

El único extra opcional es `[browser]` (Playwright), usado como fallback del
envío de chat cuando PubNub no responde. La comunicación normal no añade
ninguna dependencia.

## Quickstart

```python
from wallapoplib import Wallapop, load_credentials, load_session

ses = load_session()  # o load_credentials() para login por email
client = Wallapop(
    token=ses.get("accessToken"),
    web_cookies=ses if ses.get("__Secure-next-auth.session-token") else None,
)

# Buscar y leer resultados tipados (España por defecto)
items = client.search("nintendo switch lite", pages=2, delay=1.0)
for it in items:
    print(it.title, it.price, it.url, it.shippable)

# Hacer una oferta
resp = client.send_offer(items[0].id, 90.0, message="Hola, aceptarias 90?")
print(resp.ok, resp.data)

# Gestionar ofertas recibidas (aceptar / rechazar / contraofertar)
client.offer_response(offer_id, "accept")          # ACCEPTED
client.offer_response(offer_id, "decline")         # DECLINED
client.counter_offer(offer_id, 75.0)               # PATCH offer-price 75.00

# Chat (PubNub REST; idempotente: no reenvía el mismo texto)
client.send_message(items[0].id, "Hola! Tiene cargador?")

# Marcar/desmarcar favorito
client.favorite(items[0].id)            # marca como favorito (PUT .../favorite → 204)
client.favorite(items[0].id, False)     # desmarca
```

### Imágenes de un item

```python
it = client.item(items[0].id)      # it.images -> URLs medium de las fotos
print(it.images[0])                # https://cdn.wallapop.com/images/...?pictureSize=W640

# Descargarlas a disco (solo stdlib)
paths = client.download_images(it, "descargas")
print(paths)                       # ['descargas/abc_1.jpg', ...]
```

## Dataclasses públicas

Ver `wallapoplib/models.py`.

| Tipo | Campos | Uso |
| --- | --- | --- |
| `Item` | `id, title, price: float, currency, city, user_id, user_name, url, shippable: bool`, `reserved`, `sold`, `category + category_name`, `created_at`, `images: list[str]` | Un anuncio normalizado (`images` = URLs medium de las fotos) |
| `Deal` | `item: Item, discount: float, median: float` | Un `Item` por debajo de la mediana del mercado |
| `OfferEvent` | `kind, amount: float\|None, offer_id: str\|None, date, ts, text` | Entrada del ledger de negociación (una por evento de oferta en el hilo) |
| `OfferState` | `kind, label, amount, offer_id, actionable: bool` | Estado actual derivado del último evento |

Kinds de `OfferEvent`/`OfferState`: `our-offer` · `seller-counter` · `our-counter`
· `seller-accepted` · `accepted-buy` · `seller-declined` · `our-declined` ·
`expired`.

## Funciones de dominio (con o sin datos crudos)

```python
from wallapoplib import parse_offer_events, offer_state_summary, rank_deals

events = parse_offer_events(thread_messages)   # list[OfferEvent], ordenado por timestamp
state = offer_state_summary(events)            # OfferState | None
deals, median = rank_deals(items, min_discount=0.15, max_price=None)
```

- `parse_offer_events`: reconstruye el ledger del hilo y hace *forward-fill* de
  `amount`/`offer_id` en los eventos terminales (que no los traen).
- `offer_state_summary`: estado puro (sin colores de terminal); `actionable`
  solo es `True` cuando hay que responder a una contraoferta.
- `rank_deals`: mediana del conjunto de candidatos + ranking por descuento.

## Cliente (`Wallapop`)

Constructor: `Wallapop(token=None, refresh_token=None, email=None, persist_fn=None, web_cookies=None)`.

| Método | Endpoint |
| --- | --- |
| `item(item_id)` → `Item` | `GET /items/{id}` |
| `seller(user_id)` → `dict` | `GET /users/{id}` |
| `seller_stats(user_id)` → `dict` | `GET /users/{id}/stats` (rating_average + contadores de reseñas) |
| `seller_reviews(user_id)` → `list[dict]` | `GET /users/{id}/reviews` |
| `download_images(item, dest="images")` → `list[str]` | descarga `item.images` a `dest` (solo stdlib) y devuelve las rutas guardadas |
| `search(keywords, category_id?, subcategory_ids?, order_by?, latitude?, longitude?, pages?, delay?, condition?, min_price?, max_price?, ship_only?)` → `list[Item]` | `GET /search/components` + `/search/section` (o fallback `/general/search`). Sin `latitude`/`longitude` usa España por defecto (40.0, -3.5); pasa ambas para anclar a otra zona. |
| `send_offer(item_id, amount, message?)` | `POST /api/v3/delivery/buyer/offers` (offer_id uuid generado por cliente) |
| `offer_response(offer_id, action)` — `accept`/`decline` | `PATCH /delivery/offers/{id}/status` |
| `counter_offer(offer_id, amount, currency="EUR")` | `PATCH /delivery/offers/{id}/offer-price` |
| `offer_details(offer_id)` / `make_offer_info(item_id)` | BFF (sujetos a anti-bot) |
| `ensure_conversation(item_hash)` / `conversation_thread(conv_id)` / `inbox()` / `unread_count()` | mensajería |
| `send_message(item_hash, message, report?)` | PubNub REST publish; fallback navegador (Playwright) |
| `publish(photo_path, title, price, category_leaf_id?, condition?, description?, brand?, model?, color?, max_weight_kg?)` → `Response` | publica un artículo por API pura replicando el multipart de `POST /api/v3/items` (foto + JSON `item`). `Response.data` = `{"id": <item id>, "flags": {}}` |
| `favorite(item_id, favorited=True)` → `Response` | marca/desmarca un item como favorito (`PUT /items/{id}/favorite`, body `{"favorited": <bool>}`, 204) |
| `login(email, password)` / `try_refresh()` / `refresh_web()` | sesión |

`report` (dict opcional de `send_message`) recibe `{"path": "rest"|"browser"|"skipped", "skipped": bool, "browser_s": float}`.

## Errores

Todas las operaciones de red lanzan `ApiError` solo en fallos del transporte
(reinstatable) o de respuesta no consumible; el resto devuelve `Response`:

```python
from wallapoplib import ApiError, Response

resp: Response = client.send_offer("id", 10.0)
if not resp.ok:
    print(resp.status, resp.error_reason)
```

## Sesión
- `load_credentials()` — dict con `email`, `access_token`, `refresh_token`, `user` (login por email).
- `load_session()` — dict de cookies de navegador; con `__Secure-next-auth.session-token` activa la vía de refresh por cookie.
- `save_session(data)` — persiste la sesión de navegador.

Los datos se guardan en `~/.config/wallapop_cli/` con permisos `600`, fuera del repo.

## Geografía y selección de país

`wallapoplib.config.CITIES` es el mapa lugares → `(lat, lon)` del que cuelgan el
default de `search()` y el `--city` del CLI. Incluye ciudades, países y regiones
españolas (`italia`, `portugal`, `francia`, `galicia`, `andalucia`, …).

```python
from wallapoplib.config import CITIES, DEFAULT_GEO
lat, lon = CITIES["italia"]          # ancla a Italia
```

- Sin `latitude`/`longitude`, `search()` usa `DEFAULT_GEO` (España, 40.0, -3.5),
  que reproduce **exactamente** la búsqueda de `es.wallapop.com`. No es una
  restricción: pasar coords de otro país devuelve los items de ese país.
- El país real de los resultados lo decide principalmente `search_country`
  (que la lib no envía; el backend infiere por coords). Con `order_by` de
  relevancia los anuncios salen del país anclado; con `newest` el API devuelve
  un conjunto más global (mezcla de países), como a veces hace la app.

El detalle completo del comportamiento geográfico está en
`docs/ENDPOINTS.md` (sección "Comportamiento geográfico").

## Referencias a ids

- `resolve_internal_id(ref)` — convierte url / slug / hash de 12 chars al id interno (`v6gpwy9d476e`).

## Compatibilidad

- Python `>= 3.10`, cero dependencias obligatorias.
- CI: matrix `3.10 / 3.11 / 3.12` (`pip install -e '.[test]'` → `pytest`).