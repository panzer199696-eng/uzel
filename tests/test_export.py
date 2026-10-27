"""Выгрузка в Obsidian и Telegram: FR-050, FR-051, FR-052, ТЗ 2026-08-24 п. 4."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from uzel.export.obsidian import (
    ConversationView,
    FactView,
    append_tasks,
    existing_anchors,
    expired_raw_notes,
    render_day,
    render_task_line,
    render_transcript,
    write_day_note,
    write_transcript_note,
)
from uzel.export.telegram import DaySummary, TelegramNotifier, render_summary

DAY = date(2026, 8, 24)


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def conversation(**overrides) -> ConversationView:
    base = dict(
        conversation_id=17,
        started_at=ts("2026-08-24T10:00:00"),
        ended_at=ts("2026-08-24T10:20:00"),
        summary="Обсудили заявку ООО «Ромашка» и сроки поставки.",
        speech_seconds=600.0,
        facts=[
            FactView(
                text="Заявка подаётся 30 августа",
                said_at=ts("2026-08-24T10:05:00"),
            ),
            FactView(text="Смета согласована"),
        ],
        tasks=[{"text": "подготовить КП", "assignee": "Иван", "due": "2026-08-30"}],
        topics=["заявка", "поставка кирпича"],
        people=["Иван", "Пётр"],
        utterances=[(ts("2026-08-24T10:00:05"), "Иван", "Когда подаём заявку?")],
        accepted=14,
        rejected=3,
    )
    base.update(overrides)
    return ConversationView(**base)


# --- заметка за день ---


def test_day_note_has_no_utterances_at_all():
    """Пункт 4.1 ТЗ: дословных реплик в заметке дня не бывает никогда."""
    note = render_day(DAY, [conversation()])

    assert "Когда подаём заявку?" not in note
    assert "<details>" not in note
    assert "Расшифровка" not in note


def test_fact_with_time_gets_timecode_and_without_stays_plain():
    note = render_day(DAY, [conversation()])

    assert "- `[10:05]` Заявка подаётся 30 августа" in note
    assert "- Смета согласована" in note


def test_frontmatter_has_rag_metrics_and_normalised_tags():
    note = render_day(DAY, [conversation()], accepted=14, rejected=3)

    assert "rag: false" in note
    assert "принято: 14" in note
    assert "отклонено: 3" in note
    assert "tags: [заявка, поставка-кирпича]" in note


def test_no_tags_key_without_topics():
    note = render_day(DAY, [conversation(topics=[])])

    assert "tags:" not in note


def test_flagged_metric_shows_filter_work_while_it_only_marks():
    """При quality_enforce=False отклонённых нет вовсе — видно только «помечено»."""
    note = render_day(DAY, [conversation()], accepted=14, rejected=0, flagged=3)

    assert "отклонено: 0" in note
    assert "помечено: 3" in note


def test_rejected_count_of_conversation_is_visible():
    note = render_day(DAY, [conversation(rejected=2)])

    assert "_Фильтр качества отсеял реплик: 2._" in note


def test_clean_conversation_has_no_filter_line():
    note = render_day(DAY, [conversation(rejected=0)])

    assert "Фильтр качества отсеял" not in note


def test_metrics_line_in_body():
    note = render_day(DAY, [conversation()], accepted=14, rejected=3)

    assert "Реплик принято **14**, отклонено **3**." in note


def test_people_render_as_links_or_stay_hidden():
    note = render_day(DAY, [conversation()])
    assert "**Участники:** [[Иван]], [[Пётр]]" in note

    empty = render_day(DAY, [conversation(people=[])])
    assert "Участники" not in empty


def test_tasks_in_day_note_have_no_checkboxes():
    note = render_day(DAY, [conversation()])

    assert "**Задачи** → [[Входящее — задачи]]" in note
    assert "- [ ]" not in note
    assert "- подготовить КП (Иван) — до 2026-08-30" in note


def test_conversation_heading_carries_id():
    note = render_day(DAY, [conversation()])

    assert "## 1. 10:00–10:20 (10 мин) · разговор 17" in note


def test_day_note_without_conversations_says_so():
    note = render_day(DAY, [])

    assert "записей нет" in note
    assert "разговоров: 0" in note
    assert "rag: false" in note


def test_day_note_skips_missing_sections():
    note = render_day(
        DAY,
        [conversation(facts=[], tasks=[], topics=[], people=[], summary=None)],
    )

    assert "**Факты**" not in note
    assert "**Задачи**" not in note
    assert "Участники" not in note
    assert "Темы:" not in note


def test_write_day_note_names_file_the_new_way(tmp_path):
    first = write_day_note(
        tmp_path / "Дни", DAY, [conversation()], accepted=1, rejected=2
    )
    assert first.name == "2026-08-24 — день.md"

    write_day_note(tmp_path / "Дни", DAY, [])

    assert "записей нет" in first.read_text(encoding="utf-8")


# --- сырая расшифровка ---


def test_render_transcript_has_utterances_and_expiry():
    text = render_transcript(DAY, [conversation()])

    assert "удалить_после: 2026-09-23" in text
    assert "временный: true" in text
    assert "rag: false" in text
    assert "## 10:00–10:20 · разговор 17" in text
    assert "- `10:00` **Иван:** Когда подаём заявку?" in text


def test_render_transcript_skips_empty_utterances():
    text = render_transcript(
        DAY, [conversation(utterances=[(ts("2026-08-24T10:01:00"), None, None)])]
    )

    assert "10:01" not in text


def test_write_transcript_note_creates_file(tmp_path):
    path = write_transcript_note(tmp_path / "Сырое", DAY, [conversation()])

    assert path.name == "2026-08-24 — расшифровка.md"
    assert path.exists()


def test_expired_raw_notes_finds_only_stale(tmp_path):
    write_transcript_note(tmp_path, DAY, [conversation()], ttl_days=30)
    write_transcript_note(tmp_path, date(2026, 9, 18), [conversation()])

    expired = expired_raw_notes(tmp_path, ttl_days=30, today=date(2026, 9, 24))

    assert [path.name for path in expired] == ["2026-08-24 — расшифровка.md"]


def test_expired_raw_notes_of_missing_dir_is_empty(tmp_path):
    assert expired_raw_notes(tmp_path / "нет") == []


def test_expired_raw_notes_ignores_foreign_files(tmp_path):
    (tmp_path / "заметка — расшифровка.md").write_text("", encoding="utf-8")

    assert expired_raw_notes(tmp_path, ttl_days=1, today=date(2026, 9, 24)) == []


# --- список задач ---


def test_render_task_line_matches_the_spec():
    task = {
        "id": 231,
        "text": "подготовить КП",
        "assignee": "Пётр",
        "due": "2026-08-30",
        "conversation_started_at": ts("2026-08-24T10:42:00"),
        "conversation_id": 17,
    }

    assert (
        render_task_line(task)
        == "- [ ] подготовить КП (Пётр) — до 2026-08-30 · [10:42] разговор 17 ^uzel-231"
    )


def test_render_task_line_drops_missing_parts():
    assert render_task_line({"id": 5, "text": "дело"}) == "- [ ] дело ^uzel-5"
    # нет времени — метка [ЧЧ:ММ] опускается, разговор остаётся
    assert (
        render_task_line({"id": 5, "text": "дело", "conversation_id": 17})
        == "- [ ] дело · разговор 17 ^uzel-5"
    )


def test_render_task_line_requires_an_anchor():
    with pytest.raises(ValueError):
        render_task_line({"text": "дело без id"})


def test_append_writes_new_tasks(tmp_path):
    path = tmp_path / "Входящее — задачи.md"

    added = append_tasks(
        path,
        [
            {"id": 1, "text": "подготовить КП"},
            {"id": 2, "text": "позвонить Петру"},
        ],
    )

    assert added == 2
    text = path.read_text(encoding="utf-8")
    assert "- [ ] подготовить КП ^uzel-1" in text
    assert "## Из разговоров" in text


def test_append_twice_with_same_id_writes_once(tmp_path):
    """Якорь — страховка от двойной записи при падении между файлом и базой."""
    path = tmp_path / "задачи.md"
    task = {"id": 231, "text": "подготовить КП", "conversation_id": 17}

    assert append_tasks(path, [task]) == 1
    assert append_tasks(path, [task]) == 0

    assert path.read_text(encoding="utf-8").count("подготовить КП") == 1


def test_append_after_manual_deletion_writes_again(tmp_path):
    """Ожидаемое поведение: защита — в базе (exported_at), а не в файле."""
    path = tmp_path / "задачи.md"
    task = {"id": 231, "text": "подготовить КП"}
    append_tasks(path, [task])

    path.write_text(
        "# Задачи\n\n## Из разговоров\n\n- [ ] ручная задача\n", encoding="utf-8"
    )

    assert append_tasks(path, [task]) == 1
    assert path.read_text(encoding="utf-8").count("подготовить КП") == 1


def test_append_keeps_existing_content(tmp_path):
    path = tmp_path / "задачи.md"
    path.write_text("# Задачи\n\n- [ ] старое дело\n", encoding="utf-8")

    append_tasks(path, [{"id": 7, "text": "новое дело"}])

    text = path.read_text(encoding="utf-8")
    assert "старое дело" in text and "новое дело" in text


def test_new_tasks_go_right_under_the_heading(tmp_path):
    path = tmp_path / "задачи.md"
    append_tasks(path, [{"id": 1, "text": "первая"}])

    append_tasks(path, [{"id": 2, "text": "вторая"}])

    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    heading = lines.index("## Из разговоров")
    assert lines[heading + 1] == "- [ ] вторая ^uzel-2"


def test_append_of_empty_list_changes_nothing(tmp_path):
    path = tmp_path / "задачи.md"

    assert append_tasks(path, []) == 0
    assert not path.exists()


def test_existing_anchors_of_missing_file_is_empty(tmp_path):
    assert existing_anchors(tmp_path / "нет.md") == set()


# --- Telegram ---


def summary(**overrides) -> DaySummary:
    base = dict(
        day=DAY,
        conversations=2,
        speech_seconds=1800.0,
        summaries=["Обсудили заявку", "Согласовали объёмы"],
        tasks=["подготовить КП"],
    )
    base.update(overrides)
    return DaySummary(**base)


def test_summary_contains_counts_and_items():
    text = render_summary(summary())

    assert "24.08.2026" in text
    assert "Разговоров: 2" in text
    assert "30 мин" in text
    assert "• Обсудили заявку" in text
    assert "☐ подготовить КП" in text


def test_summary_of_empty_day():
    text = render_summary(summary(conversations=0, summaries=[], tasks=[]))

    assert "записей нет" in text


def test_summary_trims_long_lists():
    text = render_summary(
        summary(summaries=[f"тема {i}" for i in range(20)]), max_items=3
    )

    assert "…и ещё 17" in text


def test_summary_escapes_html():
    text = render_summary(summary(summaries=["<script>alert(1)</script>"]))

    assert "&lt;script&gt;" in text
    assert "<script>" not in text


def test_summary_never_exceeds_telegram_limit():
    text = render_summary(
        summary(summaries=["очень длинная тема" * 40] * 50, tasks=[]), max_items=50
    )

    assert len(text) <= 4096


def test_notifier_is_disabled_without_token():
    assert TelegramNotifier(None, "123").enabled is False
    assert TelegramNotifier("token", None).enabled is False
    assert TelegramNotifier("token", "123").enabled is True


def test_disabled_notifier_sends_nothing_and_does_not_fail():
    assert TelegramNotifier(None, None).send("текст") is False


def test_notifier_posts_to_telegram(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(
            200, json={"ok": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    assert TelegramNotifier("SECRET", "42").send("привет") is True
    assert captured["url"].endswith("/botSECRET/sendMessage")
    assert captured["json"]["chat_id"] == "42"
    assert captured["json"]["parse_mode"] == "HTML"


def test_notifier_reports_failure_without_raising(monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        return httpx.Response(403, text="forbidden", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    assert TelegramNotifier("SECRET", "42").send("привет") is False


def test_notifier_survives_network_error(monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("нет сети")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert TelegramNotifier("SECRET", "42").send("привет") is False
