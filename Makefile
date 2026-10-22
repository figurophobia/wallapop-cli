GO ?= go
PYTHON ?= python3
VERSION ?= dev

.PHONY: build test-go test-python benchmark
build:
	$(GO) build -trimpath -ldflags="-s -w -X main.version=$(VERSION)" -o bin/wallapop-mcp ./cmd/wallapop-mcp

test-go:
	$(GO) vet ./...
	$(GO) test -race ./...

test-python:
	$(PYTHON) -m pytest

benchmark:
	$(GO) test ./internal/mcpserver ./internal/transport -run '^$$' -bench . -benchmem
