"""Interactive web login wizard (Playwright).

Opens es.wallapop.com in a visible Chromium so the user can log in the normal
way (email/password or Google), then captures the resulting session cookies and
mints an access token from them. This is the path for accounts whose Wallapop
session lives only in a browser (e.g. registered with Google) without having to
come from Firefox.

playwright is an optional dependency: ``pip install -e '.[browser]'`` or
``pip install playwright && python3 -m playwright install chromium``.
"""

from __future__ import annotations

import time

from .client import persist_browser_session, refresh_web_session
from .config import WEB_BASE, WALLAPOP_COOKIE_KEYS

POLL_S = 2.0
DEFAULT_TIMEOUT_S = 300


def wizard_login(timeout_s: int = DEFAULT_TIMEOUT_S) -> tuple[dict, str]:
    """Log in once in a real browser and capture the Wallapop session.

    Returns ``(session, access_token)`` where ``session`` is the cookie dict
    (``accessToken``, ``__Secure-next-auth.session-token``, ``device_id``,
    ``device_fgp``) picked up from the browser after login. The session is
    also persisted to local storage before returning.

    Raises ``RuntimeError`` if the session cookie never appears within
    ``timeout_s`` or no access token can be minted from it.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 (optional dep)
    except ImportError:
        raise RuntimeError(
            "playwright is not installed. run: pip install -e '.[browser]' "
            "or: pip install playwright && python3 -m playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--window-size=1280,900"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        print("finish logging in in the browser window (email/password or Google)...",
              flush=True)
        page.goto(WEB_BASE, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2500)
        page.goto(f"{WEB_BASE}/app/access", wait_until="domcontentloaded", timeout=30_000)

        deadline = time.time() + timeout_s
        session: dict[str, str] = {}
        while time.time() < deadline:
            time.sleep(POLL_S)
            cookies = {
                c["name"]: c["value"]
                for c in ctx.cookies(WEB_BASE)
                if c["name"] in WALLAPOP_COOKIE_KEYS and c["value"]
            }
            if "__Secure-next-auth.session-token" in cookies and "device_id" in cookies:
                session = cookies
                break
        browser.close()

    if not session:
        raise RuntimeError(
            f"no session cookie captured after {timeout_s}s; relaunch with a bigger "
            "--timeout if you need more time to log in"
        )
    token = refresh_web_session(session)
    if not token:
        raise RuntimeError("session cookie captured but could not mint an access token; log in again")
    persist_browser_session(session, token)
    return session, token