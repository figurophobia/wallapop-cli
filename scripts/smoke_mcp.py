"""Offline end-to-end MCP stdio check against a compiled server."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def check(binary: str, writes: bool = False) -> float:
    # Never consume a developer's authentication configuration in a smoke test.
    env = {k: v for k, v in os.environ.items() if not k.startswith("WALLAPOP_")}
    args = [str(Path(binary).resolve())]
    if writes:
        args.append("--allow-writes")
    started = time.perf_counter()
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    replies: queue.Queue = queue.Queue()

    def read():
        for line in process.stdout:
            try:
                replies.put(json.loads(line))
            except ValueError:
                replies.put({"invalid_stdout": line})
        replies.put({"eof": True})

    thread = threading.Thread(target=read, daemon=True)
    thread.start()

    def send(message):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()

    def receive(identifier):
        deadline = time.monotonic() + 10
        while True:
            response = replies.get(timeout=max(0.01, deadline - time.monotonic()))
            if "invalid_stdout" in response or "eof" in response:
                raise AssertionError(response)
            if response.get("id") == identifier:
                return response
            if time.monotonic() >= deadline:
                raise TimeoutError("MCP response deadline")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "offline-smoke", "version": "1"}}})
        initialized = receive(1)
        assert "result" in initialized, initialized
        ready_ms = (time.perf_counter() - started) * 1000
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/list", "params": {}})
        tools = receive(2)["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert {"search", "session_status", "get_item"}.issubset(names), names
        assert ("delete_item" in names) == writes, names

        send({"id": 3, "method": "tools/call", "params": {
            "name": "session_status", "arguments": {}}})
        status = receive(3)["result"]
        assert not status.get("isError"), status
        structured = status["structuredContent"]
        assert not structured["logged_in"], structured
        assert structured["writes_enabled"] == writes, structured
        assert "token" not in structured, structured

        # Invalid arguments must fail before any upstream request.
        send({"id": 4, "method": "tools/call", "params": {
            "name": "search", "arguments": {"keywords": "test", "pages": 100}}})
        invalid = receive(4)
        assert "error" in invalid or invalid.get("result", {}).get("isError"), invalid
        process.stdin.close()
        assert process.wait(timeout=5) == 0, process.stderr.read()
        return ready_ms
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/smoke_mcp.py /path/to/wallapop-mcp")
    for writes in (False, True):
        elapsed = check(sys.argv[1], writes)
        print(f"stdio OK (writes={writes}, initialize={elapsed:.1f} ms; no network)")
