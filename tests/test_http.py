"""Offline tests for the HTTP transport (wallapop_mcp.http).

No network: ``urllib.request.urlopen`` is replaced by a scripted fake and the
``time`` module by a deterministic clock, so retries/backoff/pacing can be
asserted without waiting.
"""

import datetime
import email.message
import io
import socket
import ssl
import urllib.error

import pytest

import wallapop_mcp.http as http_mod
from wallapop_mcp.http import (
    ApiError,
    http_request,
    http_request_raw,
    is_retry_safe,
    parse_retry_after,
)


class FakeResponse:
    def __init__(self, status: int, body: bytes, ctype: str) -> None:
        self.status = status
        self.body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = ctype

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, headers: dict | None = None, body: bytes = b"") -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return urllib.error.HTTPError("http://wallapop.test/", code, "err", hdrs, io.BytesIO(body))


class FakeClock:
    """Deterministic stand-in for the ``time`` module used by the transport."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


class Opener:
    """Scripted ``urlopen`` replacement."""

    def __init__(self, script: list, clock: FakeClock) -> None:
        self.script = list(script)
        self.clock = clock
        self.requests: list = []
        self.started_at: list[float] = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.started_at.append(self.clock.now)
        item = self.script.pop(0) if self.script else (200, b"{}", "application/json")
        if isinstance(item, Exception):
            raise item
        status, body, ctype = item
        return FakeResponse(status, body, ctype)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(http_mod, "time", fake)
    monkeypatch.setattr(http_mod, "RATE_PACE", 0.0)
    monkeypatch.setattr(http_mod, "LAST_REQUEST", 0.0)
    return fake


def install(monkeypatch, script, clock) -> Opener:
    opener = Opener(script, clock)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    return opener


# --- retry policy -----------------------------------------------------------

def test_get_retries_transient_status(clock, monkeypatch):
    opener = install(
        monkeypatch,
        [http_error(503), http_error(502), (200, b'{"ok": true}', "application/json")],
        clock,
    )
    status, data, ctype = http_request("GET", "http://wallapop.test/x")
    assert status == 200
    assert data == {"ok": True}
    assert ctype == "application/json"
    assert len(opener.requests) == 3


def test_get_retries_rate_limit(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(429), (200, b"{}", "application/json")], clock)
    status, _, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 200
    assert len(opener.requests) == 2


def test_write_is_never_retried(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(503), (200, b"{}", "application/json")], clock)
    status, _, _ = http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert status == 503
    assert len(opener.requests) == 1


def test_raw_write_is_never_retried(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(503), (200, b"{}", "application/json")], clock)
    status, _ = http_request_raw("POST", "http://wallapop.test/x", raw_body=b"{}")
    assert status == 503
    assert len(opener.requests) == 1


def test_retry_safe_false_disables_retries_on_get(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(503), (200, b"{}", "application/json")], clock)
    status, _, _ = http_request("GET", "http://wallapop.test/x", retry_safe=False)
    assert status == 503
    assert len(opener.requests) == 1


def test_403_is_not_retried(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(403), (200, b"{}", "application/json")], clock)
    status, _, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 403
    assert len(opener.requests) == 1


def test_404_is_not_retried(clock, monkeypatch):
    opener = install(monkeypatch, [http_error(404), (200, b"{}", "application/json")], clock)
    status, _, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 404
    assert len(opener.requests) == 1


def test_network_error_is_retried_for_reads(clock, monkeypatch):
    opener = install(monkeypatch, [urllib.error.URLError("boom") for _ in range(3)], clock)
    with pytest.raises(ApiError):
        http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 3


def test_network_error_is_not_retried_for_writes(clock, monkeypatch):
    opener = install(monkeypatch, [urllib.error.URLError("boom")], clock)
    with pytest.raises(ApiError):
        http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert len(opener.requests) == 1


# --- Retry-After ------------------------------------------------------------

def test_retry_after_header_is_honoured(clock, monkeypatch):
    install(monkeypatch, [http_error(429, {"Retry-After": "2"}), (200, b"{}", "application/json")], clock)
    http_request("GET", "http://wallapop.test/x")
    assert clock.sleeps == [2.0]


def test_retry_after_longer_than_the_limit_gives_up(clock, monkeypatch):
    """A server that asks for more than MAX_RETRY_AFTER is not hammered."""
    opener = install(
        monkeypatch,
        [http_error(429, {"Retry-After": "120"}), (200, b"{}", "application/json")],
        clock,
    )
    status, data, _ = http_request("GET", "http://wallapop.test/x")
    # the current HTTP error is returned, no sleep, no second attempt
    assert status == 429
    assert len(opener.requests) == 1
    assert clock.sleeps == []


def test_retry_after_http_date_longer_than_the_limit_gives_up(clock, monkeypatch):
    when = datetime.datetime.fromtimestamp(
        clock.time() + 120, tz=datetime.timezone.utc
    ).strftime("%a, %d %b %Y %H:%M:%S GMT")
    opener = install(
        monkeypatch,
        [http_error(503, {"Retry-After": when}), (200, b"{}", "application/json")],
        clock,
    )
    status, _, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 503
    assert len(opener.requests) == 1
    assert clock.sleeps == []


def test_retry_after_http_date_within_the_limit_is_honoured(clock, monkeypatch):
    when = datetime.datetime.fromtimestamp(
        clock.time() + 5, tz=datetime.timezone.utc
    ).strftime("%a, %d %b %Y %H:%M:%S GMT")
    opener = install(
        monkeypatch,
        [http_error(503, {"Retry-After": when}), (200, b"{}", "application/json")],
        clock,
    )
    status, _, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 200
    assert len(opener.requests) == 2
    assert clock.sleeps == [5.0]


def test_parse_retry_after_variants():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("nonsense") is None
    assert parse_retry_after("5") == 5.0
    # the value is *not* clamped: the caller decides what to do with a long wait
    assert parse_retry_after("99999") == 99999.0
    assert parse_retry_after("120") == 120.0
    # a date in the past means "retry now"
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def test_parse_retry_after_rejects_malformed_numbers():
    assert parse_retry_after("nan") is None
    assert parse_retry_after("NaN") is None
    assert parse_retry_after("-nan") is None
    assert parse_retry_after("inf") is None
    assert parse_retry_after("-inf") is None
    assert parse_retry_after("infinity") is None


def test_parse_retry_after_http_date():
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=120)
    parsed = parse_retry_after(when.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert parsed is not None
    assert 118 <= parsed <= 121


# --- network failures --------------------------------------------------------

def test_transient_network_error_is_retried(clock, monkeypatch):
    opener = install(
        monkeypatch,
        [urllib.error.URLError(ConnectionResetError("reset by peer")) for _ in range(3)],
        clock,
    )
    with pytest.raises(ApiError):
        http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 3


def test_certificate_error_is_not_retried(clock, monkeypatch):
    """A certificate validation error is permanent: never replay it."""
    err = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    opener = install(monkeypatch, [err], clock)
    with pytest.raises(ApiError):
        http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 1


def test_raw_certificate_error_is_not_retried(clock, monkeypatch):
    opener = install(monkeypatch, [ssl.SSLCertVerificationError("certificate verify failed")], clock)
    with pytest.raises(ApiError):
        http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 1


def test_ssl_eof_on_write_has_unknown_outcome(clock, monkeypatch):
    opener = install(monkeypatch, [ssl.SSLEOFError("EOF after request")], clock)
    with pytest.raises(http_mod.UnknownOutcomeError):
        http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert len(opener.requests) == 1


def test_raw_socket_timeout_on_a_read_is_retried(clock, monkeypatch):
    opener = install(monkeypatch, [socket.timeout("timed out") for _ in range(3)], clock)
    with pytest.raises(ApiError):
        http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 3


def test_raw_socket_timeout_on_a_write_is_not_retried_and_labelled_unknown(clock, monkeypatch):
    opener = install(monkeypatch, [socket.timeout("timed out")], clock)
    with pytest.raises(ApiError) as excinfo:
        http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert len(opener.requests) == 1
    err = excinfo.value
    assert getattr(err, "outcome", None) == "unknown"
    assert "outcome unknown" in str(err)


def test_raw_connection_error_on_a_write_is_labelled_unknown(clock, monkeypatch):
    opener = install(monkeypatch, [ConnectionResetError("connection reset by peer")], clock)
    with pytest.raises(ApiError) as excinfo:
        http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert len(opener.requests) == 1
    assert getattr(excinfo.value, "outcome", None) == "unknown"


def test_refused_connection_on_a_write_is_labelled_not_sent(clock, monkeypatch):
    opener = install(monkeypatch, [ConnectionRefusedError("connection refused")], clock)
    with pytest.raises(ApiError) as excinfo:
        http_request("POST", "http://wallapop.test/x", body={"a": 1})
    assert len(opener.requests) == 1
    assert getattr(excinfo.value, "outcome", None) == "not_sent"


def test_http_error_body_is_closed(clock, monkeypatch):
    err = http_error(503, body=b"nope")
    install(monkeypatch, [err, (200, b"{}", "application/json")], clock)
    status, data, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 200
    assert err.fp is not None and err.fp.closed


def test_http_error_payload_is_still_decoded(clock, monkeypatch):
    err = http_error(400, headers={"Content-Type": "application/json"}, body=b'{"error_message": "boom"}')
    install(monkeypatch, [err], clock)
    status, data, _ = http_request("GET", "http://wallapop.test/x")
    assert status == 400
    assert data == {"error_message": "boom"}


# --- pacing -----------------------------------------------------------------

def test_pacing_is_applied_to_every_attempt(clock, monkeypatch):
    monkeypatch.setattr(http_mod, "RATE_PACE", 5.0)
    opener = install(
        monkeypatch,
        [http_error(503), http_error(503), (200, b"{}", "application/json")],
        clock,
    )
    http_request("GET", "http://wallapop.test/x")
    assert len(opener.requests) == 3
    gaps = [b - a for a, b in zip(opener.started_at, opener.started_at[1:])]
    assert all(gap >= 5.0 for gap in gaps), gaps
    # two retries -> two backoff waits + two pacing waits
    assert len(clock.sleeps) == 4


def test_pacing_lock_is_not_held_during_the_request(clock, monkeypatch):
    seen: list[bool] = []

    class CheckingOpener(Opener):
        def __call__(self, req, timeout=None):
            seen.append(http_mod._PACE_LOCK.locked())
            return super().__call__(req, timeout=timeout)

    opener = CheckingOpener([(200, b"{}", "application/json")], clock)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    http_request("GET", "http://wallapop.test/x")
    assert seen == [False]


# --- payload / api shape ----------------------------------------------------

def test_response_shape_is_preserved(clock, monkeypatch):
    install(monkeypatch, [(200, b'{"a": 1}', "application/json")], clock)
    result = http_request("GET", "http://wallapop.test/x")
    assert isinstance(result, tuple) and len(result) == 3
    status, data, ctype = result
    assert (status, data, ctype) == (200, {"a": 1}, "application/json")


def test_raw_response_shape_is_preserved(clock, monkeypatch):
    install(monkeypatch, [(200, b'{"a": 1}', "application/json")], clock)
    result = http_request_raw("GET", "http://wallapop.test/x")
    assert isinstance(result, tuple) and len(result) == 2
    assert result == (200, {"a": 1})


def test_is_retry_safe_helper():
    assert is_retry_safe("GET") is True
    assert is_retry_safe("head") is True
    assert is_retry_safe("POST") is False
    assert is_retry_safe("PUT") is False
    assert is_retry_safe("DELETE") is False
    assert is_retry_safe("GET", has_body=True) is False
