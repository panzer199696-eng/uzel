"""Командная строка на английском интерфейсе (27.10.2026): справка, итоги, ошибки."""

from __future__ import annotations

import pytest
from test_cli import closed_day, filled, run  # noqa: F401 — фикстуры
from uzel import cli, i18n


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("UZEL_LANG", "en")
    i18n.set_language("en")


def test_help_is_english(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "UZEL core" in out
    assert "search the memory" in out
    assert "night batch" not in out  # подкоманды worker — в своей справке
    assert "поиск по памяти" not in out


def test_main_takes_language_from_settings(monkeypatch, settings, capsys):
    """main() выбирает язык до справки argparse — по настройке, а не по системе."""
    i18n.set_language("ru")
    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"lang": "en"}))
    with pytest.raises(SystemExit):
        cli.main(["worker", "--help"])
    assert "return interrupted work to the queue" in capsys.readouterr().out
    assert i18n.get_language() == "en"


def test_stats_and_search_in_english(filled, capsys):  # noqa: F811
    run(["stats"], filled)
    out = capsys.readouterr().out
    assert "Database:" in out and "Ingest queue:" in out and "utterances" in out

    assert run(["search", "нет такого", "--plain"], filled) == 1
    assert "Nothing found." in capsys.readouterr().out
    run(["search", "другое", "--plain"], filled)
    assert "speaker not labelled" in capsys.readouterr().out


def test_show_and_errors_in_english(closed_day, capsys):  # noqa: F811
    assert run(["show", "999"], closed_day) == 1
    assert "Conversation 999 not found." in capsys.readouterr().err
    assert run(["export", "--day", "вчера"], closed_day) == 2
    assert "Cannot parse the date" in capsys.readouterr().err


def test_privacy_and_quality_in_english(settings, capsys):
    run(["privacy"], settings)
    out = capsys.readouterr().out
    assert "причина: allowed" in out  # ключи — машинные, значения — по языку
    run(["quality-report"], settings)
    out = capsys.readouterr().out
    assert "No utterances in the period." in out
    assert "Mode: flag without rejecting" in out
