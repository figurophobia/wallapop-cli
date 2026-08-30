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
| `--category ID` | Filtra por categoría (ej. `12900` Consolas y videojuegos) |
| `--subcat ID` | Filtra por hoja de taxonomía (ej. `10088` consolas, `10092` accesorios) |
| `--min-price` / `--max-price` | Rango de precio |
| `--city NAME` | Preset de ciudad (`madrid`, `barcelona`, `valencia`, …) |
| `--lat L` / `--lon L` | Coordenadas exactas (se usan juntos) |
| `--order` | `relevance`, `newest`, `price_asc`, `price_desc`, `closest` |
| `--pages N` | Páginas de resultados (1 por defecto) |
| `--delay S` | Pausa entre páginas (1.5 s) |
| `--ship-only` | Solo artículos enviables |
| `--json` / `--csv` | Salida estructurada |
| `--quiet` | Oculta avisos |

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

## Detalles generales

- Guard de ritmo: `WALLAPOP_RATE_PACE` (segundos entre requests, 1.0 por
  defecto).
- Salidas con color se autodesactivan si stdout no es una TTY (`Paint`).
- Los errores salen por stderr con prefijo `error:`; los OK de acciones
  aparecen como `[ok] ...`, lo simulado como `[dry-run] would ...`.
- Todo el transporte es HTTP plano (`urllib`); salvo el fallback de chat, no
  se usa Playwright en ninguna llamada a la API.