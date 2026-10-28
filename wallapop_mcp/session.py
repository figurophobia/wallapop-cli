"""Session bootstrap: build an authenticated client from the stored session.

This is the library side of "give me a client for the logged in user".  It
lives in the library (and not in the CLI) so that consumers do not have to
import the command line module or catch ``SystemExit``::

    from wallapop_mcp import build_client

    client, creds = build_client()          # raises SessionError if not logged in
    client, creds = build_client(required=False)   # anonymous client if not logged in

The CLI (``wallapop_mcp.cli.commands.authed_client``) wraps this helper and
turns a missing session into a friendly ``SystemExit(1)``.
"""

from __future__ import annotations

from .client import Wallapop, persist_browser_session, refresh_web_session
from .config import load_credentials, load_session, save_credentials
from .http import ApiError
from .tokens import refresh_if_expired, token_expiry


class SessionError(ApiError):
    """Raised when no usable session (credentials or browser cookies) exists."""


def build_client(required: bool = True) -> tuple[Wallapop, dict]:
    """Return ``(client, credentials)`` built from the stored session.

    The access token is taken from the stored credentials, else from the
    stored browser session; when only the browser session exists a token is
    minted from it.  The client is wired to persist any refreshed token back
    to the local credential/session files.

    Raises :class:`SessionError` when ``required`` is True and no token could
    be obtained.
    """
    creds = load_credentials()
    session = load_session()
    web_cookies = session if session.get("__Secure-next-auth.session-token") else None
    token = creds.get("access_token") or session.get("accessToken") or ""
    if not token and web_cookies:
        refreshed = refresh_web_session(web_cookies)
        if refreshed:
            token = refreshed
    if required and not token:
        raise SessionError(
            "not logged in. run: wallapop login <email> <password>  "
            "or  wallapop login --browser firefox"
        )

    def persist(client: Wallapop) -> None:
        save_credentials(
            {
                "email": client.email,
                "access_token": client.token,
                "refresh_token": client.refresh_token,
                "user": client.user,
            }
        )
        if client.web_cookies:
            persist_browser_session(client.web_cookies, client.token)

    client = Wallapop(
        token=token or None,
        refresh_token=creds.get("refresh_token"),
        email=creds.get("email"),
        persist_fn=persist,
        web_cookies=web_cookies,
    )
    if token:
        client.user = creds.get("user", {}) or {}
        refresh_if_expired(client)
    return client, creds


def session_info() -> dict:
    """Describe the stored session (source, expiry) without any network call."""
    creds = load_credentials()
    session = load_session()
    token = creds.get("access_token") or session.get("accessToken") or ""
    source = (
        "browser"
        if session.get("__Secure-next-auth.session-token")
        else ("email" if creds.get("access_token") else "none")
    )
    expiry = token_expiry(token)
    return {"source": source, "logged_in": bool(token), "expiry": expiry.isoformat() if expiry else None}


__all__ = ["SessionError", "build_client", "session_info"]
