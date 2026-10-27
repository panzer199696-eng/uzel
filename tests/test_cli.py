"""Командная строка ядра: FR-053 и обслуживание."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from uzel import cli
from uzel.storage import Storage


@pytest.fixture
def filled(settings):
    """База с парой расшифрованных реплик."""
    with Storage(settings.db_path) as storage:
        person = storage.upsert_person("Иван")
        first = storage.add_utterance(
            uid="dev-1",
            started_at=1_756_000_000.0,
            ended_at=1_756_000_010.0,
            speech_seconds=8.0,
        )
        second = storage.add_utterance(
            uid="dev-1",
            started_at=1_756_000_100.0,
            ended_at=1_756_000_110.0,
            speech_seconds=9.0,
        )
        storage.set_transcript(
            first, text="заявка ООО «Ромашка» на август", language="ru", backend="local"
        )
        storage.set_transcript(
            second, text="совсем про другое", language="ru", backend="local"
        )
        storage.set_speaker(first, person)
    return settings


def run(args, settings) -> int:
    parsed = cli.build_parser().parse_args(args)
    return parsed.func(parsed, settings)


def test_search_prints_match_with_time_and_speaker(filled, capsys):
    code = run(["search", "Ромашка", "--plain"], filled)

    out = capsys.readouterr().out
    assert code == 0
    assert "заявка ООО «Ромашка» на август" in out
    assert "Иван" in out


def test_search_marks_unlabelled_speaker(filled, capsys):
    run(["search", "другое", "--plain"], filled)

    assert "не размечен" in capsys.readouterr().out


def test_search_without_matches_returns_error_code(filled, capsys):
    code = run(["search", "такого точно нет", "--plain"], filled)

    assert code == 1
    assert "Ничего не нашлось" in capsys.readouterr().out


def test_search_respects_limit(filled, capsys):
    with Storage(filled.db_path) as storage:
        for i in range(5):
            uid = storage.add_utterance(
                uid="dev-1",
                started_at=1_756_001_000.0 + i,
                ended_at=1_756_001_005.0 + i,
                speech_seconds=1.0,
            )
            storage.set_transcript(
                uid, text=f"общее слово {i}", language="ru", backend="local"
            )

    run(["search", "общее слово", "--plain", "--limit", "2"], filled)

    assert capsys.readouterr().out.count("общее слово") == 2


def test_stats_reports_counts(filled, capsys):
    code = run(["stats"], filled)

    out = capsys.readouterr().out
    assert code == 0
    assert "реплик" in out and "2" in out
    assert "Очередь приёма" in out


def test_privacy_status_is_the_default_action(settings, capsys):
    run(["privacy"], settings)

    assert "запись: идёт" in capsys.readouterr().out


def test_privacy_mute_and_unmute(settings, capsys):
    run(["privacy", "mute", "30"], settings)
    assert "выключена" in capsys.readouterr().out

    run(["privacy", "unmute"], settings)
    assert "идёт" in capsys.readouterr().out


def test_privacy_shows_configured_windows(settings, capsys):
    run(["privacy"], settings.model_copy(update={"quiet_windows": ("10:00-12:00",)}))

    assert "стоп-зоны: 10:00–12:00" in capsys.readouterr().out


def test_purge_removes_expired_audio(settings, tmp_path, capsys):
    audio = tmp_path / "old.wav"
    audio.write_bytes(b"x")
    with Storage(settings.db_path) as storage:
        utterance = storage.add_utterance(
            uid="dev-1",
            started_at=0.0,
            ended_at=1.0,
            speech_seconds=1.0,
            audio_path=str(audio),
        )
        # Нерасшифрованный звук TTL не трогает (P11.2) — реплика расшифрована.
        storage.set_transcript(utterance, text="т", language="ru", backend="local")

    code = run(["purge"], settings)

    assert code == 0
    assert not audio.exists()
    assert "записей аудио 1" in capsys.readouterr().out


def test_backup_creates_copy(filled, tmp_path, capsys):
    destination = tmp_path / "копия.sqlite"

    code = run(["backup", str(destination)], filled)

    assert code == 0
    assert destination.exists()
    assert "Копия базы" in capsys.readouterr().out


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


# ---------- export / show / prune-raw (ТЗ 2026-08-24, пакет 4) ----------


@pytest.fixture
def closed_day(settings):
    """Один закрытый разговор 18.09.2026: факт с таймкодом, задача, реплика."""
    started = datetime(2026, 9, 18, 10, 0).timestamp()
    with Storage(settings.db_path) as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=started)
        uid = storage.add_utterance(
            uid="dev-1",
            started_at=started,
            ended_at=started + 1200,
            speech_seconds=600.0,
            conversation_id=cid,
        )
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="local",
        )
        storage.attach_utterances(cid, [uid])
        storage.add_fact(cid, "Заявка подаётся 30 августа", said_at=started + 300)
        storage.add_task(cid, "подготовить КП", assignee="Иван", due="2026-08-30")
        storage.close_conversation(cid, summary="Обсудили заявку ООО «Ромашка».")
    return settings


def test_export_writes_day_note_from_db(closed_day, capsys):
    code = run(["export", "--day", "2026-09-18"], closed_day)

    out = capsys.readouterr().out
    assert code == 0
    note = closed_day.vault_days_dir / "2026-09-18 — день.md"
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "Обсудили заявку ООО «Ромашка»." in text
    assert "- `[10:05]` Заявка подаётся 30 августа" in text
    # дословных реплик в заметке нет — они в базе
    assert "Пётр подаёт заявку на поставку кирпича" not in text
    assert "Задач выгружено: 1" in out
    tasks_text = closed_day.tasks_note_path.read_text(encoding="utf-8")
    assert tasks_text.count("подготовить КП") == 1
    assert "^uzel-1" in tasks_text


def test_export_without_closed_conversations_writes_nothing(settings, capsys):
    code = run(["export", "--day", "2026-09-18"], settings)

    assert code == 0
    assert "не писалась" in capsys.readouterr().out
    assert not settings.vault_days_dir.exists()


def test_export_rejects_undecipherable_day(closed_day, capsys):
    code = run(["export", "--day", "32.13.2026"], closed_day)

    assert code == 2
    assert capsys.readouterr().err != ""


def test_show_prints_whole_conversation(closed_day, capsys):
    code = run(["show", "1"], closed_day)

    out = capsys.readouterr().out
    assert code == 0
    assert "Обсудили заявку ООО «Ромашка»." in out
    assert "[10:05] Заявка подаётся 30 августа" in out
    assert "подготовить КП (Иван) — до 2026-08-30" in out
    assert "[10:00] Пётр подаёт заявку на поставку кирпича" in out


def test_show_missing_conversation_is_an_error(closed_day, capsys):
    code = run(["show", "99"], closed_day)

    assert code == 1
    assert capsys.readouterr().err != ""


# ---------- приватные реплики (P11.2, находка 5) ----------

PRIV_MARK = "секретныйприватныймаркер2026"


def _add_private_utterance(settings, *, text=None):
    """Приватная реплика в разговор 1 фикстуры closed_day."""
    started = datetime(2026, 9, 18, 10, 30).timestamp()
    with Storage(settings.db_path) as storage:
        uid = storage.add_utterance(
            uid="dev-1",
            started_at=started,
            ended_at=started + 30,
            speech_seconds=20.0,
            conversation_id=1,
            is_private=True,
        )
        storage.set_transcript(
            uid,
            text=text if text is not None else f"заявка на поставку кирпича {PRIV_MARK}",
            language="ru",
            backend="local",
        )


def test_show_hides_private_without_flag(closed_day, capsys):
    _add_private_utterance(closed_day)

    code = run(["show", "1"], closed_day)

    out = capsys.readouterr().out
    assert code == 0
    assert PRIV_MARK not in out
    assert "(скрыто приватных реплик: 1, показать — --private)" in out
    assert "[10:00] Пётр подаёт заявку" in out  # публичная реплика на месте


def test_show_private_flag_reveals_marked_rows(closed_day, capsys):
    _add_private_utterance(closed_day)

    code = run(["show", "1", "--private"], closed_day)

    out = capsys.readouterr().out
    assert code == 0
    assert PRIV_MARK in out
    assert "[приват]" in out
    assert "скрыто приватных" not in out


def test_search_plain_does_not_print_private(closed_day, capsys):
    _add_private_utterance(closed_day)

    code = run(["search", "поставку кирпича", "--plain"], closed_day)

    out = capsys.readouterr().out
    assert code == 0
    assert PRIV_MARK not in out
    assert "Пётр подаёт заявку на поставку кирпича" in out


def test_search_semantic_reports_hidden_private(closed_day, capsys, monkeypatch):
    """Старые точки смыслового индекса могли остаться от времён до фильтра —
    такие находки не печатаются, а число скрытых честно уходит в stderr."""
    _add_private_utterance(closed_day)

    class FakeIndex:
        def search(self, query, limit=10):
            return [{"id": 2, "score": 0.9}, {"id": 1, "score": 0.8}]

    import uzel.vector as vector_module

    monkeypatch.setattr(vector_module, "build_index", lambda _settings: FakeIndex())

    code = run(["search", "Ромашка"], closed_day)

    captured = capsys.readouterr()
    assert code == 0
    assert PRIV_MARK not in captured.out
    assert "Пётр подаёт заявку на поставку кирпича" in captured.out
    assert "скрыто приватных: 1" in captured.err


def test_prune_raw_without_yes_deletes_nothing(settings, capsys):
    raw_dir = settings.vault_raw_dir
    raw_dir.mkdir(parents=True)
    stale = raw_dir / f"{date.today() - timedelta(days=40):%Y-%m-%d} — расшифровка.md"
    stale.write_text("", encoding="utf-8")

    code = run(["prune-raw", "--ttl-days", "30"], settings)

    assert code == 0
    assert stale.exists()  # без --yes ничего не удаляет
    out = capsys.readouterr().out
    assert stale.name in out
    assert "добавьте --yes" in out


def test_prune_raw_with_yes_removes_only_stale(settings, capsys):
    raw_dir = settings.vault_raw_dir
    raw_dir.mkdir(parents=True)
    stale = raw_dir / f"{date.today() - timedelta(days=40):%Y-%m-%d} — расшифровка.md"
    stale.write_text("", encoding="utf-8")
    fresh = raw_dir / f"{date.today() - timedelta(days=1):%Y-%m-%d} — расшифровка.md"
    fresh.write_text("", encoding="utf-8")

    code = run(["prune-raw", "--ttl-days", "30", "--yes"], settings)

    assert code == 0
    assert not stale.exists()
    assert fresh.exists()
    assert "Удалено файлов: 1" in capsys.readouterr().out


def test_prune_raw_without_stale_files(settings, capsys):
    code = run(["prune-raw"], settings)

    assert code == 0
    assert "Просроченных расшифровок нет." in capsys.readouterr().out


# ---------- quality-report (ТЗ 2026-08-24, пакет 2) ----------


@pytest.fixture
def quality_db(settings):
    """База с тремя принятыми и двумя помеченными репликами 18.09.2026."""
    day = datetime(2026, 9, 18, 12, 0).timestamp()
    with Storage(settings.db_path) as storage:
        for index in range(3):
            uid = storage.add_utterance(
                uid="dev-1",
                started_at=day + index,
                ended_at=day + index + 5,
                speech_seconds=4.0,
            )
            storage.set_transcript(
                uid,
                text=f"принятая рабочая реплика номер {index}",
                language="ru",
                backend="local",
            )
        short = storage.add_utterance(
            uid="dev-1",
            started_at=day + 100,
            ended_at=day + 105,
            speech_seconds=4.0,
        )
        storage.set_transcript(short, text="ага", language="ru", backend="local")
        storage.mark_quality(short, reason="too_short", enforce=False)
        loop = storage.add_utterance(
            uid="dev-1",
            started_at=day + 200,
            ended_at=day + 205,
            speech_seconds=4.0,
        )
        storage.set_transcript(
            loop, text="так так так так", language="ru", backend="local"
        )
        storage.mark_quality(loop, reason="looped", enforce=False)
    return settings


def test_quality_report_prints_counts_and_reasons(quality_db, capsys):
    code = run(["quality-report"], quality_db)

    out = capsys.readouterr().out
    assert code == 0
    assert "реплик всего" in out and "5" in out
    assert "принято" in out and "3" in out
    assert "помечено" in out and "2" in out
    assert "слишком коротко" in out
    assert "зацикливание" in out
    assert "пометка без отбрасывания" in out


def test_quality_report_day_filters_period(quality_db, capsys):
    code = run(["quality-report", "--day", "2026-09-18"], quality_db)

    assert code == 0
    out = capsys.readouterr().out
    assert "2026-09-18" in out and "5" in out


def test_quality_report_day_without_utterances(settings, capsys):
    code = run(["quality-report", "--day", "2020-01-01"], settings)

    assert code == 0
    assert "За период реплик нет." in capsys.readouterr().out


def test_quality_report_rejects_undecipherable_day(quality_db, capsys):
    code = run(["quality-report", "--day", "32.13.2026"], quality_db)

    assert code == 2
    assert capsys.readouterr().err != ""


def test_quality_report_limit_shows_examples(quality_db, capsys):
    code = run(["quality-report", "--limit", "2"], quality_db)

    assert code == 0
    out = capsys.readouterr().out
    assert "Примеры" in out
    assert "too_short" in out and "ага" in out
    assert "looped" in out and "так так так так" in out


def test_quality_report_enforce_mode_line(settings, capsys):
    run(["quality-report"], settings.model_copy(update={"quality_enforce": True}))

    assert "отбрасывание включено" in capsys.readouterr().out


# ---------- llm-check (ТЗ P5) ----------


def test_llm_check_ready_local_backend_returns_zero(settings, capsys, monkeypatch):
    import httpx

    class TagsResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"models": [{"name": settings.ollama_model}]}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: TagsResponse())

    code = run(["llm-check"], settings)

    out = capsys.readouterr().out
    assert code == 0
    assert "ollama" in out
    assert "локально" in out
    assert "готова" in out
    assert "облако на штатном пути: запрещено" in out


def test_llm_check_without_local_backend_returns_one(settings, capsys):
    settings = settings.model_copy(
        update={"llm_backends": ("groq",), "groq_api_key": "ключ-для-теста"}
    )

    code = run(["llm-check"], settings)

    captured = capsys.readouterr()
    assert code == 1
    assert "нет локальной" in captured.err  # строка-предупреждение на месте


def test_llm_check_probe_answers_through_backend(settings, capsys, monkeypatch):
    import httpx

    class TagsResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"models": [{"name": settings.ollama_model}]}

    class ChatResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": "Москва"}}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: TagsResponse())
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: ChatResponse())

    code = run(["llm-check", "--probe"], settings)

    out = capsys.readouterr().out
    assert code == 0
    assert "Москва" in out  # первые 60 символов ответа на контрольный вопрос
