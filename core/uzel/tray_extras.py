"""Мост с ПК, ручная расшифровка и заряд кулона — для трея (12.10.2026).

Кулон владелец выключает и включает руками, поэтому «кулона нет в эфире» —
обычное состояние, а не авария: трей красит иконку жёлтым и молчит. Сторож
перезапускает только **сам мост**, если тот завис (его отчёты перестали
доходить до ядра), а не потому, что кулон выключен.

Решения вынесены в чистые функции — их проверяют тесты без Bluetooth и GUI.
Процессы (мост, батч) запускаются через подменяемую фабрику ``popen``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import i18n
from .i18n import t

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

# uid, под которым ходит мост с ПК (телефон — phone-1).
BRIDGE_UID = "pendant-pc"
# Пока кулон выключен, мост ищет его не реже чем раз в столько секунд:
# включил кулон — подхватится быстро, а не через минуту.
BRIDGE_RECONNECT_MAX_S = 10.0
# Мост шлёт отчёт раз в 30 с даже без кулона. Тишина дольше — мост завис
# (12.10 поймали: после WinError -2147024890 цикл встал без единой строки).
BRIDGE_HANG_S = 90.0
# После запуска даём мосту время на первый отчёт, прежде чем судить.
BRIDGE_GRACE_S = 60.0
BATTERY_THRESHOLDS = (20, 10)

# CREATE_NO_WINDOW: под pythonw у дочернего процесса не мигает консоль.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ---------------------------------------------------------------- состояние


def device_info(status: dict) -> dict:
    return status.get("device") or {}


def pendant_online(status: dict) -> bool:
    """Кулон реально на связи: свежий отчёт моста и открытая сессия."""
    device = device_info(status)
    return bool(
        device.get("known") and device.get("connected") and not device.get("stale")
    )


def tray_state(status: dict) -> str:
    """``muted`` — запись выключена; ``ok`` — пишем и кулон на связи;
    ``waiting`` — писать можно, но звука нет (кулон выключен или далеко)."""
    if (status.get("privacy") or {}).get("код") != "allowed":
        return "muted"
    return "ok" if pendant_online(status) else "waiting"


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 90:
        return t("tray.ago.just_now")
    minutes = int(seconds // 60)
    if minutes < 60:
        return t("tray.ago.minutes", m=minutes)
    return t("tray.ago.hours", h=minutes // 60, m=minutes % 60)


def device_line(status: dict) -> str:
    """Строка состояния кулона для меню и подсказки трея."""
    device = device_info(status)
    if not device.get("known"):
        return t("tray.device.no_data")
    battery = device.get("battery_percent")
    via = (
        t("tray.device.via_pc")
        if device.get("uid") == BRIDGE_UID
        else t("tray.device.via_phone")
    )
    if pendant_online(status):
        charge = t("tray.device.charge", n=battery) if battery is not None else ""
        return t("tray.device.online", charge=charge, via=via)
    # Заряд из последнего отчёта — помечаем, что он не текущий.
    charge = t("tray.device.charge_was", n=battery) if battery is not None else ""
    if device.get("stale"):
        return t("tray.device.silent", ago=_ago(device.get("ago_s")), charge=charge)
    return t("tray.device.offline", charge=charge)


# --------------------------------------------------------------------- заряд


class BatteryAlerts:
    """Предупреждает по разу на каждом пороге; рост заряда сбрасывает пороги."""

    def __init__(self, thresholds: tuple[int, ...] = BATTERY_THRESHOLDS):
        self.thresholds = tuple(sorted(thresholds, reverse=True))
        self._fired: set[int] = set()
        self._last: int | None = None

    def observe(self, percent: int | None) -> str | None:
        if percent is None:
            return None
        if self._last is not None and percent > self._last:
            # Кулон заряжали — пороги ниже текущего заряда снова в силе.
            self._fired = {t for t in self._fired if t >= percent}
        self._last = percent
        crossed = [t for t in self.thresholds if percent <= t and t not in self._fired]
        if not crossed:
            return None
        self._fired.update(crossed)
        return t("tray.battery.low", n=percent)


class ReconnectNotice:
    """«Кулон подключён» — только при появлении, на пропадание молчим:
    выключение руками от разрыва связи не отличить."""

    def __init__(self) -> None:
        self._online: bool | None = None

    def observe(self, status: dict) -> str | None:
        online = pendant_online(status)
        was, self._online = self._online, online
        if online and was is False:
            battery = device_info(status).get("battery_percent")
            charge = t("tray.device.charge", n=battery) if battery is not None else ""
            return t("tray.reconnected", charge=charge)
        return None


def bridge_hung(
    status: dict, *, alive: bool, started_at: float | None, now: float
) -> bool:
    """Пора перезапускать мост: процесс жив, а его отчёты до ядра не доходят."""
    if not alive or started_at is None or now - started_at < BRIDGE_GRACE_S:
        return False
    device = device_info(status)
    if device.get("uid") != BRIDGE_UID:
        # Последний отчёт не от нашего моста — от ПК не было ни одного.
        return True
    ago = device.get("ago_s")
    return ago is None or ago > BRIDGE_HANG_S


# --------------------------------------------------------------- расшифровка

# Поля итога батча (подписи — cli.batch.<поле>), которые идут в уведомление.
_KEY_STATS = ("transcribed", "conversations", "exported_tasks", "failed")


def _batch_labels() -> dict[str, str]:
    """Подпись строки итога на любом языке каталогов → поле.

    Дочерний батч печатает итог на своём языке, а трей мог успеть сменить
    язык — узнаём подписи всех языков.
    """
    labels: dict[str, str] = {}
    for lang in i18n.available_languages():
        for stat in _KEY_STATS:
            labels[t(f"cli.batch.{stat}", lang=lang)] = stat
    return labels


def batch_summary(output: str) -> str:
    """Короткий итог батча для уведомления из строк «ключ: число»."""
    labels = _batch_labels()
    found: dict[str, int] = {}
    for line in output.splitlines():
        match = re.match(r"\s*([^:]+):\s*(\d+)\s*$", line)
        if match and match.group(1).strip() in labels:
            found[labels[match.group(1).strip()]] = int(match.group(2))
    if not found:
        return t("tray.batch.no_summary")
    if found.get("failed") == 0:
        del found["failed"]
    parts = [f"{t(f'cli.batch.{stat}')} {found[stat]}" for stat in _KEY_STATS if stat in found]
    return t("tray.batch.done", parts=", ".join(parts))


# ------------------------------------------------------------------ процессы


def _child_env() -> dict:
    env = dict(os.environ)
    paths = [str(ROOT / "core"), str(ROOT / "bridge")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUTF8"] = "1"
    return env


def _python() -> str:
    """Консольный python рядом с pythonw: вывод батча нужен в pipe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def kill_tree(proc) -> None:
    """Гасит процесс вместе с детьми: python из .venv на Windows — лаунчер,
    настоящий интерпретатор — его дочерний процесс, и terminate() оставил бы
    мост висеть сиротой, держа кулон."""
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            creationflags=_NO_WINDOW,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


class BridgeProcess:
    """Мост кулон → ядро как дочерний процесс приложения."""

    def __init__(
        self,
        core_url: str,
        log_path: Path,
        *,
        address: str | None = None,
        popen=subprocess.Popen,
        clock=time.time,
    ) -> None:
        self.core_url = core_url
        self.log_path = log_path
        self.address = address
        self._popen = popen
        self._clock = clock
        self._proc = None
        self.started_at: float | None = None
        self.restarts = 0

    def command(self) -> list[str]:
        cmd = [
            _python(),
            "-m",
            "uzel_bridge",
            "--uid",
            BRIDGE_UID,
            "--core-url",
            self.core_url,
            "--reconnect-max",
            str(BRIDGE_RECONNECT_MAX_S),
        ]
        if self.address:
            cmd += ["--address", self.address]
        return cmd

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            self._proc = self._popen(
                self.command(),
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=_child_env(),
                cwd=str(ROOT),
                creationflags=_NO_WINDOW,
            )
        finally:
            stream.close()  # дескриптор уже унаследован дочерним процессом
        self.started_at = self._clock()
        log.info("мост с ПК запущен, pid %s", self._proc.pid)

    def stop(self) -> None:
        kill_tree(self._proc)
        self._proc = None
        self.started_at = None

    def restart(self) -> None:
        self.stop()
        self.start()
        self.restarts += 1


class BatchRun:
    """«Расшифровать сейчас»: то же, что ночной батч (recover + batch)."""

    def __init__(self, log_path: Path, *, popen=subprocess.Popen) -> None:
        self.log_path = log_path
        self._popen = popen
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_done) -> bool:
        """False — батч уже идёт. ``on_done(текст)`` зовётся из фонового потока."""
        if self.running:
            return False
        self._thread = threading.Thread(
            target=self._run, args=(on_done,), name="uzel-batch", daemon=True
        )
        self._thread.start()
        return True

    def _run(self, on_done) -> None:
        output: list[str] = []
        failed = False
        for step in ("recover", "batch"):
            try:
                proc = self._popen(
                    [_python(), "-m", "uzel.cli", "worker", step],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=_child_env(),
                    cwd=str(ROOT),
                    creationflags=_NO_WINDOW,
                )
                out, _ = proc.communicate()
                text = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
                output.append(text or "")
                failed = failed or proc.returncode != 0
            except Exception as exc:
                output.append(f"не запустилось: {exc}")
                failed = True
        joined = "\n".join(output)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"===== вручную из трея {time.strftime('%d.%m.%Y %H:%M:%S')}\n"
                )
                fh.write(joined.rstrip() + "\n")
                fh.write(f"===== done exit {1 if failed else 0}\n")
        except OSError:
            pass
        summary = batch_summary(joined)
        if failed:
            summary = t("tray.batch.failed") + summary
        on_done(summary)


# ------------------------------------------------------- память между запусками


def load_tray_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_tray_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("не записал состояние трея в %s", path)


def open_path(path: Path) -> bool:
    """Открыть файл программой по умолчанию. False — файла нет."""
    if not path.exists():
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        return False
    return True
