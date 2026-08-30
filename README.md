# Wallapop CLI

CLI de automatización para Wallapop: busca anuncios, detecta gangas (precio por
debajo de la mediana), envía ofertas y mensajes de chat, y gestiona ofertas
recibidas (aceptar / rechazar / contraofertar). Todo por HTTP plano (`urllib`),
sin navegador salvo en un fallback puntual del envío de chat.

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

## Documentación

La documentación está dividida en tres documentos:

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

## Instalación rápida

No hay dependencias obligatorias para casi todo el flujo; solo se necesita
`python3`. El único caso con dependencia extra es el **fallback** del envío de
chat por navegador (Playwright + Firefox), que no es necesario si PubNub
responde:

```
pip install --user --break-system-packages playwright
python3 -m playwright install firefox
```

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