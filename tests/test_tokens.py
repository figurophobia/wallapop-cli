"""Offline tests for JWT helpers (tokens).

Tokens are synthetic but shaped like real wallapop JWTs (header.payload.sig,
base64url payload with exp/sub/hashed_sub claims). No network.
"""

import base64
import datetime
import json
import time

from wallapoplib.tokens import jwt_claims, token_expiry, user_hash_of


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