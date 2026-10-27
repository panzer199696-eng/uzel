"""Окно и трей приложения УЗЕЛ (ТЗ P7).

Окно — это pywebview вокруг страницы ``/ui`` ядра, трей — pystray. Своей
логики приватности здесь нет: кнопки бьют по HTTP в своё же ядро
(``/privacy/…``), чтобы состояние менялось в одном месте.

Тяжёлые импорты (webview, pystray, PIL, uvicorn) живут внутри функций:
``import uzel.desktop`` должен работать там, где GUI нет, и не тянуть
WinForms в тесты.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

# Как часто трей перечитывает /ui/api/status (ТЗ: «раз в несколько секунд»).
TRAY_REFRESH_S = 3.0


@dataclass
class AppOptions:
    host: str = "127.0.0.1"
    port: int = 8770
    window: bool = True  # False — только сервер и трей
    tray: bool = True
    hidden: bool = False  # окно создать скрытым: старт сразу в трей (автозагрузка)
    autoclose_s: float | None = None  # для проверок: закрыть окно через N секунд


def tray_tooltip(status: dict) -> str:
    """Строка подсказки трея. Чистая функция от ответа ``/ui/api/status``."""
    privacy = status.get("privacy") or {}
    if privacy.get("код") == "allowed":
        pending = (status.get("queue") or {}).get("pending")
        tail = f" · в очереди {pending}" if isinstance(pending, int) else ""
        return f"УЗЕЛ · запись идёт{tail}"
    reason = privacy.get("причина") or "причина неизвестна"
    return f"УЗЕЛ · выключена: {reason}"


def tray_title(status: dict) -> str:
    """Подсказка трея: запись + кулон. Windows режет подсказку на 127 символах."""
    from .tray_extras import device_line

    return f"{tray_tooltip(status)}\n{device_line(status)}"[:127]


_TRAY_COLORS = {
    "ok": (61, 168, 79, 255),  # пишем, кулон на связи
    "waiting": (222, 170, 40, 255),  # писать можно, но кулона нет в эфире
    "muted": (201, 61, 61, 255),  # пауза / приват / стоп-зона
}


def tray_image(state: bool | str):
    """Иконка трея 64×64: зелёный — пишем, жёлтый — кулона нет, красный — выключено.

    ``state`` — строка из ``tray_extras.tray_state`` или, по-старому, bool
    «запись выключена». Рисуется кодом: файлов с картинками не заводим.
    """
    from PIL import Image, ImageDraw

    if isinstance(state, bool):
        state = "muted" if state else "ok"
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = _TRAY_COLORS.get(state, _TRAY_COLORS["ok"])
    draw.ellipse((4, 4, size - 5, size - 5), fill=fill)
    draw.ellipse((14, 14, size - 15, size - 15), outline=(255, 255, 255, 170), width=3)
    return image


def window_closer(window, stop: threading.Event) -> threading.Thread:
    """Поток, закрывающий окно по событию ``stop`` (пункт трея «Выход»)."""

    def ждать() -> None:
        stop.wait()
        try:
            window.destroy()
        except Exception:
            pass  # окно уже закрыто руками — выходим тихо

    return threading.Thread(target=ждать, name="uzel-closer", daemon=True)


def close_to_tray(window, stop: threading.Event):
    """Обработчик ``window.events.closing``: крестик прячет окно в трей.

    Раньше крестик закрывал окно, ``webview.start()`` возвращался и гасил
    трей вместе с ядром — запись молча прекращалась (06.10.2026). Теперь
    выйти можно только пунктом трея «Выход» (он ставит ``stop``).
    pywebview отменяет закрытие, если обработчик вернул False.
    """

    def on_closing() -> bool:
        if stop.is_set():
            return True  # «Выход» из трея — закрываем по-настоящему
        try:
            window.hide()
        except Exception:
            pass
        return False

    return on_closing


class CoreServer:
    """Ядро (uvicorn) в фоновом потоке — чтобы окну было что показывать."""

    def __init__(self, settings: Settings, options: AppOptions | None = None):
        self._settings = settings
        self._options = options or AppOptions()
        self._server = None
        self._thread: threading.Thread | None = None
        self._port = self._options.port

    @property
    def url(self) -> str:
        # Слушать «все интерфейсы» можно, подключиться к 0.0.0.0 на Windows —
        # нет: окно не дожидалось ядра и закрывалось. Ходим через петлю.
        host = self._options.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{self._port}"

    def start(self) -> None:
        import socket

        import uvicorn

        from .app import create_app
        from .security import ensure_safe_listen_host

        host, port = self._options.host, self._options.port
        # Проверка по фактическому host, а не по settings.host: окно можно
        # попросить слушать и не петлевой адрес (P11, находка 1).
        ensure_safe_listen_host(host, self._settings.api_token)
        if port:
            # Занятый порт должен заканчиваться понятной строкой, а не
            # простынёй трейсбека изнутри uvicorn.
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind((host, port))
            except OSError as exc:
                raise RuntimeError(
                    f"порт {port} уже занят: ядро уже запущено?"
                ) from exc
            finally:
                probe.close()

        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(self._settings),
                host=host,
                port=port,
                log_level=self._settings.log_level.lower(),
            )
        )
        self._thread = threading.Thread(
            target=self._server.run, name="uzel-core", daemon=True
        )
        self._thread.start()

    def _resolve_port(self) -> None:
        """Порт 0 («любой свободный») — узнаём фактический после подъёма."""
        if self._port != 0:
            return
        for server in getattr(self._server, "servers", None) or []:
            for sock in server.sockets:
                self._port = sock.getsockname()[1]
                return

    def wait_ready(self, timeout_s: float = 20.0) -> bool:
        """Опрашивает ``GET /health``, пока ядро не ответит. False — не дождались."""
        import time

        import httpx

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._server is not None and self._server.started:
                self._resolve_port()
                try:
                    response = httpx.get(f"{self.url}/health", timeout=2.0)
                    if response.status_code == 200:
                        return True
                # Не OSError: httpx.ConnectError наследуется от HTTPError,
                # к OSError отношения не имеет — узкий except пропускал бы
                # обычный отказ соединения наружу и ронял запуск.
                except Exception:
                    pass
            time.sleep(0.1)
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10.0)


class TrayController:
    """Трей: иконка, меню и поток обновления по ``/ui/api/status``.

    Поток обязан завершаться при выходе — его гасит ``stop``-событие.
    """

    def __init__(
        self,
        server: CoreServer,
        stop: threading.Event,
        window=None,
        settings: Settings | None = None,
    ):
        from .tray_extras import (
            BatchRun,
            BatteryAlerts,
            BridgeProcess,
            ReconnectNotice,
            load_tray_state,
        )

        self._server = server
        self._stop = stop
        self._window = window
        self._settings = settings
        self._icon = None
        self._thread: threading.Thread | None = None
        self._status: dict = {}
        self._last_menu_key: tuple | None = None
        data_dir = (
            settings.data_dir if settings is not None else Path.home() / "uzel-data"
        )
        self._logs = data_dir / "logs"
        self._state_path = data_dir / "tray.json"
        self._state = load_tray_state(self._state_path)
        self.bridge = BridgeProcess(
            server.url,
            self._logs / "bridge-pc.log",
            address=self._state.get("bridge_address"),
        )
        self.batch = BatchRun(self._logs / "batch.log")
        self._battery = BatteryAlerts()
        self._reconnect = ReconnectNotice()

    def start(self) -> None:
        import pystray

        from .tray_extras import device_line

        Item = pystray.MenuItem
        self._icon = pystray.Icon(
            "УЗЕЛ",
            icon=tray_image("waiting"),
            title="УЗЕЛ",
            menu=pystray.Menu(
                Item(lambda _i: device_line(self._status), None, enabled=False),
                Item("Открыть окно", self._open_window, default=True),
                pystray.Menu.SEPARATOR,
                Item(
                    "Мост с ПК (телефон кулон не увидит)",
                    self._toggle_bridge,
                    checked=lambda _i: self.bridge.alive,
                ),
                Item(
                    lambda _i: (
                        "Расшифровка идёт…"
                        if self.batch.running
                        else "Расшифровать сейчас"
                    ),
                    self._run_batch,
                    enabled=lambda _i: not self.batch.running,
                ),
                pystray.Menu.SEPARATOR,
                Item("Пауза 30 минут", self._mute),
                Item("Приват", self._pause),
                Item("Возобновить запись", self._unmute),
                pystray.Menu.SEPARATOR,
                Item("Отчёт за сегодня", self._open_report),
                Item("Журнал моста", self._open_bridge_log),
                pystray.Menu.SEPARATOR,
                Item("Выход", self._exit),
            ),
        )
        self._icon.run_detached()
        if self._state.get("bridge"):
            self.bridge.start()  # мост был включён до перезапуска — включаем снова
        self._thread = threading.Thread(
            target=self._refresh_loop, name="uzel-tray", daemon=True
        )
        self._thread.start()

    # мост, расшифровка, файлы

    def _toggle_bridge(self, *_args) -> None:
        from .tray_extras import save_tray_state

        if self.bridge.alive:
            self.bridge.stop()
            self._state["bridge"] = False
        else:
            self.bridge.start()
            self._state["bridge"] = True
        save_tray_state(self._state_path, self._state)
        self._refresh_menu(force=True)

    def _run_batch(self, *_args) -> None:
        def done(summary: str) -> None:
            self._notify(summary)
            self._refresh_menu(force=True)

        if self.batch.start(done):
            self._notify("Расшифровка запущена — уведомлю, когда закончится")
            self._refresh_menu(force=True)

    def _open_report(self, *_args) -> None:
        from datetime import date

        from .tray_extras import open_path

        report_dir = getattr(self._settings, "report_dir", None)
        if report_dir is None:
            self._notify("Папка отчётов не задана (UZEL_REPORT_DIR)")
            return
        path = Path(report_dir) / f"{date.today().isoformat()}.txt"
        if not open_path(path):
            self._notify("Отчёта за сегодня ещё нет — он пишется после расшифровки")

    def _open_bridge_log(self, *_args) -> None:
        from .tray_extras import open_path

        if not open_path(self._logs / "bridge-pc.log"):
            self._notify("Мост с ПК ещё не запускался")

    def _notify(self, text: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(text, "УЗЕЛ")
        except Exception:
            log.info("уведомление не показано: %s", text)

    def _refresh_menu(self, *, force: bool = False) -> None:
        """Перерисовать меню, только если поменялось видимое: на Windows
        update_menu() пересобирает его целиком."""
        from .tray_extras import device_line

        key = (device_line(self._status), self.bridge.alive, self.batch.running)
        if not force and key == self._last_menu_key:
            return
        self._last_menu_key = key
        try:
            self._icon.update_menu()
        except Exception:
            pass

    # пункты меню приватности бьют по HTTP в своё же ядро

    def _mute(self, *_args) -> None:
        self._post("/privacy/mute?minutes=30")

    def _pause(self, *_args) -> None:
        self._post("/privacy/pause")

    def _unmute(self, *_args) -> None:
        self._post("/privacy/unmute")

    def _post(self, path: str) -> None:
        import httpx

        try:
            httpx.post(f"{self._server.url}{path}", timeout=5.0)
        except Exception:
            # Ядро недоступно — трей не падает. Именно Exception: ошибки
            # httpx не наследуются от OSError, и пункт меню уронил бы трей.
            log.warning("не достучался до ядра: %s", path)

    def _open_window(self, *_args) -> None:
        if self._window is None:
            return
        try:
            self._window.show()
        except Exception:
            pass  # окно уже закрыто — просто ничего не делаем

    def _exit(self, *_args) -> None:
        self._stop.set()

    def _refresh_loop(self) -> None:
        import httpx

        while not self._stop.wait(TRAY_REFRESH_S):
            try:
                response = httpx.get(f"{self._server.url}/ui/api/status", timeout=3.0)
                status = response.json()
            except Exception:
                continue  # ядро ещё не отвечает — попробуем в следующий такт
            self.apply(status)

    def apply(self, status: dict, *, now: float | None = None) -> None:
        """Красит иконку, подписывает трей, следит за мостом и зарядом."""
        import time

        from .tray_extras import device_info, tray_state

        self._status = status
        now = time.time() if now is None else now
        for message in (
            self._battery.observe(device_info(status).get("battery_percent")),
            self._reconnect.observe(status),
            self._watch_bridge(status, now),
        ):
            if message:
                self._notify(message)
        if self._icon is None:
            return
        state = tray_state(status)
        try:
            if state != getattr(self, "_drawn_state", None):
                self._icon.icon = tray_image(state)
                self._drawn_state = state
            self._icon.title = tray_title(status)
        except Exception:
            pass  # погасший трей не должен ронять поток обновления
        self._refresh_menu()

    def _watch_bridge(self, status: dict, now: float) -> str | None:
        """Сторож: поднимает упавший мост и перезапускает зависший.

        Выключенный кулон — не повод: мост тогда жив и отчитывается
        «кулон отключён». Повод — мост молчит (12.10 завис на WinError).
        """
        from .tray_extras import BRIDGE_GRACE_S, bridge_hung

        if not self._state.get("bridge"):
            return None
        last = getattr(self, "_last_bridge_fix", 0.0)
        if now - last < BRIDGE_GRACE_S:
            return None  # не чаще раза в минуту — без карусели перезапусков
        if not self.bridge.alive:
            self._last_bridge_fix = now
            self.bridge.start()
            log.warning("мост с ПК упал — поднял заново")
            return None
        if bridge_hung(
            status, alive=True, started_at=self.bridge.started_at, now=now
        ):
            self._last_bridge_fix = now
            self.bridge.restart()
            log.warning("мост с ПК завис — перезапустил (%d-й раз)", self.bridge.restarts)
            return "Мост с ПК завис — перезапустил"
        return None

    def shutdown(self) -> None:
        self._stop.set()
        # Мост — дочерний процесс: при выходе гасим, иначе он держит кулон.
        # Галочку в tray.json не трогаем — при следующем запуске включится.
        self.bridge.stop()
        if self._thread is not None:
            self._thread.join(timeout=TRAY_REFRESH_S + 2.0)
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass


def run_app(settings: Settings, options: AppOptions | None = None) -> int:
    """Поднимает ядро, трей и окно. Код возврата — для CLI и ярлыка."""
    options = options or AppOptions()
    server = CoreServer(settings, options)
    try:
        server.start()
    except RuntimeError as exc:
        print(exc)
        return 1

    if not server.wait_ready():
        print("ядро не поднялось за отведённое время — посмотрите журнал")
        server.stop()
        return 1

    stop = threading.Event()

    window = None
    if options.window:
        import webview

        window = webview.create_window(
            "УЗЕЛ",
            f"{server.url}/ui",
            width=1100,
            height=760,
            hidden=options.hidden and options.tray,  # без трея скрытое окно не открыть
        )

    tray = None
    if options.tray:
        tray = TrayController(server, stop, window, settings)
        tray.start()

    # «Выход» в трее ставит событие, но webview.start() блокирует главный
    # поток до закрытия окна: без этого сторожа пункт меню молча ничего
    # не делал — а это конфигурация по умолчанию (окно + трей).
    if options.window and options.tray:
        window_closer(window, stop).start()
        if options.autoclose_s is None:  # проверкам с автозакрытием окно нужно закрыть
            window.events.closing += close_to_tray(window, stop)

    timer = None
    if options.autoclose_s is not None:
        if options.window:

            def close_window() -> None:
                try:
                    window.destroy()
                except Exception:
                    pass

            timer = threading.Timer(options.autoclose_s, close_window)
        else:
            timer = threading.Timer(options.autoclose_s, stop.set)
        timer.daemon = True
        timer.start()

    try:
        if options.window:
            import webview

            webview.start()  # блокируется до закрытия окна
        else:
            stop.wait()  # сервер с треем без окна — ждём «Выход» или таймер
    finally:
        stop.set()
        if timer is not None:
            timer.cancel()
        if tray is not None:
            tray.shutdown()
        server.stop()
    return 0


def _ensure_streams(log_path: Path) -> None:
    """Под ``pythonw`` (ярлык автозагрузки) ``sys.stdout``/``stderr`` — None:
    uvicorn на настройке журнала падает «Unable to configure formatter»,
    окно не поднимается вовсе и молча. Без консоли — пишем в файл."""
    import sys

    if sys.stdout is not None and sys.stderr is not None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    """Точка входа ``ops/запуск-приложения.py`` (его зовёт ярлык автозагрузки)."""
    import argparse

    from .config import get_settings
    from .logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="uzel-app", description="УЗЕЛ — окно и трей")
    parser.add_argument("--host", default=None, help="адрес ядра")
    parser.add_argument("--port", type=int, default=None, help="порт ядра")
    parser.add_argument("--no-window", action="store_true", help="только сервер и трей")
    parser.add_argument("--no-tray", action="store_true", help="без трея")
    parser.add_argument(
        "--hidden", action="store_true", help="стартовать свёрнутым в трей"
    )
    parser.add_argument(
        "--autoclose",
        type=float,
        default=None,
        help="закрыть окно через N секунд (для проверок)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    _ensure_streams(settings.data_dir / "logs" / "app.log")
    setup_logging(settings.log_level)
    options = AppOptions(
        host=args.host or settings.host,
        port=args.port if args.port is not None else settings.port,
        window=not args.no_window,
        tray=not args.no_tray,
        hidden=args.hidden,
        autoclose_s=args.autoclose,
    )
    try:
        return run_app(settings, options)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
