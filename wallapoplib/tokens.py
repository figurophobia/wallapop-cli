"""JWT helpers: parse claims, user hash, token expiry/refresh policy."""

from __future__ import annotations

import base64
import datetime
import json

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


def refresh_if_expired(client: Wallapop) -> None:
    exp = token_expiry(client.token or "")
    if exp is not None and exp <= datetime.datetime.now() + datetime.timedelta(minutes=1):
        if not client.try_refresh():
            client.refresh_web()
