"""Склейка MCP-сервера: инструменты УЗЛА + JSON-RPC транспорт."""

from __future__ import annotations

import logging
import sys

from . import SERVER_NAME, SERVER_VERSION
from .protocol import MethodNotFound, negotiate_protocol, serve
from .tools import UzelTools

log = logging.getLogger(__name__)

TOOL_SPECS = [
    {
        "name": "search",
        "description": "Найти реплику в памяти по слову или смыслу — когда нужно вспомнить, что и когда говорилось.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "поисковый запрос"},
                "limit": {
                    "type": "integer",
                    "description": "сколько результатов отдать (по умолчанию 10)",
                },
                "semantic": {
                    "type": "boolean",
                    "description": "сначала смысловой поиск, при неудаче подстрочный (по умолчанию)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "conversation",
        "description": "Показать разговор по id: саммари, факты, задачи, темы, участников — детали разбора дня.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "id разговора"},
                "with_utterances": {
                    "type": "boolean",
                    "description": "добавить дословные реплики (по умолчанию нет)",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "day",
        "description": "Обзор суток: разговоры с интервалами и счётчиками — когда нужен отчёт «что было в этот день».",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "сутки ГГГГ-ММ-ДД; без значения — сегодня",
                },
            },
            "required": [],
        },
    },
    {
        "name": "facts",
        "description": "Факты за период до N суток назад — когда нужны упомянутые обстоятельства без чтения реплик.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "конечные сутки ГГГГ-ММ-ДД; без значения — сегодня",
                },
                "days": {
                    "type": "integer",
                    "description": "сколько суток захватить, считая от конечного (по умолчанию 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "максимум фактов (по умолчанию 50)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "tasks",
        "description": "Задачи из разговоров по статусу и, при желании, за конкретный день.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "done", "all"],
                    "description": "какие задачи показать (по умолчанию open)",
                },
                "date": {
                    "type": "string",
                    "description": "только задачи разговоров этих суток, ГГГГ-ММ-ДД",
                },
                "limit": {
                    "type": "integer",
                    "description": "максимум задач (по умолчанию 50)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "stats",
        "description": "Статистика памяти: сколько накоплено, очередь приёма, размер смыслового индекса.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "privacy",
        "description": "Статус приватности: пишется ли сейчас, стоп-зоны и куда уходят данные.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

_TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}


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
        return _tool_result(f"неизвестный инструмент: {name!r}", is_error=True)
    try:
        text = getattr(tools, name)(**arguments)
    except Exception as exc:
        log.exception("инструмент %s упал", name)
        return _tool_result(f"инструмент {name} не смог ответить: {exc}", is_error=True)
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
            return {"tools": TOOL_SPECS}
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
        raise MethodNotFound(f"метод не поддерживается: {method}")

    return dispatch


def main(argv: list[str] | None = None) -> int:
    from uzel.config import get_settings

    settings = get_settings()
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
