"""PyInstaller entry point for the ``wallapop-mcp`` executable."""
from wallapop_mcp.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
