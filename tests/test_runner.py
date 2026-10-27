"""Тесты автономного раннера: защиты, которые некому проверить вручную.

Раннер работает две недели без человека, поэтому его предохранители (стоп-файл,
суточный лимит, остановка при холостых прогонах) должны быть проверены тестом,
а не надеждой.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).resolve().parent.parent / "ops" / "runner.py"
if not RUNNER_PATH.exists():  # ops/ — служебная папка, в публичную выгрузку не входит
    pytest.skip("нет ops/runner.py", allow_module_level=True)

spec = importlib.util.spec_from_file_location("uzel_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
sys.modules["uzel_runner"] = runner
spec.loader.exec_module(runner)


@pytest.fixture
def ops(tmp_path, monkeypatch):
    """Переносит пути раннера во временную папку."""
    ops_dir = tmp_path / "ops"
    ops_dir.mkdir()
    monkeypatch.setattr(runner, "OPS", ops_dir)
    monkeypatch.setattr(runner, "QUEUE", ops_dir / "очередь.md")
    monkeypatch.setattr(runner, "RULES", ops_dir / "правила.md")
    monkeypatch.setattr(runner, "STOP", ops_dir / "STOP")
    monkeypatch.setattr(runner, "JOURNAL", ops_dir / "журнал.jsonl")
    monkeypatch.setattr(runner, "LOGS", ops_dir / "logs")
    return ops_dir


def journal_record(hours_ago: float, committed: bool) -> dict:
    started = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"started": started.isoformat(), "committed": committed, "package": "P0"}


def test_next_package_takes_first_unchecked(ops):
    (ops / "очередь.md").write_text(
        "# Очередь\n\n"
        "- [x] **P0 — сделано**\n"
        "  тело нулевого\n\n"
        "- [ ] **P1 — локальный STT**\n"
        "  тело первого\n"
        "  вторая строка\n\n"
        "- [ ] **P2 — фильтр**\n"
        "  тело второго\n",
        encoding="utf-8",
    )
    code, body = runner.next_package()
    assert code == "P1"
    assert "тело первого" in body
    assert "вторая строка" in body
    assert "тело второго" not in body


def test_next_package_understands_fractional_number(ops):
    """Довесок P0.1 — тоже пакет: без этого раннер перепрыгивал его на P1."""
    (ops / "очередь.md").write_text(
        "- [x] **P0 — сделано**\n"
        "  тело\n\n"
        "- [ ] **P0.1 — сверить индекс**\n"
        "  тело довеска\n\n"
        "- [ ] **P1 — следующий**\n"
        "  тело первого\n",
        encoding="utf-8",
    )
    code, body = runner.next_package()
    assert code == "P0.1"
    assert "тело довеска" in body
    assert "тело первого" not in body


def test_next_package_returns_none_when_all_done(ops):
    (ops / "очередь.md").write_text("- [x] **P0 — сделано**\n", encoding="utf-8")
    assert runner.next_package() is None


def test_runs_last_24h_ignores_older(ops):
    journal = [
        journal_record(30, True),
        journal_record(5, True),
        journal_record(1, False),
    ]
    assert runner.runs_last_24h(journal) == 2


def test_runs_last_24h_survives_broken_line(ops):
    (ops / "журнал.jsonl").write_text(
        json.dumps(journal_record(1, True), ensure_ascii=False) + "\n"
        "не json\n"
        '{"started": "мусор", "committed": true}\n',
        encoding="utf-8",
    )
    journal = runner.read_journal()
    assert runner.runs_last_24h(journal) == 1


def test_idle_streak_counts_trailing_runs_without_commit(ops):
    journal = [
        journal_record(10, False),
        journal_record(8, True),
        journal_record(5, False),
        journal_record(2, False),
    ]
    assert runner.idle_streak(journal) == 2


def test_idle_streak_zero_after_commit(ops):
    journal = [journal_record(5, False), journal_record(2, True)]
    assert runner.idle_streak(journal) == 0


def test_stop_file_blocks_run(ops, monkeypatch, capsys):
    (ops / "STOP").write_text("причина", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runner.py"])
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("сессия не должна запускаться"),
    )
    assert runner.main() == 0
    assert "STOP" in capsys.readouterr().out


def test_daily_limit_blocks_run(ops, monkeypatch, capsys):
    (ops / "очередь.md").write_text("- [ ] **P1 — тест**\n  тело\n", encoding="utf-8")
    with (ops / "журнал.jsonl").open("w", encoding="utf-8") as fh:
        for _ in range(runner.MAX_RUNS_PER_DAY):
            fh.write(json.dumps(journal_record(1, True), ensure_ascii=False) + "\n")
    monkeypatch.setattr(sys, "argv", ["runner.py"])
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("сессия не должна запускаться"),
    )
    assert runner.main() == 0
    assert "лимит" in capsys.readouterr().out.lower()


def test_idle_streak_creates_stop_file(ops, monkeypatch):
    (ops / "очередь.md").write_text("- [ ] **P1 — тест**\n  тело\n", encoding="utf-8")
    with (ops / "журнал.jsonl").open("w", encoding="utf-8") as fh:
        for _ in range(runner.IDLE_RUNS_BEFORE_STOP):
            fh.write(json.dumps(journal_record(1, False), ensure_ascii=False) + "\n")
    monkeypatch.setattr(sys, "argv", ["runner.py"])
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("сессия не должна запускаться"),
    )
    assert runner.main() == 1
    assert (ops / "STOP").exists()
    assert "вхолостую" in (ops / "STOP").read_text(encoding="utf-8")


def test_prompt_carries_package_and_paths(ops):
    prompt = runner.build_prompt("P3", "**P3 — воркер**\n  тело пакета")
    assert "P3" in prompt
    assert "тело пакета" in prompt
    assert "ops/правила-автономии.md" in prompt
    # Холодный контекст: сессия обязана получить путь к проекту в самом промпте.
    assert str(runner.PROJECT_ROOT) in prompt
