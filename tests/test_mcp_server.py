"""Tests for the Python MCP server (wallapoplib.mcp_server).

Skipped when the optional ``mcp`` SDK is not installed. No network: these only
check tool registration, read/write gating, and session enforcement.
"""
import asyncio

import pytest

pytest.importorskip("mcp.server.mcpserver")

import wallapoplib.mcp_server as mcp_server  # noqa: E402

READ_TOOLS = {
    "session_status", "search", "get_item", "get_seller", "list_categories",
    "list_inbox", "get_conversation", "unread_count", "list_purchases",
    "list_sales", "get_offer_constraints", "get_offer_status",
}
WRITE_TOOLS = {
    "set_favorite", "send_message", "send_offer", "respond_offer",
    "counter_offer", "publish_item", "edit_item", "reserve_item",
    "mark_sold", "delete_item",
}


def _tool_names(allow_writes):
    srv = mcp_server.build_server(allow_writes)
    return {t.name for t in asyncio.run(srv.list_tools())}


def test_writes_hidden_by_default(tmp_path):
    mcp_server._init(str(tmp_path), allow_writes=False)
    reads = _tool_names(False)
    assert reads == READ_TOOLS
    assert not (reads & WRITE_TOOLS)


def test_allow_writes_exposes_mutations(tmp_path):
    mcp_server._init(str(tmp_path), allow_writes=True)
    both = _tool_names(True)
    assert both == READ_TOOLS | WRITE_TOOLS
    assert len(both) == 22


def test_account_tool_requires_a_session(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLAPOP_ACCESS_TOKEN", raising=False)
    mcp_server._init(str(tmp_path), allow_writes=True)  # empty dir -> no token
    with pytest.raises(RuntimeError, match="session"):
        mcp_server.unread_count()


def test_publish_rejects_non_shipping(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLAPOP_ACCESS_TOKEN", "tok")
    mcp_server._init(str(tmp_path), allow_writes=True)
    with pytest.raises(ValueError, match="shipping"):
        mcp_server.publish_item(
            title="t", description="d", price=10.0, category_leaf_id="10311",
            condition="good", photo_paths=["x.jpg"], shipping_allowed=False,
        )
