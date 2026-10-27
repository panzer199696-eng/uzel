"""Окно и трей (ТЗ P7) — без запуска GUI.

Тестируются чистые функции и сервер ядра в потоке; webview и pystray в тестах
не поднимаются.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from uzel.desktop import AppOptions, CoreServer, tray_image, tray_tooltip

RECORDING_STATUS = {
    "version": "0.7.0",
    "privacy": {"запись": "идёт", "причина": "разрешено", "код": "allowed", "до": None},
    "queue": {"pending": 12, "processing": 0},
    "device": {"known": False, "reason": "мост не выходил на связь"},
}

MUTED_STATUS = {
    "version": "0.7.0",
    "privacy": {
        "запись": "выключена",
        "причина": "ручная заглушка",
        "код": "manual",
        "до": "2026-09-19T19:00:00",
    },
    "queue": {"pending": 0, "processing": 0},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_tooltip_differs_between_recording_and_muted():
    recording = tray_tooltip(RECORDING_STATUS)
    muted = tray_tooltip(MUTED_STATUS)

    assert "УЗЕЛ" in recording and "УЗЕЛ" in muted
    assert recording != muted
    assert "запись идёт" in recording
    assert "в очереди 12" in recording
    assert "ручная заглушка" in muted


def test_tray_image_is_64px_and_changes_color():
    ok = tray_image(False)  # пишется — зелёный
    off = tray_image(True)  # выключено — красный

    assert ok.size == (64, 64)
    assert off.size == (64, 64)
    assert ok.getpixel((32, 32)) != off.getpixel((32, 32))
    assert ok.getpixel((32, 32)) == (61, 168, 79, 255)
    assert off.getpixel((32, 32)) == (201, 61, 61, 255)


def test_core_server_starts_answers_health_and_stops(settings):
    server = CoreServer(
        settings, AppOptions(port=free_port(), window=False, tray=False)
    )
    try:
        server.start()
        assert server.wait_ready(timeout_s=20) is True

        response = httpx.get(f"{server.url}/health", timeout=5.0)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # С P11 /health отдаёт только живость и версию: он единственный
        # эндпоинт без токена, состояние приватности туда не отдаём.
        assert "privacy" not in body
        privacy = httpx.get(f"{server.url}/privacy", timeout=5.0).json()
        assert privacy["код"] == "allowed"
    finally:
        server.stop()

    # после stop() поток ядра закончился
    assert not server._thread.is_alive()


def test_core_server_on_busy_port_gives_clear_error(settings):
    first = CoreServer(settings, AppOptions(port=free_port(), window=False, tray=False))
    first.start()
    try:
        assert first.wait_ready(timeout_s=20)

        second = CoreServer(
            settings, AppOptions(port=first._port, window=False, tray=False)
        )
        with pytest.raises(RuntimeError, match="уже занят"):
            second.start()
    finally:
        first.stop()


# --- отказоустойчивость трея ---


def test_tray_menu_survives_dead_core(settings):
    """Ядро не отвечает — пункт меню молча проигрывает, а не роняет трей.

    Ошибки httpx не наследуются от OSError, и узкий `except OSError`
    пропускал отказ соединения наружу, в поток pystray.
    """
    import threading

    from uzel.desktop import TrayController

    server = CoreServer(
        settings, AppOptions(port=free_port(), window=False, tray=False)
    )
    tray = TrayController(server, threading.Event())

    tray._mute()  # ничего не поднято по этому адресу
    tray._pause()
    tray._unmute()


def test_window_closer_closes_window_on_exit():
    """«Выход» в трее обязан закрывать окно: webview.start() блокирует поток."""
    import threading

    from uzel.desktop import window_closer

    class ОкноПустышка:
        def __init__(self) -> None:
            self.закрыто = threading.Event()

        def destroy(self) -> None:
            self.закрыто.set()

    окно = ОкноПустышка()
    stop = threading.Event()
    поток = window_closer(окно, stop)
    поток.start()

    assert not окно.закрыто.is_set()
    stop.set()
    поток.join(timeout=5)

    assert окно.закрыто.is_set()


def test_window_closer_ignores_already_closed_window():
    import threading

    from uzel.desktop import window_closer

    class СломанноеОкно:
        def destroy(self) -> None:
            raise RuntimeError("окно уже закрыто")

    stop = threading.Event()
    поток = window_closer(СломанноеОкно(), stop)
    поток.start()
    stop.set()
    поток.join(timeout=5)

    assert not поток.is_alive()


def test_close_to_tray_hides_window_instead_of_closing():
    """Крестик окна гасил ядро вместе с треем (06.10.2026) — теперь прячет в трей."""
    import threading

    from uzel.desktop import close_to_tray

    class Окно:
        скрыто = False

        def hide(self) -> None:
            self.скрыто = True

    окно = Окно()
    stop = threading.Event()
    обработчик = close_to_tray(окно, stop)

    assert обработчик() is False  # pywebview отменяет закрытие
    assert окно.скрыто


def test_close_to_tray_lets_exit_from_tray_close_window():
    import threading

    from uzel.desktop import close_to_tray

    class Окно:
        def hide(self) -> None:
            raise AssertionError("при выходе прятать нельзя")

    stop = threading.Event()
    stop.set()

    assert close_to_tray(Окно(), stop)() is True


def test_hidden_flag_parsed(monkeypatch):
    from uzel import desktop

    пойманное = {}
    monkeypatch.setattr(
        desktop,
        "run_app",
        lambda settings, options: пойманное.setdefault("o", options) and 0,
    )
    desktop.main(["--hidden"])

    assert пойманное["o"].hidden is True


def test_pythonw_without_streams_gets_log_file(tmp_path, monkeypatch):
    """Ярлык автозагрузки идёт через pythonw: без потоков uvicorn падал (05.10.2026)."""
    import sys

    from uzel.desktop import _ensure_streams

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = tmp_path / "logs" / "app.log"
    _ensure_streams(log)
    assert sys.stdout is not None and sys.stderr is not None
    print("строка в журнал")
    sys.stdout.flush()
    assert "строка в журнал" in log.read_text(encoding="utf-8")
    sys.stdout.close()


def test_console_streams_left_alone(tmp_path):
    import sys

    from uzel.desktop import _ensure_streams

    before = sys.stdout
    _ensure_streams(tmp_path / "app.log")
    assert sys.stdout is before
    assert not (tmp_path / "app.log").exists()
