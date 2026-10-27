"""Транспорт stdio и JSON-RPC 2.0 для MCP — только стандартная библиотека.

Слой ничего не знает про УЗЕЛ: принимает разобранные сообщения, зовёт
dispatch и упаковывает ответы. Официальный SDK ``mcp`` не ставится: он тянет
13 пакетов ради HTTP/OAuth, которыми проект не пользуется (решение зафиксировано
в ТЗ P6), а stdio-MCP — это JSON-RPC построчно.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


class MethodNotFound(Exception):
    """Метод JSON-RPC не существует."""


class InvalidParams(Exception):
    """Аргументы метода не годятся."""


def negotiate_protocol(requested: str | None) -> str:
    """Запрошенная версия, если знаем её, иначе последняя поддерживаемая."""
    if requested in SUPPORTED_PROTOCOLS:
        return requested
    return LATEST_PROTOCOL


def _error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle_message(message: dict, dispatch: Callable[[str, dict], dict]) -> dict | None:
    """Одно разобранное сообщение. ``None`` — отвечать не нужно (уведомление)."""
    if "id" not in message:
        return None
    try:
        result = dispatch(message.get("method"), message.get("params"))
    except MethodNotFound as exc:
        return _error(message["id"], -32601, str(exc) or "метод не найден")
    except InvalidParams as exc:
        return _error(message["id"], -32602, str(exc) or "неверные аргументы")
    except Exception as exc:  # сервер не имеет права падать от чужого запроса
        log.exception("обработка %s упала", message.get("method"))
        return _error(message["id"], -32603, str(exc)[:500])
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(reader, writer, dispatch: Callable[[str, dict], dict]) -> None:
    """Цикл «строка stdin → строка stdout» до EOF.

    Битый JSON не убивает цикл: отвечаем ``-32700`` и читаем дальше.
    В stdout не должно попадать ничего, кроме JSON-RPC — логи только в stderr.
    """
    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(writer, _error(None, -32700, f"невалидный JSON: {exc}"))
            continue
        response = handle_message(message, dispatch)
        if response is not None:
            _write(writer, response)


def _write(writer, payload: dict) -> None:
    writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
    writer.flush()
