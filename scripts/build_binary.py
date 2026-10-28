"""Build the standalone executables with PyInstaller and smoke-test them.

Usage: ``python scripts/build_binary.py`` (needs ``pip install -e '.[build]'``).
Executables land in ``dist/bin/``; the MCP binary is checked with a real
``initialize`` + ``tools/list`` exchange over stdio.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "bin"
EXE = ".exe" if platform.system() == "Windows" else ""


def build() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyinstaller"),
         str(ROOT / "packaging" / "wallapop.spec")],
        check=True,
    )


def smoke_mcp(binary: Path) -> int:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(
        [str(binary), "--session", str(ROOT / "build" / "empty-session")],
        input=stdin, capture_output=True, text=True, timeout=120,
    )
    for line in proc.stdout.splitlines():
        msg = json.loads(line)
        if msg.get("id") == 2:
            return len(msg["result"]["tools"])
    raise SystemExit(f"no tools/list answer from {binary.name}:\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    build()
    (ROOT / "build" / "empty-session").mkdir(parents=True, exist_ok=True)
    mcp_bin, cli_bin = DIST / f"wallapop-mcp{EXE}", DIST / f"wallapop{EXE}"
    version = subprocess.run([str(mcp_bin), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"{version}: {smoke_mcp(mcp_bin)} tools")
    subprocess.run([str(cli_bin), "--help"], capture_output=True, check=True)
    print(f"{cli_bin.name}: --help OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
