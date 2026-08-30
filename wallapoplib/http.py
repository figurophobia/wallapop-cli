"""Low-level HTTP transport: rate-limited requests, retries, payload decode."""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

RATE_PACE = float(os.environ.get("WALLAPOP_RATE_PACE", "1.0"))


LAST_REQUEST = 0.0


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


class ApiError(Exception):
    pass


@dataclass
class Response:
    status: int
    data: Any
    ok: bool
    unauthorized: bool

    @property
    def error_reason(self) -> str:
        data = self.data
        if isinstance(data, dict):
            status_block = data.get("status")
            if isinstance(status_block, dict) and isinstance(status_block.get("message"), str):
                return status_block["message"]
            for key in ("error_message", "message", "error"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val
        return ""


def http_request(
    method: str,
    url: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    retries: int = 3,
    timeout: int = 25,
) -> tuple[int, Any, str]:
    global LAST_REQUEST
    if RATE_PACE > 0:
        wait = RATE_PACE - (time.monotonic() - LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        LAST_REQUEST = time.monotonic()
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    status = 0
    ctype = ""
    payload = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=encoded, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            break
        except urllib.error.HTTPError as err:
            status = err.code
            payload = err.read()
            ctype = err.headers.get("Content-Type", "")
            if status in (403, 429) and attempt < retries - 1:
                time.sleep(2 ** attempt + random.uniform(0.3, 1.2))
                continue
            break
        except urllib.error.URLError as err:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.uniform(0.3, 1.2))
                continue
            raise ApiError(f"network error: {err.reason}")
    return status, decode_payload(payload, ctype), ctype


def decode_payload(payload: Optional[bytes], ctype: str) -> Any:
    if payload is None:
        return None
    text = payload.decode("utf-8", "replace")
    if "json" in (ctype or "").lower() or text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text
