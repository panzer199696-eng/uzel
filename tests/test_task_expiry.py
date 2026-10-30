"""Снятие застоявшихся задач (30.10.2026: 86 открытых, ни одной закрытой с 07.10)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from uzel.export.obsidian import append_tasks, archive_tasks
from uzel.storage import Storage
from uzel.stt.postprocess import strip_hallucination

TODAY = date(2026, 10, 30)


def ts(day: date) -> float:
    return datetime.combine(day, datetime.min.time()).replace(hour=12).timestamp()


@pytest.fixture
def store(tmp_path):
    with Storage(tmp_path / "uzel.sqlite") as store:
        yield store


def test_expire_old_undated_and_overdue_keeps_fresh(store):
    old = store.open_conversation(uid="p", started_at=ts(date(2026, 10, 13)))
    fresh = store.open_conversation(uid="p", started_at=ts(date(2026, 10, 27)))
    a = store.add_task(old, "выложить на Авито")  # 17 дней, без срока
    b = store.add_task(fresh, "оплатить счёт на 90")  # 3 дня
    c = store.add_task(fresh, "заключить договор", due="2026-10-26")  # срок прошёл 4 дня назад
    d = store.add_task(fresh, "позвонить Эльне", due="2026-10-29")  # срок вчера — ещё живая

    assert sorted(store.expire_tasks(TODAY, ttl_days=10)) == sorted([a, c])
    status = {row["id"]: row["status"] for row in store.tasks_between()}
    assert status == {a: "expired", b: "open", c: "expired", d: "open"}
    assert store.expire_tasks(TODAY, ttl_days=10) == []  # повтор ничего не трогает


def test_expire_disabled(store):
    cid = store.open_conversation(uid="p", started_at=ts(date(2026, 9, 1)))
    store.add_task(cid, "старая задача")
    assert store.expire_tasks(TODAY, ttl_days=0) == []


def test_archive_moves_only_unticked_lines(tmp_path):
    note = tmp_path / "Входящее — задачи.md"
    append_tasks(
        note,
        [{"id": i, "text": f"задача номер {i}", "conversation_id": None} for i in (1, 2, 3)],
    )
    text = note.read_text(encoding="utf-8").replace("- [ ] задача номер 2", "- [x] задача номер 2")
    note.write_text(text, encoding="utf-8")

    assert archive_tasks(note, [1, 2]) == 1
    lines = note.read_text(encoding="utf-8").splitlines()
    head = lines.index("## Устаревшие — сняты автоматически")
    assert "- [ ] задача номер 1 ^uzel-1" in lines[head:]
    assert "- [x] задача номер 2 ^uzel-2" in lines[:head]
    assert "- [ ] задача номер 3 ^uzel-3" in lines[:head]

    # второй перенос дописывается под тот же заголовок
    assert archive_tasks(note, [3]) == 1
    text = note.read_text(encoding="utf-8")
    assert text.count("## Устаревшие") == 1
    assert archive_tasks(note, [99]) == 0


def test_subtitle_credit_variant_is_stripped():
    assert strip_hallucination("Субтитры подогнал «Симон»")[0] == ""
    assert strip_hallucination("Субтитры от DimaTorzok")[0] == ""
