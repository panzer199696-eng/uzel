"""Отчёт за день для папки на Рабочем столе: только полезное.

Заметка дня в Obsidian — для системы (frontmatter, метрики фильтра, ссылки
[[...]]). Отчёт — для человека: разговоры, о чём шла речь, факты и задачи,
обычным текстом, чтобы открывался Блокнотом. Разговор без саммари, фактов и
задач в отчёт не попадает; день без единого такого разговора файла не даёт.
Дословных реплик нет — как и в заметке дня.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from .obsidian import ConversationView

log = logging.getLogger(__name__)


def _hhmm(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def _task_line(task: dict) -> str:
    parts = [str(task.get("text") or "").strip()]
    if task.get("assignee"):
        parts.append(f"({task['assignee']})")
    if task.get("due"):
        parts.append(f"— до {task['due']}")
    return " ".join(part for part in parts if part)


def is_useful(view: ConversationView) -> bool:
    return bool((view.summary or "").strip() or view.facts or view.tasks)


def render_report(day: date, conversations: list[ConversationView]) -> str | None:
    """Текст отчёта или ``None``, если за день нет ничего полезного."""
    useful = [view for view in conversations if is_useful(view)]
    if not useful:
        return None

    lines = [f"УЗЕЛ — отчёт за {day.strftime('%d.%m.%Y')}", ""]

    all_tasks = [task for view in useful for task in view.tasks]
    if all_tasks:
        lines += ["ЗАДАЧИ", ""]
        lines += [f"  [ ] {_task_line(task)}" for task in all_tasks]
        lines.append("")

    lines += ["РАЗГОВОРЫ", ""]
    for view in useful:
        header = f"{_hhmm(view.started_at)}–{_hhmm(view.ended_at)}"
        if view.people:
            header += f" · {', '.join(view.people)}"
        if view.topics:
            header += f" · {', '.join(view.topics)}"
        lines.append(header)
        if view.summary:
            lines.append(f"  {view.summary.strip()}")
        for fact in view.facts:
            stamp = f"[{_hhmm(fact.said_at)}] " if fact.said_at is not None else ""
            lines.append(f"  • {stamp}{fact.text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(
    report_dir: Path | str, day: date, conversations: list[ConversationView]
) -> Path | None:
    """Пишет ``ГГГГ-ММ-ДД.txt``; существующий перезаписывается. Пусто — ``None``."""
    text = render_report(day, conversations)
    if text is None:
        return None
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.txt"
    # utf-8-sig: Блокнот и старые просмотрщики без BOM путают кириллицу.
    path.write_text(text, encoding="utf-8-sig")
    log.info("отчёт дня записан: %s", path)
    return path
