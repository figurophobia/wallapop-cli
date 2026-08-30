# Arquitectura

CLI de automatización para Wallapop: busca anuncios, detecta gangas (precio por
debajo de la mediana), envía ofertas y mensajes, y gestiona ofertas recibidas
(aceptar / rechazar / contraofertar). Todo se hace por HTTP plano (`urllib`),
sin Playwright salvo en un fallback puntual del envío de chat.

## Estructura del repo

```
wallapop.py                    # entrypoint fino (launches wallapoplib.cli.main)
wallapoplib/
  __init__.py                  # API pública curada (dataclasses+cliente+dominio re-exportados)
  config.py                    # constantes API, paths, storage local, cookies
  http.py                      # transporte HTTP (rate-limit, retries, decode) + eprint
  tokens.py                    # JWT: claims, hash de usuario, política de expiración
  models.py                    # dominio: dataclasses Item/Deal/OfferEvent/OfferState + derivaciones
  ids.py                       # resolución item url/slug/hash -> id interno
  client.py                    # clase Wallapop: TODAS las llamadas a endpoints
  cli/                         # capa de consumo (NO contrato público del paquete)
    main.py                    # argparse + main()   (entry point wallapoplib.cli.main:main)
    commands.py                # handlers de comandos (login, search, deals, watch, offer, message, replies)
    output.py                  # render en terminal (colores, tablas, announce, colas/hilos)
```

La gerencia es sencilla y unidireccional:

```
cli/main.py  ->  cli/commands.py  ->  client.py  ->  http.py
                  |                      |
                  v                      v
       models / ids / tokens  <-   config.py (constantes y storage)
                 |
                 v
          cli/output.py (terminal)
```

Ningún módulo `importa` a un superior jerárquico (evitando ciclos): `models`
nunca toca `output`/`client`; `client` importa `eprint` desde `http` (no desde
`cli.output`, para no cerrar un ciclo `client`↔`cli`); las etiquetas con color
del estado de ofertas viven en `cli/output` (que importa `models`), no al
revés.

## Cómo fluye una invocación

1. `python3 wallapop.py <comando> ...` → `wallapoplib.cli.main.main()`.
2. `build_parser()` monta subcomandos (`search`, `deals`, `watch`, `offer`,
   `message`, `replies`, `login`, `logout`, `session`) y registra su
   `cmd_*` como `func` en el namespace de argparse.
3. `main()` llama `args.func(args)`. `ApiError` se captura arriba para
   imprimir `error: ...` en rojo y devolver código 1.

## Sesión y autenticación

Hay dos vías, ambas guardadas bajo `~/.config/wallapop_cli/`:

| Vía | Flujo | Storage |
| --- | --- | --- |
| Email/password | `POST /api/v3/access/login` → `access_token` + `refresh_token` | `credentials.json` |
| Navegador | Importa cookies `__Secure-next-auth.session-token` (+`device_id`/`device_fgp`) desde Firefox, y genera el `accessToken` vía `GET https://es.wallapop.com/api/auth/session` | `session.json` |

`authed_client()` (en `commands.py`) une ambas fuentes en una instancia de
`Wallapop` y registra el token de sesión web como credencial principal. El
JWT del token incluye el `exp` y el `hashed_sub`; `refresh_if_expired()`
renueva vía `/access/refresh` cuando el token va a caducar.

## Transporte HTTP (`http.py`)

- `http_request()`: rate-limit opcional (`WALLAPOP_RATE_PACE`, 1.0 s por
  defecto), retries con backoff exponencial en 403/429 y en fallos de red,
  timeout 25 s. Devuelve `(status, data, content_type)`.
- `decode_payload()`: intenta parsear JSON (también si el content-type no lo
  dice pero el cuerpo empieza por `{`).
- Cabeceras de la aplicación: `User-Agent` de un pool de Chrome, `Origin`/
  `Referer` de `es.wallapop.com`, y los marcadores de cliente
  `X-DeviceOS: 0`, `X-Product: wallapop`, `X-AppVersion: 826560`.
- **Ojo anti-bot**: algunos endpoints `GET` de la familia `/bff/...` (p. ej.
  `offer-details`) son sensibles al *fingerprint TLS* de `urllib` y devuelven
  el shell de la SPA (o un redirect a `/notAvailable`). Los `POST`/`PATCH` de
  acciones (ofertas, mensajes) **no** están bloqueados y llegan a la
  validación real del servicio.

## Búsqueda de anuncios

`Wallapop.search()` intenta primero el flujo moderno de la web:

```
GET /api/v3/search/components?keywords=...&source=search_box&order_by=...&filters_source=search_box
        -> search_id
GET /api/v3/search/section?search_id=...&section_type=organic_search_results&source=deep_link&next_page=...
        -> items[] + next_page (cursor)
```

y si no obtiene `search_id` cae al legado `GET /api/v3/general/search`
(`search_objects[]`). Los resultados se normalizan con `normalize_item()`
(modelo plano con `id`, `price`, `city`, `shippable`, `url`, etc.).

`deals`/`watch` rankean sobre la mediana del precio con `rank_deals()`.

## Mensajería (chat)

`Wallapop.send_message()` hace:

1. `POST /api/v3/instant-messaging/conversation {item_hash_id}` →
   `conversation_id` (idempotente: crea o devuelve la existente).
2. `GET /bff/messaging/conversation/{id}` → hilo (mensajes, `channel`,
   `with_user`, `item`).
3. Comprueba si el texto ya fue nuestro último mensaje (guard de idempotencia).
4. Pide token de PubNub: `GET /api/v3/instant-messaging/token`.
5. Publica el mensaje por la **REST pubnub** con `GET` al origin `ps7.pndsn.com`
   (así lo hace la web en realidad; `meta` y `auth` incluidos). El ack
   `[1,"Sent",<timetoken>]` confirma la entrega.
6. Si PubNub no hace ack, cae al fallback de navegador (Playwright Firefox
   con las cookies de sesión) en `send_chat_text()`.

## Ofertas

Traslado completo del flujo de la web (los endpoints se extrajeron del bundle
JS, ver `docs/ENDPOINTS.md`):

- Enviar: `POST /api/v3/delivery/buyer/offers` con un `offer_id` (uuid) que
  genera el cliente, el importe y `item_ids`.
- Aceptar/rechazar: `PATCH /api/v3/delivery/offers/{id}/status` con
  `{status: "ACCEPTED"|"DECLINED"}`.
- Contraofertar: `PATCH /api/v3/delivery/offers/{id}/offer-price` con el nuevo
  `offer_price_amount`.
- Info del editor: `GET /bff/delivery/make-an-offer?item_id=` (devuelve
  precio, `max_offer_percentage` y ofertas restantes del día).
- Estado: no se usa el modal OTS (`offer-details` está vetado por anti-bot);
  se reconstruye el **ledger de ofertas** a partir de los mensajes del hilo
  (`parse_offer_events()` → `offer_state_summary()`). Cada evento del payload
  de "delivery" se clasifica (`our-offer`, `seller-counter`, `our-counter`,
  `seller-accepted`, `accepted-buy`, `seller-declined`, `our-declined`,
  `expired`) y se presentan en orden cronológico (el inbox llega
  newest-first; siempre se ordena por `timestamp`).

## Almacenamiento local

| Fichero | Contenido |
| --- | --- |
| `~/.config/wallapop_cli/credentials.json` | email + access/refresh token (chmod 600) |
| `~/.config/wallapop_cli/session.json` | cookies de sesión web + token |
| `~/.config/wallapop_cli/contacted.json` | ids de anuncios ya contactados (para `deals`/`watch`) |