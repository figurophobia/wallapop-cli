"""PyInstaller entry point for the ``wallapop`` CLI executable."""
from wallapop_mcp.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
