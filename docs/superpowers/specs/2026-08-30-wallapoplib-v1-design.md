# Diseño: wallapoplib v1.0 — librería PyPI de comunicación con Wallapop

Fecha: 2026-08-30
Estado: propuesta (design doc previo al plan de implementación)

## Contexto y objetivo

`wallapop-cli` es hoy una CLI en Python que se comunica con Wallapop por HTTP
plano (urllib, sin navegador simulado). El objetivo es convertirlo en una
**librería publicada en PyPI** (`wallapoplib`) con la que otros proyectos puedan
integrarse por red, **manteniendo la CLI como parte del mismo paquete**.

Decisiones ya cerradas en la entrevista:

1. **Un único paquete** `wallapoplib` que expone API pública de librería y trae
   la CLI dentro (`wallapoplib.cli`). Instalando una vez obtienes ambos.
2. Superficie pública hasta la **capa 2**: cliente tipado (endpoints) + dominio
   (ledger de negociación, estados, ranking de gangas). Sin fachada de capa 3.
3. Los tipos públicos del dominio son **dataclasses tipadas** (no dicts).
4. La CLI **se mantiene íntegra** (mismos comandos), solo se reorganiza.
5. Barrera pre-PyPI: **tests de pytest reales (offline) + CI en GitHub Actions**.
6. Nombre: **`wallapoplib`** en PyPI e import.
7. **Cero dependencias obligatorias** (solo stdlib). Playwright es opcional
   (extra `[browser]`), únicamente para el fallback de envío de chat por
   navegador. La importación de cookies desde Firefox es stdlib (`sqlite3`).

## Estructura del paquete y frontera público/interno

Layout plano (la raíz del repo se mantiene; `python3 wallapop.py` sigue
funcionando sin instalación):

```
wallapop.py               # entrypoint fino -> wallapoplib.cli.main:main
wallapoplib/
  __init__.py             # API pública curada (lo único prometido)
  client.py               # Wallapop (PÚBLICO)
  models.py               # dataclasses Item/OfferEvent/Deal + funciones de dominio (PÚBLICO)
  ids.py                  # resolve_internal_id (PÚBLICO)
  tokens.py               # utils de JWT/claims (PÚBLICO)
  http.py                 # ApiError, Response (PÚBLICO: errores del cliente)
  config.py               # constantes + storage de sesión (semipúblico: load/save)
  cli/                    # NUEVO subpaquete de consumo (NO contrato público)
    __init__.py           #   re-exporta main
    main.py               #   argparse + main()   (entry point: wallapoplib.cli.main:main)
    commands.py           #   handlers: login, search, deals, watch, offer, message, replies...
    output.py             #   render en terminal (colores, tablas)
```

Cambios concretos de organización:

- Mover `wallapoplib/cli.py` -> `wallapoplib/cli/main.py`
- Mover `wallapoplib/commands.py` -> `wallapoplib/cli/commands.py`
- Mover `wallapoplib/output.py` -> `wallapoplib/cli/output.py`
- Adaptar los imports internos: `from .config import ...` sigue igual; lo que
  importaba `from ..cli...`, `from .commands import ...`, `from .output import
  ...` se reencamina.
- `wallapop.py` y el entry point instalado apuntan a `wallapoplib.cli.main:main`.
- Los módulos semipúblicos (`http`, `tokens`, `config`, `ids`) son importables
  pero **no forman parte del contrato**; `__init__` solo exporta lo prometido.

## API pública (contrato estable v1)

`wallapoplib/__init__.py` exporta:

- `Wallapop` (cliente: `item`, `search`, `send_offer`, `offer_response`,
  `counter_offer`, `send_message`, `conversation_thread`, `inbox`,
  `make_offer_info`, login/session...).
- `models`: `Item`, `OfferEvent`, `OfferState`, `Deal`, `parse_offer_events`,
  `offer_state_summary`, `rank_deals`.
- `ids.resolve_internal_id`.
- `http`: `ApiError`, `Response`.
- `config`: ayudas de sesión (`load_credentials`, `load_session`, `save_session`).
- `__version__`.

### Dataclasses públicas

```python
@dataclass Item:
    id, title, price: float, currency, city, user_id, user_name,
    url, shippable: bool

@dataclass OfferEvent:
    date, kind, amount: float|None, offer_id: str|None   # kinds:
    # our-offer | seller-counter | our-counter | seller-accepted | accepted-buy |
    # seller-declined | our-declined | expired

@dataclass OfferState:
    kind, label, amount, offer_id, actionable
    # estado terminal CLI: PENDIENTE / EN ESPERA / ACEPTADA / RECHAZADA / CADUCADA

@dataclass Deal:
    item: Item, discount: float, median: float
```

`parse_offer_events(messages) -> list[OfferEvent]` (con forward-fill de
`amount`/`offer_id` en eventos terminales).
`offer_state_summary(events) -> OfferState`.
`rank_deals(items) -> list[Deal]` (mediana + descuento).

La CLI se adapta para consumir estas dataclasses (leer atributos en vez de
keys). Es el refactor principal de esta fase.

## Empaquetado y distribución

- `pyproject.toml` con **hatchling** (respeta `.gitignore`: `cookies.txt`,
  `session.json`, `credentials.json` no entran al sdist/whl).
- Nombre `wallapoplib`, versión `0.1.0`, `requires-python >= 3.10`.
- Dependencias: ninguna obligatoria (solo stdlib). Extra opcional
  `[browser] = ["playwright"]` solo para el fallback de chat.
  Extra `[test] = ["pytest"]` para desarrollo.
- Entry point: `wallapop = wallapoplib.cli.main:main`.
- Licencia MIT; README como long-description.
- Verificación post-build: `python -m build` no debe contener `cookies.txt` ni
  `session.json` ni `credentials.json`.

## Tests + CI

`pytest`, sin red (sin tocar la API):

- `tests/test_models.py`: construir mensajes de hilo con los payloads reales de
  Wallapop (`delivery_generic`) y comprobar la clasificación de eventos por
  kind, el orden por timestamp, el forward-fill de importe/offer_id en eventos
  terminales, el mapeo a estados (PENDIENTE/EN ESPERA/ACEPTADA/RECHAZADA/
  CADUCADA) y `rank_deals` (mediana, % de descuento).
- `tests/test_config.py`: `parse_cookie_string` con el valor real de una cookie
  de Wallapop (llaves/commas en `g_state`, valores URL-encoded) → solo las 4
  claves; y detección de claves que faltan.
- `tests/test_ids.py`: `resolve_internal_id` con hash, URL completa, slug y
  entradas inválidas.
- `tests/test_tokens.py`: decodificar un JWT sin firmar (claims: `hashed_sub`,
  `sub`, `device_id`, `exp`) con el JWT real capturado.
- `tests/test_client.py`: transporte HTTP mockeado. Verifica semántica real:
  guard de idempotencia del chat (no reenvía texto idéntico), PATCH accept /
  decline con `{"status": "ACCEPTED"|"DECLINED"}`, cuerpo del counter
  (`offer_price_amount`), y la ruta de error cuando no hay playwright ni ack.

CI: GitHub Actions `python-app.yml` — matrix `3.10/3.11/3.12`,
`pip install -e .[test]`, `pytest` en cada push/PR.

## Docs

- `docs/API.md`: quickstart de uso como librería + referencia de la API pública
  y las dataclasses.
- README: sección "Usar como librería" con ejemplo; nota explícita de que
  Playwright es opcional y para qué (fallback de chat), manteniendo el claim de
  "cero dependencias" para la comunicación por red.
- Docstrings en todos los métodos públicos.

## Envío a PyPI

- **Diferido por decisión del usuario (2026-08-30): NO publicar en PyPI por ahora.**
  El empaquetado (`pyproject.toml`) y la verificación del build se hacen, pero la
  subida (TestPyPI/PyPI) queda fuera de alcance hasta que el usuario lo pida.
- Cuando se active: `0.1.0` en TestPyPI primero; luego PyPI real vía `twine`
  (solo código + docs, nunca sesiones ni cookies).
- Versionado semántico; `__version__` liberada.

## Fuera de alcance (YAGNI, para fases posteriores)

- Fachada de capa 3 (`negotiate(...)` en una llamada).
- `curl_cffi` para desbloquear endpoints BFF vetados por anti-bot TLS.
- Lado vendedor (publicar/edit gestionar anuncios, ofertas recibidas).
- SQLite/estado local histórico, notificaciones externas, multi-cuenta.