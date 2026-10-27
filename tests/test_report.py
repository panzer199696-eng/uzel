"""Отчёт за день для папки на Рабочем столе: только полезное, по файлу на дату."""

from __future__ import annotations

from datetime import date, datetime

from uzel.export.obsidian import ConversationView, FactView
from uzel.export.report import render_report, write_report
from uzel.storage import Storage

from test_worker import BASE_S, add_pending, make_worker, relaxed

DAY = date(2026, 10, 5)
T0 = datetime(2026, 10, 5, 10, 0).timestamp()


def view(cid=1, summary="Обсудили поставку кирпича", facts=(), tasks=(), **kw):
    return ConversationView(
        conversation_id=cid,
        started_at=T0,
        ended_at=T0 + 600,
        summary=summary,
        speech_seconds=300,
        facts=list(facts),
        tasks=list(tasks),
        **kw,
    )


def test_report_has_summary_facts_tasks_without_noise():
    text = render_report(
        DAY,
        [
            view(
                facts=[FactView("Заявка до 30-го", said_at=T0 + 60)],
                tasks=[{"text": "подготовить КП", "assignee": "Иван", "due": "2026-10-07"}],
                people=["Пётр"],
                topics=["закупки"],
                rejected=3,
            )
        ],
    )
    assert "05.10.2026" in text
    assert "Обсудили поставку кирпича" in text
    assert "[10:01] Заявка до 30-го" in text
    assert "[ ] подготовить КП (Иван) — до 2026-10-07" in text
    assert "10:00–10:10 · Пётр · закупки" in text
    # служебное из заметки Obsidian — не для человека
    for noise in ("---", "rag:", "[[", "Фильтр качества", "принято"):
        assert noise not in text


def test_empty_conversations_are_dropped():
    text = render_report(DAY, [view(cid=1, summary=None), view(cid=2, summary="Суть")])
    assert text.count("10:00–10:10") == 1


def test_day_without_anything_useful_gives_no_file(tmp_path):
    assert render_report(DAY, [view(summary="  ")]) is None
    assert write_report(tmp_path / "отчёты", DAY, [view(summary=None)]) is None
    assert not (tmp_path / "отчёты").exists()


def test_file_named_by_date_and_opens_in_notepad(tmp_path):
    path = write_report(tmp_path / "отчёты", DAY, [view()])
    assert path.name == "2026-10-05.txt"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM для Блокнота
    assert "поставку кирпича" in path.read_text(encoding="utf-8-sig")


def test_batch_writes_report_when_dir_is_set(settings, tmp_path):
    reports = tmp_path / "Рабочий стол" / "УЗЕЛ — отчёты"
    settings = relaxed(settings, vault_dir=tmp_path / "vault", report_dir=reports)
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid, text="Пётр подаёт заявку на поставку кирпича", language="ru", backend="fake"
        )

    with make_worker(settings) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    report = reports / f"{day.isoformat()}.txt"
    assert report.exists()
    text = report.read_text(encoding="utf-8-sig")
    assert "подготовить КП" in text
    assert "Пётр подаёт заявку" not in text  # дословных реплик нет


def test_no_report_dir_no_report(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    assert settings.report_dir is None
