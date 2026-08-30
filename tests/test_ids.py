"""Offline tests for item reference resolution (ids)."""

import pytest

from wallapoplib.ids import is_internal_id, resolve_internal_id


def test_is_internal_id_12_char_hash():
    assert is_internal_id("v6gpwy9d476e") is True
    assert is_internal_id("8z88dd7qx1z3") is True


def test_is_internal_id_rejects_uuids_and_slugs():
    assert is_internal_id("wzy050ee4e65") is True
    assert is_internal_id("72f36f2c-f26d-4089-8b07-d7b341c462b6") is False
    assert is_internal_id("nintendo-switch-lite") is False


def test_is_internal_id_rejects_non_alnum():
    assert is_internal_id("abcde fghij1") is False
    assert is_internal_id("ABCDEFGHIJKL") is False


def test_resolve_internal_id_is_non_network_for_hashes():
    assert resolve_internal_id(" v6gpwy9d476e ") == "v6gpwy9d476e"


def test_resolve_internal_id_pulls_hash_from_url_without_network():
    url = "https://es.wallapop.com/item/v6gpwy9d476e"
    assert resolve_internal_id(url) == "v6gpwy9d476e"


def test_resolve_internal_id_slug_without_network_raises():
    with pytest.raises(Exception):
        resolve_internal_id("nintendo-switch-lite-sin-red")