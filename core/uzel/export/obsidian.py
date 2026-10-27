"""Выгрузка в Obsidian (FR-050, FR-052, ТЗ 2026-08-24 пункт 4).

Заметка дня — саммари, факты с таймкодами и метрики; дословные реплики в неё
не попадают никогда (пункт 4.1 ТЗ): расшифровка живёт отдельным временным
файлом и пишется только по явному флагу. Задачи дописываются в общий список
с якорями ``^uzel-N``: настоящий дедуп — в базе (``tasks.exported_at``),
якорь лишь страхует от двойной записи, если воркер упал между записью в файл
и пометкой в базе.

Заголовки, подписи, ключи frontmatter, имена файлов и формат даты — на языке
интерфейса (``lang``, по умолчанию текущий; каталоги ``export.*``). Русский
вывод совпадает с прежним байт-в-байт.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import i18n
from ..i18n import t

log = logging.getLogger(__name__)

# Якорь блока Obsidian: ^uzel-<tasks.id>.
ANCHOR = re.compile(r"\^uzel-(\d+)")

# В теге Obsidian допустимы буквы, цифры, -, _ и /; прочее выбрасываем.
_TAG_FORBIDDEN = re.compile(r"[^\w\-/]+", re.UNICODE)

# Суффикс файла расшифровки — по языку; старое имя оставлено для совместимости.
RAW_NOTE_SUFFIX = " — расшифровка.md"


def raw_note_suffix(lang: str | None = None) -> str:
    return t("export.raw.suffix", lang=lang)


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


def _minutes(seconds: float, lang: str | None = None) -> str:
    if seconds >= 60:
        return t("unit.minutes", lang=lang, value=f"{seconds / 60:.0f}")
    return t("unit.seconds", lang=lang, value=f"{seconds:.0f}")


def _frontmatter(lang: str | None, *pairs: tuple[str, object]) -> list[str]:
    """Строки ``ключ: значение``; ключи с префиксом ``@`` — из каталога языка."""
    lines = []
    for key, value in pairs:
        name = t(f"export.fm.{key[1:]}", lang=lang) if key.startswith("@") else key
        lines.append(f"{name}: {value}")
    return lines


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
    lang: str | None = None,
) -> str:
    """Заметка за сутки: саммари, факты, задачи, метрики — без единой реплики."""
    lang = lang or i18n.get_language()
    total_speech = sum(item.speech_seconds for item in conversations)

    tags: list[str] = []
    for item in conversations:
        for topic in item.topics:
            tag = normalise_tag(topic)
            if tag and tag not in tags:
                tags.append(tag)

    lines = [
        "---",
        *_frontmatter(
            lang,
            ("@date", day.isoformat()),
            ("@source", t("export.fm.source_value", lang=lang)),
            ("rag", "false"),
            ("@conversations", len(conversations)),
            ("@speech_minutes", f"{total_speech / 60:.0f}"),
            ("@accepted", accepted),
            ("@rejected", rejected),
            # Помеченные фильтром, но пропущенные: пока quality_enforce=False
            # (ТЗ пакета 2, первые две недели) отклонённых не бывает вовсе, и без
            # этой строки работа фильтра в заметке не видна никак.
            ("@flagged", flagged),
        ),
    ]
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines += [
        "---",
        "",
        t("export.day.title", lang=lang, date=day.strftime(t("format.date", lang=lang))),
        "",
    ]

    if not conversations:
        lines += [t("export.day.empty", lang=lang), ""]
        return "\n".join(lines)

    lines += [
        t(
            "export.day.totals",
            lang=lang,
            conversations=len(conversations),
            speech=_minutes(total_speech, lang),
            accepted=accepted,
            rejected=rejected,
        ),
        "",
    ]

    for index, item in enumerate(conversations, start=1):
        lines += [
            t(
                "export.day.conversation",
                lang=lang,
                index=index,
                start=_hhmm(item.started_at),
                end=_hhmm(item.ended_at),
                speech=_minutes(item.speech_seconds, lang),
                id=item.conversation_id,
            ),
            "",
        ]
        if item.summary:
            lines += [item.summary, ""]

        if item.people:
            names = ", ".join(f"[[{name}]]" for name in item.people)
            lines += [t("export.day.people", lang=lang, names=names), ""]

        if item.facts:
            lines += [t("export.day.facts", lang=lang), ""]
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
            lines += [t("export.day.tasks", lang=lang), ""]
            for task in item.tasks:
                lines.append(f"- {_format_task(task, lang)}")
            lines.append("")

        if item.topics:
            lines += [t("export.day.topics", lang=lang, topics=", ".join(item.topics)), ""]

        if item.rejected:
            # Счёт по разговору, а не по суткам: пороги калибруются на том,
            # в каком именно разговоре фильтр сработал.
            lines += [t("export.day.rejected", lang=lang, count=item.rejected), ""]

    return "\n".join(lines)


def _format_task(task: dict, lang: str | None = None) -> str:
    """Задача в заметке дня: только суть, без чекбокса, источника и якоря."""
    parts = [str(task.get("text") or "").strip()]
    assignee = task.get("assignee")
    due = task.get("due")
    if assignee:
        parts.append(f"({assignee})")
    if due:
        parts.append(t("export.task.due", lang=lang, due=due))
    return " ".join(part for part in parts if part)


def write_day_note(
    days_dir: Path | str,
    day: date,
    conversations: list[ConversationView],
    *,
    accepted: int = 0,
    rejected: int = 0,
    flagged: int = 0,
    lang: str | None = None,
) -> Path:
    """Пишет заметку за день. Существующая перезаписывается целиком."""
    lang = lang or i18n.get_language()
    directory = Path(days_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / t("export.day.file", lang=lang, date=day.isoformat())
    path.write_text(
        render_day(
            day,
            conversations,
            accepted=accepted,
            rejected=rejected,
            flagged=flagged,
            lang=lang,
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
    lang: str | None = None,
) -> str:
    """Дословная расшифровка дня — временный файл под TTL.

    Реплики — дословно на языке разговора; по языку интерфейса только подписи.
    """
    lang = lang or i18n.get_language()
    lines = [
        "---",
        *_frontmatter(
            lang,
            ("@date", day.isoformat()),
            ("@source", t("export.fm.source_value", lang=lang)),
            ("rag", "false"),
            ("@temporary", "true"),
            ("@delete_after", (day + timedelta(days=ttl_days)).isoformat()),
        ),
        "---",
        "",
    ]
    for item in conversations:
        lines += [
            t(
                "export.raw.conversation",
                lang=lang,
                start=_hhmm(item.started_at),
                end=_hhmm(item.ended_at),
                id=item.conversation_id,
            ),
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
    lang: str | None = None,
) -> Path:
    """Пишет файл сырой расшифровки. Существующий перезаписывается целиком."""
    lang = lang or i18n.get_language()
    directory = Path(raw_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}{raw_note_suffix(lang)}"
    path.write_text(
        render_transcript(day, conversations, ttl_days=ttl_days, lang=lang),
        encoding="utf-8",
    )
    log.info("расшифровка записана: %s", path)
    return path


def render_task_line(task: dict, *, lang: str | None = None) -> str:
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
        parts.append(t("export.task.due", lang=lang, due=due))

    conversation_id = task.get("conversation_id")
    if conversation_id is not None:
        started_at = task.get("conversation_started_at")
        stamp = f"[{_hhmm(float(started_at))}] " if started_at is not None else ""
        parts.append(t("export.task.source", lang=lang, stamp=stamp, id=conversation_id))

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
    path: Path | str,
    tasks: list[dict],
    *,
    heading: str | None = None,
    lang: str | None = None,
) -> int:
    """Дописывает задачи с якорями, пропуская уже записанные. Число новых.

    Дедуп — по якорю, а не по тексту: настоящий дедуп живёт в базе
    (``tasks.exported_at``). Строка, удалённая руками, вернётся при следующей
    выгрузке, только пока задача не помечена в базе, — и это правильно:
    защита в базе, а не в файле.
    """
    path = Path(path)
    lang = lang or i18n.get_language()
    if heading is None:
        heading = t("export.tasks.heading", lang=lang)
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
        fresh.append(render_task_line(task, lang=lang))

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
    Узнаёт расшифровки на любом языке каталогов: язык интерфейса могли сменить.
    """
    directory = Path(raw_dir)
    if not directory.exists():
        return []
    now = date.today() if today is None else today
    limit = now - timedelta(days=ttl_days)

    suffixes = sorted(set(i18n.data_all("export.raw.suffix")) | {RAW_NOTE_SUFFIX})
    expired: list[Path] = []
    for path in sorted(directory.iterdir()):
        suffix = next((item for item in suffixes if path.name.endswith(item)), None)
        if suffix is None or not path.is_file():
            continue
        stamp = path.name[: -len(suffix)]
        try:
            created = date.fromisoformat(stamp)
        except ValueError:
            continue  # чужой файл с похожим именем — не наше дело
        if created < limit:
            expired.append(path)
    return expired
