"""Offline tests for the library session initializer (wallapoplib.session).

The stored credentials/session are faked, so nothing is read from (or
written to) the real configuration directory and no network call is made.
"""

import pytest

import wallapoplib.session as session_mod
from wallapoplib import build_client as exported_build_client
from wallapoplib.session import SessionError, build_client


@pytest.fixture
def stored(monkeypatch):
    """Fake the stored credentials/session."""

    state = {"creds": {}, "session": {}, "minted": None}

    def fake_refresh(web_session):
        state["minted"] = dict(web_session)
        return "minted-token"

    monkeypatch.setattr(session_mod, "load_credentials", lambda: dict(state["creds"]))
    monkeypatch.setattr(session_mod, "load_session", lambda: dict(state["session"]))
    monkeypatch.setattr(session_mod, "refresh_web_session", fake_refresh)
    return state


def test_build_client_is_exported_by_the_library():
    assert exported_build_client is build_client


def test_build_client_raises_session_error_without_credentials(stored):
    with pytest.raises(SessionError):
        build_client()


def test_session_error_is_an_api_error(stored):
    from wallapoplib.http import ApiError

    with pytest.raises(ApiError):
        build_client()
    # ... and never a SystemExit (library consumers must not lose the process)
    try:
        build_client()
    except SystemExit:  # pragma: no cover - regression guard
        pytest.fail("build_client must not raise SystemExit")
    except ApiError:
        pass


def test_build_client_can_return_an_anonymous_client(stored):
    client, creds = build_client(required=False)
    assert client.token is None
    assert creds == {}


def test_build_client_uses_stored_credentials(stored):
    stored["creds"] = {
        "email": "daniel@example.com",
        "access_token": "access-123",
        "refresh_token": "refresh-123",
        "user": {"id": "wz1", "micro_name": "Daniel"},
    }
    client, creds = build_client()
    assert client.token == "access-123"
    assert client.refresh_token == "refresh-123"
    assert client.email == "daniel@example.com"
    assert client.user["micro_name"] == "Daniel"


def test_build_client_falls_back_to_the_browser_session(stored):
    stored["session"] = {
        "__Secure-next-auth.session-token": "st",
        "device_id": "d1",
        "accessToken": "browser-token",
    }
    client, _ = build_client()
    assert client.token == "browser-token"
    assert client.web_cookies is not None


def test_build_client_mints_a_token_from_the_browser_session(stored):
    stored["session"] = {
        "__Secure-next-auth.session-token": "st",
        "device_id": "d1",
    }
    client, _ = build_client()
    assert client.token == "minted-token"
    assert stored["minted"] is not None


def test_cli_helper_turns_a_missing_session_into_system_exit(stored, monkeypatch):
    from wallapoplib.cli import commands

    monkeypatch.setattr(commands, "build_client", build_client)
    with pytest.raises(SystemExit) as exc:
        commands.authed_client()
    assert exc.value.code == 1


def test_session_info_returns_only_metadata(stored):
    stored["creds"] = {"access_token": "private-access", "refresh_token": "private-refresh", "email": "private@example.com"}
    stored["session"] = {"__Secure-next-auth.session-token": "private-cookie"}
    result = session_mod.session_info()
    assert result == {"source": "browser", "logged_in": True, "expiry": None}
    assert stored["minted"] is None
