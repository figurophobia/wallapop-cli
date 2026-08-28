#!/usr/bin/env python3
"""Wallapop automation CLI. Search listings, hunt devalued deals, place offers and message sellers."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import getpass
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

API_BASE = "https://api.wallapop.com/api/v3"
API_ROOT = "https://api.wallapop.com"  # host root; bff endpoints live under /bff (no /api/v3)
WEB_BASE = "https://es.wallapop.com"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "wallapop_cli")
CRED_PATH = os.path.join(CONFIG_DIR, "credentials.json")
SESSION_PATH = os.path.join(CONFIG_DIR, "session.json")
CONTACTED_PATH = os.path.join(CONFIG_DIR, "contacted.json")
CLIENT_ID = "wallapop"
VERSION = "1.0.0"

RATE_PACE = float(os.environ.get("WALLAPOP_RATE_PACE", "1.0"))
LAST_REQUEST = 0.0

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]

CITIES = {
    "espana": (40.0, -3.5),
    "spain": (40.0, -3.5),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3896, 2.1476),
    "valencia": (39.4699, -0.3763),
    "sevilla": (37.3891, -5.9845),
    "bilbao": (43.2630, -2.9350),
    "malaga": (36.7213, -4.4214),
    "zaragoza": (41.6488, -0.8891),
    "granada": (37.1773, -3.5986),
}

DEFAULT_GEO = CITIES["espana"]

CATEGORIES = {
    100: "Coches",
    200: "Inmobiliaria",
    14000: "Motos",
    12465: "Moda y accesorios",
    12545: "Tecnologia y electronica",
    16000: "Moviles y telefonia",
    15000: "Informatica",
    12579: "Deporte y ocio",
    17000: "Bicicletas",
    12900: "Consolas y videojuegos",
    12467: "Hogar y jardin",
    13100: "Electrodomesticos",
    12463: "Cine, libros y musica",
    12461: "Ninos y bebes",
    18000: "Coleccionismo",
    19000: "Construccion y reformas",
    20000: "Industria y agricultura",
    21000: "Empleo",
    13200: "Servicios",
}

ORDER_ALIASES = {
    "relevance": "most_relevance",
    "most_relevance": "most_relevance",
    "newest": "newest",
    "price_asc": "price_low_to_high",
    "price_low_to_high": "price_low_to_high",
    "price_desc": "price_high_to_low",
    "price_high_to_low": "price_high_to_low",
    "closest": "closest",
}


class ApiError(Exception):
    pass


@dataclass
class Paint:
    enabled: bool = field(default_factory=lambda: sys.stdout.isatty())

    def _p(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, text: str) -> str:
        return self._p(text, "32")

    def red(self, text: str) -> str:
        return self._p(text, "31")

    def yellow(self, text: str) -> str:
        return self._p(text, "33")

    def cyan(self, text: str) -> str:
        return self._p(text, "36")

    def bold(self, text: str) -> str:
        return self._p(text, "1")

    def dim(self, text: str) -> str:
        return self._p(text, "2")


paint = Paint()


def ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data: Any, private: bool = True) -> None:
    ensure_config_dir()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if private:
        os.chmod(path, 0o600)


def load_credentials() -> dict:
    return load_json(CRED_PATH, {})


def save_credentials(creds: dict) -> None:
    save_json(CRED_PATH, creds)


def clear_credentials() -> None:
    try:
        os.remove(CRED_PATH)
    except OSError:
        pass


def load_session() -> dict:
    return load_json(SESSION_PATH, {})


def save_session(session: dict) -> None:
    save_json(SESSION_PATH, session)


def clear_session() -> None:
    try:
        os.remove(SESSION_PATH)
    except OSError:
        pass


def parse_cookie_string(raw: str) -> dict:
    result: dict[str, str] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        result[key.strip()] = value.strip()
    return result


WALLAPOP_COOKIE_KEYS = ("accessToken", "__Secure-next-auth.session-token", "device_id", "device_fgp")


def read_firefox_cookies() -> dict:
    profile_dir = os.path.expanduser("~/.mozilla/firefox")
    db_path = None
    best_mtime = -1
    try:
        for entry in os.scandir(profile_dir):
            if not entry.is_dir():
                continue
            candidate = os.path.join(entry.path, "cookies.sqlite")
            if os.path.isfile(candidate) and os.path.getmtime(candidate) > best_mtime:
                best_mtime = os.path.getmtime(candidate)
                db_path = candidate
    except OSError:
        return {}
    if not db_path:
        return {}
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(db_path, tmp)
    except OSError:
        os.remove(tmp)
        return {}
    found: dict[str, str] = {}
    try:
        with sqlite3.connect(tmp, timeout=5) as con:
            rows = con.execute(
                "select host, name, value from moz_cookies where host like '%wallapop.com'"
            ).fetchall()
    except (sqlite3.Error, OSError):
        rows = []
    finally:
        os.remove(tmp)
    for host, name, value in rows:
        if name in WALLAPOP_COOKIE_KEYS and value:
            found[name] = value
    return found


def refresh_web_session(session: dict) -> Optional[str]:
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
    updated = dict(session)
    if token:
        updated["accessToken"] = token
    save_session(updated)


def load_contacted() -> set[str]:
    return set(load_json(CONTACTED_PATH, []))


def save_contacted(ids: set[str]) -> None:
    save_json(CONTACTED_PATH, sorted(ids), private=False)


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(width - 1, 1)] + "\u2026"


def clock() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass
class Response:
    status: int
    data: Any
    ok: bool
    unauthorized: bool

    @property
    def error_reason(self) -> str:
        data = self.data
        if isinstance(data, dict):
            status_block = data.get("status")
            if isinstance(status_block, dict) and isinstance(status_block.get("message"), str):
                return status_block["message"]
            for key in ("error_message", "message", "error"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val
        return ""


def http_request(
    method: str,
    url: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    retries: int = 3,
    timeout: int = 25,
) -> tuple[int, Any, str]:
    global LAST_REQUEST
    if RATE_PACE > 0:
        wait = RATE_PACE - (time.monotonic() - LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        LAST_REQUEST = time.monotonic()
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    status = 0
    ctype = ""
    payload = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=encoded, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            break
        except urllib.error.HTTPError as err:
            status = err.code
            payload = err.read()
            ctype = err.headers.get("Content-Type", "")
            if status in (403, 429) and attempt < retries - 1:
                time.sleep(2 ** attempt + random.uniform(0.3, 1.2))
                continue
            break
        except urllib.error.URLError as err:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.uniform(0.3, 1.2))
                continue
            raise ApiError(f"network error: {err.reason}")
    return status, decode_payload(payload, ctype), ctype


def decode_payload(payload: Optional[bytes], ctype: str) -> Any:
    if payload is None:
        return None
    text = payload.decode("utf-8", "replace")
    if "json" in (ctype or "").lower() or text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


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
        hdrs = {
            "Accept": "application/json, text/plain, */*",
            "Origin": WEB_BASE,
            "Referer": f"{WEB_BASE}/",
            "User-Agent": random.choice(USER_AGENTS),
            "X-DeviceOS": "0",
            "X-Product": "wallapop",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }
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
        order_by: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        pages: int = 1,
        delay: float = 0.0,
    ) -> list[dict]:
        order = order_by or "most_relevance"
        base = {
            "keywords": keywords,
            "source": "search_box",
            "order_by": order,
            "filters_source": "search_box",
        }
        if category_id:
            base["category_id"] = category_id
        if latitude is not None and longitude is not None:
            base["latitude"] = latitude
            base["longitude"] = longitude
        components = self._request("GET", "/search/components", params=base)
        search_id = first(deep_find(components.data, "search_id"))
        if search_id:
            return self._search_sections(base, search_id, pages, delay)
        return self._search_legacy(keywords, category_id, order, latitude, longitude)

    def _search_sections(self, base: dict, search_id: str, pages: int, delay: float) -> list[dict]:
        all_items: list[dict] = []
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
    ) -> list[dict]:
        params = {
            "keywords": keywords,
            "order_by": order,
            "filters_source": "search_box",
            "source": "search_box",
        }
        if category_id:
            params["category_ids"] = category_id
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

    def item(self, item_id: str) -> dict:
        resp = self._request("GET", f"/items/{item_id}")
        if not resp.ok:
            raise ApiError(f"item {item_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        return data if isinstance(data, dict) else {}

    def send_offer(self, item_id: str, amount: float, message: Optional[str] = None) -> Response:
        body: dict[str, Any] = {"amount": int(round(amount))}
        if message:
            body["message"] = message
        return self._authed("POST", f"/items/{item_id}/offers", body=body)

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

    def conversation_thread(self, conversation_id: str) -> Response:
        return self._bff("GET", f"/bff/messaging/conversation/{conversation_id}")

    def inbox(self) -> Response:
        return self._bff("GET", "/bff/messaging/inbox", params={"page_size": 30, "max_messages": 30})

    def send_message(self, item_hash: str, message: str, report: Optional[dict] = None) -> Response:
        """Send a chat message to an item's seller via the browser chat session.

        Idempotent guard: if the exact same text is already the last message we
        sent in this conversation, the browser send is skipped. When `report`
        is given, it is filled with {"browser_s": float, "skipped": bool}.
        """
        resp = self.ensure_conversation(item_hash)
        if not resp.ok:
            return resp
        conv_id = first(deep_find(resp.data, "conversation_id"))
        if not conv_id:
            return Response(resp.status, resp.data, False, resp.unauthorized)
        thread = self.conversation_thread(conv_id)
        skipped = False
        if thread.ok:
            msgs = (((thread.data or {}).get("messages") or {}).get("messages")) or []
            for m in reversed(msgs):
                if m.get("from_self"):
                    skipped = str(m.get("text") or "") == message
                    break
        browser_s = 0.0
        if not skipped:
            t0 = time.monotonic()
            if not send_chat_text(item_hash, message):
                return Response(0, {"error_message": "chat delivery failed (browser step)"}, False, False)
            browser_s = time.monotonic() - t0
            time.sleep(2)
        if report is not None:
            report["skipped"] = skipped
            report["browser_s"] = browser_s
        return self.conversation_thread(conv_id)


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


def deep_find(node: Any, key: str) -> Iterator[Any]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from deep_find(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from deep_find(v, key)


def first(gen: Iterator[Any]) -> Any:
    return next(gen, None)


def extract_items(payload: Any) -> list:
    for val in deep_find(payload, "items"):
        if isinstance(val, list):
            return val
    return []


def safe_price(raw: dict) -> float:
    price = raw.get("price")
    if price is None:
        return 0.0
    if isinstance(price, (int, float)):
        return float(price)
    if isinstance(price, dict):
        for key in ("amount", "cash_amount", "full_amount"):
            val = price.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        for sub in ("cash", "full"):
            box = price.get(sub)
            if isinstance(box, dict):
                val = box.get("amount")
                if isinstance(val, (int, float)):
                    return float(val)
    return 0.0


def extract_currency(raw: dict) -> str:
    price = raw.get("price")
    if isinstance(price, dict):
        if isinstance(price.get("currency"), str):
            return price["currency"]
        for sub in ("cash", "full"):
            box = price.get(sub)
            if isinstance(box, dict) and isinstance(box.get("currency"), str):
                return box["currency"]
    currency = raw.get("currency")
    return currency if isinstance(currency, str) else "EUR"


def extract_shipping(raw: dict) -> bool:
    shipping = raw.get("shipping")
    if isinstance(shipping, dict):
        for key in ("item_is_shippable", "isItemShippable", "is_shippable", "itemShippable"):
            if shipping.get(key) is True:
                return True
        for key in ("user_allows_shipping", "isShippingAllowedByUser"):
            if shipping.get(key) is True:
                return True
    for key in ("is_shippable", "shipping_available", "can_ship"):
        if raw.get(key) is True:
            return True
    if (raw.get("flags") or {}).get("shipping") is True:
        return True
    return False


def normalize_item(raw: dict, fallback_user_id: Optional[str] = None) -> dict:
    item_id = str(raw.get("id") or "")
    user = raw.get("user") or {}
    user_id = str(raw.get("user_id") or raw.get("userId") or user.get("id") or "")
    if not user_id and fallback_user_id:
        user_id = str(fallback_user_id)
    user_name = str(raw.get("user_name") or user.get("micro_name") or user.get("name") or "")
    title = raw.get("title") or ""
    if isinstance(title, dict):
        title = title.get("original") or ""
    price = safe_price(raw)
    flags = raw.get("flags") or {}
    reserved = raw.get("reserved")
    if isinstance(reserved, dict):
        reserved = bool(reserved.get("flag") or reserved.get("value"))
    else:
        reserved = bool(reserved or raw.get("is_reserved") or flags.get("reserved"))
    sold = bool(raw.get("is_sold") or flags.get("sold"))
    location = raw.get("location") or {}
    city = raw.get("city") or location.get("city") or ""
    if isinstance(city, dict):
        city = city.get("title") or ""
    web_slug = raw.get("web_slug") or ""
    slug = web_slug or item_id
    taxonomy = raw.get("taxonomy") or []
    category = ""
    category_name = ""
    if isinstance(taxonomy, list) and taxonomy:
        leaf = taxonomy[-1]
        if isinstance(leaf, dict):
            category = str(leaf.get("id") or "")
            category_name = str(leaf.get("name") or "")
    return {
        "id": item_id,
        "user_id": user_id,
        "user_name": user_name,
        "title": str(title),
        "price": price,
        "currency": extract_currency(raw),
        "reserved": reserved,
        "sold": sold,
        "shippable": extract_shipping(raw),
        "city": str(city),
        "category": category,
        "category_name": category_name,
        "created_at": str(raw.get("created_at") or ""),
        "url": f"{WEB_BASE}/item/{slug}" if slug else "",
    }


def median_price(prices: list[float]) -> float:
    if not prices:
        return 0.0
    ordered = sorted(prices)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def rank_deals(items: list[dict], min_discount: float, max_price: Optional[float]) -> tuple[list[dict], float]:
    candidates = [it for it in items if not it["reserved"] and not it["sold"] and it["price"] > 0]
    if max_price is not None:
        candidates = [it for it in candidates if it["price"] <= max_price]
    median = median_price([it["price"] for it in candidates])
    deals = []
    for it in candidates:
        discount = (median - it["price"]) / median if median else 0.0
        deals.append({**it, "median": median, "discount": discount})
    deals.sort(key=lambda d: d["discount"], reverse=True)
    return [d for d in deals if d["discount"] >= min_discount], median


def token_expiry(token: str) -> Optional[datetime.datetime]:
    if not token.startswith("eyJ"):
        return None
    try:
        part = token.split(".")[1]
        padded = part + "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.datetime.fromtimestamp(exp)
    except Exception:
        pass
    return None


def is_internal_id(item_ref: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]{12}", item_ref)) and any(ch.isalpha() for ch in item_ref)


def resolve_internal_id(item_ref: str) -> str:
    ref = item_ref.strip()
    if "wallapop.com" in ref:
        ref = urllib.parse.urlparse(ref).path.rstrip("/").split("/")[-1]
    if is_internal_id(ref):
        return ref
    url = f"{WEB_BASE}/item/{ref}"
    status, payload, _ = http_request("GET", url, headers={"User-Agent": random.choice(USER_AGENTS)})
    if status != 200 or not isinstance(payload, str):
        raise ApiError(f"could not load item page for {item_ref} (status {status})")
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', payload, re.S)
    if not match:
        raise ApiError(f"no product data found on {url}")
    try:
        data = json.loads(match.group(1))
        item = data["props"]["pageProps"]["item"]
        item_id = str(item.get("id") or "")
    except Exception as err:
        raise ApiError(f"could not parse item page for {item_ref}: {err}")
    if not item_id:
        raise ApiError(f"item page for {item_ref} has no id (may be sold or removed)")
    return item_id


def refresh_if_expired(client: Wallapop) -> None:
    exp = token_expiry(client.token or "")
    if exp is not None and exp <= datetime.datetime.now() + datetime.timedelta(minutes=1):
        if not client.try_refresh():
            client.refresh_web()


def authed_client(required: bool = True) -> tuple[Wallapop, dict]:
    creds = load_credentials()
    session = load_session()
    web_cookies = session if session.get("__Secure-next-auth.session-token") else None
    token = creds.get("access_token") or session.get("accessToken") or ""
    if not token and web_cookies:
        refreshed = refresh_web_session(web_cookies)
        if refreshed:
            token = refreshed
    if required and not token:
        eprint("not logged in. run: wallapop login <email> <password>  or  wallapop login --browser firefox")
        raise SystemExit(1)

    def persist(client: Wallapop) -> None:
        save_credentials(
            {
                "email": client.email,
                "access_token": client.token,
                "refresh_token": client.refresh_token,
                "user": client.user,
            }
        )
        if client.web_cookies:
            persist_browser_session(client.web_cookies, client.token)

    client = Wallapop(
        token=token or None,
        refresh_token=creds.get("refresh_token"),
        email=creds.get("email"),
        persist_fn=persist,
        web_cookies=web_cookies,
    )
    if token:
        client.user = creds.get("user", {})
        refresh_if_expired(client)
    return client, creds


def emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def emit_csv(items: list[dict]) -> None:
    fieldnames = ["id", "title", "price", "currency", "city", "user_id", "user_name", "url", "shippable", "discount", "median"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for it in items:
        writer.writerow(it)


def render_template(template: str, it: dict, amount: Optional[float] = None) -> str:
    price = f"{it['price']:.2f}"
    text = template.replace("{title}", it["title"])
    text = text.replace("{price}", price)
    text = text.replace("{link}", it["url"])
    text = text.replace("{city}", it["city"])
    if amount is not None:
        text = text.replace("{offer}", f"{amount:.2f}")
    return text


def print_items(items: list[dict]) -> None:
    if not items:
        print("no results")
        return
    width = shutil.get_terminal_size((100, 24)).columns
    title_w = max(20, width - 65)
    print(paint.bold(f"{'#':>3}  {'PRICE':>9}  {'CTY':<11}  {'SELLER':<12}  SHP  TITLE"))
    for idx, it in enumerate(items, 1):
        ship = paint.green("yes") if it["shippable"] else paint.dim("no")
        row = (
            f"{idx:>3}  {paint.cyan(f'{it['price']:8.2f}')}  {truncate(it['city'], 11):<11}  "
            f"{truncate(it['user_name'] or it['user_id'], 12):<12}  {ship}  {truncate(it['title'], title_w)}"
        )
        print(row)
        if it["url"]:
            print(f"     {paint.dim(it['url'])}")


def print_deals(deals: list[dict], show_discount_bars: bool = True) -> None:
    if not deals:
        print("no deals at the current threshold")
        return
    width = shutil.get_terminal_size((100, 24)).columns
    title_w = max(20, width - 69)
    print(paint.bold(f"{'#':>3}  {'DEAL':>5}  {'PRICE':>9}  {'MEDIAN':>9}  {'CTY':<10}  SHP  TITLE"))
    for idx, it in enumerate(deals, 1):
        pct = it["discount"] * 100
        if it["discount"] > 0:
            deal_tag = paint.green(f"{-pct:4.0f}%")
        elif it["discount"] == 0:
            deal_tag = paint.dim("  0%")
        else:
            deal_tag = paint.red(f"+{abs(pct):3.0f}%")
        ship = paint.green("yes") if it["shippable"] else paint.dim("no")
        row = (
            f"{idx:>3}  {deal_tag}  {paint.cyan(f'{it['price']:8.2f}')}  {paint.dim(f'{it['median']:8.2f}')}  "
            f"{truncate(it['city'], 10):<10}  {ship}  {truncate(it['title'], title_w)}"
        )
        print(row)
        if it["url"]:
            print(f"     {paint.dim(it['url'])}")


def announce(label: str, resp: Optional[Response]) -> None:
    if resp is None:
        print(paint.yellow(f"[dry-run] would {label}"))
        return
    if resp.ok:
        print(paint.green(f"[ok] {label}"))
        detail = resp.error_reason
        if detail and detail.lower() not in ("ok", "done", "success"):
            print(f"      {detail}")
    else:
        print(paint.red(f"[failed] {label} ({resp.status}): {resp.error_reason or str(resp.data)[:300]}"))


def resolve_geo(args: argparse.Namespace) -> tuple[Optional[float], Optional[float]]:
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ApiError("--lat and --lon must be provided together")
        return args.lat, args.lon
    if args.city:
        key = args.city.strip().lower()
        if key not in CITIES:
            raise ApiError(f"unknown city '{args.city}'. available: {', '.join(sorted(CITIES))}")
        return CITIES[key]
    return DEFAULT_GEO


def fetch_items(client: Wallapop, args: argparse.Namespace, keyword: str) -> list[dict]:
    lat, lon = resolve_geo(args)
    if lat is None and args.city is None and args.lat is None and not args.quiet:
        eprint("note: no location given, searching all of Spain (--city or --lat/--lon to narrow)")
    order = ORDER_ALIASES.get(args.order, "most_relevance")
    if order == "price_low_to_high" and not args.quiet:
        eprint("note: Wallapop hides prices on 'price_asc' sort; results may show 0.00")
    items = client.search(
        keyword,
        category_id=args.category,
        order_by=order,
        latitude=lat,
        longitude=lon,
        pages=args.pages,
        delay=args.delay,
    )
    items = [it for it in items if not it["reserved"] and not it["sold"]]
    if args.subcat:
        items = [it for it in items if it["category"] == str(args.subcat)]
    if args.min_price is not None:
        items = [it for it in items if it["price"] >= args.min_price]
    if args.max_price is not None:
        items = [it for it in items if it["price"] <= args.max_price]
    if getattr(args, "ship_only", False):
        items = [it for it in items if it["shippable"]]
    return items


def deliver(client: Wallapop, it: dict, offer_pct: Optional[float], message: Optional[str], dry_run: bool) -> None:
    amount = None
    if offer_pct is not None:
        amount = max(1.0, round(it["price"] * offer_pct, 2))
    text = render_template(message, it, amount) if message else None
    snippet = truncate(it["title"], 40)
    if offer_pct is not None:
        label = f"offer {amount:.2f}€ on {snippet!r} ({it['id']})"
        if dry_run:
            announce(label, None)
            if text:
                print(f"      message: {text}")
        else:
            announce(label, client.send_offer(it["id"], amount, message=text))
    elif text:
        label = f"message seller of {it['id']} re {snippet!r}"
        if dry_run:
            announce(label, None)
            print(f"      text: {text}")
        else:
            announce(label, client.send_message(it["id"], text))


def maybe_deliver_all(client: Wallapop, args: argparse.Namespace, deals: list[dict]) -> None:
    if args.offer_pct is None and not args.msg:
        return
    contacted = set(load_contacted())
    for it in deals:
        if it["id"] in contacted:
            continue
        deliver(client, it, args.offer_pct, args.msg, args.dry_run)
        if not args.dry_run:
            contacted.add(it["id"])
            save_contacted(contacted)


def cmd_login(args: argparse.Namespace) -> int:
    if getattr(args, "browser", None) == "firefox":
        cookies = read_firefox_cookies()
        if not cookies:
            eprint("no wallapop cookies found in any Firefox profile; log in at es.wallapop.com first")
            return 1
        session = {k: cookies[k] for k in WALLAPOP_COOKIE_KEYS if k in cookies}
        token = refresh_web_session(session)
        if not token:
            eprint("session cookie found but could not get an access token; log in again at es.wallapop.com")
            return 1
        client = Wallapop(
            token=token,
            email="browser-session",
            persist_fn=lambda c: persist_browser_session(c.web_cookies or {}, c.token),
        )
        client.web_cookies = session
        save_credentials({"email": "browser-session", "access_token": token, "refresh_token": "", "user": {}})
        persist_browser_session(session, token)
        print(f"imported Firefox session ({', '.join(sorted(session))})")
        print(f"session stored in {SESSION_PATH}")
        return 0

    if getattr(args, "cookies", None):
        parsed = parse_cookie_string(args.cookies)
        session = {k: parsed[k] for k in WALLAPOP_COOKIE_KEYS if k in parsed}
        missing = ", ".join(k for k in WALLAPOP_COOKIE_KEYS if k not in session)
        if missing:
            eprint(f"missing cookies: {missing}. needed: {', '.join(WALLAPOP_COOKIE_KEYS)}")
            return 1
        token = refresh_web_session(session)
        if not token:
            eprint("could not get an access token from those cookies; are they fresh?")
            return 1
        persist_browser_session(session, token)
        save_credentials({"email": "browser-session", "access_token": token, "refresh_token": "", "user": {}})
        print("session imported from cookies")
        print(f"session stored in {SESSION_PATH}")
        return 0

    if not args.email:
        eprint("provide an email, or use --browser firefox / --cookies")
        return 1
    password = args.password
    if not password:
        password = getpass.getpass("password: ")
    client = Wallapop(persist_fn=lambda c: save_credentials({
        "email": c.email,
        "access_token": c.token,
        "refresh_token": c.refresh_token,
        "user": c.user,
    }))
    client.login(args.email, password)
    name = client.user.get("micro_name") or client.user.get("name") or args.email
    print(f"logged in as {name} ({args.email})")
    print(f"credentials stored in {CRED_PATH}")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    clear_credentials()
    clear_session()
    print("credentials cleared")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    creds = load_credentials()
    session = load_session()
    source = "browser" if session.get("__Secure-next-auth.session-token") else ("email" if creds.get("access_token") else "none")
    token = creds.get("access_token") or session.get("accessToken") or ""
    if not token:
        print(f"source:  none")
        print("not logged in")
        return 0
    exp = token_expiry(token)
    name = (creds.get("user") or {}).get("micro_name") or ""
    print(f"source:  {source}")
    print(f"name:    {name or 'browser session'}")
    print(f"token:   stored at {SESSION_PATH if source == 'browser' else CRED_PATH}")
    print(f"expires: {exp.strftime('%Y-%m-%d %H:%M:%S') if exp else 'unknown'}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    client = Wallapop()
    keyword = " ".join(args.keyword)
    items = fetch_items(client, args, keyword)
    if args.json:
        emit_json(items)
    elif args.csv:
        emit_csv(items)
    else:
        print(paint.bold(f"{len(items)} listings for {keyword!r}"))
        print_items(items)
    return 0


def cmd_deals(args: argparse.Namespace) -> int:
    client = Wallapop()
    keyword = " ".join(args.keyword)
    items = fetch_items(client, args, keyword)
    deals, median = rank_deals(items, args.min_discount, args.max_price)
    deals = deals[: args.top]
    if args.json:
        emit_json({"keyword": keyword, "median": median, "count": len(deals), "deals": deals})
    elif args.csv:
        emit_csv(deals)
    else:
        print(
            paint.bold(
                f"{len(deals)} deals for {keyword!r} "
                f"(median {median:.2f}€, {args.min_discount * 100:.0f}%+ below market)"
            )
        )
        print_deals(deals)
    maybe_deliver_all(client, args, deals)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    sending = args.offer_pct is not None or bool(args.msg)
    client, _ = authed_client(required=sending and not args.dry_run)
    keyword = " ".join(args.keyword)

    def stop(sig, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            items = fetch_items(client, args, keyword)
            deals, median = rank_deals(items, args.min_discount, args.max_price)
            contacted = set(load_contacted())
            fresh = [d for d in deals if d["id"] not in contacted]
            print(
                f"[{clock()}] scanned {len(items)} listings, median {median:.2f}€, "
                f"{len(deals)} deals, {len(fresh)} new"
            )
            for it in fresh[: args.top]:
                print_deals([it])
                deliver(client, it, args.offer_pct, args.msg, args.dry_run)
                if not args.dry_run:
                    contacted.add(it["id"])
                    save_contacted(contacted)
            if args.once:
                break
            time.sleep(max(args.interval, 5))
    except KeyboardInterrupt:
        print("\nwatch stopped")
    return 0


def cmd_offer(args: argparse.Namespace) -> int:
    if args.price is None and args.pct is None:
        raise ApiError("provide --price or --pct")
    if args.price is not None and args.pct is not None:
        raise ApiError("provide only one of --price or --pct")
    item_id = resolve_internal_id(args.item_id)
    if args.price is not None:
        amount = args.price
    else:
        asking = safe_price(Wallapop().item(item_id))
        amount = max(1.0, round(asking * args.pct, 2))
    label = f"offer {amount:.2f}€ on item {item_id}"
    if args.dry_run:
        announce(label, None)
        return 0
    client, _ = authed_client()
    announce(label, client.send_offer(item_id, amount, message=args.msg))
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    item_hash = resolve_internal_id(args.item_id)
    label = f"message seller of item {item_hash}"
    if args.dry_run:
        announce(label, None)
        print(f"      text: {args.text}")
        return 0
    client, _ = authed_client()
    started = time.monotonic()
    report: dict[str, Any] = {}
    resp = client.send_message(item_hash, args.text, report=report)
    elapsed = time.monotonic() - started
    announce(label, resp)
    if resp.ok:
        whom = (resp.data or {}).get("with_user") or {}
        name = whom.get("name") or "?"
        note = " (same text already sent, skipped)" if report.get("skipped") else ""
        print(f"      delivered to {name} in {elapsed:.1f}s total {note}")
        if report.get("browser_s"):
            print(f"      browser send itself took {report['browser_s']:.1f}s")
        return 0
    return 1


def cmd_replies(args: argparse.Namespace) -> int:
    client, _ = authed_client()
    resp = client.inbox()
    if not resp.ok:
        eprint(f"inbox failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        return 1
    convs = (resp.data or {}).get("conversations") or []
    if not convs:
        print("empty inbox")
        return 0
    if args.conversation:
        resp = client.conversation_thread(args.conversation)
        if not resp.ok:
            eprint(f"conversation {args.conversation} failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            return 1
        data = resp.data or {}
        with_user = (data.get("with_user") or {}).get("name") or ""
        item = data.get("item") or {}
        title = item.get("title") or ""
        print(f"conversation {args.conversation} with {with_user}  ·  {title}")
        msgs = (((data.get("messages") or {}).get("messages")) or [])
        for m in msgs:
            mark = paint.green("->") if m.get("from_self") else paint.cyan("<-")
            stamp = m.get("timestamp")
            when = datetime.datetime.fromtimestamp((stamp or 0) / 1000).strftime("%m-%d %H:%M") if stamp else ""
            print(f"  {mark} {when}  {m.get('text')}")
        return 0
    for c in convs:
        item = c.get("item") or {}
        who = str((c.get("with_user") or {}).get("name") or "")
        title = str(item.get("title") or "")[:44]
        price = item.get("price")
        amount = price.get("amount") if isinstance(price, dict) else ""
        msgs = ((c.get("messages") or {}).get("messages")) or []
        last = msgs[-1] if msgs else {}
        text = str(last.get("text") or "(no messages)")[:90]
        unread = c.get("unread_messages", 0)
        print(f"{paint.cyan(c['hash'])}  {who:<22} {amount}{'€' if amount not in (None, '') else ''} · {unread} unread")
        print(f"      {title}  —  {text}")
    return 0


def add_search_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--category", type=int, help=f"category id. common: {', '.join(f'{k}({v})' for k, v in sorted(CATEGORIES.items()))}")
    sp.add_argument("--subcat", help="filter by taxonomy leaf id, e.g. 10088 = consolas, 10092 = accesorios de consola")
    sp.add_argument("--min-price", type=float)
    sp.add_argument("--max-price", type=float)
    sp.add_argument("--city", help=f"city preset: {', '.join(sorted(CITIES))}")
    sp.add_argument("--lat", type=float)
    sp.add_argument("--lon", type=float)
    sp.add_argument("--order", choices=sorted(ORDER_ALIASES), default="relevance")
    sp.add_argument("--pages", type=int, default=1, help="result pages to fetch (default 1)")
    sp.add_argument("--delay", type=float, default=1.5, help="seconds to wait between page fetches")
    sp.add_argument("--ship-only", action="store_true", help="only keep listings that can be shipped")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--csv", action="store_true")
    sp.add_argument("--quiet", action="store_true")


def add_deal_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--min-discount", type=float, default=0.15, help="require at least this discount vs median (default 0.15)")
    sp.add_argument("--top", type=int, default=10, help="act on the best N deals only (default 10)")
    sp.add_argument("--offer-pct", type=float, help="auto-send an offer at this fraction of the asking price, e.g. 0.8")
    sp.add_argument(
        "--msg",
        help="auto-message template. placeholders: {title} {price} {link} {city} {offer}",
    )
    sp.add_argument("--dry-run", action="store_true", help="print intent without sending")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallapop",
        description="Wallapop automation CLI: search, hunt deals, send offers and messages.",
        epilog=(
            "examples:\n"
            "  wallapop search 'nintendo switch' --city madrid --order price_asc\n"
            "  wallapop deals 'ps5' --max-price 250 --min-discount 0.2\n"
            "  wallapop offer 123456789 --pct 0.8 --msg 'Hola, aceptarias 200?'\n"
            "  wallapop message nintendo-switch-lite-blu-1295309387 --text 'Hola, funciona bien?'\n"
            "  wallapop replies --conversation nzxp0m9qp5j2\n"
            "  wallapop watch 'steam deck' --max-price 300 --msg 'hola amigo, funciona bien?' --interval 600\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    lp = sub.add_parser("login", help="log in and store credentials (prompts for password)")
    lp.add_argument("email", nargs="?", default=None, help="email for password login")
    lp.add_argument("password", nargs="?", help="optional; if omitted you are prompted safely")
    lp.add_argument("--browser", choices=["firefox"], help="import your session from the Firefox profile (works with Google accounts)")
    lp.add_argument("--cookies", help="paste cookies as a 'name=value; ...' string (accessToken, __Secure-next-auth.session-token)"
    )
    lp.set_defaults(func=cmd_login)

    so = sub.add_parser("logout", help="clear stored credentials")
    so.set_defaults(func=cmd_logout)

    ss = sub.add_parser("session", help="show current session")
    ss.set_defaults(func=cmd_session)

    se = sub.add_parser("search", help="search listings")
    add_search_args(se)
    se.add_argument("keyword", nargs="+")
    se.set_defaults(func=cmd_search)

    dl = sub.add_parser("deals", help="find listings priced below market median")
    add_search_args(dl)
    add_deal_args(dl)
    dl.add_argument("keyword", nargs="+")
    dl.set_defaults(func=cmd_deals)

    wc = sub.add_parser("watch", help="loop a scan and auto-offer/message fresh deals")
    add_search_args(wc)
    add_deal_args(wc)
    wc.add_argument("--interval", type=float, default=300, help="seconds between scans (default 300, min 5)")
    wc.add_argument("--once", action="store_true", help="run a single scan then exit")
    wc.add_argument("keyword", nargs="+")
    wc.set_defaults(func=cmd_watch)

    of = sub.add_parser("offer", help="send a price offer for an item")
    of.add_argument("item_id")
    of.add_argument("--price", type=float, help="exact offer amount")
    of.add_argument("--pct", type=float, help="offer as a fraction of the asking price, e.g. 0.8")
    of.add_argument("--msg", help="optional message attached to the offer")
    of.add_argument("--dry-run", action="store_true")
    of.set_defaults(func=cmd_offer)

    me = sub.add_parser("message", help="send a chat message to an item's seller")
    me.add_argument("item_id", help="item url, web slug or internal hash")
    me.add_argument("--text", required=True, help="message text")
    me.add_argument("--dry-run", action="store_true")
    me.set_defaults(func=cmd_message)

    rp = sub.add_parser("replies", help="list your inbox conversations (or print one full thread)")
    rp.add_argument("--conversation", help="conversation id to dump the full message thread")
    rp.set_defaults(func=cmd_replies)

    parser.add_argument("--version", action="version", version=f"wallapop-cli {VERSION}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ApiError as err:
        eprint(paint.red(f"error: {err}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())