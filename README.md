# wallapop-mcp

An [MCP](https://modelcontextprotocol.io) server for Wallapop, written in pure
Python: let an AI agent search listings, spot deals, negotiate offers, chat with
buyers and sellers, keep saved-search alerts and manage your own listings — no
browser required for the core workflows.

The same package ships the `wallapop` CLI and the typed `wallapop_mcp` Python client.
All three share one API client, so a fix lands in one place.

## Install

Python 3.10+.

```sh
pip install wallapop-mcp          # or: uvx wallapop-mcp / pipx install wallapop-mcp
```

Prefer no Python at all? Download the standalone `wallapop-mcp` executable
(Linux, macOS, Windows; built with PyInstaller) from the
[releases page](https://github.com/figurophobia/wallapop-mcp/releases).

## Use it from an MCP client

```json
{
  "mcpServers": {
    "wallapop": {
      "command": "wallapop-mcp",
      "args": ["--allow-writes"]
    }
  }
}
```

Claude Code: `claude mcp add wallapop -- wallapop-mcp --allow-writes`.

- **Reads work without an account** (search, suggestions, item details,
  sellers, categories). Inbox, offers, favorites, purchases/sales, saved
  searches and every write need a session.
- **Writes are hidden unless `--allow-writes`** is passed.
- Session: `--session <dir|file>` (default `~/.config/wallapop-mcp`, override
  with `WALLAPOP_CONFIG_DIR`) or `WALLAPOP_ACCESS_TOKEN` (+ optional
  `WALLAPOP_REFRESH_TOKEN`).

30 tools (17 read, 13 write): search & deals, item/seller lookup, inbox and
conversations, offers (send, accept, decline, counter), favorites, saved-search
alerts, publish/edit/reserve/sold/pause/delete listings. Full reference and
agent flows in [AGENTS.md](https://github.com/figurophobia/wallapop-mcp/blob/main/AGENTS.md).

## Log in

The server never logs in by itself; create a session once with the CLI:

```sh
wallapop login you@mail.com                  # email + password (prompts)
wallapop login --browser firefox             # import an existing Firefox session
wallapop login --cookies-file cookies.txt    # paste the Cookie: header from DevTools
wallapop session                             # check status, offline
```

For the Google/browser wizard: `pip install 'wallapop-mcp[browser]' && playwright install chromium`,
then `wallapop login --wizard`. Credentials live in `~/.config/wallapop-mcp/`,
outside any repository; never paste them into prompts or issues.

## CLI

```sh
wallapop search "nintendo switch" --city galicia --max-price 200
wallapop deals "nintendo switch"                  # bargains vs. the median price
wallapop show <item-url-or-id>
wallapop offer <item> --price 30
wallapop alert save "switch 2" --max-price 300    # saved search (alert)
wallapop publish <photos_dir> --summary "..." --price 42 --condition as_good_as_new
```

See [docs/USAGE.md](https://github.com/figurophobia/wallapop-mcp/blob/main/docs/USAGE.md).

## Library

```python
from wallapop_mcp import Wallapop, build_client

public = Wallapop()
for item in public.search("nintendo switch", pages=1):
    print(item.price, item.title, item.url)

client, _ = build_client()  # signed-in client; raises SessionError if not logged in
```

See [docs/API.md](https://github.com/figurophobia/wallapop-mcp/blob/main/docs/API.md).

## Develop

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test,build]'
.venv/bin/python -m pytest                 # offline: local fixtures, no account
.venv/bin/python -m build                  # sdist + wheel in dist/
.venv/bin/python scripts/build_binary.py   # PyInstaller executables in dist/bin/
```

Releasing is described in [CONTRIBUTING.md](https://github.com/figurophobia/wallapop-mcp/blob/main/CONTRIBUTING.md).

## Status

Experimental, unofficial integration. The endpoints are derived from the web
application and can change or reject non-browser clients. Offline tests cover
protocol behaviour and known response shapes; they do not establish current live
compatibility. Writes may have partial or unknown outcomes; there is no checkout
or payment.

MIT license. Wallapop and its trademarks belong to their respective owners; this
project is not affiliated with Wallapop. See [CREDITS.md](https://github.com/figurophobia/wallapop-mcp/blob/main/CREDITS.md).
