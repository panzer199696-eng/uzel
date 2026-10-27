"""Стоп-режимы: FR-070, FR-071."""

from __future__ import annotations

from datetime import datetime
from datetime import time as dtime

import pytest
from uzel.privacy import PrivacyGate, QuietWindow, parse_window


def at(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


# --- разбор расписания ---


def test_parse_simple_window():
    window = parse_window("10:00-12:00")

    assert window.start == dtime(10, 0)
    assert window.end == dtime(12, 0)
    assert window.weekdays == frozenset(range(7))


def test_parse_window_with_weekdays():
    window = parse_window("09:30-18:00/пн,вт,ср,чт,пт")

    assert window.weekdays == frozenset({0, 1, 2, 3, 4})
    assert "пн" in window.describe()


@pytest.mark.parametrize(
    "spec", ["", "10:00", "25:00-26:00", "10:00-12:00/абв", "10:00-12:00/"]
)
def test_parse_rejects_broken_specs(spec):
    with pytest.raises(ValueError):
        parse_window(spec)


def test_window_contains_only_inside_the_interval():
    window = parse_window("10:00-12:00")

    assert window.contains(datetime(2026, 8, 24, 10, 0))
    assert window.contains(datetime(2026, 8, 24, 11, 59))
    assert not window.contains(datetime(2026, 8, 24, 12, 0))  # правая граница открыта
    assert not window.contains(datetime(2026, 8, 24, 9, 59))


def test_window_across_midnight():
    window = parse_window("22:00-06:00")

    assert window.contains(datetime(2026, 8, 24, 23, 30))
    assert window.contains(datetime(2026, 8, 24, 2, 0))
    assert not window.contains(datetime(2026, 8, 24, 12, 0))


def test_window_respects_weekday():
    window = parse_window("10:00-12:00/сб,вс")

    assert not window.contains(datetime(2026, 8, 24, 11, 0))  # понедельник
    assert window.contains(datetime(2026, 8, 29, 11, 0))  # суббота


# --- заглушка ---


@pytest.fixture
def gate(tmp_path) -> PrivacyGate:
    return PrivacyGate(tmp_path / "privacy.json")


def test_recording_is_allowed_by_default(gate):
    assert gate.is_muted() is False
    assert gate.state().reason == "разрешено"


def test_manual_mute_expires_on_its_own(gate):
    now = at("2026-08-24T10:00:00")
    gate.mute(10, now=now)

    assert gate.is_muted(now + 60) is True
    assert gate.is_muted(now + 9 * 60) is True
    assert gate.is_muted(now + 11 * 60) is False


def test_mute_reports_deadline(gate):
    now = at("2026-08-24T10:00:00")
    gate.mute(30, now=now)

    state = gate.state(now + 60)
    assert state.muted is True
    assert state.muted_until == pytest.approx(now + 1800)
    assert "10:30" in state.as_dict()["до"]


def test_mute_extends_but_never_shortens(gate):
    now = at("2026-08-24T10:00:00")
    gate.mute(60, now=now)
    gate.mute(5, now=now)  # короче — не должно отменять длинную заглушку

    assert gate.is_muted(now + 30 * 60) is True


def test_mute_rejects_nonpositive_duration(gate):
    with pytest.raises(ValueError):
        gate.mute(0)


def test_unmute_resumes_recording(gate):
    gate.mute(60)
    gate.unmute()

    assert gate.is_muted() is False


def test_pause_lasts_until_explicitly_lifted(gate):
    gate.pause()

    assert gate.is_muted(at("2027-01-01T00:00:00")) is True
    gate.unmute()
    assert gate.is_muted() is False


def test_mute_survives_restart(tmp_path):
    """Перезапуск ядра не имеет права включить запись обратно."""
    path = tmp_path / "privacy.json"
    now = at("2026-08-24T10:00:00")
    PrivacyGate(path).mute(60, now=now)

    revived = PrivacyGate(path)

    assert revived.is_muted(now + 60) is True


def test_pause_survives_restart(tmp_path):
    path = tmp_path / "privacy.json"
    PrivacyGate(path).pause()

    assert PrivacyGate(path).is_muted() is True


@pytest.mark.parametrize("content", ["{не json", "[1, 2]", '{"muted_until": "мусор"}'])
def test_broken_state_file_mutes_recording(tmp_path, content):
    """P11.2, находка 10: нечитаемый файл мог означать «пауза» — отказ закрытым."""
    path = tmp_path / "privacy.json"
    path.write_text(content, encoding="utf-8")

    state = PrivacyGate(path).state()

    assert state.muted is True
    assert state.code == "bad_state"


def test_broken_state_file_recovers_after_unmute(tmp_path):
    path = tmp_path / "privacy.json"
    path.write_text("{не json", encoding="utf-8")
    gate = PrivacyGate(path)

    gate.unmute()  # явное действие владельца пишет целый файл

    assert gate.is_muted() is False
    assert PrivacyGate(path).is_muted() is False


def test_broken_state_file_fixed_by_another_process(tmp_path):
    path = tmp_path / "privacy.json"
    path.write_text("{не json", encoding="utf-8")
    сервер = PrivacyGate(path)
    assert сервер.is_muted() is True

    PrivacyGate(path).unmute()

    assert сервер.is_muted() is False


# --- расписание ---


def test_schedule_mutes_inside_the_window(tmp_path):
    gate = PrivacyGate(tmp_path / "p.json", (parse_window("10:00-12:00"),))

    assert gate.is_muted(at("2026-08-24T11:00:00")) is True
    assert gate.is_muted(at("2026-08-24T13:00:00")) is False
    assert "стоп-зона" in gate.state(at("2026-08-24T11:00:00")).reason


def test_manual_mute_wins_outside_the_window(tmp_path):
    gate = PrivacyGate(tmp_path / "p.json", (parse_window("10:00-12:00"),))
    now = at("2026-08-24T15:00:00")

    gate.mute(30, now=now)

    assert gate.is_muted(now + 60) is True


def test_several_windows_are_all_checked(tmp_path):
    gate = PrivacyGate(
        tmp_path / "p.json",
        (parse_window("09:00-10:00"), parse_window("14:00-15:00")),
    )

    assert gate.is_muted(at("2026-08-24T09:30:00")) is True
    assert gate.is_muted(at("2026-08-24T14:30:00")) is True
    assert gate.is_muted(at("2026-08-24T12:00:00")) is False


def test_build_gate_mutes_on_broken_window_instead_of_skipping(settings):
    """P11.2, находка 9: опечатка в расписании не должна молча включать запись."""
    from uzel.privacy import build_gate

    gate = build_gate(
        settings.model_copy(update={"quiet_windows": ("10:00-12:00", "мусор")})
    )

    assert len(gate.windows) == 1  # не падаем — окно и CLI должны показать причину
    state = gate.state(at("2026-08-24T15:00:00"))  # вне исправного окна
    assert state.muted is True
    assert state.code == "bad_schedule"
    assert "мусор" in state.reason


def test_batch_takes_allow_cloud_from_the_same_policy(settings, monkeypatch):
    """Строка «облако запрещено» и батч читают одно и то же место правды."""
    import inspect

    import uzel.privacy
    from uzel.worker import Worker

    assert uzel.privacy.BATCH_ALLOW_CLOUD is False
    default = inspect.signature(Worker.run_batch).parameters["allow_cloud"].default
    assert default is None  # None → privacy.BATCH_ALLOW_CLOUD при вызове


def test_quiet_window_describe_is_readable():
    assert QuietWindow(dtime(10, 0), dtime(12, 0)).describe() == "10:00–12:00 ежедневно"


# --- состояние общее для нескольких процессов ---


def test_gate_sees_mute_made_by_another_process(tmp_path):
    """Окно на ПК и CLI — разные процессы над одним файлом состояния.

    Без сверки файла живой сервер показывал бы «запись идёт» после того,
    как её выключили из терминала.
    """
    path = tmp_path / "privacy.json"
    сервер = PrivacyGate(path)
    терминал = PrivacyGate(path)

    терминал.pause()

    assert сервер.state().code == "paused"

    терминал.unmute()

    assert сервер.is_muted() is False


def test_gate_does_not_shorten_mute_set_elsewhere(tmp_path):
    path = tmp_path / "privacy.json"
    окно = PrivacyGate(path)
    трей = PrivacyGate(path)
    now = at("2026-09-19T10:00:00")

    окно.mute(60, now=now)
    трей.mute(5, now=now)

    assert трей.state(now).muted_until == pytest.approx(now + 3600)
