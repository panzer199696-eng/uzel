"""Выгрузка в Obsidian (FR-050, FR-052, ТЗ 2026-08-24 пункт 4).

Заметка дня — саммари, факты с таймкодами и метрики; дословные реплики в неё
не попадают никогда (пункт 4.1 ТЗ): расшифровка живёт отдельным временным
файлом и пишется только по явному флагу. Задачи дописываются в общий список
с якорями ``^uzel-N``: настоящий дедуп — в базе (``tasks.exported_at``),
якорь лишь страхует от двойной записи, если воркер упал между записью в файл
и пометкой в базе.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Якорь блока Obsidian: ^uzel-<tasks.id>.
ANCHOR = re.compile(r"\^uzel-(\d+)")

# В теге Obsidian допустимы буквы, цифры, -, _ и /; прочее выбрасываем.
_TAG_FORBIDDEN = re.compile(r"[^\w\-/]+", re.UNICODE)

RAW_NOTE_SUFFIX = " — расшифровка.md"


@dataclass(frozen=True)
class FactView:
    """Факт с абсолютным временем реплики, откуда он взят (или без него)."""

    text: str
    said_at: float | None = None


@dataclass(frozen=True)
class ConversationView:
    """То, что попадает в заметки. Собирается вызывающим из хранилища.

    ``utterances`` нужен только файлу сырой расшифровки: в заметку дня
    дословные реплики не попадают никогда.
    """

    conversation_id: int
    started_at: float
    ended_at: float
    summary: str | None
    speech_seconds: float
    facts: list[FactView] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)  # только известные, фильтрует воркер
    utterances: list[tuple[float, str | None, str | None]] = field(default_factory=list)
    accepted: int = 0
    rejected: int = 0


def _hhmm(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f} мин" if seconds >= 60 else f"{seconds:.0f} с"


def normalise_tag(topic: str) -> str:
    """Тема → тег Obsidian: нижний регистр, пробелы — дефисы, мусор выброшен."""
    tag = re.sub(r"\s+", "-", topic.strip().lower())
    return _TAG_FORBIDDEN.sub("", tag)


def render_day(
    day: date,
    conversations: list[ConversationView],
    *,
    accepted: int = 0,
    rejected: int = 0,
    flagged: int = 0,
) -> str:
    """Заметка за сутки: саммари, факты, задачи, метрики — без единой реплики."""
    total_speech = sum(item.speech_seconds for item in conversations)

    tags: list[str] = []
    for item in conversations:
        for topic in item.topics:
            tag = normalise_tag(topic)
            if tag and tag not in tags:
                tags.append(tag)

    lines = [
        "---",
        f"дата: {day.isoformat()}",
        "источник: УЗЕЛ",
        "rag: false",
        f"разговоров: {len(conversations)}",
        f"речи_минут: {total_speech / 60:.0f}",
        f"принято: {accepted}",
        f"отклонено: {rejected}",
        # Помеченные фильтром, но пропущенные: пока quality_enforce=False
        # (ТЗ пакета 2, первые две недели) отклонённых не бывает вовсе, и без
        # этой строки работа фильтра в заметке не видна никак.
        f"помечено: {flagged}",
    ]
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines += [
        "---",
        "",
        f"# {day.strftime('%d.%m.%Y')} — день",
        "",
    ]

    if not conversations:
        lines += ["_За этот день записей нет._", ""]
        return "\n".join(lines)

    lines += [
        f"Разговоров: **{len(conversations)}**, речи всего: **{_minutes(total_speech)}**. "
        f"Реплик принято **{accepted}**, отклонено **{rejected}**.",
        "",
    ]

    for index, item in enumerate(conversations, start=1):
        lines += [
            f"## {index}. {_hhmm(item.started_at)}–{_hhmm(item.ended_at)} "
            f"({_minutes(item.speech_seconds)}) · разговор {item.conversation_id}",
            "",
        ]
        if item.summary:
            lines += [item.summary, ""]

        if item.people:
            names = ", ".join(f"[[{name}]]" for name in item.people)
            lines += [f"**Участники:** {names}", ""]

        if item.facts:
            lines += ["**Факты**", ""]
            for fact in item.facts:
                if fact.said_at is not None:
                    # Скобки — время, обратные кавычки — чтобы Obsidian не
                    # принял его за ссылку.
                    lines.append(f"- `[{_hhmm(fact.said_at)}]` {fact.text}")
                else:
                    lines.append(f"- {fact.text}")
            lines.append("")

        if item.tasks:
            # Чекбоксы живут только во входящем файле: Tasks не должен видеть
            # одну задачу дважды.
            lines += ["**Задачи** → [[Входящее — задачи]]", ""]
            for task in item.tasks:
                lines.append(f"- {_format_task(task)}")
            lines.append("")

        if item.topics:
            lines += [f"**Темы:** {', '.join(item.topics)}", ""]

        if item.rejected:
            # Счёт по разговору, а не по суткам: пороги калибруются на том,
            # в каком именно разговоре фильтр сработал.
            lines += [f"_Фильтр качества отсеял реплик: {item.rejected}._", ""]

    return "\n".join(lines)


def _format_task(task: dict) -> str:
    """Задача в заметке дня: только суть, без чекбокса, источника и якоря."""
    parts = [str(task.get("text") or "").strip()]
    assignee = task.get("assignee")
    due = task.get("due")
    if assignee:
        parts.append(f"({assignee})")
    if due:
        parts.append(f"— до {due}")
    return " ".join(part for part in parts if part)


def write_day_note(
    days_dir: Path | str,
    day: date,
    conversations: list[ConversationView],
    *,
    accepted: int = 0,
    rejected: int = 0,
    flagged: int = 0,
) -> Path:
    """Пишет заметку за день. Существующая перезаписывается целиком."""
    directory = Path(days_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()} — день.md"
    path.write_text(
        render_day(
            day,
            conversations,
            accepted=accepted,
            rejected=rejected,
            flagged=flagged,
        ),
        encoding="utf-8",
    )
    log.info("заметка дня записана: %s", path)
    return path


def render_transcript(
    day: date,
    conversations: list[ConversationView],
    *,
    ttl_days: int = 30,
) -> str:
    """Дословная расшифровка дня — временный файл под TTL."""
    lines = [
        "---",
        f"дата: {day.isoformat()}",
        "источник: УЗЕЛ",
        "rag: false",
        "временный: true",
        f"удалить_после: {(day + timedelta(days=ttl_days)).isoformat()}",
        "---",
        "",
    ]
    for item in conversations:
        lines += [
            f"## {_hhmm(item.started_at)}–{_hhmm(item.ended_at)} "
            f"· разговор {item.conversation_id}",
            "",
        ]
        for started, speaker, text in item.utterances:
            if not text:
                continue
            who = f"**{speaker}:** " if speaker else ""
            lines.append(f"- `{_hhmm(started)}` {who}{text}")
        lines.append("")
    return "\n".join(lines)


def write_transcript_note(
    raw_dir: Path | str,
    day: date,
    conversations: list[ConversationView],
    *,
    ttl_days: int = 30,
) -> Path:
    """Пишет файл сырой расшифровки. Существующий перезаписывается целиком."""
    directory = Path(raw_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}{RAW_NOTE_SUFFIX}"
    path.write_text(
        render_transcript(day, conversations, ttl_days=ttl_days), encoding="utf-8"
    )
    log.info("расшифровка записана: %s", path)
    return path


def render_task_line(task: dict) -> str:
    """Строка задачи во входящем списке (ТЗ 4.3): чекбокс, источник, якорь.

    ``(Пётр)`` — исполнитель, ``— до …`` — срок, ``· [10:42] разговор 17`` —
    источник. Нет времени — метка ``[ЧЧ:ММ]`` опускается, нет id разговора —
    весь хвост. ``^uzel-<id>`` обязателен: по нему ссылаются и он же защищает
    от двойной записи.
    """
    parts = [str(task.get("text") or "").strip()]
    assignee = task.get("assignee")
    due = task.get("due")
    if assignee:
        parts.append(f"({assignee})")
    if due:
        parts.append(f"— до {due}")

    conversation_id = task.get("conversation_id")
    if conversation_id is not None:
        started_at = task.get("conversation_started_at")
        stamp = f"[{_hhmm(float(started_at))}] " if started_at is not None else ""
        parts.append(f"· {stamp}разговор {conversation_id}")

    anchor = task.get("id")
    if anchor is None:
        raise ValueError(f"у задачи нет id — якорь ^uzel-N обязателен: {task!r}")
    return f"- [ ] {' '.join(parts)} ^uzel-{int(anchor)}"


def existing_anchors(path: Path | str) -> set[int]:
    """Id задач, уже записанных в файл как ``^uzel-N``."""
    path = Path(path)
    if not path.exists():
        return set()
    found: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        found.update(int(match) for match in ANCHOR.findall(line))
    return found


def append_tasks(
    path: Path | str, tasks: list[dict], *, heading: str = "## Из разговоров"
) -> int:
    """Дописывает задачи с якорями, пропуская уже записанные. Число новых.

    Дедуп — по якорю, а не по тексту: настоящий дедуп живёт в базе
    (``tasks.exported_at``). Строка, удалённая руками, вернётся при следующей
    выгрузке, только пока задача не помечена в базе, — и это правильно:
    защита в базе, а не в файле.
    """
    path = Path(path)
    known = existing_anchors(path)

    fresh: list[str] = []
    for task in tasks:
        if not str(task.get("text") or "").strip():
            continue
        if task.get("id") is None:
            log.error("у задачи нет id, якорь невозможен — пропущена: %r", task)
            continue
        anchor = int(task["id"])
        if anchor in known:
            continue
        known.add(anchor)
        fresh.append(render_task_line(task))

    if not fresh:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    block = "\n".join(fresh)

    if heading in current:
        # дописываем сразу под заголовком, чтобы новое не тонуло в конце файла
        head, _, tail = current.partition(heading)
        updated = f"{head}{heading}\n{block}\n{tail.lstrip(chr(10))}"
    else:
        separator = "" if not current or current.endswith("\n\n") else "\n"
        updated = f"{current}{separator}\n{heading}\n\n{block}\n"

    path.write_text(updated, encoding="utf-8")
    log.info("в список задач добавлено: %d", len(fresh))
    return len(fresh)


def expired_raw_notes(
    raw_dir: Path | str, *, ttl_days: int = 30, today: date | None = None
) -> list[Path]:
    """Просроченные заметки-расшифровки по дате в имени файла.

    Только находит: удаление — явная команда человека (``uzel prune-raw --yes``).
    """
    directory = Path(raw_dir)
    if not directory.exists():
        return []
    now = date.today() if today is None else today
    limit = now - timedelta(days=ttl_days)

    expired: list[Path] = []
    for path in sorted(directory.glob(f"*{RAW_NOTE_SUFFIX}")):
        stamp = path.name[: -len(RAW_NOTE_SUFFIX)]
        try:
            created = date.fromisoformat(stamp)
        except ValueError:
            continue  # чужой файл с похожим именем — не наше дело
        if created < limit:
            expired.append(path)
    return expired
