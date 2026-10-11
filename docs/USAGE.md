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
| `favorite` | Marcar/desmarcar un anuncio como favorito |
| `favorites` | Ver todos tus favoritos (lista `/favorite-lists`) |
| `purchases` | Ver tus compras: en curso o finalizadas (centro de compras) |
| `selling` | Ver tus ventas: en curso o finalizadas (centro de ventas) |
| `alert` | Búsquedas guardadas (snipar ofertones): `save` / `check` / `hits` / `remove` |
| `suggest` | Autocompletar palabras clave (como el buscador de la web) |
| `seller-items` | Todos los anuncios de un vendedor (paginado) |
| `edit` | Editar un anuncio tuyo publicado (precio, título, descripción, marca, modelo, color, condición, peso) |
| `reserve` | Reservar / desreservar uno de tus artículos |
| `sold` | Marcar uno de tus artículos como vendido (o desmarcarlo) |
| `inactivate` | Pausar/reactivar uno de tus anuncios |
| `delete` | Borrar permanentemente uno de tus artículos (`DELETE /api/v3/items/{id}`) |
| `seller` | Ver el perfil completo de un vendedor (valoración, stats, reseñas y `--extra` para info extra) |
| `publish` | Publicar un artículo por API pura (o con wizard Playwright con `--mode browser`) |

---

## Login y sesión

**Lo más sencillo: `wallapop login` a secas.** Sin argumentos detecta qué tienes
y hace la vía con menos fricción:

1. Si ya hay una sesión válida guardada → `already logged in` (para cambiar de
   cuenta usa `wallapop login --force`).
2. Si tu Firefox tiene una sesión de `es.wallapop.com` → la importa sin abrir
   nada (funciona con cuentas Google).
3. Si `playwright` está instalado → abre un navegador visible para tu login
   normal (Google, …) y captura la sesión.
4. Si no → te sugiere el login por email.

Vías explícitas (según el tipo de cuenta):

| Vía | Comando | Vale para |
| --- | --- | --- |
| **Auto** | `wallapop login` | Todo: usa lo que esté disponible |
| Email + contraseña | `wallapop login your@email.com` | Cuentas normales |
| Wizard de navegador | `wallapop login --wizard` | **Google o cualquier navegador** |
| Importar de Firefox | `wallapop login --browser firefox` | Google (si ya estás logueado en Firefox) |
| Pegar cookies | `wallapop login --cookies '...'` o `--cookies-file mi.txt` | Cualquier navegador (F12) |

### 1. Email + contraseña

```
wallapop login your@email.com              # pide contraseña de forma segura
wallapop login your@email.com PASSWORD      # o inline
```

> **Importante:** si tu cuenta se registró con Google **no tiene contraseña de
> Wallapop**; este login responderá 401. Usa el wizard de navegador o la
> importación de Firefox.

### 2. Wizard de navegador (`--wizard`) — login con Google

La vía universal para cuentas Google (o cualquier sesión que solo exista en un
navegador), sin depender de qué navegador uses. Google no se puede loguear
por API pura (su OAuth exige la pantalla de consentimiento); el wizard abre el
navegador, te logueas con Google y la CLI captura la sesión:

```
wallapop login --wizard            # espera 300 s
wallapop login --wizard --timeout 600
```

Abre un **Chromium visible** en `es.wallapop.com` (pantalla de acceso). Haz tu
login normal (Google, email, …) y al terminar la CLI captura la sesión y
genera el token; puedes cerrar el navegador con `Ctrl+C` si te equivocas
(no guardará nada). Requiere `playwright`:

```
pip install -e '.[browser]'
```

### 3. Importar de Firefox (`--browser firefox`)

```
wallapop login --browser firefox           # importa tu sesión de Firefox (sirve con cuentas Google)
```

Con `--browser firefox` la CLI lee `cookies.sqlite` del perfil de Firefox más
reciente y guarda la sesión bajo `~/.config/wallapop_cli/session.json`. Es la
vía que funcionaba antes de existir el wizard; el wizard sirve para cualquier
navegador y también funciona con cuentas Google.

### 4. Cookies pegadas (cualquier navegador / Windows)

> `wallapop login` a secas ya **no** lee `cookies.txt` automáticamente (lo
> dejó de hacer para no colisionar con la auto-detección). Usa `--cookies` o
> `--cookies-file` de forma explícita:

```
python3 wallapop.py login --cookies-file mi.txt
python3 wallapop.py login --cookies 'accessToken=...; __Secure-next-auth.session-token=...; device_id=...; device_fgp=...'
```

1. Abre `https://es.wallapop.com` logueado → `F12` → pestaña **Red**.
2. Clic en una petición que apunte a `es.wallapop.com` / `api.wallapop.com` →
   pestaña **Cabeceras** → apartado **Request Headers** → campo **Cookie:** →
   clic derecho → **Copiar valor**.
3. Pega ese valor en un fichero (p. ej. `cookies.txt`) y ejecuta
   `login --cookies-file cookies.txt`.

La CLI se queda solo con las cookies que necesita (`accessToken`,
`__Secure-next-auth.session-token`, `device_id`, `device_fgp`). No depende del
shell ni del SO.

### Estado de la sesión

```
wallapop session                           # origen de la sesión y caducidad del token
wallapop logout                            # borra los ficheros de credenciales
```

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

> **`price_asc` oculta precios**: Wallapop devuelve `0.00 €` (precio oculto)
> en los resultados ordenados por precio ascendente. Combinado con
> `--ship-only`, la primera página del API viene vacía (los items a 0€ no
> tienen envío); la CLI sigue el cursor `next_page` y trae el resto igualmente.
> Añadir `--min-price` evita el placeholder y devuelve precios reales.

> **Ids de categoría**: el catálogo completo (18 categorías top + todas sus
> subcategorías con sus ids) está en `docs/categories.json`, o se puede obtener
> en vivo con `Wallapop.categories()`. Los ids de `--category`/`--subcat` son
> los de ese árbol, no invenciones. En la web, al hacer clic en una subcategoría
> se abre otro submenú y la URL queda `category_id=X&subcategory_ids=Y` — esos
> `Y` son ids de subcategoría del mismo árbol.
>
> - `--subcat` **sin `--category` funciona**: la CLI sube al nodo raíz
>   automáticamente (p. ej. `--subcat 10088` → `category_id=24200`).
> - `--category` con un **nodo intermedio** (p. ej. `24203`, hijo de `24200`)
>   también se normaliza a su raíz. El API acepta esos ids pero devuelve 0
>   resultados, por eso la CLI los corrige.

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
  no hay ack, la entrega se considera desconocida: comprueba el hilo antes de reenviar. El fallback de navegador requiere `--browser-fallback` y no debe repetir envíos ambiguos.
  El fallback opcional requiere Playwright y Firefox, instalados en el entorno virtual de la CLI.
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

## Favoritos

```
wallapop favorite <item>          # añadir a favoritos
wallapop favorite <item> --remove # quitar de favoritos
```

- `<item>` acepta url, slug web o hash interno (igual que `show`/`message`/`offer`).
- Usa el endpoint real `PUT /items/{id}/favorite` de la web, con body
  `{"favorited": true|false}`. Devuelve 204 sin body.
- Para verificar el estado de favorito, usa `show <item>` — la línea de stats
  muestra `(te gusta)` cuando el item está en favoritos.

### `favorites` (todos tus favoritos)

```
wallapop favorites                # tus favoritos (lista /favorite-lists)
wallapop favorites --max 50       # más resultado por página
wallapop favorites --json         # dump crudo
```

- Endpoint `GET /favorite-lists` de la web; pagina automáticamente con el cursor
  `meta.next` → `since`. La URL de cada item sale con su slug web.
- Vaciando tus favoritos a disco: `wallapop favorites --json > favs.json`.

---

## Búsquedas guardadas / alertas (snipar ofertones)

```
wallapop alert save 'nintendo switch' --min-price 100 --max-price 300
wallapop alert check 'nintendo switch' --max-price 300
wallapop alert hits
wallapop alert remove --id 87b2e4fc-b3c9-4351-81af-43e6788aafa5
```

- `save` → `POST /searchalerts/savedsearch`. El server contesta el **UUID** de la
  búsqueda (header `Location`); se imprime y puedes guardártelo para `remove`.
- `check` → `HEAD /searchalerts/savedsearch/exists` con los mismos filtros:
  imprime `saved (id <uuid>): '<keywords>'` o `not saved: '<keywords>'`.
- `hits` → `GET /searchalerts/savedsearch/alerts/total-hits`: el contador de
  novedades de la web (el badge junto a "búsquedas guardadas").
- `remove --id <uuid>` → `DELETE /searchalerts/savedsearch/{id}` (200).
- Filtros aceptados (mismos nombres que la web): `--min-price`, `--max-price`,
  `--category-id`, `--city/-geo`, `--ship-only` y los de `search`.
- La sesión debe estar logueada (endpoints autenticados). Verificado en vivo el
  ciclo completo `save → check → remove` sobre una búsqueda real.

---

## Autocompletar (suggest)

```
wallapop suggest 'nintendo sw'     # sugerencias del buscador de la web
wallapop suggest 'ps' --limit 5 --json
```

- `GET /suggesters/nonlocated?keyword=...` (200, autenticado); imprime
  `texto [subtitulo]` por línea.

---

## Gestionar tus artículos

Comandos para los anuncios que tú publicaste:

```
wallapop edit <item> --price 200 --title 'Nintendo 3DS Roja' --condition as_good_as_new
wallapop edit <item> --brand Nintendo --model 3DS --color red --max-weight-kg 1
wallapop reserve <item>            # reservar (lo tapa en el marketplace)
wallapop reserve <item> --remove   # desreservar
wallapop sold <item>               # marcar como vendido
wallapop sold <item> --undo        # desmarcar
wallapop inactivate <item>         # pausar (quitar del marketplace sin borrar)
wallapop inactivate <item> --undo  # reactivar
wallapop delete <item>             # borrar permanentemente (pide confirmación)
wallapop delete <item> --yes       # borrar sin confirmar
```

- `<item>` acepta url, slug web o hash interno (igual que `show`); debe ser
  **uno de tus anuncios** (el API responde 403/404 si no es tuyo).
- `edit` usa `PUT /api/v3/items/{id}` con el header `Accept:
  application/vnd.upload-v2+json` (el mismo del formulario "Actualizar" de la
  web). Solo cambia lo que pases; el resto se rellena con el estado actual del
  anuncio. Devuelve el item actualizado con `--json`.
- `reserve` → `PUT /items/{id}/reserve` (body `{"reserved": true|false}`, 204);
  `sold` → `PUT /items/{id}/sold` (body vacío al marcar, `{"sold": false}` al
  desmarcar, 204).
- `inactivate` → `PUT /items/{id}/inactivate` (204 al pausar) usando el mismo
  interruptor que el perfil de la web ("Pausar anuncio"; en vivo devuelve 403 si
  el item no es tuyo).
- `delete` → `DELETE /api/v3/items/{id}` (204). **Borrado permanente**: quita
  el anuncio del marketplace y desaparece (un `show` posterior da 404). Es
  distinto de `reserve`/`sold`/`inactivate`, que lo mantienen en tu historial.
  Pide confirmación salvo con `--yes`.
- Verificado en vivo: publicar → `delete --yes` → 204; `show` del mismo id → 404.

---

## Compras y ventas (centros de gestión)

```
wallapop purchases                  # compras en curso
wallapop purchases --finished       # compras finalizadas
wallapop purchases --json           # dump crudo de las transacciones
wallapop selling                    # ventas en curso
wallapop selling --finished         # ventas finalizadas
wallapop selling --finished --json  # dump crudo
```

- Replican los centros **Compras** (`/app/purchases?selectedTab=ongoing|finished`) y
  **Ventas** (`/app/selling?selectedTab=ongoing|finished`) de la web, por API
  pura (`GET /sdui/purchases/{ongoing|finished}` y `GET /sdui/selling/...`).
- Cada tarjeta muestra título, precio, detalle del estado (p. ej. "Transacción
  completada 25 abr 2025"), campos extra (fecha de pedido, código de envío,
  método de entrega, contacto) y el enlace del pedido/anuncio.
- Si no hay nada en la pestaña se muestra `(no hay nada aquí)`.
- `--json` devuelve la lista de transacciones aplanadas (`id`, `title`,
  `detail`, `price`, `details[]`, `url`).

---

## Ver el perfil de un vendedor

```
wallapop seller <user_id>             # valoración, stats de ventas y reseñas
wallapop seller <user_id> --json      # dump crudo (profile + stats + reviews)
wallapop seller <user_id> --extra     # + info extra (response_rate, verificación, actividad)
wallapop seller-items <user_id>       # todos sus anuncios (paginado)
wallapop seller-items <user_id> --max 4 --json
```

- `<user_id>` es el id del vendedor que sale en la línea `seller:` de `show`.
- Muestra el nombre, la valoración media y el total de reseñas (igual que el
  item), además de stats de la cuenta (ventas, envíos, días de entrega media)
  y sus reseñas detalladas (qué vendió y valoración).
- `--extra` añade `GET /users/{id}/extra-info` (rapidez de respuesta,
  verificaciones y nivel de actividad del vendedor).
- `seller-items` lista todos los anuncios del vendedor con `GET /users/{id}/items`
  (cursor `meta.next` → `since`); cada item sale con su URL de slug web.
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
- `--price`: precio de venta en EUR; admite céntimos (por ejemplo, `19.95`).
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