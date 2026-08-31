"""Offline tests for cookie parsing (config.parse_cookie_string)."""

from wallapoplib.config import parse_cookie_string


def test_parse_semicolon_pairs():
    raw = "device_id=fc74ab160001; device_fgp=1a2b3c4d5e6f7g8h9i0j; accessToken=tok123"
    parsed = parse_cookie_string(raw)
    assert parsed == {"device_id": "fc74ab160001", "device_fgp": "1a2b3c4d5e6f7g8h9i0j", "accessToken": "tok123"}


def test_parse_values_with_spaces():
    raw = "accessToken=abc def ghi; device_id=1"
    parsed = parse_cookie_string(raw)
    assert parsed["accessToken"] == "abc def ghi"
    assert parsed["device_id"] == "1"


def test_parse_newlines_and_extra_spaces():
    raw = "device_id=1 2 3  ;\n  accessToken=x"
    parsed = parse_cookie_string(raw)
    assert parsed == {"device_id": "1 2 3", "accessToken": "x"}


def test_parse_chunk_without_equals_ignored():
    raw = "device_id=1; junk-without-operator; accessToken=2"
    parsed = parse_cookie_string(raw)
    assert parsed == {"device_id": "1", "accessToken": "2"}


def test_parse_empty():
    assert parse_cookie_string("") == {}
    assert parse_cookie_string("   ") == {}


def test_parse_keeps_first_of_duplicate_key():
    parsed = parse_cookie_string("device_id=a;device_id=b")
    assert parsed["device_id"] == "b"

def test_cities_include_countries_and_regions():
    from wallapoplib.config import CITIES, DEFAULT_GEO
    assert CITIES["italia"] == (41.9, 12.5)
    assert CITIES["portugal"] == (38.7, -9.1)
    assert CITIES["galicia"] == (42.6, -8.1)
    assert DEFAULT_GEO == CITIES["espana"]


def test_cities_preserve_existing_city_presets():
    from wallapoplib.config import CITIES
    assert CITIES["madrid"] == (40.4168, -3.7038)
    assert CITIES["barcelona"] == (41.3896, 2.1476)
