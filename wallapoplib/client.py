"""The Wallapop API client: every endpoint call + session bootstrap."""

from __future__ import annotations

import json
import os
import random
import time
import uuid
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from .config import (
    API_BASE,
    API_ROOT,
    DEFAULT_GEO,
    PNSDK,
    PUB_KEY,
    PUB_ORIGIN,
    SUB_KEY,
    USER_AGENTS,
    WALLAPOP_COOKIE_KEYS,
    WEB_APP_VERSION,
    WEB_BASE,
    load_session,
    save_session,
)
from .http import ApiError, Response, eprint, http_request, http_request_raw
from .models import Item, deep_find, extract_items, first, normalize_item
from .tokens import user_hash_of

def refresh_web_session(session: dict) -> Optional[str]:
    """Exchange a browser session cookie set for a fresh access token (GET /api/auth/session)."""
    session_token = session.get("__Secure-next-auth.session-token")
    if not session_token:
        return None
    cookie_parts = [f"__Secure-next-auth.session-token={session_token}"]
    for key in ("device_id", "device_fgp", "accessToken"):
        if session.get(key):
            cookie_parts.append(f"{key}={session[key]}")
    headers = {
        "Accept": "application/json",
        "Cookie": "; ".join(cookie_parts),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    status, data, _ = http_request("GET", f"{WEB_BASE}/api/auth/session", headers=headers)
    if status != 200 or not isinstance(data, dict):
        return None
    token = data.get("token") or data.get("accessToken") or ""
    return str(token) if isinstance(token, str) and token else None


def persist_browser_session(session: dict, token: Optional[str] = None) -> None:
    """Save a browser session (cookies + optional access token) to local storage."""
    updated = dict(session)
    if token:
        updated["accessToken"] = token
    save_session(updated)


class Wallapop:
    def __init__(
        self,
        token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        email: Optional[str] = None,
        persist_fn: Optional[Callable[["Wallapop"], None]] = None,
        web_cookies: Optional[dict] = None,
    ) -> None:
        self.token = token
        self.refresh_token = refresh_token
        self.email = email
        self.persist_fn = persist_fn
        self.web_cookies = web_cookies
        self.user: dict = {}

    def _headers(self, auth: bool = False) -> dict:
        device_id = (self.web_cookies or {}).get("device_id", "") or ""
        hdrs = {
            "Accept": "application/json, text/plain, */*",
            "Origin": WEB_BASE,
            "Referer": f"{WEB_BASE}/",
            "User-Agent": random.choice(USER_AGENTS),
            "X-DeviceOS": "0",
            "X-Product": "wallapop",
            "X-AppVersion": "826560",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }
        if device_id:
            hdrs["X-DeviceID"] = device_id
        if auth and self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        return hdrs

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        auth: bool = False,
    ) -> Response:
        status, data, _ = http_request(
            method, f"{API_BASE}{path}", params=params, body=body, headers=self._headers(auth=auth)
        )
        return Response(status, data, 200 <= status < 300, status == 401)

    def _authed(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Response:
        resp = self._request(method, path, params=params, body=body, auth=True)
        if resp.unauthorized:
            self.refresh_web()
            resp = self._request(method, path, params=params, body=body, auth=True)
        return resp

    def refresh_web(self) -> bool:
        """Refresh the access token from the browser session cookie; return True on success."""
        if not self.web_cookies:
            return False
        token = refresh_web_session(self.web_cookies)
        if not token:
            return False
        self.token = token
        if self.persist_fn is not None:
            self.persist_fn(self)
        return True

    def _save(self) -> None:
        if self.persist_fn is not None:
            self.persist_fn(self)

    def login(self, email: str, password: str) -> tuple[str, str]:
        """Log in by email/password and return (access_token, refresh_token)."""
        resp = self._request(
            "POST",
            "/access/login",
            body={"email": email, "password": password, "client_id": CLIENT_ID},
        )
        if not resp.ok:
            raise ApiError(f"login failed ({resp.status}): {resp.error_reason or resp.data}")
        data = resp.data
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        token = payload.get("access_token") or payload.get("token") or ""
        refresh = payload.get("refresh_token") or ""
        if not token:
            raise ApiError(f"login accepted but no token in response: {json.dumps(data, ensure_ascii=False)[:400]}")
        self.email = email
        self.token = token
        self.refresh_token = refresh
        self.user = payload.get("user", {}) if isinstance(payload, dict) else {}
        self._save()
        return token, refresh

    def try_refresh(self) -> bool:
        """Exchange refresh_token for a fresh access token; return True on success."""
        if not self.refresh_token or not self.token:
            return False
        resp = self._request(
            "POST",
            "/access/refresh",
            body={"refresh_token": self.refresh_token, "client_id": CLIENT_ID},
        )
        if not resp.ok:
            return False
        data = resp.data
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        token = payload.get("access_token") or payload.get("token") or ""
        if not token:
            return False
        self.token = token
        self.refresh_token = payload.get("refresh_token") or self.refresh_token
        self._save()
        return True

    def search(
        self,
        keywords: str,
        category_id: Optional[int] = None,
        subcategory_ids: Optional[int] = None,
        order_by: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        pages: int = 1,
        delay: float = 0.0,
        condition: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        ship_only: Optional[bool] = None,
    ) -> list[Item]:
        if latitude is None or longitude is None:
            latitude, longitude = DEFAULT_GEO
        order = order_by or "most_relevance"
        base = {
            "keywords": keywords,
            "source": "search_box",
            "order_by": order,
            "filters_source": "search_box",
        }
        if category_id:
            base["category_id"] = category_id
        if subcategory_ids:
            base["subcategory_ids"] = subcategory_ids
        if condition:
            base["condition"] = condition
        if min_price is not None:
            base["min_sale_price"] = min_price
        if max_price is not None:
            base["max_sale_price"] = max_price
        if ship_only:
            base["is_shippable"] = "true"
        if latitude is not None and longitude is not None:
            base["latitude"] = latitude
            base["longitude"] = longitude
        components = self._request("GET", "/search/components", params=base)
        search_id = first(deep_find(components.data, "search_id"))
        if search_id:
            return self._search_sections(base, search_id, pages, delay)
        return self._search_legacy(keywords, category_id, order, latitude, longitude, condition=condition, subcategory_ids=subcategory_ids, min_price=min_price, max_price=max_price, ship_only=ship_only)

    def _search_sections(self, base: dict, search_id: str, pages: int, delay: float) -> list[Item]:
        all_items: list[Item] = []
        params = dict(base)
        params.update({"search_id": search_id, "section_type": "organic_search_results", "source": "deep_link"})
        next_page: Optional[str] = None
        for page in range(max(pages, 1)):
            qp = dict(params)
            if next_page:
                qp["next_page"] = next_page
            resp = self._request("GET", "/search/section", params=qp)
            if not resp.ok:
                raise ApiError(f"search failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            items = extract_items(resp.data)
            all_items.extend(normalize_item(item) for item in items)
            next_page = first(deep_find(resp.data, "next_page"))
            if not next_page:
                break
            if page < max(pages, 1) - 1 and delay > 0:
                time.sleep(delay + random.uniform(0, 0.5))
        return all_items

    def _search_legacy(
        self,
        keywords: str,
        category_id: Optional[int],
        order: str,
        latitude: Optional[float],
        longitude: Optional[float],
        condition: Optional[str] = None,
        subcategory_ids: Optional[int] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        ship_only: Optional[bool] = None,
    ) -> list[Item]:
        params = {
            "keywords": keywords,
            "order_by": order,
            "filters_source": "search_box",
            "source": "search_box",
        }
        if category_id:
            params["category_ids"] = category_id
        if subcategory_ids:
            params["subcategory_ids"] = subcategory_ids
        if condition:
            params["condition"] = condition
        if min_price is not None:
            params["min_sale_price"] = min_price
        if max_price is not None:
            params["max_sale_price"] = max_price
        if ship_only:
            params["is_shippable"] = "true"
        if latitude is not None and longitude is not None:
            params["latitude"] = latitude
            params["longitude"] = longitude
            params["distance"] = 1000000
        resp = self._request("GET", "/general/search", params=params)
        if not resp.ok:
            raise ApiError(f"search failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        objs = data.get("search_objects", []) if isinstance(data, dict) else []
        return [normalize_item(obj) for obj in objs]

    def categories(self) -> list[dict[str, Any]]:
        """Return the search category tree (same source as the web sidebar).

        Each node is ``{"id": int, "name": str, "subcategories": [...]}``
        (recursive). Backed by ``GET /api/v3/categories?context=search``, the
        canonical menu the search page renders.
        """
        resp = self._request("GET", "/categories", params={"context": "search"})
        if not resp.ok:
            raise ApiError(f"categories failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        raw = data.get("categories", []) if isinstance(data, dict) else []

        def clean(node: dict) -> dict[str, Any]:
            return {
                "id": node.get("id"),
                "name": node.get("name", ""),
                "subcategories": [clean(s) for s in node.get("subcategories") or []],
            }

        return [clean(c) for c in raw]

    def item(self, item_id: str) -> Item:
        resp = self._request("GET", f"/items/{item_id}")
        if not resp.ok:
            raise ApiError(f"item {item_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        raw = data if isinstance(data, dict) else {}
        return Item.from_raw(raw)

    def seller(self, user_id: str) -> dict:
        """Fetch a seller's public profile (GET /users/{id})."""
        resp = self._request("GET", f"/users/{user_id}")
        data = resp.data if isinstance(resp.data, dict) else {}
        return data

    def seller_reviews(self, user_id: str) -> list[dict]:
        """Fetch a seller's recent reviews (GET /users/{id}/reviews)."""
        resp = self._request("GET", f"/users/{user_id}/reviews")
        data = resp.data
        if not isinstance(data, list):
            return []
        return data

    def seller_stats(self, user_id: str) -> dict:
        """Fetch a seller's aggregated stats incl. rating average and review count (GET /users/{id}/stats).

        Returns the real values the web shows on an item page (~rating_average
        and counters[].reviews), unlike /reviews which only gives one page.
        """
        resp = self._request("GET", f"/users/{user_id}/stats")
        data = resp.data if isinstance(resp.data, dict) else {}
        return data

    def download_images(self, item: Item, dest: str = "images") -> list[str]:
        """Download an item's images into ``dest`` and return the saved file paths.

        Uses each URL in ``item.images``; filenames are derived from the item id
        plus an index (``{item_id}_1.jpg``, ...). The directory is created if it
        does not exist. Uses only the standard library.
        """
        images = list(item.images)
        if not images:
            return []
        out_dir = Path(dest)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for i, url in enumerate(images, start=1):
            ext = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
            filename = f"{item.id}_{i}{ext}"
            target = out_dir / filename
            try:
                with urllib.request.urlopen(url, timeout=30) as resp, open(target, "wb") as f:
                    f.write(resp.read())
                saved.append(str(target))
            except Exception:
                continue
        return saved

    def send_offer(self, item_id: str, amount: float, message: Optional[str] = None) -> Response:
        """POST the delivery/buyer offer endpoint (the web app's real offer send).

        The legacy /items/{id}/offers endpoint is dead (404). The current flow
        posts to /api/v3/delivery/buyer/offers with a client-generated offer_id
        (uuid) and the item's price currency.
        """
        body: dict[str, Any] = {
            "offer_id": str(uuid.uuid4()),
            "offer_price_amount": float(round(amount, 2)),
            "offer_price_currency": self._item_currency(item_id),
            "item_ids": [item_id],
        }
        if message:
            body["message"] = message
        resp = self._authed("POST", "/delivery/buyer/offers", body=body)
        if not resp.ok and message and (resp.status == 400 or resp.status == 422):
            del body["message"]
            resp = self._authed("POST", "/delivery/buyer/offers", body=body)
        return resp

    def offer_response(self, offer_id: str, action: str) -> Response:
        """Accept or decline an offer (e.g. a seller's counteroffer).

        Maps to PATCH /api/v3/delivery/offers/{id}/status with the status the
        web app uses (ACCEPTED / DECLINED).
        """
        status_map = {"accept": "ACCEPTED", "decline": "DECLINED"}
        status = status_map.get(action)
        if not status:
            raise ApiError(f"invalid offer action: {action!r} (use accept or decline)")
        return self._authed(
            "PATCH", f"/delivery/offers/{offer_id}/status", body={"status": status}
        )

    def counter_offer(self, offer_id: str, amount: float, currency: str = "EUR") -> Response:
        """Replace a pending offer's price with a new counteroffer.

        Maps to PATCH /api/v3/delivery/offers/{id}/offer-price (the web app's
        "hacer una nueva oferta" flow on an open offer).
        """
        return self._authed(
            "PATCH",
            f"/delivery/offers/{offer_id}/offer-price",
            body={
                "offer_price_amount": float(round(amount, 2)),
                "offer_price_currency": currency,
            },
        )

    def offer_details(self, offer_id: str) -> Response:
        """Fetch the Offer Tracking Status payload for an offer.

        Only meaningful when WE are the party waiting for reply (the modal
        payload is generated for the current responder).
        """
        return self._bff("GET", "/delivery/offer-details", params={"offer_id": offer_id})

    def _item_currency(self, item_id: str) -> str:
        return self.item(item_id).currency

    def make_offer_info(self, item_id: str) -> Response:
        """Fetch the offer editor config (price, max_offer_percentage, restriction)."""
        return self._bff("GET", "/bff/delivery/make-an-offer", params={"item_id": item_id})

    def _bff(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> Response:
        full = f"{API_ROOT}{path}"
        if params:
            full += ("&" if "?" in full else "?") + urllib.parse.urlencode(params)
        status, data, _ = http_request(method, full, body=body, headers=self._headers(auth=True))
        if status == 401:
            self.refresh_web()
            status, data, _ = http_request(method, full, body=body, headers=self._headers(auth=True))
        return Response(status, data, 200 <= status < 300, status == 401)

    def ensure_conversation(self, item_hash: str) -> Response:
        """Create (or fetch the existing) conversation for an item. Idempotent."""
        return self._authed("POST", "/instant-messaging/conversation", body={"item_hash_id": item_hash})

    def publish(
        self,
        photo_paths: list[str],
        title: str,
        price: float,
        category_leaf_id: str = "10088",
        condition: str = "good",
        description: str = "",
        brand: str = "",
        model: str = "",
        color: str = "",
        max_weight_kg: int = 1,
    ) -> Response:
        """Publish an item through the pure API (no browser).

        Mirrors the exact body the web wizard POSTs to ``/api/v3/items``:
        a ``multipart/form-data`` request with two fields — ``image`` (the
        cover photo bytes) and ``item`` (a JSON document with the attributes,
        category leaf, seller location, delivery config and a fresh
        ``upload_id``).

        When ``photo_paths`` has more than one entry, the extra photos are
        first uploaded to the draft for ``upload_id`` via
        ``POST /api/v3/upload/<upload_id>/pictures`` (field ``image``, one per
        photo) while the upload wizard is on the ``title`` step, and only the
        first photo is sent as the ``image`` of the final POST.

        .. warning:: Multi-photo is NOT yet fully resolved for the pure API: as
           of 2026-09-03 the resulting item is created with only the cover
           photo (1 image). Uploading extras to the draft does NOT associate
           them to the final item. The browser wizard (`Wallapop`` CLI
           ``publish``) is the working path for N photos. See
           ``docs/multi-photo-upload.md`` for the research and open paths.

        The seller location is taken from ``GET /users/me``; the delivery
        ``cost_configuration_id`` is the account's constant (returned by
        published items' ``shipping``).

        On success the response body is ``{"id":"<item id>","flags":{}}``
        (``Response.data`` carries the parsed JSON).
        """
        if not photo_paths:
            return Response(0, {"error_message": "at least one photo is required"}, False, False)

        me = self._authed("GET", "/users/me")
        loc = {}
        if me.ok and isinstance(me.data, dict):
            loc = me.data.get("location") or {}
        latitude = loc.get("approximated_latitude")
        longitude = loc.get("approximated_longitude")
        approximated = bool(loc.get("approximated_location"))
        if latitude is None or longitude is None:
            return Response(me.status, {"error_message": "no location in /users/me; publish requires geolocation"}, False, False)

        upload_id = str(uuid.uuid4())

        if len(photo_paths) > 1:
            init = self._authed("POST", "/steps", body={"mode": {"action": "upload", "id": upload_id}})
            if not init.ok:
                return Response(init.status, {"error_message": f"steps init failed: {init.data}"}, False, init.unauthorized)
            title_resp = self._authed("POST", "/steps", body={
                "mode": {"action": "upload", "id": upload_id},
                "current_step": "title",
                "draft": {"summary": title[:50]},
            })
            if not title_resp.ok:
                return Response(title_resp.status, {"error_message": f"steps title failed: {title_resp.data}"}, False, title_resp.unauthorized)
            for extra_photo in photo_paths[1:]:
                up_resp = self._upload_photo(upload_id, extra_photo)
                if not up_resp.ok:
                    return Response(up_resp.status, {"error_message": f"extra photo upload failed: {up_resp.data}"}, False, up_resp.unauthorized)

        item = {
            "attributes": {
                "suggested_data_banner": None,
                "brand": brand,
                "model": model,
                "color": color,
                "title": title,
                "description": description,
                "condition": condition,
                "price_amount": int(round(price)),
            },
            "category_leaf_id": category_leaf_id,
            "apply_discount": False,
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "approximated": approximated,
            },
            "delivery": {
                "allowed_by_user": True,
                "max_weight_kg": max_weight_kg,
                "cost_configuration_id": self._cost_configuration_id(),
            },
            "upload_id": upload_id,
        }

        with open(photo_paths[0], "rb") as fh:
            photo = fh.read()
        boundary = "----publishFormBoundary" + uuid.uuid4().hex[:14]
        item_json = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="image"; filename="foto.jpg"\r\n',
            b"Content-Type: image/jpeg\r\n\r\n",
            photo, b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="item"\r\n\r\n',
            item_json, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])

        headers = self._headers(auth=True)
        headers.update({
            "Accept": "application/vnd.upload-v2+json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        status, payload = http_request_raw("POST", f"{API_BASE}/items", raw_body=body, headers=headers)
        if status == 401:
            refreshed = self.refresh_web()
            if refreshed:
                headers = self._headers(auth=True)
                headers.update({
                    "Accept": "application/vnd.upload-v2+json",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                })
                status, payload = http_request_raw("POST", f"{API_BASE}/items", raw_body=body, headers=headers)
        if status != 200:
            return Response(status, {"error_message": f"publish POST failed with status {status}"}, False, status == 401)
        return Response(200, payload or {}, True, False)

    def _upload_photo(self, upload_id: str, photo_path: str) -> Response:
        """Upload a single photo to the draft for ``upload_id``.

        ``POST /api/v3/upload/<upload_id>/pictures`` mirrors the wizard's photo
        step: a multipart with an ``image`` field (image/jpeg) and the app
        headers (``x-appversion``, ``x-deviceos``, ``x-deviceid``). Returns 204
        on success.
        """
        with open(photo_path, "rb") as fh:
            photo = fh.read()
        boundary = "----uploadBoundary" + uuid.uuid4().hex[:22]
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="image"; filename="foto.jpg"\r\n',
            b"Content-Type: image/jpeg\r\n\r\n",
            photo, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        device_id = (self.web_cookies or {}).get("device_id", "")
        headers = self._headers(auth=True)
        headers.update({
            "Accept": "application/json",
            "x-appversion": "826740",
            "x-deviceos": "0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        if device_id:
            headers["x-deviceid"] = device_id
        status, _ = http_request_raw(
            "POST", f"{API_BASE}/upload/{upload_id}/pictures",
            raw_body=body, headers=headers, timeout=60,
        )
        if status in (200, 201, 204):
            return Response(status, {}, True, False)
        return Response(status, {"error_message": f"photo upload failed with status {status}"}, False, status == 401)

    def _cost_configuration_id(self) -> str:
        """Our account's delivery cost configuration UUID (constant per account)."""
        uid = self.user.get("id") or ""
        if not uid:
            me = self._authed("GET", "/users/me")
            if me.ok and isinstance(me.data, dict):
                uid = me.data.get("id") or ""
        if uid:
            items = self._authed("GET", f"/users/{uid}/items", params={"page": 0, "max_items": 25})
            if items.ok and isinstance(items.data, dict):
                for it in items.data.get("data") or []:
                    shipping = (it or {}).get("shipping") or {}
                    cfg = shipping.get("cost_configuration_id")
                    if cfg:
                        return str(cfg)
        return "814429d6-7844-471d-97af-196cd4020f26"

    def conversation_thread(self, conversation_id: str) -> Response:
        """Fetch the full message thread (messages, item, with_user) for a conversation."""
        return self._bff("GET", f"/bff/messaging/conversation/{conversation_id}")

    def inbox(self) -> Response:
        """Fetch the inbox: up to 30 conversations with their latest messages."""
        return self._bff("GET", "/bff/messaging/inbox", params={"page_size": 30, "max_messages": 30})

    def unread_count(self) -> Response:
        """Total unread message counter (instant messaging)."""
        return self._authed("GET", "/instant-messaging/messages/unread")

    def send_message(self, item_hash: str, message: str, report: Optional[dict] = None) -> Response:
        """Send a chat message to an item's seller.

        Prefers the PubNub REST publish (GET to pndsn.com), which is how the
        web app actually delivers chat messages; falls back to the browser
        flow only if the REST publish does not ack. Idempotent guard: no send
        when the exact text is already our last message in the thread. When
        `report` is given it is filled with {"path": "rest"|"browser",
        "skipped": bool, "browser_s": float}.
        """
        resp = self.ensure_conversation(item_hash)
        if not resp.ok:
            return resp
        conv_id = first(deep_find(resp.data, "conversation_id"))
        if not conv_id:
            return Response(resp.status, resp.data, False, resp.unauthorized)
        thread = self.conversation_thread(conv_id)
        thread_data = thread.data if isinstance(thread.data, dict) else {}
        msgs = (((thread_data.get("messages") or {}).get("messages")) or [])
        skipped = False
        for m in reversed(msgs):
            if m.get("from_self"):
                skipped = str(m.get("text") or "") == message
                break
        if not skipped:
            channel = thread_data.get("channel") or ""
            to_user = (thread_data.get("with_user") or {}).get("hash") or ""
            if channel and to_user:
                tok = self.im_token()
                if tok.ok:
                    ack = self.pubnub_publish(channel, message, to_user, conv_id, tok.data.get("token", ""))
                    if ack:
                        time.sleep(1)
                        if report is not None:
                            report["path"] = "rest"
                            report["skipped"] = False
                        return self.conversation_thread(conv_id)
        browser_s = 0.0
        if not skipped:
            t0 = time.monotonic()
            if not send_chat_text(item_hash, message):
                return Response(0, {"error_message": "chat delivery failed (browser step)"}, False, False)
            browser_s = time.monotonic() - t0
            time.sleep(2)
        if report is not None:
            report["path"] = "browser" if not skipped else "skipped"
            report["skipped"] = skipped
            report["browser_s"] = browser_s
        return self.conversation_thread(conv_id)

    def im_token(self) -> Response:
        """Fetch the PubNub access-manager token used as `auth` on publish."""
        return self._authed("GET", "/instant-messaging/token")

    def pubnub_publish(
        self,
        channel: str,
        text: str,
        to_user_hash: str,
        conversation_hash: str,
        auth_token: str,
    ) -> bool:
        """Publish a chat message to a conversation channel via PubNub REST.

        Returns True when PubNub acks with `[1,"Sent",<timetoken>]`.
        """
        message = json.dumps({"id": str(uuid.uuid4()), "payload": {"text": text}}, separators=(",", ":"))
        meta = {
            "type": "text",
            "sender": {
                "platform": {"app_version": WEB_APP_VERSION, "os_version": "0"},
            },
            "to_user_hash": to_user_hash,
            "from_user_hash": user_hash_of(self.token),
            "conversation_hash": conversation_hash,
        }
        path = (
            f"https://{PUB_ORIGIN}/publish/{PUB_KEY}/{SUB_KEY}/0/"
            f"{channel}/0/{urllib.parse.quote(message, safe='')}"
        )
        params = {
            "meta": json.dumps(meta, separators=(",", ":")),
            "uuid": user_hash_of(self.token),
            "requestid": str(uuid.uuid4()),
            "pnsdk": PNSDK,
            "auth": auth_token,
        }
        url = path + "?" + urllib.parse.urlencode(params)
        status, payload, _ = http_request("GET", url, headers={})
        if status != 200 or not isinstance(payload, str):
            return False
        try:
            ack = json.loads(payload)
        except json.JSONDecodeError:
            return False
        return bool(ack and ack[0] == 1 and ack[1] == "Sent")


def send_chat_text(item_hash: str, text: str) -> bool:
    """Actually deliver a chat message using a headless browser session (the web app sends over PubNub)."""
    session = load_session()
    cookies = {k: session[k] for k in WALLAPOP_COOKIE_KEYS if session.get(k)}
    if not cookies or not cookies.get("accessToken"):
        eprint("no browser session stored; run: wallapop login --browser firefox  (or --cookies ...)")
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        eprint("playwright not installed. run: pip install --user --break-system-packages playwright && python3 -m playwright install firefox")
        return False
    try:
        t0 = time.monotonic()
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_cookies(
                [{"name": n, "value": v, "domain": ".wallapop.com", "path": "/"}
                 for n, v in cookies.items() if v]
            )
            page = ctx.new_page()
            page.goto(f"{WEB_BASE}/app/chat?itemId={item_hash}", wait_until="domcontentloaded", timeout=90000)
            t1 = time.monotonic()
            eprint(f"[chat] browser up + page loaded in {t1 - t0:.1f}s")
            ta = page.locator("textarea.wallapop-chat-input__textarea").first
            ready = False
            for _ in range(30):
                if ta.count():
                    ready = True
                    break
                page.wait_for_timeout(1000)
            if not ready:
                browser.close()
                eprint("browser chat did not open a textarea")
                return False
            t2 = time.monotonic()
            eprint(f"[chat] textarea ready in {t2 - t1:.1f}s")
            ta.fill(text)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text() or ""
            browser.close()
            t3 = time.monotonic()
            eprint(f"[chat] send + settle in {t3 - t2:.1f}s  (total {t3 - t0:.1f}s)")
            return text in body
    except Exception as err:
        eprint(f"browser send failed: {err}")
        return False
