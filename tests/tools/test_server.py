"""Tests for the MCP server bootstrap (Phase 3): tools are registered."""

from __future__ import annotations

import asyncio

from harmonizer_tools.server import register_tools


def test_all_tools_registered():
    mcp = register_tools()
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {
        "list_interfaces",
        "get_slots",
        "validate_value",
        "record_mapping",
        "leave_placeholder",
        "execute_code",
    } <= names
