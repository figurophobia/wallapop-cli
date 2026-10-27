# Wallapop MCP

A local MCP server for agents that search and manage Wallapop listings.

Written in Go, using the official [MCP Go SDK](https://github.com/modelcontextprotocol/go-sdk). One executable, stdio transport, no browser or Python runtime required by the server. The original Python library and CLI remain available.

This project is a fork of [figurophobia/wallapop-cli](https://github.com/figurophobia/wallapop-cli). Its API research, Python implementation and Git history are preserved. See [credits](CREDITS.md).

## Build

Requires Go 1.25 or later.

```sh
git clone https://github.com/jrrome/wallapop-mcp.git
cd wallapop-mcp
go build -trimpath -ldflags="-s -w" -o bin/wallapop-mcp ./cmd/wallapop-mcp
./bin/wallapop-mcp --help
```

No release binaries or package registry publication are provided yet.

## Connect an agent

For MCP clients that use a `mcpServers` configuration:

```json
{
  "mcpServers": {
    "wallapop": {
      "command": "/absolute/path/to/wallapop-mcp/bin/wallapop-mcp",
      "args": []
    }
  }
}
```

The process communicates over stdin/stdout. Diagnostics go to stderr. Starting the server or calling `session_status` does not contact Wallapop.

Public search, listings, sellers and categories work without configuring an account, subject to Wallapop's access controls. Account operations require a session. Reuse the Python CLI configuration with:

```json
{
  "mcpServers": {
    "wallapop": {
      "command": "/absolute/path/to/wallapop-mcp",
      "args": ["--session", "/absolute/path/to/.config/wallapop_cli"]
    }
  }
}
```

Alternatively set `WALLAPOP_ACCESS_TOKEN` and optionally `WALLAPOP_REFRESH_TOKEN` in the process environment. `WALLAPOP_SESSION_PATH` is equivalent to `--session`. Session files are loaded only from an explicitly configured path. Refreshed sessions are saved atomically with owner-only permissions.

## Tools

| Group | Operations |
| --- | --- |
| Public reads | Search, item details, seller profile and reviews, category tree |
| Account reads | Session status, inbox, conversation, unread count, purchases, sales, offer limits and history |
| Optional writes | Favorites, messages, offers, publish, edit, reserve, mark sold, delete |

Writes are hidden and disabled by default. Add `--allow-writes` only when the agent should be able to change the account. The flag grants capability; the MCP client should still obtain the user's intent before consequential actions.

[Tool and endpoint reference](docs/MCP.md) · [Architecture](docs/ARCHITECTURE.md) · [Python CLI](docs/PYTHON.md)

## Reliability and resource limits

- Reuses HTTP connections for reads. Read-only requests can retry transient failures; writes are never automatically retried after an uncertain outcome.
- Short, bounded, in-memory caches; concurrent identical reads share a request. `fresh: true` bypasses cached values. Writes invalidate the client's cache.
- Context cancellation and a 45-second tool budget. Default concurrency: 4 calls. Default upstream pacing: one request start per second.
- Bounded search pages, response bodies, cache storage, uploads and MCP output.
- Explicit partial publication and unknown delivery results. Check the existing listing or conversation before retrying.
- Decimal price inputs preserve cents. No automatic browser fallback in the Go server.

Run `make benchmark` to measure local overhead. See [local measurements and resource bounds](docs/PERFORMANCE.md). Network latency and Wallapop's rate limits dominate real requests; these local measurements are not live API throughput claims.

## Development

```sh
make test-go
make build
python3 scripts/smoke_mcp.py ./bin/wallapop-mcp
python3 -m venv .venv
.venv/bin/pip install -e '.[test]' build
make test-python PYTHON=.venv/bin/python
```

Tests use local fixtures and HTTP test servers. They do not need a Wallapop account. See [contributing](CONTRIBUTING.md).

## Status and scope

Experimental, unofficial integration. The endpoints are derived from the web application and can change or reject non-browser clients. Offline tests cover protocol behavior and known response shapes; they do not establish current live compatibility.

The Go server does not implement login UI, checkout, payment, shipping-label purchase, background watchers, or a remotely hosted HTTP transport. The Python CLI retains its existing login and browser workflows.

MIT license. Wallapop and its trademarks belong to their respective owners; this project is not affiliated with Wallapop.
