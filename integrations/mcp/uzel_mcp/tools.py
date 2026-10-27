"""Инструменты MCP поверх ядра УЗЛА — только чтение.

Хранилище открывается на каждый вызов: долгоживущее соединение SQLite в чужом
процессе (Claude Code) не нужно. Ни один инструмент не пишет в базу; ошибки
ядра (нет Qdrant, нет модели) ловятся и возвращаются строкой, а не исключением.
Ответы — на языке интерфейса (каталоги ``mcp.*`` и общие ``cli.*``).
"""

from __future__ import annotations

import datetime as dt

from uzel.config import Settings
from uzel.i18n import t
from uzel.storage import Storage

from .protocol import InvalidParams


def _fmt_duration(seconds: float) -> str:
    if seconds >= 60:
        return t("unit.minutes", value=f"{seconds / 60:.0f}")
    return t("unit.seconds", value=f"{seconds:.0f}")


def _speaker_name(storage: Storage, utterance_id: int) -> str:
    with storage.transaction() as conn:
        row = conn.execute(
            """SELECT p.name AS name FROM utterances u
               LEFT JOIN people p ON p.id = u.speaker_id WHERE u.id=?""",
            (utterance_id,),
        ).fetchone()
    return row["name"] if row and row["name"] else t("cli.speaker_unknown")


def _participants(storage: Storage, conversation_id: int) -> list[str]:
    with storage.transaction() as conn:
        rows = conn.execute(
            """SELECT DISTINCT p.name FROM people p
               JOIN utterances u ON u.speaker_id = p.id
               WHERE u.conversation_id=? ORDER BY p.name""",
            (conversation_id,),
        ).fetchall()
    return [row["name"] for row in rows]


def _hidden_note(n: int) -> str:
    return t("mcp.hidden", n=n)


class UzelTools:
    """Семь инструментов памяти УЗЛА для Claude Code.

    Приватные реплики наружу не отдаются вовсе (P11, находка 4): MCP —
    канал облачной модели, и «скрыть текст, оставив метаданные» было бы
    той же утечкой, что отдать его целиком. Вместо реплики — честная
    строка о числе скрытых.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---------- служебное ----------

    def _parse_day(self, value: str | None) -> dt.date:
        """``None`` — сегодня, иначе ГГГГ-ММ-ДД. Остальное — InvalidParams."""
        if value is None:
            return dt.date.today()
        try:
            return dt.datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise InvalidParams(t("api.err.bad_date", value=repr(value))) from exc

    @staticmethod
    def _day_bounds(day: dt.date) -> tuple[float, float]:
        """Локальные сутки как полуинтервал ``[start, end)``."""
        start = dt.datetime.combine(day, dt.time.min).timestamp()
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp()
        return start, end

    # ---------- инструменты ----------

    def search(self, query: str, limit: int = 10, semantic: bool = True) -> str:
        """Смысловой поиск с откатом на подстрочный (как ``uzel search``)."""
        if not (query or "").strip():
            raise InvalidParams(t("api.err.empty_query"))

        with Storage(self.settings.db_path) as storage:
            rows: list[tuple[float | None, object]] = []

            if semantic:
                try:
                    from uzel.vector import build_index

                    index = build_index(self.settings)
                    for hit in index.search(query, limit=limit):
                        row = storage.get_utterance(int(hit["id"]))
                        if row is not None:
                            rows.append((hit.get("score"), row))
                except Exception:
                    rows = []  # смысловой поиск недоступен — откат ниже

            if rows:
                origin = t("api.search.semantic")
            else:
                # Для подсчёта скрытых берём всё, включая приватные: умолчание
                # хранилища их прячет, а «скрыто N» обязано остаться честным
                # (P11.2). Наружу фильтр ниже отдаёт только видимое.
                rows = [
                    (None, row)
                    for row in storage.search_text(
                        query, limit=limit, include_private=True
                    )
                ]
                origin = t("api.search.substring")

            if not rows:
                return t("cli.nothing_found")

            visible = [pair for pair in rows if not pair[1].is_private]
            hidden = len(rows) - len(visible)
            if not visible:
                if hidden:
                    return t("mcp.search.all_hidden", n=hidden)
                return t("cli.nothing_found")

            lines = [t("mcp.search.found", origin=origin)]
            for score, row in visible:
                stamp = dt.datetime.fromtimestamp(row.started_at).strftime(
                    t("format.datetime")
                )
                conversation = f"#{row.conversation_id}" if row.conversation_id else "—"
                speaker = _speaker_name(storage, row.id)
                mark = f" score={score:.2f}" if score is not None else ""
                text = (row.text or "").replace("\n", " ")
                lines.append(f"{conversation} [{stamp}] {speaker}: {text}{mark}")
            if hidden:
                lines.append(f"({_hidden_note(hidden)})")
            return "\n".join(lines)

    def conversation(self, id: int, with_utterances: bool = False) -> str:
        """Разговор: шапка, саммари, факты, задачи, темы, участники.

        Дословные реплики — только по явному ``with_utterances=True``: ответ
        не должен раздуваться на длинном разговоре.
        """
        with Storage(self.settings.db_path) as storage:
            record = storage.get_conversation(id)
            if record is None:
                return t("cli.show.missing", id=id)

            started = dt.datetime.fromtimestamp(record["started_at"])
            ended = dt.datetime.fromtimestamp(record["ended_at"])
            duration = record["ended_at"] - record["started_at"]
            lines = [
                t(
                    "cli.show.header",
                    id=record["id"],
                    date=started.strftime(t("format.date")),
                )
            ]
            lines.append(
                t(
                    "cli.show.span",
                    start=f"{started:%H:%M}",
                    end=f"{ended:%H:%M}",
                    duration=_fmt_duration(duration),
                    status=record["status"],
                )
            )
            if record["summary"]:
                lines.append("")
                lines.append(record["summary"])

            facts = storage.fact_rows_of(id)
            if facts:
                lines += ["", t("cli.show.facts")]
                for row in facts:
                    mark = (
                        f"[{dt.datetime.fromtimestamp(row['said_at']):%H:%M}] "
                        if row["said_at"] is not None
                        else ""
                    )
                    lines.append(f"  {mark}{row['text']}")

            tasks = storage.tasks_of(id)
            if tasks:
                lines += ["", t("cli.show.tasks")]
                for row in tasks:
                    parts = [row["text"]]
                    if row["assignee"]:
                        parts.append(f"({row['assignee']})")
                    if row["due"]:
                        parts.append(t("export.task.due", due=row["due"]))
                    lines.append("  " + " ".join(parts))

            tags = storage.tags_of(id)
            if tags:
                lines += ["", t("mcp.topics", topics=", ".join(tags))]

            participants = _participants(storage, id)
            if participants:
                lines += ["", t("mcp.participants", names=", ".join(participants))]

            if with_utterances:
                # Все реплики — ради честного «скрыто N»; наружу уходит
                # только видимое (фильтр ниже).
                rows = [
                    row
                    for row in storage.utterances_of(id, include_private=True)
                    if (row.text or "").strip()
                ]
                visible = [row for row in rows if not row.is_private]
                hidden = len(rows) - len(visible)
                if visible or hidden:
                    lines += ["", t("cli.show.utterances")]
                    for row in visible:
                        lines.append(
                            f"  [{dt.datetime.fromtimestamp(row.started_at):%H:%M}]"
                            f" {row.text}"
                        )
                    if hidden:
                        lines.append(f"  ({_hidden_note(hidden)})")
            return "\n".join(lines)

    def day(self, date: str | None = None) -> str:
        """Сводка суток: разговоры с интервалами и счётчиками, итог дня."""
        day = self._parse_day(date)
        start, end = self._day_bounds(day)

        with Storage(self.settings.db_path) as storage:
            rows = storage.conversations_between(start, end)
            if not rows:
                return t("mcp.day.none", date=day.isoformat())

            lines = [t("mcp.day.title", date=day.isoformat())]
            total_facts = total_tasks = total_utterances = 0
            for row in rows:
                started = dt.datetime.fromtimestamp(row["started_at"])
                ended = dt.datetime.fromtimestamp(row["ended_at"])
                facts = len(storage.fact_rows_of(row["id"]))
                tasks = len(storage.tasks_of(row["id"]))
                utterances = len(storage.utterances_of(row["id"]))
                total_facts += facts
                total_tasks += tasks
                total_utterances += utterances
                lines.append(
                    t(
                        "mcp.day.row",
                        id=row["id"],
                        start=f"{started:%H:%M}",
                        end=f"{ended:%H:%M}",
                        status=row["status"],
                        facts=facts,
                        tasks=tasks,
                    )
                )
                if row["summary"]:
                    first = (row["summary"] or "").splitlines()[0]
                    if first.strip():
                        lines.append(f"    {first}")

            lines += [
                "",
                t(
                    "mcp.day.total",
                    conversations=len(rows),
                    facts=total_facts,
                    tasks=total_tasks,
                    utterances=total_utterances,
                ),
            ]
            return "\n".join(lines)

    def facts(self, date: str | None = None, days: int = 1, limit: int = 50) -> str:
        """Факты за период: от начала суток ``date`` минус ``days - 1`` суток
        до конца суток ``date``."""
        if days < 1:
            raise InvalidParams(t("mcp.err.days"))

        day = self._parse_day(date)
        _, end = self._day_bounds(day)
        start = end - days * 86_400

        with Storage(self.settings.db_path) as storage:
            rows = storage.facts_between(start, end, limit=limit)
            if not rows:
                return t("mcp.facts.none")

            lines = []
            for row in rows:
                stamp = (
                    "["
                    + dt.datetime.fromtimestamp(row["said_at"]).strftime(
                        t("format.day_month_time")
                    )
                    + "] "
                    if row["said_at"] is not None
                    else ""
                )
                source = t("mcp.facts.source", id=row["conversation_id"])
                lines.append(f"{stamp}{row['text']}  {source}")
            return "\n".join(lines)

    def tasks(
        self, status: str = "open", date: str | None = None, limit: int = 50
    ) -> str:
        """Задачи: по статусу и, при заданном дне, только этого дня."""
        if status not in {"open", "done", "all"}:
            raise InvalidParams(t("mcp.err.status"))

        status_filter = None if status == "all" else status
        with Storage(self.settings.db_path) as storage:
            if date is not None:
                start, end = self._day_bounds(self._parse_day(date))
                rows = storage.tasks_between(
                    start, end, status=status_filter, limit=limit
                )
            else:
                rows = storage.tasks_between(status=status_filter, limit=limit)

            if not rows:
                return t("mcp.tasks.none")

            lines = []
            for row in rows:
                mark = "[x]" if row["status"] == "done" else "[ ]"
                parts = [mark, row["text"]]
                if row["assignee"]:
                    parts.append(f"({row['assignee']})")
                if row["due"]:
                    parts.append(t("mcp.tasks.due", due=row["due"]))
                started = row["conversation_started_at"]
                if row["conversation_id"] is not None and started is not None:
                    stamp = dt.datetime.fromtimestamp(started).strftime(
                        t("format.day_month_time")
                    )
                    parts.append(
                        t("mcp.tasks.source", id=row["conversation_id"], stamp=stamp)
                    )
                if row["exported_at"] is not None:
                    parts.append(t("mcp.tasks.exported"))
                lines.append(" ".join(parts))
            return "\n".join(lines)

    def stats(self) -> str:
        """Состояние памяти: счётчики, очередь приёма, размер индекса."""
        settings = self.settings
        with Storage(settings.db_path) as storage:
            counts = storage.stats()
            pending = len(storage.pending_utterances(limit=10_000))
            schema_version = storage.schema_version

        from uzel.queue import DiskQueue

        queue_len = DiskQueue(settings.queue_dir).pending_count()

        lines = [t("mcp.stats.db", path=settings.db_path, schema=schema_version)]
        for key in ("conversations", "utterances", "facts", "tasks", "people", "tags"):
            lines.append(f"  {t(f'cli.stats.{key}'):12s} {counts[key]}")
        lines.append(f"  {t('mcp.stats.pending'):12s} {pending}")
        lines.append(t("cli.stats.queue", n=queue_len))

        try:
            from uzel.vector import build_index

            lines.append(t("mcp.stats.index", n=build_index(settings).count()))
        except Exception as exc:
            lines.append(t("mcp.stats.index_down", error=exc))
        return "\n".join(lines)

    def privacy(self) -> str:
        """Статус записи и куда уходят данные. ``available()`` не дёргаем:
        это секунды ожидания и поход в сеть, инструмент обязан отвечать мгновенно."""
        from uzel.privacy import build_gate

        gate = build_gate(self.settings)
        lines = []
        for key, value in gate.state().as_dict().items():
            if value is not None:
                lines.append(f"{key}: {value}")
        if gate.windows:
            windows = "; ".join(window.describe() for window in gate.windows)
            lines.append(t("cli.privacy.windows", windows=windows))

        lines += ["", t("mcp.privacy.where")]
        cloud: list[str] = []
        # Облачность берётся у самого бэкенда, а не по списку известных имён:
        # на таком списке в P5 и прогорел агент-шлюз — локальный порт, облачный счёт.
        try:
            from uzel.stt.router import build_router

            for backend in build_router(self.settings).backends:
                kind = t("cli.llm.cloud") if backend.is_cloud else t("cli.llm.local")
                lines.append(t("mcp.privacy.stt", name=backend.name, kind=kind))
                if backend.is_cloud:
                    cloud.append(backend.name)
        except Exception as exc:
            lines.append(t("mcp.privacy.stt_failed", error=exc))
        try:
            from uzel.llm import build_llm_router

            for backend in build_llm_router(self.settings).backends:
                kind = t("cli.llm.cloud") if backend.is_cloud else t("cli.llm.local")
                lines.append(t("mcp.privacy.llm", name=backend.name, kind=kind))
                if backend.is_cloud:
                    cloud.append(backend.name)
        except Exception as exc:
            lines.append(t("mcp.privacy.llm_failed", error=exc))
        # P11.2, находка 13: из фактического состояния, а не константой.
        from uzel.privacy import cloud_policy_line

        lines.append(cloud_policy_line(cloud))
        return "\n".join(lines)
