# Contributing

Keep changes focused and describe the observable behavior they fix. Bug reports should include the command or MCP tool, version, sanitized input, expected result and actual result. Never include session files, tokens, cookies or private messages.

## Checks

Go 1.25+ and Python 3.10+ are supported.

```sh
gofmt -w cmd internal
go vet ./...
go test -race ./...
go build -trimpath -o bin/wallapop-mcp ./cmd/wallapop-mcp
python3 scripts/smoke_mcp.py ./bin/wallapop-mcp
python3 -m venv .venv
.venv/bin/pip install -e '.[test]' build
.venv/bin/python -m pytest
.venv/bin/python -m build
```

Add regression tests for changed API behavior using local HTTP servers or synthetic fixtures. The regular test suite must not contact Wallapop or read a developer's session. Label any manually verified live behavior separately.

Keep stdout reserved for MCP. Bound new network responses and outputs. Treat mutations as non-retryable unless the upstream service provides a verified idempotency mechanism. Preserve created resource IDs after partial failures.

Use the upstream Python client and endpoint research as evidence, not as proof that an endpoint still works. Document changes in [MCP.md](docs/MCP.md) and keep credits intact. General Python fixes can be proposed separately to the original project.

AI-assisted changes are welcome when reviewed and tested by the contributor. Do not commit agent transcripts, private prompts, worktree paths or generated planning logs.
