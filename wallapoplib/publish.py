"""Publish an item through the real web wizard (Playwright).

The upload flow is a stateless step machine served by ``POST /api/v3/steps``.
All the pieces are documented and the API endpoints are known, but the final
publish call (``POST /api/v3/items``) is multipart + header-gated and its body
is not exposed to CDP/Playwright (``postData: null``), so it cannot be
replayed with plain HTTP yet. Until then, publishing drives the real browser
wizard headful with the user's session and lets the page do the API calls.
"""

from __future__ import annotations

import os
import re

from .client import persist_browser_session, refresh_web_session
from .config import load_session

# JS helpers injected into the wizard page. ``window.D`` exposes the same
# primitives used to drive the uploader: clicking walla-button (shadow DOM),
# removing overlays, dumping button states.
DRIVE_JS = r"""
(function(){function removeOverlays() {
  document.querySelectorAll('#cmpwrapper,[id*="cmp"],[class*="consent"],[aria-modal="true"]').forEach(e => e.remove());
  document.querySelectorAll('div[class*="overlay"],div[class*="modal"]').forEach(e => { if (e.offsetParent !== null && e.getBoundingClientRect().width > 800) e.remove(); });
  return 'done';
}
function clickWalla(text) {
  const wbs = document.querySelectorAll('walla-button');
  for (const wb of wbs) {
    if (wb.disabled) continue;
    const shadow = wb.shadowRoot;
    let label = '';
    if (shadow) {
      const btn = shadow.querySelector('button');
      if (btn) label = btn.innerText.trim();
    }
    const full = wb.innerText.trim() || label;
    if (text && !full.toLowerCase().includes(text.toLowerCase())) continue;
    if (wb.offsetParent === null) continue;
    wb.click();
    if (shadow) { const btn = shadow.querySelector('button'); if (btn) btn.click(); }
    return 'CLICKED:' + (full.slice(0,20) || '(empty)');
  }
  return 'NOT_FOUND:' + (text || 'any');
}
function wallaStates() {
  return Array.from(document.querySelectorAll('walla-button')).map(wb => {
    const shadow = wb.shadowRoot;
    let label = '';
    if (shadow) { const btn = shadow.querySelector('button'); if (btn) label = btn.innerText.trim(); }
    return {disabled: wb.disabled, visible: wb.offsetParent !== null, label: label.slice(0,20)};
  });
}
;window.D={removeOverlays:removeOverlays,clickWalla:clickWalla,wallaStates:wallaStates};
})()
"""

UPLOAD_URL = "https://es.wallapop.com/app/catalog/upload/consumer-goods"
WEB_URL = "https://es.wallapop.com"

CONDITION_LABELS = {
    "un_opened": "Sin abrir",
    "in_box": "En caja",
    "new": "Nuevo",
    "as_good_as_new": "Como nuevo",
    "good": "En buen estado",
    "fair": "Aceptable",
    "has_given_it_all": "Le ha dado de sí",
}


def _click_continuar(page) -> None:
    r = page.evaluate('D.clickWalla("Continuar")')
    page.wait_for_timeout(5000)
    if r.startswith("NOT_FOUND"):
        raise RuntimeError(f"no 'Continuar' button found: {r}")


def publish_item(
    photos_dir: str,
    summary: str,
    price: float,
    condition: str = "good",
    weight_tier: str = "0",
    max_photos: int = 1,
    headless: bool = False,
    timeout_ms: int = 25_000,
) -> dict:
    """Publish an item through the real web wizard.

    ``photos_dir`` is a directory of jpg/png images (only ``max_photos`` of
    them are uploaded, one per call as the API requires). ``price`` is the
    list price in the account currency.

    Returns ``{"url": <final page url>, "item_id": <internal id if known>}`` or
    raises ``RuntimeError`` if the wizard fails to reach the publish call.
    """
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 (optional dep)

    session = load_session()
    token = refresh_web_session(session)
    persist_browser_session(session, token or session.get("accessToken"))

    photos = sorted(
        f for f in os.listdir(photos_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )
    if not photos:
        raise RuntimeError(f"no images found in {photos_dir!r}")
    photo_paths = [os.path.join(photos_dir, f) for f in photos[:max_photos]]

    cond_label = CONDITION_LABELS.get(condition, condition)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--window-size=1280,1400"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0", viewport={"width": 1280, "height": 1400}
        )
        ctx.add_cookies([
            {"name": "accessToken", "value": session["accessToken"], "domain": ".wallapop.com", "path": "/"},
            {"name": "device_id", "value": session.get("device_id", ""), "domain": ".wallapop.com", "path": "/"},
            {"name": "device_fgp", "value": session.get("device_fgp", ""), "domain": ".wallapop.com", "path": "/"},
        ])
        page = ctx.new_page()
        created = {"id": None}

        def on_response(resp) -> None:
            m = re.search(r"/api/v3/items/([0-9a-z]{12})\?language=es", resp.url)
            if m and resp.status == 200:
                created["id"] = m.group(1)

        page.on("response", on_response)

        page.goto(WEB_URL, wait_until="domcontentloaded", timeout=25_000)
        page.wait_for_timeout(2500)
        page.evaluate(
            "document.cookie='__Secure-next-auth.session-token=%s; path=/; secure; samesite=lax;'"
            % session["__Secure-next-auth.session-token"]
        )
        page.evaluate("location.reload()")
        page.wait_for_timeout(3000)

        listing_ok = False
        for attempt in range(3):
            try:
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
                page.evaluate(DRIVE_JS)
                for _ in range(4):
                    page.wait_for_timeout(2800)
                    page.evaluate("D.removeOverlays()")
                page.wait_for_selector('input[name="summary"]', state="attached", timeout=35_000)
                page.locator('input[name="summary"]').fill(summary)
                page.keyboard.press("Tab")
                page.wait_for_timeout(2000)
                _click_continuar(page)
                page.wait_for_selector('input[type="file"]', state="attached", timeout=25_000)
                page.locator('input[type="file"]').first.set_input_files(photo_paths)
                page.wait_for_timeout(18_000)
                _click_continuar(page)
                page.wait_for_selector('input[name="price_amount"]', state="attached", timeout=25_000)
                listing_ok = True
                break
            except Exception as exc:  # noqa: BLE001 - retry the whole wizard
                last_err = str(exc)[:120]
        if not listing_ok:
            browser.close()
            raise RuntimeError(f"could not reach the listing form: {last_err}")

        page.locator('input[name="price_amount"]').fill(str(int(price)))
        page.keyboard.press("Tab")
        page.wait_for_timeout(1500)

        # condition dropdown -> pick the localized label
        try:
            page.locator('walla-dropdown[data-testid="condition"]').first.click(timeout=8000)
            page.wait_for_timeout(2500)
            page.get_by_text(cond_label, exact=True).first.click(timeout=8000)
        except Exception:
            raise RuntimeError(f"could not select condition {cond_label!r}")
        page.wait_for_timeout(1500)

        # weight tier radio (default "0" = 0-1 kg); skip if the UI does not offer it
        try:
            page.locator(f'input[type=radio][name="{weight_tier}"]').check(timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        pub = page.evaluate('D.clickWalla("Publicar")')
        if not str(pub).startswith("CLICKED"):
            browser.close()
            raise RuntimeError(f"publish button not clickable: {pub}")

        page.wait_for_timeout(timeout_ms)
        url = page.url
        browser.close()

    item_id = created["id"]
    return {"url": url, "item_id": item_id}