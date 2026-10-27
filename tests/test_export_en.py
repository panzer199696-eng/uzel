"""Выгрузка на английском интерфейсе (27.10.2026) и неизменность русской.

Русский вывод обязан остаться байт-в-байт прежним: заметки владельца уже
лежат в хранилище, и Obsidian не должен увидеть «изменений» на пустом месте.
"""

from __future__ import annotations

from datetime import date

from test_export import DAY, conversation, ts
from uzel import i18n
from uzel.export.obsidian import (
    append_tasks,
    expired_raw_notes,
    render_day,
    render_task_line,
    render_transcript,
    write_day_note,
    write_transcript_note,
)
from uzel.export.report import render_report
from uzel.export.telegram import DaySummary, render_summary

# Эталон русской заметки снят с кода до локализации (коммит 94612fa).
RU_DAY_NOTE = """---
дата: 2026-08-24
источник: УЗЕЛ
rag: false
разговоров: 1
речи_минут: 10
принято: 14
отклонено: 3
помечено: 2
tags: [заявка, поставка-кирпича]
---

# 24.08.2026 — день

Разговоров: **1**, речи всего: **10 мин**. Реплик принято **14**, отклонено **3**.

## 1. 10:00–10:20 (10 мин) · разговор 17

Обсудили заявку ООО «Ромашка» и сроки поставки.

**Участники:** [[Иван]], [[Пётр]]

**Факты**

- `[10:05]` Заявка подаётся 30 августа
- Смета согласована

**Задачи** → [[Входящее — задачи]]

- подготовить КП (Иван) — до 2026-08-30

**Темы:** заявка, поставка кирпича

_Фильтр качества отсеял реплик: 3._
"""


def test_russian_day_note_is_byte_for_byte_unchanged():
    assert render_day(DAY, [conversation()], accepted=14, rejected=3, flagged=2) == RU_DAY_NOTE


def test_english_day_note_headings_frontmatter_and_dates():
    note = render_day(
        DAY, [conversation()], accepted=14, rejected=3, flagged=2, lang="en"
    )
    assert note.startswith("---\ndate: 2026-08-24\nsource: UZEL\nrag: false\nconversations: 1\n")
    assert "speech_minutes: 10\naccepted: 14\nrejected: 3\nflagged: 2\n" in note
    assert "# 2026-08-24 — day" in note
    assert "Conversations: **1**, speech in total: **10 min**. Utterances accepted **14**, rejected **3**." in note
    assert "## 1. 10:00–10:20 (10 min) · conversation 17" in note
    assert "**Participants:** [[Иван]], [[Пётр]]" in note
    assert "**Facts**" in note and "**Tasks** → [[Inbox — tasks]]" in note
    assert "- подготовить КП (Иван) — due 2026-08-30" in note
    assert "**Topics:** заявка, поставка кирпича" in note
    assert "_Utterances dropped by the quality filter: 3._" in note
    for word in ("день", "Разговоров", "Факты", "Задачи", "разговор "):
        assert word not in note


def test_english_empty_day():
    assert "_No recordings for this day._" in render_day(DAY, [], lang="en")


def test_interface_language_is_the_default():
    i18n.set_language("en")
    assert "# 2026-08-24 — day" in render_day(DAY, [conversation()])


def test_english_task_line_and_inbox_heading(tmp_path):
    task = {
        "id": 5,
        "text": "send the invoice",
        "assignee": "Mike",
        "due": "2026-08-30",
        "conversation_id": 17,
        "conversation_started_at": ts("2026-08-24T10:42:00"),
    }
    assert render_task_line(task, lang="en") == (
        "- [ ] send the invoice (Mike) — due 2026-08-30 · [10:42] conversation 17 ^uzel-5"
    )
    path = tmp_path / "Inbox — tasks.md"
    i18n.set_language("en")
    assert append_tasks(path, [task]) == 1
    assert "## From conversations\n\n- [ ] send the invoice" in path.read_text(encoding="utf-8")


def test_english_transcript_and_file_names(tmp_path):
    raw = render_transcript(DAY, [conversation()], ttl_days=30, lang="en")
    assert "temporary: true\ndelete_after: 2026-09-23\n" in raw
    assert "## 10:00–10:20 · conversation 17" in raw

    i18n.set_language("en")
    assert write_day_note(tmp_path, DAY, [conversation()]).name == "2026-08-24 — day.md"
    assert (
        write_transcript_note(tmp_path, DAY, [conversation()]).name
        == "2026-08-24 — transcript.md"
    )


def test_ttl_cleanup_sees_transcripts_of_every_language(tmp_path):
    (tmp_path / "2026-07-01 — расшифровка.md").write_text("x", encoding="utf-8")
    (tmp_path / "2026-07-02 — transcript.md").write_text("x", encoding="utf-8")
    (tmp_path / "2026-08-20 — transcript.md").write_text("x", encoding="utf-8")
    expired = expired_raw_notes(tmp_path, ttl_days=30, today=date(2026, 8, 24))
    assert sorted(path.name for path in expired) == [
        "2026-07-01 — расшифровка.md",
        "2026-07-02 — transcript.md",
    ]


def test_english_report_and_telegram():
    report = render_report(DAY, [conversation()], lang="en")
    assert report.startswith("UZEL — report for 2026-08-24\n\nTASKS\n")
    assert "  [ ] подготовить КП (Иван) — due 2026-08-30" in report
    assert "CONVERSATIONS" in report

    summary = DaySummary(
        day=DAY, conversations=2, speech_seconds=600, summaries=["a"], tasks=["b"]
    )
    text = render_summary(summary, lang="en")
    assert text.startswith("<b>UZEL — summary for 2026-08-24</b>")
    assert "Conversations: 2, speech: 10 min" in text
    assert "<b>What was discussed</b>" in text and "<b>Tasks</b>" in text
    empty = DaySummary(day=DAY, conversations=0, speech_seconds=0, summaries=[], tasks=[])
    assert "No recordings for the day." in render_summary(empty, lang="en")
