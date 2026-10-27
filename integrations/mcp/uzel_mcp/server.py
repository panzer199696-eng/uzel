"""Склейка MCP-сервера: инструменты УЗЛА + JSON-RPC транспорт."""

from __future__ import annotations

import logging
import sys

from uzel import i18n
from uzel.i18n import t

from . import SERVER_NAME, SERVER_VERSION
from .protocol import MethodNotFound, negotiate_protocol, serve
from .tools import UzelTools

log = logging.getLogger(__name__)


def tool_specs(lang: str | None = None) -> list[dict]:
    """Описания инструментов на языке интерфейса (каталог ``mcp.tool.*``).

    Их читает модель-агент: язык описаний — язык владельца, а схемы и имена
    инструментов от языка не зависят.
    """
    return [
        {
            "name": "search",
            "description": t("mcp.tool.search", lang=lang),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": t("mcp.tool.search.query", lang=lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": t("mcp.tool.search.limit", lang=lang),
                    },
                    "semantic": {
                        "type": "boolean",
                        "description": t("mcp.tool.search.semantic", lang=lang),
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "conversation",
            "description": t("mcp.tool.conversation", lang=lang),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": t("mcp.tool.conversation.id", lang=lang),
                    },
                    "with_utterances": {
                        "type": "boolean",
                        "description": t("mcp.tool.conversation.with_utterances", lang=lang),
                    },
                },
                "required": ["id"],
            },
        },
        {
            "name": "day",
            "description": t("mcp.tool.day", lang=lang),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": t("mcp.tool.day.date", lang=lang),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "facts",
            "description": t("mcp.tool.facts", lang=lang),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": t("mcp.tool.facts.date", lang=lang),
                    },
                    "days": {
                        "type": "integer",
                        "description": t("mcp.tool.facts.days", lang=lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": t("mcp.tool.facts.limit", lang=lang),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "tasks",
            "description": t("mcp.tool.tasks", lang=lang),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["open", "done", "all"],
                        "description": t("mcp.tool.tasks.status", lang=lang),
                    },
                    "date": {
                        "type": "string",
                        "description": t("mcp.tool.tasks.date", lang=lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": t("mcp.tool.tasks.limit", lang=lang),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "stats",
            "description": t("mcp.tool.stats", lang=lang),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "privacy",
            "description": t("mcp.tool.privacy", lang=lang),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]


# Имена инструментов от языка не зависят.
_TOOL_NAMES = {spec["name"] for spec in tool_specs("en")}


def _tool_result(text: str, *, is_error: bool) -> dict:
    """Результат tools/call по спецификации MCP.

    Ошибка инструмента — это результат с ``isError: True``, а не JSON-RPC-ошибка:
    клиент должен увидеть её как содержимое вызова.
    """
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(tools: UzelTools, params: dict) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in _TOOL_NAMES:
        return _tool_result(t("mcp.err.unknown_tool", name=repr(name)), is_error=True)
    try:
        text = getattr(tools, name)(**arguments)
    except Exception as exc:
        log.exception("инструмент %s упал", name)
        return _tool_result(
            t("mcp.err.tool_failed", name=name, error=exc), is_error=True
        )
    return _tool_result(text, is_error=False)


def build_dispatch(tools: UzelTools):
    """Обработчик JSON-RPC-методов MCP."""

    def dispatch(method: str | None, params: dict | None) -> dict:
        params = params or {}
        if method == "initialize":
            return {
                "protocolVersion": negotiate_protocol(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "tools/list":
            return {"tools": tool_specs()}
        if method == "tools/call":
            return _call_tool(tools, params)
        if method == "ping":
            return {}
        # Клиенты спрашивают их даже без объявленных возможностей — молча
        # отдаём пустое.
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        raise MethodNotFound(t("mcp.err.method", method=method))

    return dispatch


def main(argv: list[str] | None = None) -> int:
    from uzel.config import get_settings

    settings = get_settings()
    i18n.configure(settings)  # язык ответов и описаний инструментов
    # В stdout — только JSON-RPC, поэтому логи уходят в stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Иначе на Windows кириллица в stdout ломается на cp1251.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        serve(sys.stdin, sys.stdout, build_dispatch(UzelTools(settings)))
    except KeyboardInterrupt:
        pass  # EOF и Ctrl+C — штатный выход
    return 0
