"""Хранилище: SQLite (FR-040), TTL на сырое аудио (FR-042), бэкап (FR-043).

Одна файловая база — бэкап делается копированием файла, никакого Firestore.
Векторный поиск живёт отдельно, в ``vector.py``: разные способы отказа не должны
ронять друг друга.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id             INTEGER PRIMARY KEY,
    uid            TEXT    NOT NULL,
    started_at     REAL    NOT NULL,
    ended_at       REAL    NOT NULL,
    speech_seconds REAL    NOT NULL DEFAULT 0,
    summary        TEXT,
    status         TEXT    NOT NULL DEFAULT 'open',
    created_at     REAL    NOT NULL,
    external_id    TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE,
    voice_ref  TEXT,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS utterances (
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
    reject_reason   TEXT,
    created_at      REAL    NOT NULL,
    external_id     TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    text            TEXT    NOT NULL,
    said_at         REAL,
    created_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    text            TEXT    NOT NULL,
    assignee        TEXT,
    due             TEXT,
    status          TEXT    NOT NULL DEFAULT 'open',
    exported_at     REAL,
    created_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY,
    kind            TEXT    NOT NULL,
    utterance_id    INTEGER REFERENCES utterances(id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    created_at      REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_utterances_started  ON utterances(started_at);
CREATE INDEX IF NOT EXISTS idx_utterances_status   ON utterances(status);
CREATE INDEX IF NOT EXISTS idx_utterances_conv     ON utterances(conversation_id);
CREATE INDEX IF NOT EXISTS idx_utterances_audio    ON utterances(audio_path);
CREATE INDEX IF NOT EXISTS idx_conversations_start ON conversations(started_at);
CREATE INDEX IF NOT EXISTS idx_conversations_state ON conversations(status);
"""


@dataclass(frozen=True)
class UtteranceRow:
    id: int
    conversation_id: int | None
    uid: str
    started_at: float
    ended_at: float
    speech_seconds: float
    text: str | None
    language: str | None
    stt_backend: str | None
    status: str
    is_private: bool
    audio_path: str | None
    reject_reason: str | None = None
    external_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> UtteranceRow:
        return cls(
            id=row["id"],
            conversation_id=row["conversation_id"],
            uid=row["uid"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            speech_seconds=row["speech_seconds"],
            text=row["text"],
            language=row["language"],
            stt_backend=row["stt_backend"],
            status=row["status"],
            is_private=bool(row["is_private"]),
            audio_path=row["audio_path"],
            # Старые курсоры выбирают явные поля без этой колонки.
            reject_reason=(
                row["reject_reason"] if "reject_reason" in row.keys() else None
            ),
            external_id=(row["external_id"] if "external_id" in row.keys() else None),
        )


class Storage:
    """Тонкая обёртка над SQLite. Схема создаётся при первом обращении."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ---------- служебное ----------

    def _has_column(self, table: str, column: str) -> bool:
        """Есть ли колонка — по PRAGMA table_info, а не по перехвату исключения."""
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _ensure_column(self, table: str, column: str, declaration: str) -> bool:
        """Добавляет колонку, если её нет. Возвращает True, если добавили.

        ``ALTER TABLE ADD COLUMN`` не идемпотентен — второй запуск падает
        ``duplicate column name``, поэтому сначала смотрим PRAGMA.
        """
        if self._has_column(table, column):
            return False
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        return True

    def _migrate(self) -> None:
        with self._conn:
            self._conn.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS не добавляет колонки в существующую
            # таблицу — достраиваем вручную (миграции v1 → v2 и v2 → v3).
            added = self._ensure_column("utterances", "reject_reason", "TEXT")
            added = self._ensure_column("tasks", "exported_at", "REAL") or added
            added = self._ensure_column("facts", "said_at", "REAL") or added
            # Миграция v3 → v4 (P8): внешние идентификаторы вебхуков Omi —
            # без них повторный вебхук продублирует реплику или разговор.
            added = self._ensure_column("conversations", "external_id", "TEXT") or added
            added = self._ensure_column("utterances", "external_id", "TEXT") or added
            # Индексы по причинам и внешним ключам строим здесь, а не в SCHEMA:
            # на базе версии 1 колонок ещё нет, и CREATE INDEX внутри SCHEMA
            # падал бы. Частичный UNIQUE не мешает репликам без внешнего id.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_utterances_reject"
                " ON utterances(reject_reason)"
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_external"
                " ON conversations(external_id) WHERE external_id IS NOT NULL"
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_utterances_external"
                " ON utterances(external_id) WHERE external_id IS NOT NULL"
            )
            # INSERT OR IGNORE не перезапишет '1' в базе версии 1 —
            # версия обязана стать актуальной.
            self._conn.execute(
                """INSERT INTO meta(key, value) VALUES ('schema_version', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(SCHEMA_VERSION),),
            )
        if added:
            log.info("схема обновлена до версии %d", SCHEMA_VERSION)

    @property
    def schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---------- реплики ----------

    def add_utterance(
        self,
        *,
        uid: str,
        started_at: float,
        ended_at: float,
        speech_seconds: float,
        conversation_id: int | None = None,
        is_private: bool = False,
        audio_path: str | None = None,
        external_id: str | None = None,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO utterances
                   (conversation_id, uid, started_at, ended_at, speech_seconds,
                    status, is_private, audio_path, created_at, external_id)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    conversation_id,
                    uid,
                    started_at,
                    ended_at,
                    speech_seconds,
                    int(is_private),
                    audio_path,
                    time.time(),
                    external_id,
                ),
            )
        return int(cursor.lastrowid)

    def set_transcript(
        self, utterance_id: int, *, text: str, language: str, backend: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE utterances
                   SET text=?, language=?, stt_backend=?, status='transcribed'
                   WHERE id=?""",
                (text, language, backend, utterance_id),
            )

    def stitch_utterances(
        self,
        keep_id: int,
        drop_ids: Sequence[int],
        *,
        ended_at: float,
        speech_seconds: float,
        audio_path: str,
    ) -> None:
        """Склейка реплик до расшифровки: ``keep_id`` получает общие границы
        и звук, ``drop_ids`` удаляются. Одной транзакцией — без полусклеек."""
        with self._conn:
            self._conn.execute(
                """UPDATE utterances
                   SET ended_at=?, speech_seconds=?, audio_path=?, status='pending'
                   WHERE id=?""",
                (ended_at, speech_seconds, audio_path, keep_id),
            )
            self._conn.executemany(
                "DELETE FROM utterances WHERE id=?", [(i,) for i in drop_ids]
            )

    def mark_failed(self, utterance_id: int) -> None:
        """Расшифровка не удалась. Реплика остаётся в базе (FR-022, FR-032)."""
        with self._conn:
            self._conn.execute(
                "UPDATE utterances SET status='failed' WHERE id=?", (utterance_id,)
            )

    def mark_quality(
        self, utterance_id: int, *, reason: str | None, enforce: bool
    ) -> None:
        """Проставляет вердикт фильтра качества.

        ``reason=None`` — реплика принята: причина очищается, статус не трогаем.
        ``reason`` задан и ``enforce=False`` — только помечаем (FR-022: не теряем).
        ``reason`` задан и ``enforce=True`` — статус становится ``'rejected'``.
        """
        if reason is None:
            with self._conn:
                self._conn.execute(
                    "UPDATE utterances SET reject_reason=NULL WHERE id=?",
                    (utterance_id,),
                )
            return
        new_status = ", status='rejected'" if enforce else ""
        with self._conn:
            self._conn.execute(
                f"UPDATE utterances SET reject_reason=?{new_status} WHERE id=?",
                (reason, utterance_id),
            )

    def quality_counts(
        self, *, start: float | None = None, end: float | None = None
    ) -> dict:
        """Сводка качества за период (полуинтервал ``[start, end)``).

        Возвращает ``{"total": N, "accepted": N, "rejected": N, "flagged": N,
                      "by_reason": {код: N, ...}}``:
        ``rejected`` — со статусом 'rejected', ``flagged`` — с причиной, но
        пропущенные (режим пометки), ``accepted`` — без причины и не 'rejected'.
        """
        period, params = [], []
        if start is not None:
            period.append("started_at >= ?")
            params.append(start)
        if end is not None:
            period.append("started_at < ?")
            params.append(end)
        suffix = f" AND {' AND '.join(period)}" if period else ""

        row = self._conn.execute(
            f"""SELECT COUNT(*) AS total,
                       COALESCE(SUM(status = 'rejected'), 0) AS rejected,
                       COALESCE(SUM(reject_reason IS NOT NULL AND status != 'rejected'), 0) AS flagged,
                       COALESCE(SUM(reject_reason IS NULL AND status != 'rejected'), 0) AS accepted
                FROM utterances WHERE 1=1{suffix}""",
            params,
        ).fetchone()
        reasons = self._conn.execute(
            f"""SELECT reject_reason AS reason, COUNT(*) AS n
                FROM utterances WHERE reject_reason IS NOT NULL{suffix}
                GROUP BY reject_reason""",
            params,
        ).fetchall()
        return {
            "total": int(row["total"]),
            "accepted": int(row["accepted"]),
            "rejected": int(row["rejected"]),
            "flagged": int(row["flagged"]),
            "by_reason": {r["reason"]: int(r["n"]) for r in reasons},
        }

    def rejected_utterances(
        self, *, limit: int = 100, start: float | None = None, end: float | None = None
    ) -> list[UtteranceRow]:
        """Реплики с проставленной причиной — для калибровки порогов."""
        period, params = [], []
        if start is not None:
            period.append("started_at >= ?")
            params.append(start)
        if end is not None:
            period.append("started_at < ?")
            params.append(end)
        clause = f" AND {' AND '.join(period)}" if period else ""
        rows = self._conn.execute(
            f"""SELECT * FROM utterances
                WHERE reject_reason IS NOT NULL{clause}
                ORDER BY started_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [UtteranceRow.from_row(row) for row in rows]

    def get_utterance(self, utterance_id: int) -> UtteranceRow | None:
        row = self._conn.execute(
            "SELECT * FROM utterances WHERE id=?", (utterance_id,)
        ).fetchone()
        return UtteranceRow.from_row(row) if row else None

    def pending_utterances(
        self, limit: int = 100, *, newest_first: bool = False
    ) -> list[UtteranceRow]:
        """Очередь на расшифровку.

        ``newest_first=True`` отдаёт свежие раньше: утренняя сводка (FR-051)
        начинается с вчерашнего вечера, а хвост дня добирается следом.
        """
        order = "started_at DESC" if newest_first else "started_at"
        rows = self._conn.execute(
            f"""SELECT * FROM utterances WHERE status IN ('pending', 'failed')
                ORDER BY {order} LIMIT ?""",
            (limit,),
        ).fetchall()
        return [UtteranceRow.from_row(row) for row in rows]

    def find_utterance(
        self, *, uid: str, started_at: float, ended_at: float
    ) -> UtteranceRow | None:
        """Точный поиск по тройке ``uid + границы``.

        Защита воркера от повторной обработки того же фрагмента: время реплики
        вычисляется детерминированно из метаданных, поэтому тройка совпадает
        бит в бит.
        """
        row = self._conn.execute(
            """SELECT * FROM utterances
               WHERE uid=? AND started_at=? AND ended_at=?""",
            (uid, started_at, ended_at),
        ).fetchone()
        return UtteranceRow.from_row(row) if row else None

    def find_utterance_by_external(self, external_id: str) -> UtteranceRow | None:
        """Реплика по внешнему идентификатору вебхука (P8) — защита от дублей."""
        row = self._conn.execute(
            "SELECT * FROM utterances WHERE external_id=?", (external_id,)
        ).fetchone()
        return UtteranceRow.from_row(row) if row else None

    def ungrouped_utterances(self, *, limit: int = 1000) -> list[UtteranceRow]:
        """Расшифрованные реплики без разговора.

        Строго по возрастанию времени: группировка обязана идти хронологически,
        иначе один разговор разрезается надвое.
        """
        rows = self._conn.execute(
            """SELECT * FROM utterances
               WHERE status='transcribed' AND conversation_id IS NULL
               ORDER BY started_at LIMIT ?""",
            (limit,),
        ).fetchall()
        return [UtteranceRow.from_row(row) for row in rows]

    def utterances_of(
        self,
        conversation_id: int,
        *,
        include_rejected: bool = False,
        include_private: bool = False,
    ) -> list[UtteranceRow]:
        """Транскрипт разговора. Отклонённые и приватные не попадают, пока
        не попросят (P11.2, находка 5: приватный текст не выходит из базы)."""
        conditions = []
        if not include_rejected:
            conditions.append("status != 'rejected'")
        if not include_private:
            conditions.append("is_private = 0")
        clause = f" AND {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"""SELECT * FROM utterances
                WHERE conversation_id=?{clause} ORDER BY started_at""",
            (conversation_id,),
        ).fetchall()
        return [UtteranceRow.from_row(row) for row in rows]

    # ---------- разговоры ----------

    def open_conversation(
        self, *, uid: str, started_at: float, external_id: str | None = None
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO conversations
                   (uid, started_at, ended_at, status, created_at, external_id)
                   VALUES (?, ?, ?, 'open', ?, ?)""",
                (uid, started_at, started_at, time.time(), external_id),
            )
        return int(cursor.lastrowid)

    def attach_utterances(self, conversation_id: int, ids: Sequence[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        with self._conn:
            self._conn.execute(
                f"UPDATE utterances SET conversation_id=? WHERE id IN ({placeholders})",
                (conversation_id, *ids),
            )
            self._conn.execute(
                """UPDATE conversations SET
                     started_at = (SELECT MIN(started_at) FROM utterances WHERE conversation_id=?),
                     ended_at   = (SELECT MAX(ended_at)   FROM utterances WHERE conversation_id=?),
                     speech_seconds = (SELECT COALESCE(SUM(speech_seconds), 0)
                                       FROM utterances WHERE conversation_id=?)
                   WHERE id=?""",
                (conversation_id, conversation_id, conversation_id, conversation_id),
            )

    def close_conversation(
        self,
        conversation_id: int,
        *,
        summary: str | None = None,
        status: str = "closed",
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE conversations SET status=?, summary=? WHERE id=?",
                (status, summary, conversation_id),
            )

    def open_conversations(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM conversations WHERE status='open' ORDER BY started_at"
        ).fetchall()

    def conversations_with_status(
        self, status: str = "open", *, limit: int = 200
    ) -> list[sqlite3.Row]:
        """Разговоры с заданным статусом — воркеру для тактов ночного батча."""
        return self._conn.execute(
            "SELECT * FROM conversations WHERE status=? ORDER BY started_at LIMIT ?",
            (status, limit),
        ).fetchall()

    def conversations_between(self, start: float, end: float) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT * FROM conversations
               WHERE started_at >= ? AND started_at < ? ORDER BY started_at""",
            (start, end),
        ).fetchall()

    def get_conversation(self, conversation_id: int) -> sqlite3.Row | None:
        """Разговор по id — для ``uzel show`` и перевыгрузки суток."""
        return self._conn.execute(
            "SELECT * FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()

    def find_conversation_by_external(self, external_id: str) -> int | None:
        """id разговора по внешнему идентификатору вебхука (P8)."""
        row = self._conn.execute(
            "SELECT id FROM conversations WHERE external_id=?", (external_id,)
        ).fetchone()
        return int(row["id"]) if row else None

    def conversation_started_at(self, conversation_id: int) -> float | None:
        """Время начала разговора — источник для строки задачи в Obsidian."""
        row = self._conn.execute(
            "SELECT started_at FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        return float(row["started_at"]) if row else None

    # ---------- люди, факты, задачи, метки ----------

    def upsert_person(self, name: str, voice_ref: str | None = None) -> int:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO people(name, voice_ref, created_at) VALUES (?, ?, ?)",
                (name, voice_ref, time.time()),
            )
            if voice_ref is not None:
                self._conn.execute(
                    "UPDATE people SET voice_ref=? WHERE name=?", (voice_ref, name)
                )
        row = self._conn.execute(
            "SELECT id FROM people WHERE name=?", (name,)
        ).fetchone()
        return int(row["id"])

    def set_speaker(self, utterance_id: int, person_id: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE utterances SET speaker_id=? WHERE id=?",
                (person_id, utterance_id),
            )

    def add_fact(
        self, conversation_id: int, text: str, *, said_at: float | None = None
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO facts(conversation_id, text, said_at, created_at)
                   VALUES (?, ?, ?, ?)""",
                (conversation_id, text, said_at, time.time()),
            )
        return int(cursor.lastrowid)

    def add_task(
        self,
        conversation_id: int,
        text: str,
        *,
        assignee: str | None = None,
        due: str | None = None,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO tasks(conversation_id, text, assignee, due, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (conversation_id, text, assignee, due, time.time()),
            )
        return int(cursor.lastrowid)

    def add_tag(
        self,
        kind: str,
        *,
        utterance_id: int | None = None,
        conversation_id: int | None = None,
    ) -> int:
        # Идемпотентность по паре (kind, conversation_id): повторный разбор
        # разговора не должен умножать темы. Репличные метки (без разговора)
        # пишутся как раньше — у них дубли не исключены замыслом.
        if conversation_id is not None:
            row = self._conn.execute(
                """SELECT id FROM tags
                   WHERE kind=? AND conversation_id=? ORDER BY id LIMIT 1""",
                (kind, conversation_id),
            ).fetchone()
            if row is not None:
                return int(row["id"])
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO tags(kind, utterance_id, conversation_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (kind, utterance_id, conversation_id, time.time()),
            )
        return int(cursor.lastrowid)

    def facts_of(self, conversation_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT text FROM facts WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [row["text"] for row in rows]

    def fact_rows_of(self, conversation_id: int) -> list[sqlite3.Row]:
        """Полные строки фактов — с ``said_at`` для таймкода в заметке."""
        return self._conn.execute(
            "SELECT * FROM facts WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    def facts_between(
        self, start: float, end: float, *, limit: int = 200
    ) -> list[sqlite3.Row]:
        """Факты разговоров, начавшихся в ``[start, end)`` — для MCP.

        Порядок по моменту факта: ``said_at``, а если его нет — по началу
        разговора. ``conversation_started_at`` в выборке нужен той же строкой.
        """
        return self._conn.execute(
            """SELECT f.*, c.started_at AS conversation_started_at
               FROM facts f JOIN conversations c ON c.id = f.conversation_id
               WHERE c.started_at >= ? AND c.started_at < ?
               ORDER BY COALESCE(f.said_at, c.started_at) LIMIT ?""",
            (start, end, limit),
        ).fetchall()

    def tags_of(self, conversation_id: int) -> list[str]:
        """Темы разговора без дублей, в порядке первого появления."""
        rows = self._conn.execute(
            "SELECT kind FROM tags WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        seen: set[str] = set()
        kinds: list[str] = []
        for row in rows:
            kind = row["kind"]
            if kind not in seen:
                seen.add(kind)
                kinds.append(kind)
        return kinds

    def find_person(self, name: str) -> int | None:
        """id заведённого человека или ``None``. Сравнение точное."""
        row = self._conn.execute(
            "SELECT id FROM people WHERE name=?", (name,)
        ).fetchone()
        return int(row["id"]) if row else None

    def tasks_of(self, conversation_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM tasks WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    def unexported_tasks(self, limit: int = 200) -> list[sqlite3.Row]:
        """Задачи, ещё не выгруженные в Obsidian (``exported_at IS NULL``)."""
        return self._conn.execute(
            "SELECT * FROM tasks WHERE exported_at IS NULL ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()

    def tasks_between(
        self,
        start: float | None = None,
        end: float | None = None,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        """Задачи с необязательными границами по началу разговора и статусом.

        ``LEFT JOIN``: задача без разговора не должна пропадать из выборки.
        """
        clauses, params = [], []
        if start is not None:
            clauses.append("c.started_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("c.started_at < ?")
            params.append(end)
        if status is not None:
            clauses.append("t.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            f"""SELECT t.*, c.started_at AS conversation_started_at
                FROM tasks t LEFT JOIN conversations c ON c.id = t.conversation_id
                {where} ORDER BY t.id DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()

    def mark_tasks_exported(
        self, ids: Sequence[int], *, when: float | None = None
    ) -> None:
        """Помечает задачи выгруженными — дедуп живёт в базе, а не в тексте файла.

        Уже помеченные не перезаписываются: повторная выгрузка не сдвигает
        время первой.
        """
        if not ids:
            return
        moment = time.time() if when is None else when
        placeholders = ",".join("?" * len(ids))
        with self._conn:
            self._conn.execute(
                f"""UPDATE tasks SET exported_at=?
                    WHERE id IN ({placeholders}) AND exported_at IS NULL""",
                (moment, *ids),
            )

    # ---------- поиск, TTL, бэкап ----------

    def search_text(
        self, query: str, limit: int = 20, *, include_private: bool = False
    ) -> list[UtteranceRow]:
        """Подстрочный поиск. Смысловой — в ``vector.py``.

        Приватные реплики по умолчанию не выдаются (P11.2, находка 5):
        поиск — канал наружу, видит его не только владелец.
        """
        privacy_clause = "" if include_private else " AND is_private = 0"
        rows = self._conn.execute(
            f"""SELECT * FROM utterances
                WHERE text IS NOT NULL AND text LIKE ? ESCAPE '\\'{privacy_clause}
                ORDER BY started_at DESC LIMIT ?""",
            (f"%{_escape_like(query)}%", limit),
        ).fetchall()
        return [UtteranceRow.from_row(row) for row in rows]

    def purge_audio(self, ttl_days: int, *, now: float | None = None) -> int:
        """Удаляет сырое аудио старше TTL (FR-042). Транскрипт остаётся.

        Звук ещё не расшифрованных реплик (``pending``/``failed``) не трогается:
        с P11.2 очистка идёт в конце каждого батча, и батч, остановленный по
        пределу времени, иначе стёр бы звук, который не успел расшифровать.
        """
        if ttl_days <= 0:
            return 0
        cutoff = (time.time() if now is None else now) - ttl_days * 86_400
        rows = self._conn.execute(
            """SELECT id, audio_path FROM utterances
               WHERE audio_path IS NOT NULL AND started_at < ?
                 AND status NOT IN ('pending', 'failed')""",
            (cutoff,),
        ).fetchall()

        removed = 0
        for row in rows:
            path = Path(row["audio_path"])
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("не удалось удалить %s: %s", path, exc)
                continue
            with self._conn:
                self._conn.execute(
                    "UPDATE utterances SET audio_path=NULL WHERE id=?", (row["id"],)
                )
            removed += 1

        if removed:
            log.info("удалено сырых записей по TTL: %d", removed)
        return removed

    def backup(self, destination: Path | str) -> Path:
        """Согласованная копия базы (FR-043) — без остановки записи."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return destination

    def stats(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(
                self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            )

        return {
            "conversations": count("conversations"),
            "utterances": count("utterances"),
            "facts": count("facts"),
            "tasks": count("tasks"),
            "people": count("people"),
            "tags": count("tags"),
        }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
