"""MCP-сервер (P6): протокол JSON-RPC, инструменты, сквозной подпроцесс.

Сети, GPU и Ollama тесты не трогают: смысловой путь либо выключен
(``vector_enabled=False`` — NullIndex), либо вызван с ``semantic=False``.
Все данные — во временных папках, боевая база и Obsidian недостижимы.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest
from uzel.config import Settings
from uzel.storage import Storage
from uzel_mcp import SERVER_NAME, SERVER_VERSION
from uzel_mcp.protocol import (
    LATEST_PROTOCOL,
    InvalidParams,
    handle_message,
    negotiate_protocol,
    serve,
)
from uzel_mcp.server import build_dispatch
from uzel_mcp.tools import UzelTools

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def empty_settings(settings) -> Settings:
    """Настройки conftest с выключенным Qdrant: fastembed не грузится."""
    return settings.model_copy(update={"vector_enabled": False})


@pytest.fixture
def filled_db(tmp_path) -> Settings:
    """Тестовый день: разговор с саммари, репликами, фактом, задачей, темой."""
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        vector_enabled=False,
    )
    started = datetime(2026, 9, 17, 10, 0).timestamp()
    with Storage(settings.db_path) as storage:
        person = storage.upsert_person("Иван")
        conversation = storage.open_conversation(uid="dev-1", started_at=started)
        first = storage.add_utterance(
            uid="dev-1",
            started_at=started,
            ended_at=started + 10.0,
            speech_seconds=8.0,
            conversation_id=conversation,
        )
        second = storage.add_utterance(
            uid="dev-1",
            started_at=started + 60.0,
            ended_at=started + 70.0,
            speech_seconds=9.0,
            conversation_id=conversation,
        )
        storage.set_transcript(
            first,
            text="нужно согласовать заявку ООО «Ромашка» с ГИПом",
            language="ru",
            backend="local",
        )
        storage.set_transcript(
            second,
            text="срок до пятницы, не позднее",
            language="ru",
            backend="local",
        )
        storage.set_speaker(first, person)
        storage.attach_utterances(conversation, [first, second])
        storage.close_conversation(
            conversation,
            summary="Рабочая сессия.\nОбсудили заявку ООО «Ромашка» и сроки согласования.",
            status="closed",
        )
        storage.add_fact(
            conversation, "Пётр подал заявку на август", said_at=started + 20.0
        )
        storage.add_task(
            conversation,
            "Согласовать заявку ООО «Ромашка»",
            assignee="Иван",
            due="2026-09-18",
        )
        storage.add_tag("заявки", conversation_id=conversation)
    return settings


# ---------- протокол ----------


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("2025-06-18", "2025-06-18"),
        ("2025-03-26", "2025-03-26"),
        ("2024-11-05", "2024-11-05"),
        ("1999-01-01", LATEST_PROTOCOL),
        (None, LATEST_PROTOCOL),
    ],
)
def test_negotiate_protocol(requested, expected):
    assert negotiate_protocol(requested) == expected


def test_notification_without_id_returns_none():
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert handle_message(message, lambda *args: {}) is None


def test_unknown_method_is_32601():
    response = handle_message(
        {"jsonrpc": "2.0", "id": 7, "method": "no/such"}, build_dispatch(None)
    )
    assert response["error"]["code"] == -32601
    assert response["id"] == 7


def test_invalid_params_is_32602():
    def raising(method, params):
        raise InvalidParams("плохие аргументы")

    response = handle_message({"jsonrpc": "2.0", "id": 10, "method": "x"}, raising)
    assert response["error"]["code"] == -32602
    assert "плохие аргументы" in response["error"]["message"]


def test_internal_error_is_32603_and_truncated():
    def boom(method, params):
        raise RuntimeError("в" * 900)

    response = handle_message({"jsonrpc": "2.0", "id": 11, "method": "x"}, boom)
    assert response["error"]["code"] == -32603
    assert len(response["error"]["message"]) <= 500


def test_serve_survives_invalid_json():
    reader = StringIO('это не json\n\n{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
    writer = StringIO()
    serve(reader, writer, lambda method, params: {})

    lines = writer.getvalue().splitlines()
    assert len(lines) == 2  # пустая строка пропущена без ответа
    first = json.loads(lines[0])
    assert first["error"]["code"] == -32700
    assert first["id"] is None
    second = json.loads(lines[1])  # цикл не умер, следующее обработано
    assert second == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }


def test_serve_writes_russian_as_is():
    reader = StringIO('{"jsonrpc": "2.0", "id": 1, "method": "tools/call"}\n')
    writer = StringIO()
    serve(reader, writer, lambda method, params: {"echo": "заявка ООО «Ромашка»"})

    line = writer.getvalue().splitlines()[0]
    assert "заявка ООО «Ромашка»" in line  # ensure_ascii=False: кириллица не в \uXXXX


# ---------- tools/list и tools/call ----------


def test_tools_list_has_seven_described_tools():
    response = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, build_dispatch(None)
    )
    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "search",
        "conversation",
        "day",
        "facts",
        "tasks",
        "stats",
        "privacy",
    }
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_call_unknown_tool_is_error_result_not_rpc_error(empty_settings):
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
        build_dispatch(UzelTools(empty_settings)),
    )
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["type"] == "text"


class _BoomTools:
    """Фейковый UzelTools: любой инструмент падает."""

    def search(self, query, limit=10, semantic=True):
        raise RuntimeError("всё сломалось")


def test_call_failing_tool_is_error_result():
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "x"}},
        },
        build_dispatch(_BoomTools()),
    )
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "всё сломалось" in response["result"]["content"][0]["text"]


def test_initialize_advertises_server():
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        build_dispatch(None),
    )
    result = response["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"] == {"name": SERVER_NAME, "version": SERVER_VERSION}
    assert result["capabilities"] == {"tools": {"listChanged": False}}


def test_empty_client_capabilities_lists():
    dispatch = build_dispatch(None)
    assert dispatch("resources/list", None) == {"resources": []}
    assert dispatch("prompts/list", None) == {"prompts": []}
    assert dispatch("ping", None) == {}


# ---------- инструменты на пустой базе (пункт приёмки) ----------


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("search", {"query": "что угодно"}),
        ("conversation", {"id": 1}),
        ("day", {}),
        ("facts", {}),
        ("tasks", {}),
        ("stats", {}),
        ("privacy", {}),
    ],
)
def test_empty_database_every_tool_answers(empty_settings, name, arguments):
    text = getattr(UzelTools(empty_settings), name)(**arguments)
    assert isinstance(text, str)
    assert text.strip()


# ---------- инструменты на тестовом дне ----------


def test_search_finds_utterance_substring(filled_db):
    result = UzelTools(filled_db).search("Ромашка", semantic=False)
    assert "заявку ООО «Ромашка»" in result
    assert "#1 [17.09.2026 10:00] Иван" in result
    assert "подстрочным" in result


def test_search_empty_query_is_invalid_params(filled_db):
    with pytest.raises(Exception, match="пустой"):
        UzelTools(filled_db).search("   ")


def test_conversation_hides_transcript_by_default(filled_db):
    result = UzelTools(filled_db).conversation(1)
    assert "Обсудили заявку ООО «Ромашка»" in result  # саммари
    assert "Пётр подал заявку" in result  # факт
    assert "Согласовать заявку" in result  # задача
    assert "заявки" in result  # тема
    assert "Иван" in result  # участник
    assert "срок до пятницы" not in result  # дословные реплики скрыты


def test_conversation_with_utterances_shows_transcript(filled_db):
    result = UzelTools(filled_db).conversation(1, with_utterances=True)
    assert "нужно согласовать заявку ООО «Ромашка» с ГИПом" in result
    assert "срок до пятницы" in result


def test_conversation_missing_returns_text(filled_db):
    result = UzelTools(filled_db).conversation(999)
    assert result == "Разговора 999 нет."


def test_day_shows_conversation_and_totals(filled_db):
    result = UzelTools(filled_db).day("2026-09-17")
    assert "#1" in result
    assert "10:00–10:01" in result
    assert "закрыт" in result.lower() or "closed" in result.lower()
    assert "разговоров 1" in result


def test_day_empty_returns_text(filled_db):
    assert "За 2026-09-18 разговоров нет." in UzelTools(filled_db).day("2026-09-18")


def test_day_bad_date_is_invalid_params(filled_db):
    with pytest.raises(Exception, match="ГГГГ-ММ-ДД"):
        UzelTools(filled_db).day("17-09-2026")


def test_facts_show_timecode_and_conversation(filled_db):
    result = UzelTools(filled_db).facts(date="2026-09-17")
    assert "[17.09 10:00] Пётр подал заявку на август" in result
    assert "(разговор #1)" in result


def test_facts_days_window_reaches_back(filled_db):
    result = UzelTools(filled_db).facts(date="2026-09-18", days=2)
    assert "Пётр подал заявку" in result


def test_facts_bad_days_is_invalid_params(filled_db):
    with pytest.raises(Exception):
        UzelTools(filled_db).facts(days=0)


def test_tasks_show_assignee_and_due(filled_db):
    result = UzelTools(filled_db).tasks()
    assert "[ ] Согласовать заявку ООО «Ромашка» (Иван) до 2026-09-18" in result
    assert "(разговор #1, 17.09 10:00)" in result


def test_tasks_of_day_filters(filled_db):
    assert "Согласовать" in UzelTools(filled_db).tasks(date="2026-09-17")
    result = UzelTools(filled_db).tasks(date="2026-09-18")
    assert result == "Задач нет."


def test_tasks_bad_status_is_invalid_params(filled_db):
    with pytest.raises(Exception):
        UzelTools(filled_db).tasks(status="wip")


def test_stats_reports_storage_and_queue(filled_db):
    result = UzelTools(filled_db).stats()
    assert "разговоров" in result
    assert "Очередь приёма: 0" in result
    assert "схема" in result
    assert "Смысловой индекс" in result


def test_privacy_reports_gate_and_backends(filled_db):
    result = UzelTools(filled_db).privacy()
    assert "запись" in result
    assert "расшифровка: local (локально)" in result
    assert "разбор: ollama (локально)" in result
    assert "облако на штатном пути: запрещено" in result


def test_privacy_cloud_line_follows_real_state(filled_db, monkeypatch):
    """P11.2, находка 13: строка про облако выводится из состояния, а не
    захардкожена — иначе владельцу показывали бы гарантию, которой нет."""
    import uzel.privacy

    tools = UzelTools(filled_db.model_copy(update={"llm_backends": ("ollama", "groq")}))
    assert "облачные в настройках: groq" in tools.privacy()

    monkeypatch.setattr(uzel.privacy, "BATCH_ALLOW_CLOUD", True)
    result = tools.privacy()
    assert "РАЗРЕШЕНО" in result
    assert "запрещено" not in result


def test_privacy_marks_cloud_stt(settings, tmp_path):
    stt_cloud = settings.model_copy(
        update={"stt_backends": ("groq",), "vector_enabled": False}
    )
    result = UzelTools(stt_cloud).privacy()
    assert "расшифровка: groq (ОБЛАКО)" in result


def test_privacy_survives_broken_llm_router(settings):
    broken = settings.model_copy(update={"llm_backends": (), "vector_enabled": False})
    result = UzelTools(broken).privacy()
    assert "роутер не собрался" in result


def test_search_semantic_falls_back_without_qdrant(filled_db):
    result = UzelTools(filled_db).search("Ромашка", semantic=True)
    assert "заявку ООО «Ромашка»" in result
    assert "подстрочным" in result  # честно сказано, каким поиском нашли


# ---------- приватные реплики (P11.2, находка 5) ----------


def _add_private(filled_db, *, text="приватная реплика про Петра"):
    """Приватная реплика в разговор 1 тестового дня."""
    started = datetime(2026, 9, 17, 10, 30).timestamp()
    with Storage(filled_db.db_path) as storage:
        uid = storage.add_utterance(
            uid="dev-1",
            started_at=started,
            ended_at=started + 30.0,
            speech_seconds=20.0,
            conversation_id=1,
            is_private=True,
        )
        storage.set_transcript(uid, text=text, language="ru", backend="local")


def test_conversation_counts_hidden_private(filled_db):
    """«Скрыто N» считается и после смены умолчания хранилища."""
    _add_private(filled_db, text="нужно согласовать заявку ООО «Ромашка» с ГИПом приватно")

    result = UzelTools(filled_db).conversation(1, with_utterances=True)

    assert "приватно" not in result
    assert "скрыто 1 реплик: помечены приватными" in result
    assert "нужно согласовать заявку ООО «Ромашка» с ГИПом" in result  # публичные на месте


def test_search_counts_hidden_private_from_substring(filled_db):
    """Подстрочный поиск для подсчёта берёт всё, наружу отдаёт только видимое."""
    _add_private(filled_db, text="тоже про заявку ООО «Ромашка», но скрытыймаркер")

    result = UzelTools(filled_db).search("заявку ООО «Ромашка»", semantic=False)

    assert "скрытыймаркер" not in result
    assert "#1 [17.09.2026 10:00] Иван" in result  # публичная найдена
    assert "(скрыто 1 реплик: помечены приватными)" in result


def test_day_counts_only_visible_utterances(filled_db):
    """Счёт реплик в сводке дня — по видимым, приватные в него не входят."""
    _add_private(filled_db)

    result = UzelTools(filled_db).day("2026-09-17")

    assert "реплик 2" in result


# ---------- сквозной прогон через подпроцесс ----------


def test_subprocess_speaks_jsonrpc_with_russian(filled_db):
    env = os.environ.copy()
    env.update(
        {
            "UZEL_DATA_DIR": str(filled_db.data_dir),
            "UZEL_VAULT_DIR": str(filled_db.vault_dir),
            "UZEL_VECTOR_ENABLED": "0",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"query": "Ромашка", "semantic": False},
            },
        },
    ]
    stdin = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests)

    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "integrations" / "mcp" / "server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        out, err = proc.communicate(stdin, timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    # stdout — только JSON-RPC: каждая строка обязана разбираться
    responses = [json.loads(line) for line in out.splitlines() if line.strip()]
    by_id = {response["id"]: response for response in responses if "id" in response}

    assert by_id[1]["result"]["serverInfo"] == {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
    }
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(by_id[2]["result"]["tools"]) == 7

    search = by_id[3]["result"]
    assert search["isError"] is False
    text = search["content"][0]["text"]
    assert "заявку ООО «Ромашка»" in text  # кириллица доехала целой
