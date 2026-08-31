"""Offline tests for the Wallapop client with a mocked HTTP transport.

No network: ``wallapoplib.client.http_request`` is replaced by a fake that
answers endpoint calls the way the real API does (shapes taken from live
captures, ids de-sensibilized).
"""

import builtins
import sys
import uuid

import pytest

import wallapoplib.client as client_mod
from wallapoplib.client import Wallapop, refresh_web_session

# --- payload fixtures (live-shape, des-sensibilized) ------------------------

RAW_ITEM = {
    "id": "v6gpwy9d476e",
    "title": {"original": "Nintendo DS Lite Blanca + Funda"},
    "price": {"amount": 45.0, "currency": "EUR"},
    "user": {"id": "wz0123456789", "micro_name": "Daniel"},
    "shipping": {"item_is_shippable": True},
    "location": {"city": "Barcelona"},
    "web_slug": "nintendo-ds-lite-blanca-funda-v6gpwy9d476e",
    "flags": {"reserved": False, "sold": False},
    "images": [
        {
            "id": "nz0gdgn1dwrj",
            "average_color": "13C1AC",
            "urls": {
                "small": "https://cdn.wallapop.com/images/s/i1.jpg",
                "medium": "https://cdn.wallapop.com/images/m/i1.jpg",
                "big": "https://cdn.wallapop.com/images/b/i1.jpg",
            },
        }
    ],
}

SEARCH_COMPONENTS = {"data": {"search_id": "abc123", "sections": {"organic": {"position": 1}}}}

LEGACY_SEARCH = {
    "search_objects": [RAW_ITEM]
}

RAW_SELLER = {
    "id": "wz0123456789",
    "micro_name": "Daniel",
    "is_top_profile": True,
    "location": {"city": "Barcelona", "country_code": "ES"},
}

RAW_REVIEWS = [
    {"item": {"title": "GTA V"}, "review": {"rating_over_five": 5, "scoring": 100}},
    {"item": {"title": "3DS"}, "review": {"rating_over_five": 4, "scoring": 80}},
]

RAW_SELLER_STATS = {
    "rating_average": 4.9,
    "counters": [
        {"type": "publish", "value": 139},
        {"type": "buys", "value": 98},
        {"type": "sells", "value": 505},
        {"type": "reviews", "value": 465},
        {"type": "sold", "value": 505},
    ],
}

RAW_THREAD = {
    "channel": "channel-abc",
    "with_user": {"hash": "36ewgvee286d", "name": "pixelia"},
    "messages": {"messages": []},
}

RAW_THREAD_SAME_TEXT = {
    **RAW_THREAD,
    "messages": {
        "messages": [
            {
                "timestamp": 1760000001000,
                "from_self": True,
                "type": "text",
                "text": "Y tienes Super Mario 3D Land?",
            }
        ]
    },
}

RAW_THREAD_NO_MY_TEXT = {
    **RAW_THREAD,
    "messages": {
        "messages": [
            {"timestamp": 1760000001000, "from_self": False, "type": "text", "text": "Si, lo tengo!"}
        ]
    },
}

RAW_CATEGORIES = {
    "categories": [
        {
            "id": 100,
            "name": "Coches",
            "subcategories": [],
        },
        {
            "id": 12800,
            "name": "Motor y accesorios",
            "subcategories": [
                {"id": 10165, "name": "Herramientas", "subcategories": []},
                {
                    "id": 10167,
                    "name": "Recambios de coches y furgonetas",
                    "subcategories": [{"id": 10328, "name": "Accesorios", "subcategories": []}],
                },
            ],
        },
    ]
}

# --- transport fake ---------------------------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.thread_same = False
        self.section_pages = 1
        self.force_legacy = False

    def __call__(self, method: str, url: str, params=None, body=None, headers=None):
        self.calls.append((method, url, dict(params or {}), dict(body or {})))
        if url.endswith("/search/components"):
            if self.force_legacy:
                return 200, {"data": {}}, "application/json"
            return 200, SEARCH_COMPONENTS, "application/json"
        if "/search/section" in url:
            section_calls = [c for c in self.calls if "/search/section" in c[1]]
            current = section_calls.index((method, url, dict(params or {}), dict(body or {})))
            has_next = current < self.section_pages - 1
            return 200, {
                "data": {
                    "next_page": "cursor-page-2" if has_next else None,
                    "items": [RAW_ITEM],
                }
            }, "application/json"
        if url.endswith("/general/search"):
            return 200, LEGACY_SEARCH, "application/json"
        if url.endswith(f"/items/{RAW_ITEM['id']}"):
            return 200, RAW_ITEM, "application/json"
        if url.endswith("/users/wz0123456789/reviews"):
            return 200, RAW_REVIEWS, "application/json"
        if url.endswith("/users/wz0123456789/stats"):
            return 200, RAW_SELLER_STATS, "application/json"
        if url.endswith("/users/wz0123456789"):
            return 200, RAW_SELLER, "application/json"
        if url.endswith("/instant-messaging/conversation"):
            return 200, {"conversation_id": "wzy050ee4e65", "item_hash_id": RAW_ITEM["id"]}, "application/json"
        if "/bff/messaging/conversation/" in url:
            if self.thread_same or "same" in url:
                return 200, RAW_THREAD_SAME_TEXT, "application/json"
            return 200, RAW_THREAD_NO_MY_TEXT, "application/json"
        if url.endswith("/instant-messaging/token"):
            return 200, {"token": "im-auth-token"}, "application/json"
        if "publish" in url:
            return 200, '[1,"Sent",1234567890]', "text/plain"
        if "/bff/delivery" in url or "/bff/messaging/inbox" in url:
            data = {"restriction": {"max_offer_percentage": 30.0, "remaining_offers_per_day": 3}, "conversations": []}
            return 200, data, "application/json"
        if "/delivery/buyer/offers" in url or "/delivery/offers/" in url:
            return 200, {"offer_id": "created"}, "application/json"
        if url.endswith("/categories"):
            return 200, RAW_CATEGORIES, "application/json"
        raise AssertionError(f"unexpected call: {method} {url} params={params} body={body}")


@pytest.fixture
def fake(monkeypatch):
    t = FakeTransport()
    monkeypatch.setattr(client_mod, "http_request", t)
    return t


# --- search / normalize -----------------------------------------------------

def test_seller_profile(fake):
    client = Wallapop()
    profile = client.seller("wz0123456789")
    assert profile["micro_name"] == "Daniel"
    assert profile["is_top_profile"] is True


def test_seller_reviews(fake):
    client = Wallapop()
    reviews = client.seller_reviews("wz0123456789")
    assert len(reviews) == 2
    ratings = [r["review"]["rating_over_five"] for r in reviews]
    assert sum(ratings) / len(ratings) == 4.5


def test_seller_stats(fake):
    client = Wallapop()
    stats = client.seller_stats("wz0123456789")
    assert stats["rating_average"] == 4.9
    counters = {c["type"]: c["value"] for c in stats["counters"]}
    assert counters["reviews"] == 465


def test_search_uses_default_geo_when_no_coords(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["latitude"] == 40.0
    assert params["longitude"] == -3.5


def test_search_respects_provided_coords(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0, latitude=41.5, longitude=2.1)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["latitude"] == 41.5
    assert params["longitude"] == 2.1


def test_search_sends_filter_params(fake):
    client = Wallapop()
    client.search(
        "gamecube",
        pages=1,
        delay=0,
        category_id=1,
        subcategory_ids=2,
        condition="new",
        min_price=10.0,
        max_price=100.0,
        ship_only=True,
    )
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["keywords"] == "gamecube"
    assert params["category_id"] == 1
    assert params["subcategory_ids"] == 2
    assert params["condition"] == "new"
    assert params["min_sale_price"] == 10.0
    assert params["max_sale_price"] == 100.0
    assert params["is_shippable"] == "true"


def test_search_orders_by_relevance_by_default(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    assert comps[2]["order_by"] == "most_relevance"


def test_search_paginates_with_next_page(fake):
    fake.section_pages = 3
    client = Wallapop()
    items = client.search("gamecube", pages=3, delay=0)
    sections = [c for c in fake.calls if "/search/section" in c[1]]
    assert len(sections) == 3
    assert sections[1][2].get("next_page") == "cursor-page-2"
    assert len(items) == 3


def test_search_falls_back_to_legacy_when_no_search_id(fake):
    fake.force_legacy = True
    client = Wallapop()
    items = client.search("gamecube", pages=1, delay=0)
    assert len(items) == 1
    legacy = next(c for c in fake.calls if c[1].endswith("/general/search"))
    assert legacy[2]["keywords"] == "gamecube"
    assert legacy[2]["distance"] == 1000000


def test_search_returns_items(fake):
    client = Wallapop()
    items = client.search("nintendo ds", pages=1, delay=0)
    assert len(items) == 1
    it = items[0]
    assert it.id == RAW_ITEM["id"]
    assert it.price == 45.0
    assert it.title == "Nintendo DS Lite Blanca + Funda"


def test_item_has_image_urls(fake):
    client = Wallapop()
    it = client.item(RAW_ITEM["id"])
    assert it.images == ["https://cdn.wallapop.com/images/m/i1.jpg"]


def test_download_images(tmp_path, fake, monkeypatch):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def read(self):
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout: FakeResponse(b"JPEGDATA"),
    )
    client = Wallapop()
    it = client.item(RAW_ITEM["id"])
    saved = client.download_images(it, str(tmp_path))
    assert len(saved) == 1
    assert saved[0].endswith(f"{RAW_ITEM['id']}_1.jpg")
    with open(saved[0], "rb") as f:
        assert f.read() == b"JPEGDATA"


def test_download_images_no_op_when_no_images(fake, tmp_path):
    client = Wallapop()
    raw = dict(RAW_ITEM)
    raw["images"] = []
    item = client_mod.Item.from_raw(raw)
    assert client.download_images(item, str(tmp_path)) == []


# --- session / auth ---------------------------------------------------------

def test_refresh_web_session_returns_token(fake, monkeypatch):
    monkeypatch.setattr(client_mod, "http_request", lambda *a, **k: (
        200, {"token": "fake.access.token.xyz"}, "application/json"
    ))
    session = {"__Secure-next-auth.session-token": "st", "device_id": "d1", "device_fgp": "f1", "accessToken": "a1"}
    assert refresh_web_session(session) == "fake.access.token.xyz"


def test_authed_request_refreshes_token_on_401(fake, monkeypatch):
    calls = []

    def flaky(method, url, params=None, body=None, headers=None):
        calls.append(method)
        if "/bff/messaging/inbox" in url:
            if len(calls) == 1:
                return (401, {"error": "expired"}, "application/json")
            return 200, {"conversations": []}, "application/json"
        if "/api/auth/session" in url:
            return 200, {"token": "refreshed.token"}, "application/json"
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", flaky)
    client = Wallapop(token="old.token", web_cookies={"__Secure-next-auth.session-token": "st"})
    resp = client.inbox()
    assert resp.ok
    assert resp.data == {"conversations": []}


# --- offers -----------------------------------------------------------------

def test_send_offer_builds_uuid_and_price(fake):
    client = Wallapop(token="tk")
    resp = client.send_offer(RAW_ITEM["id"], 25.0, message="hola")
    assert resp.ok
    matched = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/delivery/buyer/offers")]
    assert len(matched) == 1
    body = matched[0][3]
    uuid.UUID(body["offer_id"])  # raises if not a uuid
    assert body["offer_price_amount"] == 25.0
    assert body["offer_price_currency"] == "EUR"
    assert body["item_ids"] == [RAW_ITEM["id"]]


def test_offer_response_maps_accept_and_decline(fake):
    client = Wallapop(token="tk")
    client.offer_response("offer-1", "accept")
    client.offer_response("offer-1", "decline")
    patches = [c for c in fake.calls if c[0] == "PATCH" and "status" in c[1]]
    assert [c[3].get("status") for c in patches] == ["ACCEPTED", "DECLINED"]


def test_counter_offer_patches_price(fake):
    client = Wallapop(token="tk")
    client.counter_offer("offer-1", 37.0)
    matched = [c for c in fake.calls if c[0] == "PATCH" and c[1].endswith("/offer-price")]
    assert len(matched) == 1
    assert matched[0][3] == {"offer_price_amount": 37.0, "offer_price_currency": "EUR"}


# --- chat idempotency -------------------------------------------------------

def test_send_message_skips_when_same_text_present(fake):
    fake.thread_same = True
    client = Wallapop(token="tk")
    client.token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "Y tienes Super Mario 3D Land?", report=report)
    assert report.get("skipped") is True
    assert report.get("path") == "skipped"
    assert not any("publish" in c[1] for c in fake.calls)


def test_send_message_publishes_via_rest_when_new_text(fake):
    client = Wallapop(token="tk")
    client.token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "Hola! Viene con stylus?", report=report)
    assert report.get("path") == "rest"
    assert report.get("skipped") is False
    publishes = [c for c in fake.calls if "publish" in c[1]]
    assert len(publishes) == 1
    assert "ps7.pndsn.com" in publishes[0][1]


# --- browser fallback degrades without playwright ---------------------------

def test_send_chat_text_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(client_mod, "load_session", lambda: {
        "accessToken": "a", "device_id": "d", "device_fgp": "f", "__Secure-next-auth.session-token": "st"
    })
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("playwright not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert client_mod.send_chat_text(RAW_ITEM["id"], "hola") is False


# --- make_offer_info --------------------------------------------------------

def test_make_offer_info_reads_restriction(fake):
    client = Wallapop(token="tk")
    resp = client.make_offer_info(RAW_ITEM["id"])
    assert resp.ok
    assert resp.data["restriction"]["max_offer_percentage"] == 30.0


# --- categories -------------------------------------------------------------

def test_categories_hits_context_search(fake):
    client = Wallapop()
    client.categories()
    call = next(c for c in fake.calls if c[1].endswith("/categories"))
    assert call[2] == {"context": "search"}


def test_categories_returns_clean_tree(fake):
    client = Wallapop()
    cats = client.categories()
    assert [c["id"] for c in cats] == [100, 12800]
    assert cats[0] == {"id": 100, "name": "Coches", "subcategories": []}
    mot = cats[1]
    assert mot["name"] == "Motor y accesorios"
    assert mot["subcategories"][1]["name"] == "Recambios de coches y furgonetas"
    assert mot["subcategories"][1]["subcategories"][0]["id"] == 10328