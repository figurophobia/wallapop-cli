#!/usr/bin/env python3
"""Wallapop automation CLI (entrypoint). All logic lives in the wallapop_mcp package."""
import sys

from wallapop_mcp.cli.main import main

if __name__ == "__main__":
    sys.exit(main())