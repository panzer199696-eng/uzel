"""Приёмка Omi-совместимого бэкенда (P8).

Payload взяты в формате документации Omi (docs.omi.me, camelCase):
realtime-транскрипт — голый список сегментов и объект, conversation —
готовый разговор со structured-разбором, day-summary — сводка дня.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from uzel.app import create_app
from uzel.config import Settings
from uzel.privacy import WEEKDAY_NAMES
from uzel.queue import ChunkMeta
from uzel.storage import Storage

PCM = b"\x00\x01" * 800   # 1600 байт = 50 мс при 16 кГц
PCM8 = b"\x00\x01" * 400  # 800 байт = 50 мс при 8 кГц

REALTIME_SEGMENTS = [
    {
        "text": "Привет, это первая реплика для проверки.",
        "speaker": "A",
        "speakerId": 0,
        "isUser": False,
        "start": 0.0,
        "end": 2.5,
    },
    {
        "text": "А это вторая реплика, проверяем порядок.",
        "speaker": "B",
        "speakerId": 1,
        "isUser": False,
        "start": 3.0,
        "end": 5.5,
    },
]

CONVERSATION = {
    "id": "conv-123",
    "createdAt": "2026-09-20T10:00:00.000000+00:00",
    "startedAt": "2026-09-20T09:55:00.000000+00:00",
    "finishedAt": "2026-09-20T10:05:00.000000+00:00",
    "transcriptSegments": [
        {
            "text": "Обсудили поставку бетона на объект.",
            "speaker": "A",
            "speakerId": 0,
            "isUser": False,
            "start": 10.0,
            "end": 14.5,
        }
    ],
    "structured": {
        "title": "План на объект",
        "overview": "Договорились о сроках поставки",
        "emoji": "\U0001f3d7\ufe0f",
        "category": "work",
        "actionItems": [
            {"description": "Позвонить поставщику бетона", "completed": False}
        ],
        "events": [
            {"title": "Звонок поставщику", "startsAt": "2026-09-20T11:00:00Z"}
        ],
    },
    "discarded": False,
}

DAY_SUMMARY = {
    "uid": "omi-1",
    "createdAt": "2026-09-20T12:00:00Z",
    "summary": "День прошёл спокойно",
    "summaryJson": {"разделы": ["утро", "вечер"]},
}

CONVERSATION_STARTED_AT = datetime(
    2026, 9, 20, 9, 55, tzinfo=timezone.utc
).timestamp()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def _queue_metas(settings) -> list[ChunkMeta]:
    return [
        ChunkMeta.from_json(path.read_text(encoding="utf-8"))
        for path in sorted((settings.queue_dir / "incoming").glob("*.json"))
    ]


# ---------- setup ----------

def test_setup_endpoint(client):
    response = client.get("/omi/setup")

    assert response.status_code == 200
    assert response.json() == {"is_setup_completed": True}


# ---------- audio ----------

def test_audio_16k_accepted_to_queue(client, settings):
    response = client.post(
        "/omi/audio", params={"uid": "omi-1", "sample_rate": 16000}, content=PCM
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["n_bytes"] == len(PCM)
    metas = _queue_metas(settings)
    assert len(metas) == 1
    assert metas[0].source == "omi-app"
    assert metas[0].sample_rate == 16000
    assert metas[0].uid == "omi-1"


def test_audio_muted_privacy_keeps_queue_empty(client, settings):
    client.post("/privacy/pause")
    response = client.post(
        "/omi/audio", params={"uid": "omi-1", "sample_rate": 16000}, content=PCM
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "paused"}
    assert _queue_metas(settings) == []


def test_audio_8k_is_upsampled_double_length(client, settings):
    response = client.post(
        "/omi/audio", params={"uid": "omi-1", "sample_rate": 8000}, content=PCM8
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    metas = _queue_metas(settings)
    assert len(metas) == 1
    assert metas[0].sample_rate == 16000
    assert metas[0].source == "omi-app-8k"
    assert metas[0].n_bytes == len(PCM8) * 2  # вдвое больше сэмплов


def test_audio_foreign_sample_rate_is_not_accepted(client, settings):
    response = client.post(
        "/omi/audio", params={"uid": "omi-1", "sample_rate": 44100}, content=PCM
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "sample_rate"}
    assert _queue_metas(settings) == []


def test_audio_empty_body(client, settings):
    response = client.post(
        "/omi/audio", params={"uid": "omi-1", "sample_rate": 16000}, content=b""
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "empty"}
    assert _queue_metas(settings) == []


# ---------- realtime transcript ----------

def test_transcript_plain_list_creates_utterances(client, settings):
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-1"},
        json=REALTIME_SEGMENTS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["created"] == 2

    with Storage(settings.db_path) as store:
        rows = store.ungrouped_utterances()
        assert len(rows) == 2
        assert [row.text for row in rows] == [
            "Привет, это первая реплика для проверки.",
            "А это вторая реплика, проверяем порядок.",
        ]
        assert all(row.stt_backend == "omi-cloud" for row in rows)
        assert all(row.status == "transcribed" for row in rows)
        assert rows[0].ended_at - rows[0].started_at == pytest.approx(2.5)
        assert rows[1].ended_at - rows[1].started_at == pytest.approx(2.5)


def test_transcript_object_with_segments_and_session(client, settings):
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1"},
        json={"segments": REALTIME_SEGMENTS, "session_id": "sess-2"},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 2
    with Storage(settings.db_path) as store:
        assert len(store.ungrouped_utterances()) == 2


def test_transcript_repeat_batch_counts_duplicates(client, settings):
    client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-3"},
        json=REALTIME_SEGMENTS,
    )
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-3"},
        json=REALTIME_SEGMENTS,
    )

    body = response.json()
    assert body["duplicates"] == 2
    assert body["created"] == 0
    with Storage(settings.db_path) as store:
        assert len(store.ungrouped_utterances()) == 2


def test_transcript_refined_text_updates_utterance(client, settings):
    client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-4"},
        json=REALTIME_SEGMENTS,
    )
    refined = [dict(REALTIME_SEGMENTS[0]), dict(REALTIME_SEGMENTS[1])]
    refined[1]["text"] = "А это вторая реплика, уточнённая версия."

    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-4"},
        json=refined,
    )

    body = response.json()
    assert body["updated"] == 1
    assert body["duplicates"] == 1
    with Storage(settings.db_path) as store:
        rows = store.ungrouped_utterances()
        assert len(rows) == 2  # не задвоена
        assert rows[1].text == "А это вторая реплика, уточнённая версия."


def test_transcript_blocked_by_privacy(client, settings):
    client.post("/privacy/pause")
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-5"},
        json=REALTIME_SEGMENTS,
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "paused"}
    with Storage(settings.db_path) as store:
        assert store.stats()["utterances"] == 0


def test_transcript_skips_empty_text(client, settings):
    segments = [
        {"text": "", "speaker": "A", "start": 0.0, "end": 1.0},
        {"text": "   ", "speaker": "A", "start": 2.0, "end": 3.0},
        {"text": "Единственная содержательная реплика.", "speaker": "B", "start": 4.0, "end": 6.0},
    ]
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-6"},
        json=segments,
    )

    body = response.json()
    assert body["created"] == 1
    assert body["skipped"] == 2
    with Storage(settings.db_path) as store:
        rows = store.ungrouped_utterances()
        assert [row.text for row in rows] == ["Единственная содержательная реплика."]


def test_transcript_accepts_camel_case_speaker_id(client, settings):
    segments = [
        {"text": "Реплика с идентификатором спикера.", "speakerId": 7, "start": 0.0, "end": 1.5}
    ]
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-7"},
        json=segments,
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    with Storage(settings.db_path) as store:
        assert len(store.ungrouped_utterances()) == 1


def test_transcript_speaker_name_matches_only_known_people(client, settings):
    with Storage(settings.db_path) as store:
        store.upsert_person("Иван")
        ivan_id = store.find_person("Иван")
    segments = [
        {
            "text": "Реплика известного человека.",
            "speaker": "S1",
            "speakerName": "Иван",
            "start": 0.0,
            "end": 1.0,
        },
        {
            "text": "Реплика незнакомца.",
            "speaker": "S2",
            "speakerName": "Незнакомец",
            "start": 2.0,
            "end": 3.0,
        },
    ]
    response = client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-8"},
        json=segments,
    )

    assert response.json()["created"] == 2
    conn = sqlite3.connect(settings.db_path)
    try:
        rows = conn.execute(
            "SELECT text, speaker_id FROM utterances ORDER BY started_at"
        ).fetchall()
    finally:
        conn.close()
    assert rows[0][1] == ivan_id
    assert rows[1][1] is None
    with Storage(settings.db_path) as store:
        assert store.stats()["people"] == 1  # новых людей не завели


# ---------- conversation ----------

def test_conversation_full_payload(client, settings):
    response = client.post(
        "/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["utterances"] == 1
    assert body["tasks"] == 1
    assert body["events_ignored"] == 1

    cid = body["conversation_id"]
    with Storage(settings.db_path) as store:
        conv = store.get_conversation(cid)
        assert conv["status"] == "closed"
        assert conv["summary"] == "План на объект. Договорились о сроках поставки"
        rows = store.utterances_of(cid)
        assert len(rows) == 1
        assert rows[0].text == "Обсудили поставку бетона на объект."
        assert rows[0].stt_backend == "omi-cloud"
        # время реплики привязано к started_at разговора
        assert rows[0].started_at == pytest.approx(CONVERSATION_STARTED_AT + 10.0)
        assert rows[0].ended_at == pytest.approx(CONVERSATION_STARTED_AT + 14.5)
        assert [task["text"] for task in store.tasks_of(cid)] == [
            "Позвонить поставщику бетона"
        ]
        assert store.tags_of(cid) == ["work"]


def test_conversation_repeat_is_duplicate(client, settings):
    client.post("/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION)
    response = client.post(
        "/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION
    )

    body = response.json()
    assert body["accepted"] is True
    assert body["duplicate"] is True
    with Storage(settings.db_path) as store:
        assert store.stats()["conversations"] == 1
        assert store.stats()["utterances"] == 1
        assert store.stats()["tasks"] == 1


def test_conversation_discarded_writes_nothing(client, settings):
    payload = dict(CONVERSATION, discarded=True)
    response = client.post("/omi/conversation", params={"uid": "omi-1"}, json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "skipped": True,
        "reason": "discarded",
    }
    with Storage(settings.db_path) as store:
        assert store.stats()["conversations"] == 0
        assert store.stats()["utterances"] == 0


# ---------- day summary ----------

def test_day_summary_saved_as_json_file(client, settings):
    response = client.post(
        "/omi/day-summary", params={"uid": "omi-1"}, json=DAY_SUMMARY
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    path = settings.data_dir / "omi" / "сводка-omi-1-2026-09-20.json"
    assert body["path"] == str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["summary"] == "День прошёл спокойно"
    assert data["summary_json"] == {"разделы": ["утро", "вечер"]}


# ---------- токен и выключение ----------

def test_token_via_query_param(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        groq_api_key=None,
        omi_token="секрет",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/omi/setup").status_code == 401
        assert client.get("/omi/setup", params={"token": "секрет"}).status_code == 200


def test_token_via_bearer_header(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        groq_api_key=None,
        omi_token="s3cret",
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.get(
                "/omi/setup", headers={"Authorization": "Bearer s3cret"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/omi/setup", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )


def test_disabled_omi_mounts_nothing(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        groq_api_key=None,
        omi_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/omi/audio",
                params={"uid": "omi-1", "sample_rate": 16000},
                content=PCM,
            ).status_code
            == 404
        )
        assert client.get("/omi/setup").status_code == 404


# ---------- миграция v3 → v4 ----------

def test_migration_adds_external_id_columns(settings):
    path = settings.db_path
    with Storage(path) as store:
        old = store.add_utterance(
            uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
        )
        store.set_transcript(old, text="старая реплика", language="ru", backend="local")

    # Снимаем колонки версии 4: имитируем базу, созданную до P8.
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP INDEX IF EXISTS idx_conversations_external")
        conn.execute("DROP INDEX IF EXISTS idx_utterances_external")
        conn.execute("ALTER TABLE conversations DROP COLUMN external_id")
        conn.execute("ALTER TABLE utterances DROP COLUMN external_id")
        conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()

    with Storage(path) as reopened:
        assert reopened.schema_version == 4
        row = reopened.get_utterance(old)
        assert row is not None and row.text == "старая реплика"  # реплики целы
        assert row.external_id is None

        # Новая функциональность работает сразу после миграции.
        new = reopened.add_utterance(
            uid="dev-1", started_at=3.0, ended_at=4.0, speech_seconds=1.0,
            external_id="omi:sess:0.00:A",
        )
        assert reopened.find_utterance_by_external("omi:sess:0.00:A").id == new
        cid = reopened.open_conversation(
            uid="dev-1", started_at=100.0, external_id="omi:conv:9"
        )
        assert reopened.find_conversation_by_external("omi:conv:9") == cid


# ---------- найдено при приёмке 20.09.2026 ----------


def test_audio_without_query_params_is_still_accepted(client, settings):
    """Сборка приложения, забывшая uid или sample_rate, не должна терять звук.

    До правки оба параметра были обязательными, и запрос без них получал 422 —
    для вебхука это ретраи и молча потерянный фрагмент.
    """
    response = client.post("/omi/audio", content=PCM)

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    metas = _queue_metas(settings)
    assert len(metas) == 1
    assert metas[0].sample_rate == settings.sample_rate
    assert metas[0].uid == "omi-app"


@pytest.mark.parametrize(
    "path, body",
    [
        ("/omi/conversation", b"{\"startedAt\": 12345}"),
        ("/omi/day-summary", b"[1, 2, 3]"),
        ("/omi/transcript", b"\"not a payload\""),
    ],
)
def test_broken_body_answers_200_instead_of_500(client, path, body):
    """Чужое тело не должно превращаться в 500: приложение уйдёт в ретраи."""
    response = client.post(
        path,
        params={"uid": "omi-1"},
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "bad_json"}


def test_conversation_supersedes_overlapping_realtime_utterances(client, settings):
    """Оба вебхука включены разом — слова не должны попасть в день дважды."""
    начало = CONVERSATION_STARTED_AT
    поток = [
        {
            "text": "Обсудили поставку бетона",
            "speaker": "A",
            "start": 10.0,
            "end": 14.5,
        }
    ]
    assert (
        client.post(
            "/omi/transcript",
            params={"uid": "omi-1", "session_id": "sess-9"},
            json=поток,
        ).json()["created"]
        == 1
    )
    # Двигаем потоковую реплику во время разговора: её время вычислено от
    # «сейчас», а разговор пришёл с меткой 20.09.2026.
    with Storage(settings.db_path) as store:
        row = store.ungrouped_utterances()[0]
        store._conn.execute(  # noqa: SLF001 — подмена времени только для теста
            "UPDATE utterances SET started_at=?, ended_at=? WHERE id=?",
            (начало + 10.0, начало + 14.5, row.id),
        )
        store._conn.commit()

    response = client.post(
        "/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION
    )

    assert response.json()["superseded"] == 1
    with Storage(settings.db_path) as store:
        # потоковая реплика осталась в базе, но выведена из оборота
        assert store.ungrouped_utterances() == []
        погашенные = store.rejected_utterances()
        assert len(погашенные) == 1
        assert погашенные[0].reject_reason == "omi_superseded"
        assert погашенные[0].text == "Обсудили поставку бетона"
        # в разговоре — только реплика из готового payload
        rows = store.utterances_of(response.json()["conversation_id"])
        assert [row.text for row in rows] == ["Обсудили поставку бетона на объект."]


def test_conversation_keeps_unrelated_realtime_utterances(client, settings):
    """Чужие по времени потоковые реплики разговор не трогает."""
    client.post(
        "/omi/transcript",
        params={"uid": "omi-1", "session_id": "sess-8"},
        json=REALTIME_SEGMENTS,
    )
    response = client.post(
        "/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION
    )

    assert response.json()["superseded"] == 0
    with Storage(settings.db_path) as store:
        assert len(store.ungrouped_utterances()) == 2


# ---------- приватность по времени записи (P11.2, находка 11) ----------


def _window_for(moment: float) -> str:
    """Стоп-окно, накрывающее момент: час с небольшим и день недели той даты.

    День недели обязателен: без него окно действовало бы ежедневно и било
    по «сейчас» всякий раз, когда тест запущен в тот же час.
    """
    local = datetime.fromtimestamp(moment)
    день = WEEKDAY_NAMES[local.weekday()]
    конец = local + timedelta(minutes=30)
    return f"{local:%H:%M}-{конец:%H:%M}/{день}"


def _client_with_window(settings, moment: float):
    """Приложение со стоп-окном на заданный момент (сейчас — разрешено)."""
    private = settings.model_copy(update={"quiet_windows": (_window_for(moment),)})
    return TestClient(create_app(private))


def test_conversation_recorded_during_window_not_saved(settings):
    """Разговор записан в стоп-зону (started_at в прошлом), а доставлен сейчас:
    реплики и разговор не сохраняются, хотя на момент доставки запись разрешена."""
    момент_сегмента = CONVERSATION_STARTED_AT + 10.0  # единственный сегмент
    with _client_with_window(settings, момент_сегмента) as client:
        response = client.post(
            "/omi/conversation", params={"uid": "omi-1"}, json=CONVERSATION
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "private"}
    with Storage(settings.db_path) as store:
        assert store.stats()["conversations"] == 0
        assert store.stats()["utterances"] == 0


def test_conversation_without_times_is_all_private(settings):
    """Время записи не разобрано — реплики сохраняются приватными,
    задачи и выжимка Omi не сохраняются вовсе."""
    payload = {
        "id": "conv-no-time",
        "transcriptSegments": [
            {"text": "Реплика без времени записи.", "speaker": "A",
             "start": 0.0, "end": 3.0},
            {"text": "И вторая тоже.", "speaker": "A",
             "start": 5.0, "end": 8.0},
        ],
        "structured": {
            "title": "Заголовок",
            "overview": "Обзор",
            "category": "work",
            "actionItems": [
                {"description": "Задача из облака", "completed": False}
            ],
        },
    }
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/omi/conversation", params={"uid": "omi-1"}, json=payload
        )

    body = response.json()
    assert body["accepted"] is True
    assert body["structured_dropped"] is True
    assert body["private"] == 0  # отсеянных нет — все помечены, не выброшены
    cid = body["conversation_id"]
    with Storage(settings.db_path) as store:
        rows = store.utterances_of(cid, include_private=True)
        assert [row.text for row in rows] == [
            "Реплика без времени записи.",
            "И вторая тоже.",
        ]
        assert all(row.is_private for row in rows)
        assert store.tasks_of(cid) == []
        assert store.tags_of(cid) == []
        assert store.get_conversation(cid)["summary"] is None


def test_conversation_partly_muted_saves_only_clear_segments(settings):
    """Заглушена часть разговора: сохраняются только чистые сегменты,
    задачи/категория/саммари не сохраняются, в ответе — число отсеянных."""
    payload = {
        **CONVERSATION,
        "id": "conv-partly",
        "transcriptSegments": [
            {"text": "Сегмент в заглушке.", "speaker": "A",
             "start": 10.0, "end": 12.0},
            {"text": "Сегмент вне заглушки.", "speaker": "A",
             "start": 7200.0, "end": 7210.0},  # +2 часа — за пределами окна
        ],
    }
    with _client_with_window(settings, CONVERSATION_STARTED_AT + 10.0) as client:
        response = client.post(
            "/omi/conversation", params={"uid": "omi-1"}, json=payload
        )

    body = response.json()
    assert body["accepted"] is True
    assert body["utterances"] == 1
    assert body["private"] == 1
    assert body["structured_dropped"] is True
    assert body["tasks"] == 0
    cid = body["conversation_id"]
    with Storage(settings.db_path) as store:
        rows = store.utterances_of(cid, include_private=True)
        assert [row.text for row in rows] == ["Сегмент вне заглушки."]
        assert all(not row.is_private for row in rows)
        assert store.tasks_of(cid) == []
        assert store.tags_of(cid) == []
        assert store.get_conversation(cid)["summary"] is None


def test_transcript_segment_in_window_not_saved(settings):
    """Realtime-сегмент, чей момент попал в стоп-окно, не сохраняется вовсе."""
    # Момент сегмента = now − last_end + start: конец пачки на 10 суток
    # раньше даёт момент с другим днём недели — окно его не заденет «сейчас».
    момент = time.time() - 10 * 86_400
    сегменты = [
        {"text": "Сегмент из глубины буфера.", "speaker": "A",
         "start": 0.0, "end": 864_000.0}
    ]
    with _client_with_window(settings, момент) as client:
        response = client.post(
            "/omi/transcript",
            params={"uid": "omi-1", "session_id": "sess-приват"},
            json=сегменты,
        )

    body = response.json()
    assert body["accepted"] is True  # сейчас запись разрешена — пачка принята
    assert body["created"] == 0
    assert body["private"] == 1
    with Storage(settings.db_path) as store:
        assert store.stats()["utterances"] == 0


def test_day_summary_rejected_when_created_during_window(settings):
    """Сводка дня, собранная в стоп-зоне, не принимается и сегодня."""
    момент = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc).timestamp()
    with _client_with_window(settings, момент) as client:
        response = client.post(
            "/omi/day-summary", params={"uid": "omi-1"}, json=DAY_SUMMARY
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "schedule"}
    assert not (settings.data_dir / "omi").exists()


def test_day_summary_rejected_when_muted_now(client, settings):
    client.post("/privacy/pause")
    response = client.post(
        "/omi/day-summary", params={"uid": "omi-1"}, json=DAY_SUMMARY
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": False, "reason": "paused"}
    assert not (settings.data_dir / "omi").exists()
