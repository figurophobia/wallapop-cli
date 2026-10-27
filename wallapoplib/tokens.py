"""JWT helpers: parse claims, user hash, token expiry/refresh policy."""

from __future__ import annotations

import base64
import datetime
import json
from typing import Any, Optional


def jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        padded = part + "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def user_hash_of(token: str) -> str:
    return str(jwt_claims(token).get("hashed_sub") or "")


def token_expiry(token: str) -> Optional[datetime.datetime]:
    if not token.startswith("eyJ"):
        return None
    try:
        claims = jwt_claims(token)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.datetime.fromtimestamp(exp)
    except Exception:
        pass
    return None


def refresh_if_expired(client: Any) -> bool:
    """Refresh the client's access token when it is expired or about to expire.

    The ``refresh_token`` is preferred; the browser session is only used when
    the refresh token is missing or rejected.  Returns ``True`` only when a
    *different* token was obtained, so a refresh that keeps returning the
    same (already dead) token is not mistaken for a successful renewal.
    """
    exp = token_expiry(client.token or "")
    if exp is None or exp > datetime.datetime.now() + datetime.timedelta(minutes=1):
        return False
    before = client.token
    if client.try_refresh() and client.token and client.token != before:
        return True
    if client.refresh_web() and client.token and client.token != before:
        return True
    return False
