"""Domain model: typed dataclasses (Item, Deal, OfferEvent, OfferState) and the
derivations that produce them from raw Wallapop payloads.

Public contract (from the v1 design) lives on these dataclasses; the CLI and
downstream projects read attributes, never dict keys.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
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


def _attr_value(attr) -> str:
    if isinstance(attr, dict):
        v = attr.get("value")
        if v is not None:
            return str(v).strip()
        return str(attr.get("text") or "").strip()
    return str(attr or "").strip()


def _attr_text(attr) -> str:
    if isinstance(attr, dict):
        return str(attr.get("text") or attr.get("value") or "").strip()
    return str(attr or "").strip()


def _flag(obj) -> bool:
    if isinstance(obj, dict):
        return bool(obj.get("flag") or obj.get("value"))
    return bool(obj)


@dataclass
class Item:
    """A listing as the CLI/library sees it (normalized from raw payloads)."""

    id: str
    title: str
    price: float
    currency: str
    city: str
    user_id: str
    user_name: str
    url: str
    shippable: bool
    reserved: bool = False
    sold: bool = False
    category: str = ""
    category_name: str = ""
    created_at: str = ""
    description: str = ""
    condition: str = ""
    condition_text: str = ""
    brand: str = ""
    genre: str = ""
    views: int = 0
    favorites: int = 0
    favorited: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    top_profile: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict, fallback_user_id: Optional[str] = None) -> "Item":
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
        desc_raw = raw.get("description")
        description = ""
        if isinstance(desc_raw, dict):
            description = str(desc_raw.get("original") or desc_raw.get("value") or "")
        elif isinstance(desc_raw, str):
            description = desc_raw
        attrs = raw.get("type_attributes") or {}
        condition = _attr_value(attrs.get("condition"))
        condition_text = _attr_text(attrs.get("condition"))
        brand = _attr_value(attrs.get("brand"))
        genre = _attr_text(attrs.get("genre"))
        counters = raw.get("counters") or {}
        views = int(counters.get("views") or 0) if isinstance(counters, dict) else 0
        favorites = int(counters.get("favorites") or 0) if isinstance(counters, dict) else 0
        favorited = _flag(raw.get("favorited"))
        loc = raw.get("location") or {}
        latitude = float(loc.get("latitude") or 0.0) if isinstance(loc, dict) else 0.0
        longitude = float(loc.get("longitude") or 0.0) if isinstance(loc, dict) else 0.0
        user = user or {}
        top_profile = bool((user.get("is_top_profile") or raw.get("is_top_profile")) or False)
        return cls(
            id=item_id,
            user_id=user_id,
            user_name=user_name,
            title=str(title),
            price=price,
            currency=extract_currency(raw),
            reserved=reserved,
            sold=sold,
            shippable=extract_shipping(raw),
            city=str(city),
            category=category,
            category_name=category_name,
            created_at=str(raw.get("created_at") or ""),
            description=description,
            condition=condition,
            condition_text=condition_text,
            brand=brand,
            genre=genre,
            views=views,
            favorites=favorites,
            favorited=favorited,
            latitude=latitude,
            longitude=longitude,
            top_profile=top_profile,
            url=f"{WEB_BASE}/item/{slug}" if slug else "",
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "title": self.title,
            "price": self.price,
            "currency": self.currency,
            "reserved": self.reserved,
            "sold": self.sold,
            "shippable": self.shippable,
            "city": self.city,
            "category": self.category,
            "category_name": self.category_name,
            "created_at": self.created_at,
            "description": self.description,
            "condition": self.condition,
            "condition_text": self.condition_text,
            "brand": self.brand,
            "genre": self.genre,
            "views": self.views,
            "favorites": self.favorites,
            "favorited": self.favorited,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "top_profile": self.top_profile,
            "url": self.url,
        }


def normalize_item(raw: dict, fallback_user_id: Optional[str] = None) -> Item:
    """Alias of ``Item.from_raw`` kept for callers of the original helper."""
    return Item.from_raw(raw, fallback_user_id=fallback_user_id)


def median_price(prices: list[float]) -> float:
    if not prices:
        return 0.0
    ordered = sorted(prices)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass
class Deal:
    """An item priced below the median price of its candidate set."""

    item: Item
    median: float
    discount: float

    def to_dict(self) -> dict:
        d = self.item.to_dict()
        d["median"] = self.median
        d["discount"] = self.discount
        return d


def rank_deals(
    items: list[Item], min_discount: float, max_price: Optional[float]
) -> tuple[list[Deal], float]:
    candidates = [it for it in items if not it.reserved and not it.sold and it.price > 0]
    if max_price is not None:
        candidates = [it for it in candidates if it.price <= max_price]
    median = median_price([it.price for it in candidates])
    deals = []
    for it in candidates:
        discount = (median - it.price) / median if median else 0.0
        deals.append(Deal(item=it, median=median, discount=discount))
    deals.sort(key=lambda d: d.discount, reverse=True)
    return [d for d in deals if d.discount >= min_discount], median


OFFER_KINDS = (
    "our-offer",
    "seller-counter",
    "our-counter",
    "seller-accepted",
    "accepted-buy",
    "seller-declined",
    "our-declined",
    "expired",
)


@dataclass
class OfferEvent:
    """One entry in a conversation's offer ledger."""

    kind: str
    amount: Optional[float]
    offer_id: Optional[str]
    date: str
    ts: int = 0
    text: str = ""


@dataclass
class OfferState:
    """The current negotiation state derived from an offer ledger."""

    kind: str
    label: str
    amount: Optional[float]
    offer_id: Optional[str]
    actionable: bool


STATE_LABELS = {
    "seller-counter": "esperando tu respuesta",
    "our-offer": "esperando la respuesta del vendedor",
    "our-counter": "esperando la respuesta del vendedor",
    "seller-accepted": "aceptada por el vendedor (comprar en 24h)",
    "accepted-buy": "aceptada por ti — comprar en 24h",
    "seller-declined": "rechazada por el vendedor",
    "our-declined": "rechazada por ti",
    "expired": "caducada",
}

__PYMAPS = {}


def _offer_event(m: dict) -> Optional[dict]:
    """Classify one message into a raw ledger event (or None if not an offer)."""
    text = str(m.get("text") or offer_payload_summary(m.get("payload")) or "").lstrip()
    low = text.lower()
    if "contraoferta" in low:
        kind = "our-counter" if text.startswith("Has") else "seller-counter"
    elif "cómpralo ahora" in low or "oferta aceptada" in low:
        kind = "accepted-buy"
    elif "ha aceptado tu oferta" in low:
        kind = "seller-accepted"
    elif "ha rechazado tu oferta" in low:
        kind = "seller-declined"
    elif low.startswith("has rechazado"):
        kind = "our-declined"
    elif "oferta de" in low and text.startswith("Has"):
        kind = "our-offer"
    elif "caducad" in low:
        kind = "expired"
    else:
        return None
    amount = None
    mm = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)\s*[€]", text)
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
    return {
        "kind": kind,
        "amount": amount,
        "offer_id": offer_id,
        "ts": m.get("timestamp") or 0,
        "text": text,
    }


def parse_offer_events(msgs: list[dict]) -> list[OfferEvent]:
    """Reconstruct the offer ledger from a thread's delivery messages."""
    events: list[dict] = []
    for m in sorted((m or {} for m in msgs), key=lambda m: m.get("timestamp") or 0):
        ev = _offer_event(m)
        if ev:
            events.append(ev)
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
    return [
        OfferEvent(
            kind=ev["kind"],
            amount=ev["amount"],
            offer_id=ev["offer_id"],
            ts=ev["ts"],
            text=ev["text"],
            date=(
                datetime.datetime.fromtimestamp(ev["ts"] / 1000).strftime("%m-%d %H:%M")
                if ev["ts"]
                else ""
            ),
        )
        for ev in events
    ]


def offer_state_summary(events: list[OfferEvent]) -> Optional[OfferState]:
    """Current negotiation state derived from the latest offer event.

    Pure data (no terminal styling): returns the last event's ``kind``, the
    standing ``offer_id`` / ``amount`` (forward-filled from older events, since
    terminal delivery messages carry neither), and whether the state is
    ``actionable`` for us. Colored labels live in ``wallapoplib.cli.output``.
    """
    if not events:
        return None
    last = events[-1]
    kind = last.kind
    actionable = kind == "seller-counter"
    return OfferState(
        kind=kind,
        label=STATE_LABELS.get(kind, kind),
        amount=last.amount,
        offer_id=last.offer_id,
        actionable=actionable,
    )


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