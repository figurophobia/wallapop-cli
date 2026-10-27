PYTHON ?= python3

.PHONY: test lint install
install:
	$(PYTHON) -m pip install -e '.[test,mcp]'

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m pip install -q ruff && ruff check wallapoplib tests
