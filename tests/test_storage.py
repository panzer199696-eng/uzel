"""Хранилище: FR-040, FR-042, FR-043, фильтр качества (ТЗ 2026-08-24, пакет 2)."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime

import pytest
from uzel.storage import SCHEMA_VERSION, Storage


@pytest.fixture
def store(tmp_path) -> Storage:
    with Storage(tmp_path / "uzel.sqlite") as store:
        yield store


def test_schema_is_created_and_versioned(store):
    assert store.schema_version == SCHEMA_VERSION
    assert store.stats() == {
        "conversations": 0,
        "utterances": 0,
        "facts": 0,
        "tasks": 0,
        "people": 0,
        "tags": 0,
    }


def test_utterance_starts_pending_and_becomes_transcribed(store):
    uid = store.add_utterance(
        uid="dev-1", started_at=100.0, ended_at=104.0, speech_seconds=3.5
    )

    assert store.get_utterance(uid).status == "pending"
    assert len(store.pending_utterances()) == 1

    store.set_transcript(uid, text="привет", language="ru", backend="local")
    row = store.get_utterance(uid)

    assert row.status == "transcribed"
    assert row.text == "привет"
    assert row.stt_backend == "local"
    assert store.pending_utterances() == []


def test_failed_utterance_keeps_row_and_returns_to_pending_list(store):
    """FR-022, FR-032: отказ расшифровки не теряет реплику."""
    uid = store.add_utterance(
        uid="dev-1", started_at=100.0, ended_at=104.0, speech_seconds=3.5
    )

    store.mark_failed(uid)

    assert store.get_utterance(uid).status == "failed"
    assert [row.id for row in store.pending_utterances()] == [uid]


def test_private_flag_is_stored(store):
    uid = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0, is_private=True
    )

    assert store.get_utterance(uid).is_private is True


def test_attaching_utterances_recomputes_conversation_bounds(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    first = store.add_utterance(
        uid="dev-1", started_at=100.0, ended_at=110.0, speech_seconds=8.0
    )
    second = store.add_utterance(
        uid="dev-1", started_at=200.0, ended_at=215.0, speech_seconds=12.0
    )

    store.attach_utterances(conversation, [first, second])
    row = store.conversations_between(0, 1000)[0]

    assert row["started_at"] == 100.0
    assert row["ended_at"] == 215.0
    assert row["speech_seconds"] == pytest.approx(20.0)
    assert len(store.utterances_of(conversation)) == 2


def test_attaching_empty_list_is_a_noop(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    store.attach_utterances(conversation, [])

    assert store.utterances_of(conversation) == []


def test_closing_conversation_stores_summary(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    assert len(store.open_conversations()) == 1

    store.close_conversation(conversation, summary="обсудили заявку")

    assert store.open_conversations() == []
    assert store.conversations_between(0, 1000)[0]["summary"] == "обсудили заявку"


def test_nlu_failure_is_a_distinct_conversation_status(store):
    """FR-031: невалидный ответ модели помечается, транскрипт остаётся."""
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    store.close_conversation(conversation, status="nlu_failed")

    assert store.conversations_between(0, 1000)[0]["status"] == "nlu_failed"


def test_person_upsert_is_idempotent_and_updates_voice_ref(store):
    first = store.upsert_person("Иван")
    second = store.upsert_person("Иван", voice_ref="voice-1")

    assert first == second
    assert store.stats()["people"] == 1


def test_speaker_can_be_attached_and_detached(store):
    person = store.upsert_person("Иван")
    utterance = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
    )

    store.set_speaker(utterance, person)
    store.set_speaker(utterance, None)  # не должно падать


def test_facts_and_tasks_belong_to_conversation(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    store.add_fact(conversation, "заявка «Ромашки» 30 августа")
    store.add_task(conversation, "подготовить КП", assignee="Иван", due="2026-08-30")

    assert store.facts_of(conversation) == ["заявка «Ромашки» 30 августа"]
    task = store.tasks_of(conversation)[0]
    assert task["assignee"] == "Иван"
    assert task["status"] == "open"


def test_deleting_conversation_cascades_to_children(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    store.add_fact(conversation, "факт")
    store.add_task(conversation, "задача")

    with store.transaction() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conversation,))

    assert store.stats()["facts"] == 0
    assert store.stats()["tasks"] == 0


def test_tags_record_marks_and_privacy(store):
    utterance = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
    )

    store.add_tag("mark", utterance_id=utterance)
    store.add_tag("private", utterance_id=utterance)

    assert store.stats()["tags"] == 2


def test_text_search_finds_matching_utterance(store):
    first = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
    )
    second = store.add_utterance(
        uid="dev-1", started_at=3.0, ended_at=4.0, speech_seconds=1.0
    )
    store.set_transcript(
        first, text="заявка ООО «Ромашка» на август", language="ru", backend="local"
    )
    store.set_transcript(
        second, text="совсем про другое", language="ru", backend="local"
    )

    found = store.search_text("Ромашка")

    assert [row.id for row in found] == [first]


def test_text_search_treats_wildcards_as_literal_characters(store):
    utterance = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
    )
    store.set_transcript(
        utterance, text="обычный текст", language="ru", backend="local"
    )

    assert store.search_text("%") == []


def test_purge_audio_removes_only_expired_files(store, tmp_path):
    """FR-042: сырое аудио живёт по TTL, транскрипт остаётся навсегда."""
    now = time.time()
    fresh_file = tmp_path / "fresh.wav"
    old_file = tmp_path / "old.wav"
    fresh_file.write_bytes(b"fresh")
    old_file.write_bytes(b"old")

    fresh = store.add_utterance(
        uid="dev-1",
        started_at=now,
        ended_at=now + 1,
        speech_seconds=1.0,
        audio_path=str(fresh_file),
    )
    old = store.add_utterance(
        uid="dev-1",
        started_at=now - 30 * 86_400,
        ended_at=now,
        speech_seconds=1.0,
        audio_path=str(old_file),
    )
    store.set_transcript(old, text="старая реплика", language="ru", backend="local")

    removed = store.purge_audio(ttl_days=7, now=now)

    assert removed == 1
    assert fresh_file.exists() and not old_file.exists()
    assert store.get_utterance(fresh).audio_path == str(fresh_file)
    assert store.get_utterance(old).audio_path is None
    assert store.get_utterance(old).text == "старая реплика"  # транскрипт цел


def test_purge_audio_survives_missing_file(store, tmp_path):
    now = time.time()
    uid = store.add_utterance(
        uid="dev-1",
        started_at=now - 30 * 86_400,
        ended_at=now,
        speech_seconds=1.0,
        audio_path=str(tmp_path / "нет-такого.wav"),
    )
    store.set_transcript(uid, text="реплика", language="ru", backend="local")

    assert store.purge_audio(ttl_days=7, now=now) == 1


def test_purge_audio_keeps_untranscribed_sound(store, tmp_path):
    """P11.2: очистка идёт в конце батча — звук, который батч не успел
    расшифровать (предел времени), стирать нельзя."""
    now = time.time()
    pending_file = tmp_path / "pending.wav"
    failed_file = tmp_path / "failed.wav"
    pending_file.write_bytes(b"p")
    failed_file.write_bytes(b"f")
    for path in (pending_file, failed_file):
        uid = store.add_utterance(
            uid="dev-1",
            started_at=now - 30 * 86_400,
            ended_at=now,
            speech_seconds=1.0,
            audio_path=str(path),
        )
    store.mark_failed(uid)

    assert store.purge_audio(ttl_days=7, now=now) == 0
    assert pending_file.exists() and failed_file.exists()


def test_purge_audio_disabled_when_ttl_not_positive(store, tmp_path):
    audio = tmp_path / "keep.wav"
    audio.write_bytes(b"x")
    store.add_utterance(
        uid="dev-1",
        started_at=0.0,
        ended_at=1.0,
        speech_seconds=1.0,
        audio_path=str(audio),
    )

    assert store.purge_audio(ttl_days=0) == 0
    assert audio.exists()


def test_backup_produces_readable_copy(store, tmp_path):
    """FR-043: бэкап — обычный файл, который открывается как база."""
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    store.add_fact(conversation, "важное")

    backup = store.backup(tmp_path / "копия" / "uzel.bak.sqlite")

    assert backup.exists()
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    finally:
        conn.close()


def test_reopening_database_keeps_data(tmp_path):
    path = tmp_path / "uzel.sqlite"
    with Storage(path) as store:
        store.add_utterance(
            uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
        )

    with Storage(path) as reopened:
        assert reopened.stats()["utterances"] == 1
        assert reopened.schema_version == SCHEMA_VERSION


# ---------- миграция v1 → v2 ----------


def _create_v1_database(path) -> None:
    """База старой схемы: без reject_reason и exported_at, версия 1."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE conversations (
            id             INTEGER PRIMARY KEY,
            uid            TEXT    NOT NULL,
            started_at     REAL    NOT NULL,
            ended_at       REAL    NOT NULL,
            speech_seconds REAL    NOT NULL DEFAULT 0,
            summary        TEXT,
            status         TEXT    NOT NULL DEFAULT 'open',
            created_at     REAL    NOT NULL
        );
        CREATE TABLE people (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL UNIQUE,
            voice_ref  TEXT,
            created_at REAL    NOT NULL
        );
        CREATE TABLE utterances (
            id              INTEGER PRIMARY KEY,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            speaker_id      INTEGER REFERENCES people(id) ON DELETE SET NULL,
            uid             TEXT    NOT NULL,
            started_at      REAL    NOT NULL,
            ended_at        REAL    NOT NULL,
            speech_seconds  REAL    NOT NULL DEFAULT 0,
            text            TEXT,
            language        TEXT,
            stt_backend     TEXT,
            status          TEXT    NOT NULL DEFAULT 'pending',
            is_private      INTEGER NOT NULL DEFAULT 0,
            audio_path      TEXT,
            created_at      REAL    NOT NULL
        );
        CREATE TABLE tasks (
            id              INTEGER PRIMARY KEY,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            text            TEXT    NOT NULL,
            assignee        TEXT,
            due             TEXT,
            status          TEXT    NOT NULL DEFAULT 'open',
            created_at      REAL    NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.execute(
        """INSERT INTO conversations(uid, started_at, ended_at, status, created_at)
           VALUES ('dev-1', 100.0, 110.0, 'closed', 1.0)"""
    )
    conn.execute(
        """INSERT INTO utterances
           (conversation_id, uid, started_at, ended_at, speech_seconds,
            text, language, stt_backend, status, is_private, created_at)
           VALUES (1, 'dev-1', 100.0, 110.0, 8.0,
                   'старая реплика', 'ru', 'local', 'transcribed', 0, 1.0)"""
    )
    conn.execute(
        """INSERT INTO tasks(conversation_id, text, status, created_at)
           VALUES (1, 'старая задача', 'open', 1.0)"""
    )
    conn.commit()
    conn.close()


def test_migration_upgrades_v1_database_and_keeps_data(tmp_path):
    """Ключевой тест приёмки: база версии 1 поднимается до 4, данные целы."""
    path = tmp_path / "uzel.sqlite"
    _create_v1_database(path)

    with Storage(path) as store:
        assert store.schema_version == 4
        row = store.get_utterance(1)
        assert row is not None and row.text == "старая реплика"
        assert row.reject_reason is None
        assert [task["text"] for task in store.unexported_tasks()] == ["старая задача"]

        # Новая функциональность работает сразу после миграции.
        store.mark_quality(1, reason="too_short", enforce=False)


def test_migration_repeated_open_does_not_fail_and_keeps_data(tmp_path):
    """Повторное открытие мигрированной базы не падает (идемпотентность)."""
    path = tmp_path / "uzel.sqlite"
    _create_v1_database(path)

    with Storage(path) as first:
        first.mark_quality(1, reason="too_short", enforce=True)

    with Storage(path) as second:
        assert second.schema_version == 4
        row = second.get_utterance(1)
        assert row.text == "старая реплика"
        assert row.reject_reason == "too_short"
        assert row.status == "rejected"
        assert [task["text"] for task in second.unexported_tasks()] == ["старая задача"]


def test_new_database_immediately_has_version_4_columns(store):
    conn = sqlite3.connect(store.db_path)
    try:
        utterance_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(utterances)")
        }
        conversation_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(conversations)")
        }
        task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        fact_cols = {row[1] for row in conn.execute("PRAGMA table_info(facts)")}
    finally:
        conn.close()

    assert SCHEMA_VERSION == 4
    assert "reject_reason" in utterance_cols
    assert "external_id" in utterance_cols
    assert "external_id" in conversation_cols
    assert "exported_at" in task_cols
    assert "said_at" in fact_cols


# ---------- вердикты фильтра качества ----------


def _transcribed(store, *, text="заявка по фундаменту", started_at=100.0) -> int:
    uid = store.add_utterance(
        uid="dev-1",
        started_at=started_at,
        ended_at=started_at + 5.0,
        speech_seconds=4.0,
    )
    store.set_transcript(uid, text=text, language="ru", backend="local")
    return uid


def test_mark_quality_flag_mode_keeps_status(store):
    """enforce=False: только помечаем, реплика не теряется (FR-022)."""
    uid = _transcribed(store)

    store.mark_quality(uid, reason="too_short", enforce=False)

    row = store.get_utterance(uid)
    assert row.reject_reason == "too_short"
    assert row.status == "transcribed"


def test_mark_quality_enforce_rejects(store):
    uid = _transcribed(store)

    store.mark_quality(uid, reason="looped", enforce=True)

    row = store.get_utterance(uid)
    assert row.reject_reason == "looped"
    assert row.status == "rejected"


def test_mark_quality_accept_clears_reason_without_touching_status(store):
    uid = _transcribed(store)
    store.mark_quality(uid, reason="too_short", enforce=True)

    store.mark_quality(uid, reason=None, enforce=True)

    row = store.get_utterance(uid)
    assert row.reject_reason is None
    assert row.status == "rejected"  # статус не трогаем


def test_quality_counts_breaks_down_by_reason_and_filters_period(store):
    day1 = datetime(2026, 9, 17, 12, 0).timestamp()
    day1_end = datetime(2026, 9, 18, 0, 0).timestamp()
    day2 = datetime(2026, 9, 18, 12, 0).timestamp()
    day2_end = datetime(2026, 9, 19, 0, 0).timestamp()

    for offset in range(3):  # три принятых в первый день
        _transcribed(store, started_at=day1 + offset)
    short = _transcribed(store, text="ага", started_at=day2)  # помеченные
    looped = _transcribed(store, text="так так так так", started_at=day2 + 10)
    store.mark_quality(short, reason="too_short", enforce=False)
    store.mark_quality(looped, reason="looped", enforce=True)  # отклонённая

    counts = store.quality_counts()
    assert counts == {
        "total": 5,
        "accepted": 3,
        "rejected": 1,
        "flagged": 1,
        "by_reason": {"too_short": 1, "looped": 1},
    }

    only_day1 = store.quality_counts(start=day1, end=day1_end)
    assert only_day1["total"] == 3 and only_day1["by_reason"] == {}

    only_day2 = store.quality_counts(start=day2, end=day2_end)
    assert only_day2["total"] == 2
    assert only_day2["by_reason"] == {"too_short": 1, "looped": 1}


def test_rejected_utterances_lists_flagged_for_calibration(store):
    day1 = datetime(2026, 9, 17, 12, 0).timestamp()
    accepted = _transcribed(store, started_at=day1)
    flagged = _transcribed(store, text="ага", started_at=day1 + 100)
    store.mark_quality(flagged, reason="too_short", enforce=False)

    rows = store.rejected_utterances()

    assert [row.id for row in rows] == [flagged]
    assert accepted not in [row.id for row in rows]


def test_utterances_of_excludes_rejected_by_default(store):
    """Отклонённая реплика не попадает в транскрипт, но остаётся в базе."""
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    kept = _transcribed(store, started_at=100.0)
    dropped = _transcribed(store, text="ага", started_at=110.0)
    store.attach_utterances(conversation, [kept, dropped])
    store.mark_quality(dropped, reason="too_short", enforce=True)

    assert [row.id for row in store.utterances_of(conversation)] == [kept]
    assert [
        row.id for row in store.utterances_of(conversation, include_rejected=True)
    ] == [
        kept,
        dropped,
    ]
    assert store.get_utterance(dropped).text == "ага"  # в базе осталась


def test_pending_utterances_do_not_include_rejected(store):
    rejected = _transcribed(store)
    store.mark_quality(rejected, reason="looped", enforce=True)

    assert store.pending_utterances() == []


# ---------- выгрузка задач ----------


def test_unexported_tasks_and_mark_tasks_exported(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    first = store.add_task(conversation, "подготовить КП")
    second = store.add_task(conversation, "позвонить подрядчику")

    assert [row["id"] for row in store.unexported_tasks()] == [first, second]

    store.mark_tasks_exported([], when=1.0)  # пустой список — тихо
    assert [row["id"] for row in store.unexported_tasks()] == [first, second]

    store.mark_tasks_exported([first], when=100.0)
    assert [row["id"] for row in store.unexported_tasks()] == [second]

    store.mark_tasks_exported([first, second], when=200.0)  # first уже помечена
    tasks = {row["id"]: row for row in store.tasks_of(conversation)}
    assert tasks[first]["exported_at"] == 100.0  # время первой выгрузки не сдвинуто
    assert tasks[second]["exported_at"] == 200.0
    assert store.unexported_tasks() == []


# ---------- миграция v2 → v3 (facts Said_at) ----------


def _create_v2_database(path) -> None:
    """База схемы версии 2: с reject_reason и exported_at, но facts без said_at."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE conversations (
            id             INTEGER PRIMARY KEY,
            uid            TEXT    NOT NULL,
            started_at     REAL    NOT NULL,
            ended_at       REAL    NOT NULL,
            speech_seconds REAL    NOT NULL DEFAULT 0,
            summary        TEXT,
            status         TEXT    NOT NULL DEFAULT 'open',
            created_at     REAL    NOT NULL
        );
        CREATE TABLE facts (
            id              INTEGER PRIMARY KEY,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            text            TEXT    NOT NULL,
            created_at      REAL    NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '2')")
    conn.execute(
        """INSERT INTO conversations(uid, started_at, ended_at, status, created_at)
           VALUES ('dev-1', 100.0, 110.0, 'closed', 1.0)"""
    )
    conn.execute(
        """INSERT INTO facts(conversation_id, text, created_at)
           VALUES (1, 'старый факт без времени', 1.0)"""
    )
    conn.commit()
    conn.close()


def test_migration_upgrades_v2_database_and_keeps_data(tmp_path):
    """База версии 2 поднимается до 4: колонка said_at появилась, факт цел."""
    path = tmp_path / "uzel.sqlite"
    _create_v2_database(path)

    with Storage(path) as store:
        assert store.schema_version == 4
        rows = store.fact_rows_of(1)
        assert [row["text"] for row in rows] == ["старый факт без времени"]
        assert rows[0]["said_at"] is None

        # Новая функциональность работает сразу после миграции.
        store.add_fact(1, "новый факт", said_at=123.5)
        assert store.fact_rows_of(1)[1]["said_at"] == 123.5


def test_migration_v2_repeated_open_does_not_fail(tmp_path):
    """Повторное открытие мигрированной базы версии 2 не падает (идемпотентность)."""
    path = tmp_path / "uzel.sqlite"
    _create_v2_database(path)

    with Storage(path) as first:
        first.add_fact(1, "факт после первой миграции", said_at=7.0)

    with Storage(path) as second:
        assert second.schema_version == 4
        texts = [row["text"] for row in second.fact_rows_of(1)]
        assert texts == ["старый факт без времени", "факт после первой миграции"]


# ---------- факты с таймкодом, темы, люди ----------


def test_add_fact_with_and_without_said_at_and_fact_rows_of_order(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    store.add_fact(conversation, "без времени")
    store.add_fact(conversation, "со временем", said_at=1758000000.0)

    rows = store.fact_rows_of(conversation)
    assert [row["text"] for row in rows] == ["без времени", "со временем"]
    assert rows[0]["said_at"] is None
    assert rows[1]["said_at"] == 1758000000.0


def test_tags_of_returns_unique_kinds_in_insertion_order(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    store.add_tag("закупки", conversation_id=conversation)
    store.add_tag("сроки", conversation_id=conversation)
    store.add_tag(
        "закупки", conversation_id=conversation
    )  # дубль от повторного разбора

    assert store.tags_of(conversation) == ["закупки", "сроки"]


def test_add_tag_same_kind_and_conversation_returns_existing_id(store):
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)

    first = store.add_tag("тема", conversation_id=conversation)
    second = store.add_tag("тема", conversation_id=conversation)

    assert first == second
    assert store.stats()["tags"] == 1


def test_add_tag_without_conversation_still_duplicates(store):
    """Репличные метки (без разговора) пишутся как раньше — дубли не схлопываются."""
    utterance = store.add_utterance(
        uid="dev-1", started_at=1.0, ended_at=2.0, speech_seconds=1.0
    )

    store.add_tag("mark", utterance_id=utterance)
    store.add_tag("mark", utterance_id=utterance)

    assert store.stats()["tags"] == 2


def test_find_person_returns_id_or_none(store):
    person = store.upsert_person("Иван")

    assert store.find_person("Иван") == person
    assert store.find_person("незнакомец") is None
    assert store.find_person("иван") is None  # сравнение точное


# ---------- внешние идентификаторы (P8) ----------


def test_utterance_external_id_roundtrip(store):
    external = "omi:sess-1:0.00:A"
    utterance = store.add_utterance(
        uid="dev-1",
        started_at=1.0,
        ended_at=2.0,
        speech_seconds=1.0,
        external_id=external,
    )

    assert store.get_utterance(utterance).external_id == external
    assert store.find_utterance_by_external(external).id == utterance
    assert store.find_utterance_by_external("нет-такого") is None


def test_conversation_external_id_lookup_and_unique_index(store):
    cid = store.open_conversation(
        uid="dev-1", started_at=100.0, external_id="omi:conv:1"
    )

    assert store.find_conversation_by_external("omi:conv:1") == cid
    assert store.find_conversation_by_external("omi:conv:2") is None
    with pytest.raises(sqlite3.IntegrityError):
        store.open_conversation(uid="dev-1", started_at=200.0, external_id="omi:conv:1")
    assert store.stats()["conversations"] == 1


def test_utterance_external_id_is_unique(store):
    store.add_utterance(
        uid="dev-1",
        started_at=1.0,
        ended_at=2.0,
        speech_seconds=1.0,
        external_id="omi:sess-1:0.00:A",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.add_utterance(
            uid="dev-1",
            started_at=3.0,
            ended_at=4.0,
            speech_seconds=1.0,
            external_id="omi:sess-1:0.00:A",
        )


# ---------- приватные реплики не покидают базу по умолчанию (P11.2) ----------


def test_utterances_of_hides_private_by_default(store):
    """P11.2, находка 5: транскрипт разговора по умолчанию без приватных."""
    conversation = store.open_conversation(uid="dev-1", started_at=100.0)
    public = _transcribed(store, started_at=100.0)
    private = store.add_utterance(
        uid="dev-1",
        started_at=110.0,
        ended_at=114.0,
        speech_seconds=3.0,
        is_private=True,
    )
    store.set_transcript(
        private, text="приватный текст", language="ru", backend="local"
    )
    store.attach_utterances(conversation, [public, private])

    assert [row.id for row in store.utterances_of(conversation)] == [public]
    assert [
        row.id for row in store.utterances_of(conversation, include_private=True)
    ] == [public, private]
    # адресный доступ не ограничен: вызывающие проверяют флаг сами
    assert store.get_utterance(private).text == "приватный текст"


def test_search_text_hides_private_by_default(store):
    """P11.2, находка 5: поиск — канал наружу, приватное по умолчанию не выдаётся."""
    public = _transcribed(store, text="заявка ООО «Ромашка» на август", started_at=100.0)
    private = store.add_utterance(
        uid="dev-1",
        started_at=105.0,
        ended_at=109.0,
        speech_seconds=3.0,
        is_private=True,
    )
    store.set_transcript(
        private, text="заявка ООО «Ромашка» приватная", language="ru", backend="local"
    )

    assert [row.id for row in store.search_text("заявка")] == [public]
    assert [row.id for row in store.search_text("заявка", include_private=True)] == [
        private,
        public,
    ]  # новая раньше: порядок по времени убывания
