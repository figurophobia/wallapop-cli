"""Resolve item references (url / slug / 12-char hash) to an internal item id."""

from __future__ import annotations

import json
import random
import re
import urllib.parse

from .config import USER_AGENTS, WEB_BASE
from .http import ApiError, http_request

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
