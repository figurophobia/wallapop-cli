# Uso de la CLI

Referencia completa de `wallapop`. Se invoca como
`python3 wallapop.py <comando> ...` (o `./wallapop.py`).

## Índice

| Comando | Qué hace |
| --- | --- |
| `login` | Identificarse (email+password o sesión importada de Firefox) |
| `logout` | Borrar credenciales |
| `session` | Ver estado de la sesión |
| `search` | Buscar anuncios |
| `deals` | Buscar y rankear gangas (precio vs mediana) |
| `watch` | Bucle de escaneo con auto-oferta/mensaje |
| `offer` | Hacer ofertas, responder ofertas, ver estado de negociaciones |
| `message` | Enviar mensaje al vendedor de un anuncio |
| `replies` | Ver el inbox y los hilos completos |
| `show` | Ver los detalles completos de un anuncio (incluye descripción y valoración del vendedor) |
| `seller` | Ver el perfil completo de un vendedor (valoración, stats de ventas y reseñas) |
| `publish` | Publicar un artículo por API pura (o con wizard Playwright con `--mode browser`) |

---

## Login y sesión

```
wallapop login your@email.com              # pide contraseña de forma segura
wallapop login your@email.com PASSWORD      # o inline
wallapop login --browser firefox           # importa tu sesión de Firefox (sirve con cuentas Google)
wallapop login --cookies 'accessToken=...; __Secure-next-auth.session-token=...; device_id=...; device_fgp=...'
wallapop session                           # origen de la sesión y caducidad del token
wallapop logout                            # borra los ficheros de credenciales
```

Con `--browser firefox` la CLI lee `cookies.sqlite` del perfil de Firefox más
reciente y guarda la sesión bajo `~/.config/wallapop_cli/session.json`. Es la
vía que funciona cuando la cuenta se registró con Google.

**La vía fácil con cookies (cualquier navegador / Windows):** no copies cookies
a mano. Pega en un fichero el valor del campo `Cookie` de una request y haz
`login`, que lo lee automáticamente.

```
python3 wallapop.py login                    # lee cookies.txt si existe
python3 wallapop.py login --cookies-file mi.txt
```

1. Abre `https://es.wallapop.com` logueado → `F12` → pestaña **Red**.
2. Clic en una petición que apunte a `es.wallapop.com` / `api.wallapop.com` →
   pestaña **Cabeceras** → apartado **Request Headers** → campo **Cookie:** →
   clic derecho → **Copiar valor**.
3. Pega ese valor en `cookies.txt` (la primera línea sin comentario; ver
   `cookies.txt.example`) y ejecuta `login`.

La CLI se queda solo con las cookies que necesita (`accessToken`,
`__Secure-next-auth.session-token`, `device_id`, `device_fgp`). No depende del
shell ni del SO: `login` busca el fichero directamente. Sigue existiendo
`--cookies '...'` si prefieres pegar el valor inline.

---

## Búsqueda

```
wallapop search 'nintendo switch' --city madrid
wallapop search 'ps5' --max-price 250 --order price_asc
wallapop search 'steam deck' --pages 2 --ship-only --json
```

Flags comunes:

| Flag | Descripción |
| --- | --- |
| `--category ID` | Filtra por categoría (ej. `24200` Tecnología y electrónica) |
| `--subcat ID` | Filtra por hoja de taxonomía (ej. `10088` consolas, `10092` accesorios) |
| `--condition LIST` | Filtra por estado de conservación (comas: `un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all`) |
| `--min-price` / `--max-price` | Rango de precio |
| `--city NAME` | Preset de ciudad/país/región (`madrid`, `barcelona`, `italia`, `portugal`, `galicia`, …). Sin `--city`/`--lat` busca en toda España |
| `--lat L` / `--lon L` | Coordenadas exactas (se usan juntos) |
| `--order` | `relevance`, `newest`, `price_asc`, `price_desc`, `closest` |
| `--pages N` | Páginas de resultados (1 por defecto) |
| `--delay S` | Pausa entre páginas (1.5 s) |
| `--ship-only` | Solo artículos enviables |
| `--json` / `--csv` | Salida estructurada |
| `--quiet` | Oculta avisos |

> **Ids de categoría**: el catálogo completo (18 categorías top + todas sus
> subcategorías con sus ids) está en `docs/categories.json`, o se puede obtener
> en vivo con `Wallapop.categories()`. Los ids de `--category`/`--subcat` son
> los de ese árbol, no invenciones. En la web, al hacer clic en una subcategoría
> se abre otro submenú y la URL queda `category_id=X&subcategory_ids=Y` — esos
> `Y` son ids de subcategoría del mismo árbol.

### Seleccionar país / región

El alcance geográfico se controla con `--city` (o `--lat`/`--lon`). Sin ninguno
se busca en toda España por defecto — y eso **no es una restricción ni una
diferencia con el navegador**: `es.wallapop.com` para un usuario en España
geolocaliza y devuelve exactamente lo mismo que la CLI sin `--city`. Es el
comportamiento natural; si quieres otro país se lo dices:

```
wallapop search "gamecube" --city italia      # solo anuncios de Italia
wallapop search "gamecube" --city portugal    # de Portugal (mezcla con la franja ES)
wallapop search "gamecube" --city galicia     # ancla a Galicia
```

Presets disponibles (además de las ciudades): países (`espana`, `portugal`,
`italia`, `francia`, `marruecos`, `andorra`, `reino-unido`, `alemania`,
`belgica`) y regiones españolas (`galicia`, `andalucia`, `cataluna`,
`pais-vasco`, `comunidad-valenciana`, …).

Nota: el orden `relevance` es el que respeta el país elegido; `newest` tiende a
devolver un conjunto más global (mezcla de España y otros países), igual que la
app cuando "a veces saca cosas de Italia o Portugal".

El detalle técnico (por qué sin coords el API devuelve 0, el papel de
`search_country`, etc.) está en `docs/ENDPOINTS.md`.

### Catálogo de categorías

El árbol completo (18 categorías top + todas las subcategorías, con sus ids)
está en `docs/categories.json`. Para obtenerlo en vivo:

```python
from wallapoplib import Wallapop
cats = Wallapop().categories()   # [{"id", "name", "subcategories": [...]}]
```

Sirve para listar/guiar al usuario por las categorías al construir una
búsqueda, en lugar de hardcodear ids. Detalle técnico del endpoint en
`docs/ENDPOINTS.md` (sección `GET /api/v3/categories`).

---

## Gangas (deals) y watch

```
wallapop deals 'nintendo 3ds' --city madrid --min-discount 0.2 --max-price 100
wallapop deals 'nintendo ds' --msg 'Hola! Te interesa venderlo por {offer}?' --offer-pct 0.8 --dry-run
wallapop watch 'steam deck' --max-price 300 --interval 600
wallapop watch 'mario' --once                        # un solo scan y salir
```

`deals` calcula la **mediana** de los resultados y nota el descuento de cada
anuncio; solo muestra/actúa sobre los que superan `--min-discount` (0.15 por
defecto). Flags extra:

| Flag | Descripción |
| --- | --- |
| `--min-discount F` | Descuento mínimo sobre la mediana (0.15) |
| `--top N` | Acciones/los N mejores (10) |
| `--offer-pct F` | Auto-oferta a esa fracción del precio (ej. `0.8`) |
| `--msg TMPL` | Auto-mensaje con plantilla `{title} {price} {offer} {link} {city}` |
| `--dry-run` | Imprime la intención sin enviar |
| `--interval S` | Segundos entre escaneos en `watch` (300, mínimo 5) |
| `--once` | Solo un escaneo en `watch` |

`contacted.json` guarda los ids ya contactados para no repetir enlazamientos.
Cada fila de deals muestra su badge de descuento (`-32%` verde) y la columna
`MEDIAN` con la referencia del mercado.

---

## Ofertas

```
wallapop offer <item> --price 60 --msg 'Hola, aceptarias 60?'   # hacer oferta
wallapop offer <item> --pct 0.8                                  # o como fracción
wallapop offer status                # panel de negociaciones
wallapop offer status <conv|item>    # detalle de una conversación/hilo
wallapop offer status <offer_id>     # resuelve el uuid -> conversación
wallapop offer status --last 5       # solo las 5 conversaciones más recientes
wallapop offer accept <offer_id>     # aceptar contraoferta del vendedor
wallapop offer decline <offer_id>    # rechazarla
wallapop offer counter <offer_id> --price N   # contraofertar
wallapop offer <item> --price 60 --dry-run     # simular sin enviar
```

- `<item>` acepta id interno, url o slug de la web; `<offer_id>` es el uuid
  que muestra el panel (`offer=xxxx-...`).
- `offer <item>` consulta el editor real de la web y avisa si el importe está
  por debajo del límite de descuento (`max_offer_percentage`) y cuántas
  ofertas quedan hoy.
- Aceptar una oferta **genera la obligación de compra en 24h** a ese precio.

### Panel `offer status`

`offer status` (sin argumentos) muestra el dashboard de conversaciones,
ordenado por actividad más reciente:

```
  asking = precio del anuncio · "actual" = importe de la última oferta en juego
  [ACEPTADA]    Juegos Mario Nintendo 3DS   28.0 EUR [published]  ·  pixelia  aceptada por ti — comprar en 24h  actual 25.00€  offer=72f36...
      conv wzy050ee4e65  ->  offer status wzy050ee4e65
  [PENDIENTE]   Nintendo Switch Lite Amarilla  95.0 EUR  ·  Fran     esperando tu respuesta  actual 90.00€  offer=ea034...
```

Leyenda de estados (badge + etiqueta):

| Badge | Etiqueta | Significado |
| --- | --- | --- |
| `[ACEPTADA]` verde | `aceptada por ti / por el vendedor (comprar en 24h)` | Oferta cerrada |
| `[PENDIENTE]` amarillo | `esperando tu respuesta` | Contraoferta del vendedor que **tú** debes responder |
| `[EN ESPERA]` cian | `esperando la respuesta del vendedor` | Tu oferta/contraoferta en pie |
| `[RECHAZADA]` rojo | `rechazada por ti / por el vendedor` | Cerrada sin compra |
| `[CADUCADA]` gris | `caducada` | Expirada |
| (sin badge) | `sin ofertas` | Sin actividad de oferta |

El `actual X€` es siempre el **último importe de oferta en juego**
(contraoferta que puedes aceptar, importe aceptado, o tu última oferta
pendiente). Detalle por conversación (`offer status <conv>`) pinta el ledger
cronológico completo (63,00 → 80,00 → 70,00 €, p. ej.) y el estado final con
el comando a ejecutar si te toca responder.

---

## Mensajería

```
wallapop message <item> --text 'Hola, funciona bien?'
wallapop message <item> --text 'Hello!' --dry-run
wallapop replies                          # lista del inbox con no leídos
wallapop replies --conversation <conv>    # hilo completo de un chat
wallapop replies --json                   # dump crudo (inbox o conversación)
wallapop replies --conversation <conv> --json
```

- Los mensajes se entregan por la REST de PubNub (forma real de la web); si
  no hay ack, la CLI intenta el fallback de navegador (Playwright), para lo
  que necesitas sesión de navegador (`login --browser firefox`) y tener
  `playwright` instalado:
  `pip install --user --break-system-packages playwright && python3 -m playwright install firefox`.
- La CLI evita reenviar un texto idéntico al último mensaje tuyo del hilo.

---

## Detalles de un anuncio

```
wallapop show <item>                 # detalles completos + descripción
wallapop show <item> --json          # dump crudo de todo el anuncio como JSON
```

- `<item>` acepta url, slug web o hash interno (igual que `message`/`offer`).
- Imprime título, precio, vendedor (id, nombre, valoración media en estrellas y
  nº de reseñas — los mismos valores que muestra la web en la página del item),
  ciudad con coordenadas, categoría, marca, género, estado de conservación,
  envío, vistas/favoritos, URL y la descripción completa del anuncio.
- El detalle sale de `GET /items/{id}` (campos `description.original`,
  `type_attributes`, `counters`, `location`, …); la valoración del vendedor
  se obtiene de `GET /users/{id}` (nombre) y `GET /users/{id}/stats` (media
  de estrellas y total de reseñas).
- Con `--json` el output incluye además `seller_name`, `seller_rating`,
  `seller_reviews` y `images` (URLs de las fotos del anuncio, tamaño medium).
- Si quieres el perfil completo del vendedor (más stats y sus reseñas), usa el
  comando `seller` (ver más abajo).

---

## Ver el perfil de un vendedor

```
wallapop seller <user_id>             # valoración, stats de ventas y reseñas
wallapop seller <user_id> --json      # dump crudo (profile + stats + reviews)
```

- `<user_id>` es el id del vendedor que sale en la línea `seller:` de `show`.
- Muestra el nombre, la valoración media y el total de reseñas (igual que el
  item), además de stats de la cuenta (ventas, envíos, días de entrega media)
  y sus reseñas detalladas (qué vendió y valoración).
- Se alimenta de `GET /users/{id}` (nombre), `GET /users/{id}/stats`
  (valoración y contadores) y `GET /users/{id}/reviews` (reseñas).

---

## Publicar un artículo

```
wallapop publish /ruta/a/fotos --summary 'Nintendo 3DS Azul' --price 310
wallapop publish ./fotos --summary '...' --price 200 --condition good --brand Nintendo --model 3DS --color blue
wallapop publish ./fotos --summary '...' --price 331 --mode browser     # wizard Playwright (vía antigua)
```

- **Por defecto publica por API pura** (`--mode api`), sin navegador: el método
  `Wallapop.publish()` (`client.py`) replica el multipart exacto del POST final
  `/api/v3/items` (campo `image` con la foto + campo `item` con el JSON de
  atributos/precio/ubicación/envío), usando tu `upload_id` fresco. Doc del body:
  `docs/ENDPOINTS.md` (sección "Paso final: publicar").
- `--mode browser`: conserva la vía antigua del **wizard real** de
  `es.wallapop.com` con tu sesión guardada (`wallapoplib/publish.py`, Playwright).
  Úsalo si quieres ver/navegar la publicación, o por compatibilidad.
- `photos_dir`: carpeta con las imágenes jpg/png del artículo. En modo API se
  publica **la primera foto** (el algoritmo anti-duplicados de Wallapop sospecha
  al publicar el mismo item varias veces con varias fotos); el modo browser sube
  una foto por defecto (`--max-photos` sube hasta 10).
- `summary`: título (máx 50).
- En modo API completas marca/modelo/color/descripción tú (`--brand`, `--model`,
  `--color`, `--description`); en modo browser la web los autocompleta al subir
  las fotos y solo editas precio/estado.
- `--condition`: `un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all`
  (por defecto `good` = "En buen estado").
- `--price`: precio de venta en EUR (entero, rango invertible 1–2500).
- `--max-weight-kg`: peso de envío en kg (por defecto 1, modo API).
- `--weight-tier`: tramo de peso envío del wizard (por defecto `0` = 0–1 kg,
  solo modo browser).
- `--headless`: modo browser solo: ejecuta sin ventana.

---

## Detalles generales

- Guard de ritmo: `WALLAPOP_RATE_PACE` (segundos entre requests, 1.0 por
  defecto).
- Salidas con color se autodesactivan si stdout no es una TTY (`Paint`).
- Los errores salen por stderr con prefijo `error:`; los OK de acciones
  aparecen como `[ok] ...`, lo simulado como `[dry-run] would ...`.
- Todo el transporte es HTTP plano (`urllib`) salvo `publish`, que usa
  Playwright para jugar el wizard real de la web (el POST final de la
  publicación no es replicable por HTTP plano de momento).