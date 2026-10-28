# Multi-photo upload through the pure API

Research notes (September 2026) on publishing listings with **several photos**
through `Wallapop.publish()` without a browser.

**Status: solved.** `Wallapop.publish(photo_paths=[...])` publishes **N photos**
through the pure API: the cover goes in `POST /api/v3/items`, every extra photo
through `picture2`.

## Single photo: how the API publishes

- `Wallapop.publish(photo_paths=[...], ...)` → multipart `POST /api/v3/items`
  (`image` + `item` JSON), headers `Accept: application/vnd.upload-v2+json`,
  `x-deviceos: 0`, `Origin/Referer: https://es.wallapop.com`.
  Answer: 200 `{"id":"<item id>","flags":{}}`.
- Reference: `docs/ENDPOINTS.md` and `wallapop_mcp/client.py`.

## The browser's real multi-photo flow (captured)

Sequence observed when publishing 3 photos (Playwright Chromium,
`set_input_files([...])` with 3 files):

1. `POST /api/v3/steps` init → `{"mode":{"action":"upload","id":"<upload_id>"}}`
2. `POST /api/v3/steps` title → `current_step:"title", draft:{"summary":"..."}`
3. `POST /api/v3/upload/<upload_id>/pictures` → uploads the **cover** (one
   processed photo, ~119 KB, field `image`), answer **204**
4. `POST /api/v3/steps` photo → category → loading
5. `POST /api/v3/items/upload/components` ×2
6. `POST /api/v3/items` → creates the item (the cover comes from the draft upload)
7. `POST /api/v3/items/<id>/picture2` ×(N-1) → attaches the remaining photos

## The `picture2` endpoint

`POST /api/v3/items/<id>/picture2` uploads and attaches an extra photo to an
already created item. It can be replicated over plain HTTP with the **right**
body:

- **body**: `multipart/form-data` with two fields:
  - `image` = the **full JPEG bytes** of the extra photo (not empty).
  - `order` = a **string** with the position (e.g. `"2"`, `"3"`).
- **headers**: the app's own (`Authorization: Bearer <token>`,
  `Origin: https://es.wallapop.com`, `x-deviceos: 0`, `x-deviceid`,
  `x-appversion`, `Accept: application/json, ...`,
  `Content-Type: multipart/form-data; boundary=...`). `_headers(auth=True)`
  already adds them.
- **answer**: 200 `text/plain` with a short photo id (e.g. `4z4ykv5508vz`).
- After each 200, `GET /items/<id>` shows **one more image** (1 → 2 → 3 ...).

### Working flow

1. `POST /api/v3/items` (multipart `image` cover + `item` JSON) → 200, item with 1 photo.
2. For **each extra photo**, `POST /api/v3/items/<id>/picture2` (full image +
   increasing `order`) → 200. Repeat N-1 times.

### Why earlier attempts failed

- Empty image + `order` (what Chromium's 291-byte body looked like) → **400
  `INVALID_PICTURE_SIZE`**: the image must be the real bytes.
- Full image **without** `order` → 400. `order` is **mandatory**.
- `order` alone, no image → 500.
- An expired token answers `401 ACCESS_TOKEN_EXPIRED`; refresh first
  (`refresh_web()` / `refresh_web_session()`).
- The draft-upload path (`/api/v3/upload/<id>/pictures`) only ever creates the
  item with **one** image: the draft does not carry the extra photos. It is no
  longer used.

### Partial failures

If an extra photo fails after the item was created, `publish()` returns a
`Response` with `partial=True` that **keeps the created item id**. Do not
publish again: inspect the item and retry only the missing photo.

## Item attributes and `color`

- `POST /api/v3/items` validates attributes per category. Early tests on
  categories with a color attribute (e.g. 10088, 12463) answered **400
  `INVALID_ATTRIBUTE`** when `brand`, `model` and `color` were all empty, and
  200 as soon as `color` was set.
- The backend does **not** infer brand/model/color from the title; the web's
  "suggested-item-data" step does that in the frontend before posting.
- `color` values are **English** (`blue`), even though the web shows them in
  Spanish ("Azul"): `azul` → 400, `blue` → 200.
- Color-less categories (e.g. mice `10311`) answer `400 INVALID_ATTRIBUTE` when
  `color` is present at all, even empty. `publish()` therefore omits it when
  empty: pass a color only where the category has one.

## Editing a published listing and its photos

- Any attribute (photos included): `PUT /api/v3/items/<id>` with the full body,
  including `pictures`. Full recipe in `docs/ENDPOINTS.md`.
- Adding photos: `picture2` as above. Changing the cover: reorder `pictures`
  in the PUT (`order: 0` = cover).

## Code

- `wallapop_mcp/client.py` → `Wallapop.publish(photo_paths)`, `_add_picture`
  (picture2: full image + `order`), `_cost_configuration_id()`, `_request`, `_authed`.
- `wallapop_mcp/publish.py` → browser publishing (Playwright); not needed for
  multi-photo any more.
- `wallapop_mcp/cli/commands.py` → `_pick_photos` (up to 10), `publish` (API by default).
- `wallapop_mcp/http.py` → `http_request_raw()` (binary multipart bodies).
- `tests/test_client.py` → publish tests, multi-photo included (POST /items + picture2).
