"""MCP на английском интерфейсе (27.10.2026): описания инструментов и ответы."""

from __future__ import annotations

from test_mcp_server import empty_settings  # noqa: F401 — фикстура
from uzel import i18n
from uzel_mcp.protocol import handle_message
from uzel_mcp.server import build_dispatch, tool_specs
from uzel_mcp.tools import UzelTools


def test_tool_descriptions_follow_language_and_names_do_not():
    i18n.set_language("en")
    response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, build_dispatch(None))
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert tools["search"]["description"].startswith("Find an utterance in memory")
    assert tools["search"]["inputSchema"]["properties"]["query"]["description"] == "search query"
    assert [spec["name"] for spec in tool_specs("ru")] == [spec["name"] for spec in tool_specs("en")]
    assert tool_specs("ru")[0]["description"].startswith("Найти реплику")


def test_tool_answers_in_english(empty_settings):  # noqa: F811
    i18n.set_language("en")
    tools = UzelTools(empty_settings)
    assert tools.day("2026-10-27") == "No conversations on 2026-10-27."
    assert tools.tasks() == "No tasks."
    assert tools.conversation(42) == "Conversation 42 not found."
    privacy = tools.privacy()
    assert "Where the data goes:" in privacy
    assert "cloud on the default path: forbidden" in privacy


def test_unknown_tool_error_in_english():
    i18n.set_language("en")
    response = handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}},
        build_dispatch(None),
    )
    assert response["result"]["isError"] is True
    assert "unknown tool: 'nope'" in response["result"]["content"][0]["text"]
