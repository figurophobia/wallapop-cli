"""Offline tests for the domain model (Item, Deal, offer ledger).

Ledger messages are shaped like real wallapop ``delivery_generic`` thread
payloads, with ids de-sensibilized. No network.
"""

from wallapoplib.models import (
    Item,
    OfferEvent,
    deep_find,
    extract_items,
    normalize_item,
    offer_state_summary,
    parse_offer_events,
    rank_deals,
)


def _delivery_message(ts: int, text: str, *, from_self: bool = False, payload=None) -> dict:
    return {
        "timestamp": ts,
        "type": "delivery_generic",
        "from_self": from_self,
        "text": text,
        "payload": payload,
    }


# Real pixelia ledger (des-sensibilized): our 19.60 offer -> seller counter 25 -> accepted.
PIXELIA_MESSAGES = [
    _delivery_message(
        1760000001000,
        "Has enviado una oferta de 19,60 € por el artículo. El vendedor tiene 24 horas para responder.",
        from_self=True,
    ),
    _delivery_message(
        1760003600000,
        "Nueva contraoferta de 25 € por el artículo. El vendedor espera tu respuesta en las próximas 24 horas.",
        payload={
            "buttons": [{"action": "/app/chat/offer/72f36f2c-f26d-4089-8b07-d7b341c462b6"}]
        },
    ),
    _delivery_message(1760007200000, "Oferta aceptada. Cómpralo ahora durante las próximas 24 horas."),
]


def test_parse_offer_events_ledger():
    events = parse_offer_events(PIXELIA_MESSAGES)
    assert [e.kind for e in events] == ["our-offer", "seller-counter", "accepted-buy"]
    assert all(isinstance(e, OfferEvent) for e in events)


def test_forward_fill_in_terminal_events():
    events = parse_offer_events(PIXELIA_MESSAGES)
    assert events[0].amount == 19.60
    assert events[1].amount == 25.0
    assert events[2].amount == 25.0
    assert events[2].offer_id == "72f36f2c-f26d-4089-8b07-d7b341c462b6"
    assert events[1].offer_id == "72f36f2c-f26d-4089-8b07-d7b341c462b6"


def test_offer_state_summary_accepted():
    state = offer_state_summary(parse_offer_events(PIXELIA_MESSAGES))
    assert state is not None
    assert state.kind == "accepted-buy"
    assert state.amount == 25.0
    assert state.offer_id == "72f36f2c-f26d-4089-8b07-d7b341c462b6"
    assert state.actionable is False


def test_offer_state_summary_counter_is_actionable():
    msgs = PIXELIA_MESSAGES[:2]
    state = offer_state_summary(parse_offer_events(msgs))
    assert state.kind == "seller-counter"
    assert state.actionable is True
    assert state.label == "esperando tu respuesta"


def test_parse_rejects_plain_chat_messages():
    msgs = [{"timestamp": 1760000001000, "type": "text", "text": "Hola! Sigue disponible?"}]
    assert parse_offer_events(msgs) == []


def test_parse_sorts_by_timestamp():
    shuffled = list(reversed(PIXELIA_MESSAGES))
    events = parse_offer_events(shuffled)
    assert [e.kind for e in events] == ["our-offer", "seller-counter", "accepted-buy"]


# --- Item ------------------------------------------------------------------

RAW_ITEM = {
    "id": "v6gpwy9d476e",
    "title": {"original": "Nintendo DS Lite Blanca + Funda"},
    "price": {"amount": 45.0, "currency": "EUR"},
    "user": {"id": "wz0123456789", "micro_name": "Daniel"},
    "shipping": {"item_is_shippable": True},
    "location": {"city": "Barcelona"},
    "web_slug": "nintendo-ds-lite-blanca-funda-v6gpwy9d476e",
    "tag_highlights": [],
    "flags": {"reserved": False, "sold": False},
    "images": [
        {
            "id": "nz0gdgn1dwrj",
            "urls": {
                "small": "https://cdn.wallapop.com/images/s/i1.jpg",
                "medium": "https://cdn.wallapop.com/images/m/i1.jpg",
                "big": "https://cdn.wallapop.com/images/b/i1.jpg",
            },
        }
    ],
}


def test_item_from_raw():
    item = normalize_item(RAW_ITEM)
    assert isinstance(item, Item)
    assert item.id == "v6gpwy9d476e"
    assert item.title == "Nintendo DS Lite Blanca + Funda"
    assert item.price == 45.0
    assert item.currency == "EUR"
    assert item.user_id == "wz0123456789"
    assert item.user_name == "Daniel"
    assert item.city == "Barcelona"
    assert item.shippable is True
    assert item.url.endswith("/item/nintendo-ds-lite-blanca-funda-v6gpwy9d476e")
    assert item.images == ["https://cdn.wallapop.com/images/m/i1.jpg"]


def test_item_images_default_and_missing_media():
    item = normalize_item(RAW_ITEM)
    assert item.images
    raw = {"id": "x", "title": "solo", "images": [{"urls": {"big": "https://cdn.wallapop.com/b.jpg"}}]}
    fallback = normalize_item(raw)
    assert fallback.images == ["https://cdn.wallapop.com/b.jpg"]
    empty = normalize_item({"id": "y", "title": "sin img"})
    assert empty.images == []


def test_item_fallback_user_id():
    raw = dict(RAW_ITEM)
    raw["user"] = {}
    item = Item.from_raw(raw, fallback_user_id="wzfedcba9876")
    assert item.user_id == "wzfedcba9876"


def test_item_to_dict_roundtrip_keys():
    item = normalize_item(RAW_ITEM)
    d = item.to_dict()
    assert d["price"] == 45.0
    assert d["id"] == "v6gpwy9d476e"
    assert "url" in d


def test_item_extracts_description_from_original():
    raw = dict(RAW_ITEM)
    raw["description"] = {"original": "Funda nueva, sin usar."}
    item = normalize_item(raw)
    assert item.description == "Funda nueva, sin usar."


def test_item_description_defaults_empty():
    assert normalize_item(RAW_ITEM).description == ""


def test_item_extracts_type_attributes_and_stats():
    raw = dict(RAW_ITEM)
    raw["type_attributes"] = {
        "brand": {"value": "Nintendo", "text": "Nintendo"},
        "genre": {"value": "rpg", "text": "Rol (RPG)"},
        "condition": {"value": "good", "text": "Buen estado"},
    }
    raw["counters"] = {"views": 12, "favorites": 3, "conversations": 1}
    raw["favorited"] = {"flag": True}
    raw["location"] = {"latitude": 41.39, "longitude": 2.17, "city": "Barcelona"}
    raw["user"] = {"id": "wz0123456789", "is_top_profile": True}
    item = normalize_item(raw)
    assert item.brand == "Nintendo"
    assert item.genre == "Rol (RPG)"
    assert item.condition == "good"
    assert item.condition_text == "Buen estado"
    assert item.views == 12
    assert item.favorites == 3
    assert item.favorited is True
    assert item.latitude == 41.39
    assert item.longitude == 2.17
    assert item.top_profile is True


def test_item_type_attributes_defaults():
    item = normalize_item(RAW_ITEM)
    assert item.brand == ""
    assert item.condition == ""
    assert item.views == 0
    assert item.favorited is False
    assert item.latitude == 0.0


# --- Deals / ranking -------------------------------------------------------

def _items(*prices: float) -> list[Item]:
    out = []
    for i, p in enumerate(prices, 1):
        out.append(
            Item(
                id=f"it{i:011d}x",
                title=f"item {i}",
                price=p,
                currency="EUR",
                city="Madrid",
                user_id="wz0",
                user_name="",
                url="",
                shippable=False,
            )
        )
    return out


def test_rank_deals_below_median():
    items = _items(10.0, 20.0, 30.0)
    deals, median = rank_deals(items, min_discount=0.3, max_price=None)
    assert median == 20.0
    assert len(deals) == 1
    assert deals[0].item.price == 10.0
    assert abs(deals[0].discount - 0.5) < 1e-9


def test_rank_deals_excludes_reserved_sold_and_zero():
    items = _items(0.0, 10.0)
    items[0].reserved = True
    items[1].sold = True
    deals, median = rank_deals(items, min_discount=0.0, max_price=None)
    assert (deals, median) == ([], 0.0)


def test_rank_deals_max_price_filter():
    items = _items(10.0, 20.0, 30.0)
    deals, _ = rank_deals(items, min_discount=0.0, max_price=15.0)
    assert [d.item.price for d in deals] == [10.0]


def test_deep_find_and_extract_items():
    payload = {"search": {"sections": [{"items": [{"id": "a"}, {"id": "b"}]}]}}
    assert extract_items(payload) == [{"id": "a"}, {"id": "b"}]
    assert first_flow(deep_find(payload, "id")) == "a"


def first_flow(gen):
    return next(gen, None)