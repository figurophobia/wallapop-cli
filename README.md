# wallapop-cli

A Python CLI, library and MCP server for Wallapop: search and manage listings,
negotiate offers and message buyers and sellers — no browser required for the
core workflows.

One codebase. The `wallapop` CLI, the `wallapoplib` library and the MCP server
all share the same API client, so a fix lands in one place.

## Install

Python 3.10+.

```sh
pip install -e .            # CLI + library
pip install -e '.[mcp]'     # + MCP server (needs the mcp SDK)
```

## CLI

```sh
wallapop search "nintendo switch" --city galicia --max-price 200
wallapop show <item-url-or-id>
wallapop login you@mail.com          # or: --wizard / --browser firefox / --cookies-file
wallapop offer <item> --price 30     # negotiate; accept/decline/counter with `offer`
wallapop publish <photos_dir> --summary "..." --price 42 --condition as_good_as_new
```

Public reads (search, item details, sellers, categories) need no account. Inbox,
offers, purchases/sales and writes need a session (stored in
`~/.config/wallapop_cli/`). See [docs/USAGE.md](docs/USAGE.md).

## MCP server

A stdio MCP server exposing the same operations as tools, over `wallapoplib`.

```sh
pip install -e '.[mcp]'
wallapop-mcp-py --session ~/.config/wallapop_cli --allow-writes
# or: python -m wallapoplib.mcp_server
```

Connect an MCP client (e.g. Claude Code / Desktop):

```json
{
  "mcpServers": {
    "wallapop": {
      "command": "wallapop-mcp-py",
      "args": ["--session", "/absolute/path/to/.config/wallapop_cli", "--allow-writes"]
    }
  }
}
```

22 tools (12 read, 10 write). **Writes are hidden unless `--allow-writes`.** Auth
via `--session <dir|file>` or `WALLAPOP_ACCESS_TOKEN`. See
[AGENTS.md](AGENTS.md) for the full agent-facing guide (flows, login, tool
reference) and [docs/API.md](docs/API.md) for the library.

## Develop

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test,mcp]'
make test        # or: .venv/bin/python -m pytest
```

Tests use local fixtures and HTTP test servers; they need no account.

## Status

Experimental, unofficial integration. The endpoints are derived from the web
application and can change or reject non-browser clients. Offline tests cover
protocol behaviour and known response shapes; they do not establish current live
compatibility.

MIT license. Wallapop and its trademarks belong to their respective owners; this
project is not affiliated with Wallapop. See [CREDITS.md](CREDITS.md).
