#!/usr/bin/env python3
"""Wallapop automation CLI (entrypoint). All logic lives in the wallapoplib package."""
import sys

from wallapoplib.cli.main import main

if __name__ == "__main__":
    sys.exit(main())