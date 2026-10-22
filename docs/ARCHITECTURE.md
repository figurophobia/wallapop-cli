# Architecture

The repository contains two clients with separate runtimes:

```text
MCP client → stdio → internal/mcpserver → internal/wallapop → internal/transport
Python CLI → wallapoplib/cli → wallapoplib/client.py → wallapoplib/http.py
```

## Go server

`cmd/wallapop-mcp` owns configuration, process signals and stdio. `internal/mcpserver` uses the official MCP SDK for the protocol and validates tool inputs before dispatch. Tool results are structured JSON with a compatible text representation. No shell subprocesses or browsers are used.

`internal/wallapop` maps the fixed operation set to the endpoints researched in the original project. It normalizes response shapes, maintains authentication, preserves decimal amounts, handles partial writes and checks the write capability independently of the MCP layer.

`internal/transport` owns connection reuse, request pacing, retry policy, cancellation, coalescing and a bounded LRU cache. It rejects redirects and limits upstream response bodies to 4 MiB. Diagnostics exclude headers, tokens, response bodies and request URLs.

## Scheduling and retries

Each tool has a 45-second default budget, including waiting for one of four concurrent slots. Upstream requests start at least one second apart by default. Cached reads consume no upstream pacing slot. No fixed post-operation sleeps are used by the Go client.

Only requests explicitly marked read-only can retry. The retry set is transient network failures and HTTP 429, 502, 503 and 504, up to three attempts. HTTP 403 is returned immediately. Retry-After is never shortened: a longer delay exhausts the caller's budget instead of retrying early.

Writes are sent once per transport invocation. A definitive 401 can trigger one replay after successful token renewal. An interrupted write is marked uncertain. Write requests use fresh HTTP/1 connections to prevent Go's transport from silently replaying a GET used by PubNub to publish a message.

## Cache and session boundaries

Read caches live in memory, are scoped to the client, and include authorization headers in their hashed key. The cache holds at most 128 entries and 32 MiB of response data. Entries expire; `fresh` bypasses cache lookup. Invalidation generations prevent an older in-flight read from repopulating a cache after a write.

Public reads use short TTLs, account reads shorter TTLs, and the category tree a longer TTL. A cache is an optimization, not proof that an offer or listing is still actionable. Mutation prerequisites must read fresh state.

Concurrent readers can share one upstream request. Canceling a waiter does not abort the request while another waiter remains. Canceling the last waiter stops the shared work.

Only an explicitly supplied session path is read. Tokens supplied through the environment are supported. Token values are never included in status output. Refresh updates are atomic and private; the process remains a local, single-account server.

## Writes and partial results

The default tool list contains only reads. Enabling writes exposes account mutations and enables a second guard in the API client.

Publishing first creates the listing with its first photo, then uploads additional photos. Later failures retain the created item ID and completed count. Unknown acknowledgements must be reconciled with current state before another write. No retry policy can provide exactly-once delivery when the upstream service does not expose an idempotency guarantee.

Listing text and messages are untrusted content. Tool descriptions instruct agents to treat them as data.

## Compatibility

The endpoint catalogue in [ENDPOINTS.md](ENDPOINTS.md) preserves upstream research. [MCP.md](MCP.md) lists the subset exposed by the new server. The Go implementation is covered by offline contract tests; live anti-bot behavior and undocumented API changes remain external dependencies.

The Python library is retained separately. Shared session formats and offline fixtures support compatibility, but its defaults and features are not identical to the MCP server.
