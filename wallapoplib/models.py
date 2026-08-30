"""Domain derivations: item normalization, deal ranking, offer-ledger parsing."""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Iterator, Optional

from .config import WEB_BASE

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


def parse_offer_events(msgs: list[dict]) -> list[dict]:
    """Reconstruct the offer ledger from a thread's delivery messages."""
    events: list[dict] = []
    for m in sorted((m or {} for m in msgs), key=lambda m: m.get("timestamp") or 0):
        t = str(m.get("text") or offer_payload_summary(m.get("payload")) or "").lstrip()
        low = t.lower()
        if "contraoferta" in low:
            kind = "our-counter" if t.startswith("Has") else "seller-counter"
        elif "cómpralo ahora" in low or "oferta aceptada" in low:
            kind = "accepted-buy"
        elif "ha aceptado tu oferta" in low:
            kind = "seller-accepted"
        elif "ha rechazado tu oferta" in low:
            kind = "seller-declined"
        elif low.startswith("has rechazado"):
            kind = "our-declined"
        elif "oferta de" in low and t.startswith("Has"):
            kind = "our-offer"
        elif "caducad" in low:
            kind = "expired"
        else:
            continue
        amount = None
        mm = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)\s*[€]", t)
        if mm:
            amount = float(mm.group(1).replace(",", "."))
        offer_id = None
        payload = m.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = None
        if isinstance(payload, dict):
            for btn in payload.get("buttons") or []:
                mid = re.search(r"/app/chat/offer/([0-9a-f-]{36})", str(btn.get("action") or ""))
                if mid:
                    offer_id = mid.group(1)
                    break
        events.append(
            {
                "kind": kind,
                "amount": amount,
                "offer_id": offer_id,
                "ts": m.get("timestamp") or 0,
                "text": t,
            }
        )
    last_amount: Optional[float] = None
    last_offer_id: Optional[str] = None
    for ev in events:
        if ev["amount"] is None:
            ev["amount"] = last_amount
        else:
            last_amount = ev["amount"]
        if ev["offer_id"] is None:
            ev["offer_id"] = last_offer_id
        else:
            last_offer_id = ev["offer_id"]
    return events


def offer_state_summary(events: list[dict]) -> Optional[dict]:
    """Current negotiation state derived from the latest offer event.

    Pure data (no terminal styling): returns the last event's ``kind``,
    the standing ``offer_id`` / ``amount`` (forward-filled from older
    events, since terminal delivery messages carry neither), and whether
    the state is ``actionable`` for us. Human/colored labels live in
    :func:`wallapoplib.output.offer_label`.
    """
    if not events:
        return None
    last = events[-1]
    kind = last["kind"]
    base = {"offer_id": last.get("offer_id"), "amount": last.get("amount"), "kind": kind}
    if kind == "seller-counter":
        return {"actionable": True, **base}
    if kind in ("our-offer", "our-counter"):
        return {"actionable": False, **base}
    return {"actionable": False, **base}


def offer_payload_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    amount = ""
    for key in ("offer_price_amount", "amount", "counter_offer_price_amount"):
        v = payload.get(key)
        if v is not None:
            amount = f"{v}€"
            break
    loc = payload.get("loc", {}) if isinstance(payload.get("loc"), dict) else {}
    cur = loc.get("current", "") or payload.get("text") or ""
    return f" {amount} · {cur}" if amount or cur else ""
