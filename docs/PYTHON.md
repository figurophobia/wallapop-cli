# Python library and CLI

The original CLI by [figurophobia](https://github.com/figurophobia) remains in this fork. Python 3.10+ is required; the core library uses the standard library.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/wallapop --help
.venv/bin/wallapop search 'nintendo switch' --city madrid
```

Without installation, run `python3 wallapop.py <command>` from the repository.

For the optional login wizard and browser publishing:

```sh
.venv/bin/pip install -e '.[browser]'
.venv/bin/python -m playwright install chromium
.venv/bin/wallapop login --wizard
```

Tokens and cookies are stored outside the repository under `~/.config/wallapop_cli/`. The Go MCP server can reuse this directory with `--session`. Keep credentials out of issues, prompts and committed configuration.

## Library

```python
from wallapoplib import Wallapop, build_client

public = Wallapop()
for item in public.search("nintendo switch", pages=1):
    print(item.price, item.title, item.url)

client, _ = build_client()  # raises SessionError rather than exiting
```

See the [command reference](USAGE.md) and [library reference](API.md).

## Changes in this fork

Prices retain cents. Writes do not use transport retries, including PubNub's write-shaped GET. HTTP errors are surfaced instead of becoming empty seller data. Refresh attempts only replay a rejected request after the token changes. Session files are replaced atomically.

A failed additional photo retains the created listing ID in a partial result. An unacknowledged chat send has unknown delivery; inspect the conversation before retrying. Browser fallback requires explicit opt-in and is not a delivery guarantee.

The Go server is a separate implementation. The Python CLI is maintained for compatibility and login workflows; it does not inherit the Go server's cache, concurrency limits or MCP tool schemas.
