"""Offline tests for JWT helpers (tokens).

Tokens are synthetic but shaped like real wallapop JWTs (header.payload.sig,
base64url payload with exp/sub/hashed_sub claims). No network.
"""

import base64
import datetime
import json
import time

from wallapop_mcp.tokens import jwt_claims, refresh_if_expired, token_expiry, user_hash_of


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_token(claims: dict) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.fakesignature"


def test_token_expiry_from_real_shaped_claims():
    now = time.time()
    token = _make_token({"iat": int(now), "exp": int(now) + 3600})
    exp = token_expiry(token)
    assert exp is not None
    assert datetime.datetime.now() < exp < datetime.datetime.now() + datetime.timedelta(hours=2)


def test_token_expiry_non_jwt():
    assert token_expiry("not-a-token") is None


def test_user_hash_of():
    token = _make_token({"hashed_sub": "wzynovmv1pj5", "exp": int(time.time()) + 100})
    assert user_hash_of(token) == "wzynovmv1pj5"


def test_jwt_claims_full_payload():
    claims = {
        "iat": 1760000000,
        "exp": 1760003600,
        "hashed_sub": "wzynovmv1pj5",
        "https://hasura.io/jwt/claims": {"x-hasura-default-role": "user"},
    }
    token = _make_token(claims)
    parsed = jwt_claims(token)
    assert parsed["hashed_sub"] == "wzynovmv1pj5"
    assert parsed["https://hasura.io/jwt/claims"]["x-hasura-default-role"] == "user"


def test_jwt_claims_garbage_is_empty():
    assert jwt_claims("") == {}


class _FakeClient:
    """Minimal stand-in for Wallapop with scripted refresh behaviour."""

    def __init__(self, token, try_refresh=None, refresh_web=None):
        self.token = token
        self.calls = []
        self._try_refresh = try_refresh
        self._refresh_web = refresh_web

    def try_refresh(self):
        self.calls.append("try_refresh")
        if self._try_refresh is None:
            return False
        result = self._try_refresh(self)
        return bool(result)

    def refresh_web(self):
        self.calls.append("refresh_web")
        if self._refresh_web is None:
            return False
        result = self._refresh_web(self)
        return bool(result)


def _expired_token(offset_seconds=-60):
    return _make_token({"iat": int(time.time()) + offset_seconds, "exp": int(time.time()) + offset_seconds})


def test_refresh_if_expired_skips_valid_token():
    client = _FakeClient(_make_token({"exp": int(time.time()) + 3600}))
    assert refresh_if_expired(client) is False
    assert client.calls == []


def test_refresh_if_expired_skips_non_jwt_token():
    client = _FakeClient("not-a-token")
    assert refresh_if_expired(client) is False
    assert client.calls == []


def test_refresh_if_expired_prefers_refresh_token():
    def try_refresh(c):
        c.token = "refreshed-token"
        return True

    def refresh_web(c):  # pragma: no cover - must not be reached
        raise AssertionError("refresh_web must not be used when refresh_token works")

    client = _FakeClient(_expired_token(), try_refresh=try_refresh, refresh_web=refresh_web)
    assert refresh_if_expired(client) is True
    assert client.token == "refreshed-token"
    assert client.calls == ["try_refresh"]


def test_refresh_if_expired_falls_back_to_web():
    def try_refresh(c):
        return False

    def refresh_web(c):
        c.token = "web-token"
        return True

    client = _FakeClient(_expired_token(), try_refresh=try_refresh, refresh_web=refresh_web)
    assert refresh_if_expired(client) is True
    assert client.token == "web-token"
    assert client.calls == ["try_refresh", "refresh_web"]


def test_refresh_if_expired_false_when_token_unchanged():
    """A refresh that hands back the very same token is not a successful renewal."""
    same = _expired_token()

    def try_refresh(c):
        c.token = same
        return True

    def refresh_web(c):
        c.token = same
        return True

    client = _FakeClient(same, try_refresh=try_refresh, refresh_web=refresh_web)
    assert refresh_if_expired(client) is False
    assert client.calls == ["try_refresh", "refresh_web"]


def test_refresh_if_expired_false_when_both_fail():
    client = _FakeClient(_expired_token(), try_refresh=lambda c: False, refresh_web=lambda c: False)
    assert refresh_if_expired(client) is False
    assert client.calls == ["try_refresh", "refresh_web"]


def test_refresh_if_expired_ignores_cleared_token():
    def try_refresh(c):
        c.token = ""
        return True

    client = _FakeClient(_expired_token(), try_refresh=try_refresh)
    assert refresh_if_expired(client) is False


def test_refresh_if_expired_refreshes_about_to_expire():
    soon = _make_token({"exp": int(time.time()) + 30})

    def try_refresh(c):
        c.token = "new-token"
        return True

    client = _FakeClient(soon, try_refresh=try_refresh)
    assert refresh_if_expired(client) is True
    assert client.token == "new-token"