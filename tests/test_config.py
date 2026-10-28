"""Offline tests for cookie parsing (config.parse_cookie_string) and for the
atomic credential/session storage.
"""

import json
import os
import stat

import pytest

from wallapop_mcp import config
from wallapop_mcp.config import (
    load_credentials,
    load_json,
    load_session,
    parse_cookie_string,
    save_credentials,
    save_json,
    save_session,
)


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
    from wallapop_mcp.config import CITIES, DEFAULT_GEO
    assert CITIES["italia"] == (41.9, 12.5)
    assert CITIES["portugal"] == (38.7, -9.1)
    assert CITIES["galicia"] == (42.6, -8.1)
    assert DEFAULT_GEO == CITIES["espana"]


def test_cities_preserve_existing_city_presets():
    from wallapop_mcp.config import CITIES
    assert CITIES["madrid"] == (40.4168, -3.7038)
    assert CITIES["barcelona"] == (41.3896, 2.1476)


def test_categories_reflects_canonical_endpoint():
    from wallapop_mcp.config import CATEGORIES
    assert CATEGORIES[100] == "Coches"
    assert CATEGORIES[12800] == "Motor y accesorios"
    assert CATEGORIES[24200] == "Tecnología y electrónica"
    assert CATEGORIES[12485] == "Otros"
    assert len(CATEGORIES) == 18
    assert 12545 not in CATEGORIES
    assert 16000 not in CATEGORIES
    assert 15000 not in CATEGORIES
    assert 12900 not in CATEGORIES


# --- atomic credential / session storage ------------------------------------

def test_save_json_writes_valid_json(tmp_path):
    path = tmp_path / "creds.json"
    save_json(str(path), {"access_token": "abc"})
    assert json.loads(path.read_text()) == {"access_token": "abc"}


def test_save_json_creates_private_files(tmp_path):
    path = tmp_path / "creds.json"
    save_json(str(path), {"access_token": "abc"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_json_can_write_public_files(tmp_path):
    path = tmp_path / "contacted.json"
    save_json(str(path), ["a"], private=False)
    assert json.loads(path.read_text()) == ["a"]
    assert stat.S_IMODE(os.stat(path).st_mode) != 0o600


def test_save_json_never_truncates_the_previous_content(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text('{"access_token": "old"}')

    class NotSerializable:
        pass

    with pytest.raises(TypeError):
        save_json(str(path), {"access_token": NotSerializable()})

    assert json.loads(path.read_text()) == {"access_token": "old"}
    # no temporary file is left behind
    assert [p.name for p in tmp_path.iterdir()] == ["creds.json"]


def test_save_credentials_and_session_are_atomic(tmp_path, monkeypatch):
    import wallapop_mcp.config as config_mod

    cred_path = tmp_path / "credentials.json"
    sess_path = tmp_path / "session.json"
    monkeypatch.setattr(config_mod, "CRED_PATH", str(cred_path))
    monkeypatch.setattr(config_mod, "SESSION_PATH", str(sess_path))

    save_credentials({"access_token": "abc", "email": "a@b.c"})
    save_session({"accessToken": "xyz"})

    assert json.loads(cred_path.read_text())["access_token"] == "abc"
    assert json.loads(sess_path.read_text())["accessToken"] == "xyz"
    assert stat.S_IMODE(os.stat(cred_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sess_path).st_mode) == 0o600
    assert load_credentials()["access_token"] == "abc"
    assert load_session()["accessToken"] == "xyz"


def test_save_json_replaces_the_file_atomically(tmp_path, monkeypatch):
    """The new content is moved into place with os.replace."""
    path = tmp_path / "creds.json"
    path.write_text("old")
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    save_json(str(path), {"a": 1})
    assert replaced and replaced[0][1] == str(path)
    assert json.loads(path.read_text()) == {"a": 1}


def test_load_json_returns_the_default_on_broken_files(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    assert load_json(str(path), {"fallback": True}) == {"fallback": True}


def test_config_dir_defaults_to_wallapop_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config._default_config_dir() == str(tmp_path / ".config" / "wallapop-mcp")


def test_config_dir_keeps_a_pre_rename_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "wallapop_cli").mkdir(parents=True)
    assert config._default_config_dir() == str(tmp_path / ".config" / "wallapop_cli")
    (tmp_path / ".config" / "wallapop-mcp").mkdir()
    assert config._default_config_dir() == str(tmp_path / ".config" / "wallapop-mcp")
