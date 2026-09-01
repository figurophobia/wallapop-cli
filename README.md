# Wallapop CLI

CLI de automatización para Wallapop: busca anuncios, detecta gangas (precio por
debajo de la mediana), envía ofertas y mensajes de chat, gestiona ofertas
recibidas (aceptar / rechazar / contraofertar) y publica artículos. Todo por
HTTP plano (`urllib`) salvo `publish`, que juega el wizard real de la web con
Playwright (el POST de publicación aún no es replicable por API pura).

```
python3 wallapop.py <comando> ...
```

## Comandos (resumen)

| Comando | Qué hace |
| --- | --- |
| `login` / `logout` / `session` | Identificarse y ver estado de la sesión |
| `search` | Buscar anuncios (`wallapop search 'ps5' --city madrid`) |
| `deals` | Buscar y rankear gangas (precio vs mediana del mercado) |
| `watch` | Escaneo periódico con auto-oferta / auto-mensaje |
| `offer` | Hacer ofertas, responder y ver el panel de negociaciones |
| `message` | Enviar mensaje de chat a un vendedor |
| `replies` | Ver inbox y hilos completos |
| `publish` | Publicar un artículo vía el wizard real de la web (Playwright) |

## Documentación

La documentación está dividida en cuatro documentos:

- [**Uso de la CLI**](docs/USAGE.md) — referencia de todos los comandos y
  flags: login, búsqueda, deals/watch, ofertas (con el panel `offer status` y
  la leyenda de estados), mensajería y detalles (ritmo de requests, colores,
  transporte).
- [**Arquitectura**](docs/ARCHITECTURE.md) — estructura del repo
  (`wallapoplib/`), cómo fluye una invocación, sesión/autenticación, transporte
  HTTP (y el anti-bot de los endpoints `BFF`), mensajería y gestión de ofertas.
- [**Endpoints**](docs/ENDPOINTS.md) — catálogo de cada endpoint que usa la
  CLI, cómo se extrajeron del bundle JS de la web, y el significado de los
  identificadores públicos de la app (las constantes `API_BASE`, `PUB_KEY`,
  `SUB_KEY`, `PUB_ORIGIN`, etc. de `config.py`).
- [**API de librería**](docs/API.md) — quickstart y contrato público de
  `wallapoplib` como librería (dataclasses, cliente, dominio).

## Usar como librería

`wallapoplib` es un paquete instalable (ya listo para PyPI; la publicación en
PyPI queda **diferida**): contiene el cliente tipado + las dataclasses de
dominio y trae la CLI dentro. Sin publicar, se instala localmente en modo
editable:

```
pip install -e '.[test]'
```

Uso mínimo:

```python
from wallapoplib import Wallapop, load_session

client = Wallapop(token=load_session().get("accessToken"))
items = client.search("nintendo switch lite", pages=1)
for it in items:
    print(it.price, it.title, it.url)
```

Ver [docs/API.md](docs/API.md) para el contrato completo (dataclasses `Item` /
`Deal` / `OfferEvent`/`OfferState`, `rank_deals`, `parse_offer_events`,
respuesta a ofertas, chating idempotente).

## Instalación rápida

**No hay dependencias obligatorias.** Toda la comunicación con Wallapop se hace
por HTTP plano con la stdlib de Python: solo necesitas `python3` (≥ 3.10).

Puedes usarla de dos formas:

1. **Sin instalar nada** — con el repo clonado:

   ```
   python3 wallapop.py <comando> ...
   ```

2. **Como paquete instalable** (con la CLI `wallapop` incluida):

   ```
   pip install -e '.[test]'     # desarrollo local
   ```

### Playwright es opcional (excepto para `publish`)

El envío de mensajes de chat funciona por PubNub REST, sin navegador. Solo si
PubNub no confirma la entrega se intenta un **fallback** que abre un navegador
headless (Playwright + Firefox) con tus cookies de sesión. Para el flujo normal
**no necesitas instalar Playwright**.

El comando `publish` sí lo necesita: abre el **wizard de la web** headful para
crear el anuncio (cromium vía Playwright):

```
pip install --user --break-system-packages playwright
python3 -m playwright install chromium
```

Añade `pip install -e '.[browser]'` para incluir la dependencia de Playwright
en el entorno del paquete.

## Sesión

- `wallapop login your@email.com` (pedirá contraseña de forma segura)
- `wallapop login --browser firefox` — importa la sesión de Firefox
  (funciona para cuentas registradas con Google)
- `wallapop login` (con `cookies.txt` en el directorio) — login pegando el
  valor del campo `Cookie` de una request (F12 → Cabeceras → Copiar valor);
  ver `cookies.txt.example` y `docs/USAGE.md`.
- Los tokens y cookies se guardan en `~/.config/wallapop_cli/`, **fuera del
  repo** (no se suben a git).

## Seguridad (qué hay en el repo y qué no)

- El repositorio contiene solo **código y docs**. Las claves de PubNub
  (`PUB_KEY`/`SUB_KEY`) y la versión de la app (`WEB_APP_VERSION`) son
  identificadores públicos que la web expone en su bundle JS: no son secretos.
- Lo que sí es sensible (access/refresh token y cookies de sesión) vive en
  `~/.config/wallapop_cli/` y está excluido de git (`.gitignore`).