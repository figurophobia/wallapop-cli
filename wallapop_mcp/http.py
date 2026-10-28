"""Low-level HTTP transport: rate-limited requests, retries, payload decode.

Transport policy (kept deliberately conservative):

- Only *safe* operations are retried: ordinary reads (``GET``/``HEAD`` without
  a body).  Writes (``POST``/``PUT``/``PATCH``/``DELETE``) and non-idempotent
  ``GET``s (e.g. the PubNub publish, which is a write in disguise) are sent
  exactly once.
- A read may be retried on transient network errors and on ``429``/``502``/
  ``503``/``504``.  ``403`` is never retried (it is an answer, not a glitch)
  and neither are the other ``4xx``/``5xx`` codes.
- Only *transient* network failures are replayed: a timeout, a reset
  connection, a DNS hiccup.  A certificate validation error (and any other
  TLS/URL level problem) is permanent and is never retried.  A bare
  ``OSError`` counts as transient only when it carries one of the known
  connection error codes (:data:`TRANSIENT_ERRNOS`).
- A server-provided ``Retry-After`` is honoured up to :data:`MAX_RETRY_AFTER`.
  A longer wait is **not** clamped: the request is given up on and the current
  HTTP error is returned, so we never hammer a server that asked us to wait.
- A write that dies at the socket level (timeout, connection reset) may have
  been applied by the server: the raised error says so (``outcome``
  attribute) instead of pretending the operation never happened.  Only a
  certificate validation error (the handshake never completed) proves the
  request was not sent.
- Every attempt is paced: the minimum gap between two request *starts* is
  :data:`RATE_PACE` seconds.  The pacing slot is reserved under a lock but the
  lock is never held while the network call is in flight.
"""

from __future__ import annotations

import errno
import http.client
import json
import math
import os
import random
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional


RATE_PACE = float(os.environ.get("WALLAPOP_RATE_PACE", "1.0"))


#: Timestamp of the last request *start* (monotonic clock).
LAST_REQUEST = 0.0


#: HTTP statuses worth retrying on a safe (read) operation.
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


#: Upper bound (seconds) for a server supplied ``Retry-After`` header.
MAX_RETRY_AFTER = 30.0


#: Upper bound (seconds) for the exponential backoff between two attempts.
MAX_BACKOFF = 8.0


#: Methods that may be retried when they carry no body.
SAFE_METHODS = frozenset({"GET", "HEAD"})


#: ``errno`` values that mean "the connection broke, the request may be
#: replayed".  An :class:`OSError` carrying any other code is *not* treated
#: as transient: retrying it would only repeat a permanent failure.
TRANSIENT_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, name, None)
        for name in (
            "EAGAIN",
            "EWOULDBLOCK",
            "EINTR",
            "ECONNRESET",
            "ECONNABORTED",
            "ECONNREFUSED",
            "EPIPE",
            "ETIMEDOUT",
            "EHOSTUNREACH",
            "ENETUNREACH",
            "ENETDOWN",
            "ENETRESET",
            "ESHUTDOWN",
        )
    )
    if code is not None
)


_PACE_LOCK = threading.Lock()


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


class ApiError(Exception):
    """Any failure the caller can report to the user."""


class TransportError(ApiError):
    """A network level failure.

    ``outcome`` tells what happened to the request:

    - ``"not_sent"``: the request never left the client (DNS failure,
      refused connection, certificate validation error...).  Replaying it is
      safe.
    - ``"unknown"``: the request may have reached the server (a timeout or a
      connection reset while the answer was on its way).  Replaying it could
      duplicate the operation.
    """

    outcome = "unknown"


class NotSentError(TransportError):
    """The request was never sent: retrying it is safe."""

    outcome = "not_sent"


class UnknownOutcomeError(TransportError):
    """The request may have been applied by the server: the outcome is unknown."""

    outcome = "unknown"


@dataclass
class Response:
    status: int
    data: Any
    ok: bool
    unauthorized: bool
    #: True when the operation completed only partially (e.g. an item was
    #: created but some of its photos could not be uploaded).
    partial: bool = False

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


def is_retry_safe(method: str, has_body: bool = False) -> bool:
    """Return True when ``method`` is an operation that may be retried.

    A read (``GET``/``HEAD``) without a body is safe; anything else is treated
    as a write and is never replayed automatically.
    """
    return method.upper() in SAFE_METHODS and not has_body


def is_transient_network_error(exc: BaseException) -> bool:
    """True when ``exc`` is a transient network glitch worth retrying.

    Certificate validation errors (and TLS problems in general) are *not*
    transient: the very same request would fail again, so it must not be
    replayed.

    A bare :class:`OSError` is only transient when it carries one of the
    known connection error codes (:data:`TRANSIENT_ERRNOS`); anything else
    (a permission problem, a closed file...) is permanent and is not
    replayed.
    """
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, ConnectionError):
        return True
    if isinstance(exc, (socket.gaierror, socket.herror)):
        return True
    if isinstance(exc, http.client.HTTPException):
        return True
    if isinstance(exc, OSError):
        return exc.errno in TRANSIENT_ERRNOS
    return False


def _failure_outcome(reason: Any) -> str:
    """Classify a failure as ``"not_sent"`` or ``"unknown"``.

    Only the failures that provably happen *before* the request leaves the
    client are ``"not_sent"``; everything else is ambiguous.

    A certificate validation error is definitive (the TLS handshake never
    completed, so no request was written).  Any other TLS error is **not**:
    an ``SSLEOFError`` may well happen *after* the request was written, so
    the outcome is ambiguous.
    """
    if isinstance(reason, ssl.SSLCertVerificationError):
        # the handshake failed: the request never left the client
        return "not_sent"
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror, socket.herror)):
        return "not_sent"
    # every other ssl.SSLError (SSLEOFError, SSLZeroReturnError...) may have
    # happened after the request was written: the outcome is ambiguous.
    return "unknown"


def _network_error(reason: Any, safe: bool) -> ApiError:
    """Build the error for a network failure, labelling the write outcome."""
    text = f"network error: {reason}"
    if safe:
        return ApiError(text)
    if _failure_outcome(reason) == "not_sent":
        return NotSentError(f"{text} (the request was not sent)")
    return UnknownOutcomeError(f"{text} (the request may have been applied: outcome unknown)")


def _pace() -> None:
    """Reserve the next request slot and wait for it.

    The slot is reserved under a lock (so concurrent callers cannot start at
    the same instant) but the lock is released before sleeping, so no lock is
    ever held while the network call is in flight.
    """
    global LAST_REQUEST
    pace = RATE_PACE
    if pace <= 0:
        return
    with _PACE_LOCK:
        now = time.monotonic()
        start = LAST_REQUEST if LAST_REQUEST > now else now
        LAST_REQUEST = start + pace
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header into a number of seconds.

    The value is returned **unbounded**: the caller decides what to do with a
    delay longer than :data:`MAX_RETRY_AFTER` (we give up instead of clamping
    and hammering the server).  ``None`` means "no usable value": the header
    is missing, malformed or not a finite number.

    Both forms of the header are understood: delta-seconds (``"120"``) and an
    HTTP-date (``"Wed, 21 Oct 2015 07:28:00 GMT"``).
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = when.timestamp() - time.time()
    if not math.isfinite(seconds):
        return None
    if seconds < 0:
        return 0.0
    return seconds


def retry_after_allowed(retry_after: Optional[float]) -> bool:
    """False when the server asked us to wait longer than we are willing to.

    A ``Retry-After`` beyond :data:`MAX_RETRY_AFTER` is not clamped: the
    request is abandoned and the current HTTP error is returned to the caller.
    """
    return retry_after is None or retry_after <= MAX_RETRY_AFTER


def _retry_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Seconds to wait before the next attempt."""
    if retry_after is not None:
        return max(0.0, min(retry_after, MAX_RETRY_AFTER))
    backoff = min(2 ** attempt, MAX_BACKOFF)
    return backoff + random.uniform(0.3, 1.2)


def _is_safe(method: str, has_body: bool, retry_safe: Optional[bool]) -> bool:
    """Whether the operation may be replayed automatically."""
    return is_retry_safe(method, has_body) if retry_safe is None else bool(retry_safe)


def _resolve_attempts(method: str, retries: int, has_body: bool, retry_safe: Optional[bool]) -> int:
    """How many attempts a request may make (1 = single shot)."""
    if not _is_safe(method, has_body, retry_safe):
        return 1
    return max(1, int(retries))


def _send(
    method: str,
    url: str,
    data: Optional[bytes],
    headers: dict,
    attempts: int,
    safe: bool,
    timeout: int,
) -> tuple[int, Optional[bytes], str, dict]:
    """Run the request loop; return ``(status, raw body, content type, headers)``.

    HTTP errors are returned (not raised) so the caller can inspect them; the
    response body of an error is always closed before returning.
    """
    status = 0
    payload: Optional[bytes] = None
    ctype = ""
    resp_headers: dict = {}
    for attempt in range(attempts):
        _pace()
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                resp_headers = dict(resp.headers.items())
            break
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                payload = err.read()
                hdrs_err = getattr(err, "headers", None)
                ctype = hdrs_err.get("Content-Type", "") if hdrs_err else ""
                resp_headers = dict(hdrs_err.items()) if hdrs_err else {}
                retry_after = parse_retry_after(hdrs_err.get("Retry-After") if hdrs_err else None)
            finally:
                # the error carries an open body: do not leak the socket
                err.close()
            if status in RETRYABLE_STATUSES and attempt < attempts - 1:
                if not retry_after_allowed(retry_after):
                    # the server wants us to wait longer than we allow: give up
                    # and hand the current error back to the caller.
                    break
                time.sleep(_retry_delay(attempt, retry_after))
                continue
            break
        except urllib.error.URLError as err:
            reason = err.reason
            transient = is_transient_network_error(reason) if isinstance(reason, BaseException) else True
            if transient and attempt < attempts - 1:
                time.sleep(_retry_delay(attempt, None))
                continue
            raise _network_error(reason, safe)
        except (OSError, http.client.HTTPException) as err:
            # a raw socket failure (timeout, connection reset while the answer
            # was on its way): the request may already be on the server.
            # Only a *transient* failure may be replayed: a certificate
            # validation error (or any other permanent OS error) would fail
            # again the very same way.
            if safe and attempt < attempts - 1 and is_transient_network_error(err):
                time.sleep(_retry_delay(attempt, None))
                continue
            raise _network_error(err, safe)
    return status, payload, ctype, resp_headers


def http_request(
    method: str,
    url: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    retries: int = 3,
    timeout: int = 25,
    retry_safe: Optional[bool] = None,
    include_headers: bool = False,
) -> tuple[int, Any, str]:
    """Send a JSON request and return ``(status, payload, content_type)``.

    ``retry_safe`` decides whether transient failures may be replayed:
    ``None`` (the default) auto-detects ordinary reads, ``True``/``False``
    force the behaviour.  Writes are always sent once.

    When ``include_headers`` is True the return is extended to
    ``(status, payload, content_type, response_headers)`` (used by
    ``Wallapop.save_search`` which needs the ``Location`` header).
    """
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    safe = _is_safe(method, encoded is not None, retry_safe)
    attempts = _resolve_attempts(method, retries, encoded is not None, retry_safe)
    status, payload, ctype, resp_headers = _send(method, url, encoded, hdrs, attempts, safe, timeout)
    if include_headers:
        return status, decode_payload(payload, ctype), ctype, resp_headers
    return status, decode_payload(payload, ctype), ctype


def http_request_raw(
    method: str,
    url: str,
    raw_body: Optional[bytes] = None,
    headers: Optional[dict] = None,
    retries: int = 3,
    timeout: int = 60,
    retry_safe: Optional[bool] = None,
) -> tuple[int, Any]:
    """Send a request with a raw (already-encoded) body, e.g. multipart bytes.

    Returns ``(status, payload)``; the payload is the raw response body
    decoded best-effort (JSON dict when the server answers JSON, else text).
    Raises ``ApiError`` on network failure.  As in :func:`http_request`, only
    safe (body-less read) operations may be retried.
    """
    hdrs = dict(headers or {})
    if raw_body is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    safe = _is_safe(method, raw_body is not None, retry_safe)
    attempts = _resolve_attempts(method, retries, raw_body is not None, retry_safe)
    status, payload, ctype, _ = _send(method, url, raw_body, hdrs, attempts, safe, timeout)
    return status, decode_payload(payload, ctype)


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
