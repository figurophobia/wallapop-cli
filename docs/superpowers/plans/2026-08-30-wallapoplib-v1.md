# Plan: wallapoplib v1 — librería tipada + CLI + tests + CI

Fecha: 2026-08-30. Spec: `docs/superpowers/specs/2026-08-30-wallapoplib-v1-design.md`.

## Purpose (resumen)

| Qué | Cómo |
| --- | --- |
| Paquete instalable único `wallapoplib` | `pyproject.toml` hatchling, `pip install -e .`, entry point `wallapop` |
| Capa 2 pública: cliente tipado + dominio | dataclasses `Item`, `Deal`, `OfferEvent`, `OfferState` en `models.py` |
| CLI dentro del paquete | `wallapoplib/cli/` (`main.py`, `commands.py`, `output.py`) |
| Tests reales sin red | pytest con payloads/URLs/tokens reales des-sensibilizados + transporte mockeado |
| CI | GitHub Actions, matrix 3.10/3.11/3.12, `pytest` |
| Docs de API | `docs/API.md` + docstrings públicos en `__init__.py` |

DECISIÓN CLAVE: **no se publica en PyPI** (anotado en la spec, commit `9196223`). El paquete se instala localmente con `-e`.

## Contexto (grados de coerción)

LO MÁS FUERTE (no mover):
- `parse_cookie_string`, `sync_user_0` y los usuarios hardcodeados viven en `config.py` (el SDK de wallapop solo responde a peticiones de `device_id`/`user_0` registrados). NO se mueven, NO se parametrizan.
- `client.py` es el único que toca la red; `commands`/`cli` solo leen/mueven/renderean sus `Response`/dataclasses.
- Los payloads de test salen de capturas reales del ledger (pixelia/Daniel/Chiara), solo con ids des-sensibilizados.
- No hay fachada `WallapopFacade`; los comandos usan `client` directamente (decisión de spec).
- `resolve_*` (ids/conversaciones) quedan como funciones puras en `ids.py` salvo `resolve_conversation_thread`, que mueve red y se queda en `client.py`… (compromiso: la función `resolve_conversation_thread` que llama red se queda en `ids.py` importando `client`; solo las puras van a quedarse puras).

LO INTERMEDIO (pasa por refactor con cuidado):
- `normalize_item`: cambia a clase `Item` con `Item.from_raw(...)`; `client.search()/_search_sections()/_search_legacy()/item()` devuelven `list[Item]` / `Item`.
- `parse_offer_events`/`offer_state_summary` → `list[OfferEvent]` / `OfferState | None`.
- `render_template`, `print_items`, `print_deals`, `emit_csv`, `rank_deals`, `create_deal` pasan a comportamiento de `Item`/`Deal`.

LO MÁS FLEXIBLE (relocación, sin cambio de lógica):
- `cli.py → wallapoplib/cli/main.py`, `commands.py → wallapoplib/cli/commands.py`, `output.py → wallapoplib/cli/output.py`; `wallapop.py` sigue en raíz como wrapper (`from wallapoplib.cli.main import main`).
- `print_offer_events` se mueve de `models.py` a `cli/output.py` (es rendering).

## Tasks

Ordenadas: cada una deja el árbol en estado verde. La última es el punto de verificación.

### Task 1 — `pyproject.toml` + entorno de tests

1. Crear `pyproject.toml` (hatchling):
   - `project.name = "wallapoplib"`, `version = "0.1.0"`, `requires-python = ">=3.10"`, `license = "MIT"`.
   - `[project.optional-dependencies]`: `test = ["pytest>=8"]`, `browser = ["playwright"]`.
   - `[project.scripts]`: `wallapop = "wallapoplib.cli.main:main"`.
   - `[tool.pytest.ini_options]` `testpaths = ["tests"]`.
   - `[tool.hatch.build.exclude]` incluye `cookies.txt.example`, `/tests`, `/docs` (evita empaquetar nada sensible).
2. Añadir a `.gitignore`: `build/`, `dist/`, `*.egg-info/`, `.venv/`, `.pytest_cache/`.
3. `python3 -m venv .venv && .venv/bin/pip install -e '.[test]'`.
   - Ejecutar `wallapop --help` → debe listar comandos (fallará al importar `wallapoplib.cli.main` porque cli/ no existe aún; marcar la Task 5 para arreglar la entrada y OK: apuntar que `--help` aún no es verde hasta la Task 5).

**Terminación**: `.venv/bin/python -m pytest` arranca (0 tests) y `.venv/bin/pip show wallapoplib` apunta a editable.

### Task 2 — `config.py`: versiones puras + tests

1. Mover `VERSION` a ser pegado con la versión de pyproject (dejar solo `VERSION = "0.1.0"`).
2. Extraer subtests de `parse_cookie_string` (ya existe) en `tests/test_config.py`:
   - `token; device_id=abc; device_fgp=xyz` → dict con `device_id`/`device_fgp`.
   - texto con espacios en valores (`accessToken=abc def`).
   - salto de línea y `; ` entre pares.
   - entrada vacía → `{}`.

**Terminación**: tests verdes con `python3 -m pytest tests/test_config.py`.

### Task 3 — `ids.py`: tests de funciones puras

En `tests/test_ids.py`:
- `is_internal_id`: "z876f1" (11 alfanum) → True; uuid → False; "v6gpwy9d476e" → True (real, no sensible).
- `is_invalid_id`/ids de pelea (id inválido `wz...`), `resolve_internal_id` con input ya-reducido.
- `resolve_offer_action` mapea `accept|decline|counter|status` y levanta `ApiError` otherwise.
- `resolve_conversation_thread` con `client` stub: thread ok → channel/hash; thread err → conv al hilo; item id → hilo nuevo.

**Terminación**: `python3 -m pytest tests/test_ids.py` verde.

### Task 4 — `tokens.py`: tests sin red

En `tests/test_tokens.py`:
- `parse_jwt_payload` del payload real des-sensibilizado (el que ya está en spec) → `{iat, exp, sub, "https://hasura.io/jwt/claims": {...}}`.
- `parse_access_token_and_expiry` con `accessToken=...` + `exp` parseado del JWT → token/expiry correctos.

**Terminación**: verde en `tokens.py`.

### Task 5 — `models.Item`: dataclass + `normalize_item` → `Item`

1. En `models.py`: `@dataclass Item` con todos los campos que hoy produce `normalize_item` (id, slug, title, description, price, currency, ubication_exact, city, category, category_id, category_name, reserved, sold, created_at, updated_at, shippable, user_id, web_slug, extra, url).
   - Método de clase `Item.from_raw(raw: dict, fallback_user_id: str | None = None) -> Item`.
   - Field `url` es property calculado (como hoy), igual que `price`.
   - Mantener `normalize_item` como alias: `def normalize_item(raw, fallback_user_id=None) -> Item: return Item.from_raw(...)`.
2. `client.py`: cambiar firmas de `search` → `list[Item]`, `_search_sections`/`_search_legacy` → `list[Item]`, `item(item_id) -> Item`, `make_offer_info` devuelve el payload crudo (NO tocar; ya es `Response`).
3. Adaptar consumidores dict→`Item`:
   - `commands.fetch_items` (líneas 106-131): `it.reserved`, `it.sold`, `it.category`, `it.price`, `it.shippable` pasan a atributos; el resto igual.
   - `commands.deliver` (134-154): `it.price`, `it.title`, `it.id` → atributos; `it["id"]`.
   - `commands.maybe_deliver_all` (157-168): until `it["id"] in contacted` → `it.id`.
   - `output.rank_deals(items: list[Item] | list[Deal], median=<patrón>) -> tuple[list[Deal], float]` (se adapta después de Deal, ver Task 6).
   - `output.render_template(template, it: Item, amount)`, `output.print_items`, `output.print_deals`, `output.emit_csv` usan atributos.
   - `cli/main.py` (cmd_search, cmd_deals, cmd_item, etc.) → atributos en `Item`.
4. `tests/test_models.py`:
   - `Item.from_raw` con payload real de search (captura de `search/general/search` des-sensibilizada) → todos los campos.
   - `normalize_item` con `fallback_user_id` (id usuario resuelto → `user_id`).
   - propiedades `url`, `price`.

**Terminación**: `python3 -m pytest tests/test_models.py` verde; `wallapop search` sigue funcionando (con sesión real o `--dry-run`).

### Task 6 — `models.OfferEvent` / `OfferState`: dataclasses + tests

1. `@dataclass OfferEvent`: `date (formateado como hoy), ts, kind, amount: float | None, offer_id: str | None, text`.
   - `parse_offer_events(msgs) -> list[OfferEvent]` mantiene la lógica de detección de eventos (from_seller/from_self × status/offer) y forward-fill de `amount`/`offer_id`.
2. `@dataclass OfferState`: `offer_id, amount: float | None, kind, actionable: bool`.
   - `offer_state_summary(events) -> OfferState | None` (misma regra terminal que hoy).
3. Adaptar consumidores:
   - `commands.run_offer_status` (351-430): `state["kind"]` → `state.kind`, `state["offer_id"]`, `state.get("amount")` → `state.amount`; `ev.get("offer_id")` → `ev.offer_id`.
   - `commands.find_conversation_for_offer` (433-452): `ev.get("offer_id")` → `ev.offer_id`.
   - `commands.render_thread_offer_status` (455-483): `state["..."], state( )` → atributos.
   - `output.print_offer_events(events)`: pasa a `cli/output.py` y usa `ev.date`, `ev.kind`, `ev.amount`.
   - `cli/main.py` usa `ev.offer_id`, `ev.amount` donde toque.
4. Tests en `tests/test_models.py` con el payload real del hilo pixelia (ledger: winner/join events con ofertas) des-sensibilizado:
   - `parse_offer_events` encuentra los 3 eventos (our-offer, seller-counter, accepted-buy) con forward-fill correcto.
   - `offer_state_summary` devuelve `kind=accepted-buy`, `offer_id` correcto, `actionable=True`/`False` según flujo.

**Terminación**: verde + `wallapop offer status <conv_hash>` y `offer status` rápidos con sesión real renderizan igual que antes.

### Task 7 — `cli/` relocation

1. `mkdir wallapoplib/cli && git mv cli.py commands.py output.py wallapoplib/cli/` + crear `wallapoplib/cli/__init__.py`.
2. Ajustar imports:
   - `cli/main.py`: `from .commands import ...`; `from .output import ...`; `from ..client import ...`; `from ..models import`.
   - `cli/commands.py`: `from ..client import` ...; `from ..models import`; los que citan `config`/`ids`/`tokens` desde `..`.
   - `cli/output.py`: `from ..config import` ...; `from ..models import`.
   - `client.py` (fuera del cli/) mantiene sus imports (`from .config/ids/http/models`).
3. `wallapop.py` → `from wallapoplib.cli.main import main`.
4. Entry point de pyproject ya apunta a `wallapoplib.cli.main:main`.

**Terminación**: `wallapop --help` y `wallapop search --dry-run` funcionan (desde raíz y con `.venv/bin/wallapop`).

### Task 8 — `__init__.py`: superficie pública + `__all__`

1. Exportar `Item`, `Deal`, `OfferEvent`, `OfferState`, `Wallapop`, `Response`, `ApiError`, `VERSION` (re-export desde `config`), `normalize_item`/`parse_offer_events`/`offer_state_summary`.
2. Sanity: docstring de módulo (FIRST-3 clientes santitarios) + `# public API` sections.
3. `conv`? `--version`/`version` CLI flags en `cli/main.py` reportan `VERSION`.

**Terminación**: `.venv/bin/wallapop version` imprime `0.1.0`; `python3 -c "import wallapoplib as w; w.Item, w.Deal"` ok.

### Task 9 — `client` con transporte mockeado

`tests/test_client.py` (monkeypatch `wallapoplib.client.http_request`):
- `search()` → mock: `GET /search/components` → payload con `search_id`, `GET /search/section` → payload con items + `next_page`; assert devuelve `list[Item]` con 2 items (idem paginación con delay=0).
- `refresh_web_session` ok → access_token actualizado; luego `_authed` llama con el nuevo token.
- `send_offer(id, 25.0)`: assert POST `/api/v3/delivery/buyer/offers` con `offer_id` uuid válido y `offer_price_amount==25.0` (payload exacto).
- `counter_offer` / `offer_response` PATCH correcto.
- `make_offer_info` → `Response` (restriccionese) parseado.
- `send_message` idempotencia: si el hilo ya contiene el mismo texto de mí → NO publica (report.skip); si es distinto → publica en `pubnub_publish` URL correcta de `PUB_ORIGIN` con `pnsdk=PNSDK`.
- `send_chat_text` sin playwright (o `import` falla) → degradación con mensaje claro (comportamiento actual del `ImportError`), verificar con monkeypatch de `importlib`.

**Terminación**: `pytest tests/test_client.py` verde — todo con `http_request` mockeado (cero red).

### Task 10 — CI

1. `.github/workflows/ci.yml`: on push/PR, checkout, `setup-python` matrix `[3.10, 3.11, 3.12]`, `pip install -e '.[test]'`, `pytest`.
2. `pytest` sin red en CI (mocks únicamente).

**Terminación**: workflow válido (se puede hacer dry-run local `act` si disponible; si no, validación YAML).

### Task 11 — Docs y docstrings

1. `docs/API.md`: superficie pública (Item/Deal/OfferEvent/OfferState/Wallapop/commands CLI) + ejemplo de búsqueda, oferta, contraoferta, acept/enviar mensaje con `wallapoplib` como librería.
2. Docstring de módulo en `__init__.py` apuntando a `docs/API.md`.
3. `docs/USAGE.md`: nota “instalado localmente”, sin referencia a PyPI.

**Terminación**: `código leído` — API.md coherente con `__all__` real.

### Task 12 — Verificación final (punto de control)

1. `python3 -m build` (instalar `build` en .venv) → sdist y wheel generados.
2. Del artefacto wheel: `unzip -l` → NO contiene `cookies.txt`, `credentials.json`, `session.json`.
3. Instalar wheel en venv limpio → `wallapop --help` y `wallapop search --dry-run` funcionan.
4. `python3 -m pytest` completo verde (tasks 2-9).
5. Confirmar que el repo no contiene secretos: `rg -i 'accessToken|session-token|device_fgp' --glob '!cookies.txt' --glob '!*.json' .` sin hallazgos.
6. Commit.

**Terminación**: TODO lo anterior verde+limpio → v1 completa, sync a `git push` (si se solicita).

## Términos (glosario mínimo)

- **ledger**: lista de eventos de oferta en un hilo de chat (messages con `type: offer`).
- **forward-fill**: propagar `amount`/`offer_id` desde el último evento que los define a los terminales.
- **capa 2**: cliente tipado + dataclasses de dominio (lo que el paquete expone); la CLI cambia pero la red vive en `client.py`.