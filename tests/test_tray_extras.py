"""Трей: мост с ПК, ручная расшифровка, заряд (12.10.2026) — без GUI и Bluetooth.

Главный сценарий: владелец выключает кулон руками. Это не авария — иконка
жёлтая, уведомлений нет, мост не перезапускается. Перезапуск — только если
завис сам мост (его отчёты перестали доходить до ядра).
"""

from __future__ import annotations

import threading

import pytest
from uzel import tray_extras as tx
from uzel.desktop import AppOptions, CoreServer, TrayController, tray_image, tray_title


def status(*, allowed=True, device=None):
    return {
        "privacy": {"код": "allowed" if allowed else "manual", "причина": "x"},
        "queue": {"pending": 0},
        "device": device if device is not None else {"known": False},
    }


ONLINE_PC = {
    "known": True,
    "uid": "pendant-pc",
    "connected": True,
    "stale": False,
    "battery_percent": 13,
    "ago_s": 5.0,
}
# Кулон выключен руками: мост жив, отчитывается свежо, но сессии нет.
OFF_BY_HAND = {**ONLINE_PC, "connected": False}
# Мост молчит: последний отчёт давно.
BRIDGE_SILENT = {**ONLINE_PC, "stale": True, "ago_s": 300.0}
ONLINE_PHONE = {**ONLINE_PC, "uid": "phone-1"}


# --- состояние и подписи ---


def test_tray_state_three_colors():
    assert tx.tray_state(status(device=ONLINE_PC)) == "ok"
    assert tx.tray_state(status(device=OFF_BY_HAND)) == "waiting"
    assert tx.tray_state(status(device=BRIDGE_SILENT)) == "waiting"
    assert tx.tray_state(status()) == "waiting"
    # Приват важнее всего: даже с кулоном на связи — красный.
    assert tx.tray_state(status(allowed=False, device=ONLINE_PC)) == "muted"


def test_device_line_variants():
    assert (
        tx.device_line(status(device=ONLINE_PC)) == "Кулон: на связи · 13 % · через ПК"
    )
    assert "через телефон" in tx.device_line(status(device=ONLINE_PHONE))
    # Выключен: заряд помечен как прошлый, не текущий.
    assert tx.device_line(status(device=OFF_BY_HAND)) == "Кулон: не в эфире · был 13 %"
    assert tx.device_line(status(device=BRIDGE_SILENT)).startswith(
        "Кулон: мост молчит (5 мин"
    )
    assert tx.device_line(status()) == "Кулон: нет данных"


def test_tray_title_fits_windows_limit():
    title = tray_title(status(device=ONLINE_PC))
    assert "запись идёт" in title and "13 %" in title
    assert len(title) <= 127


def test_tray_image_states_have_distinct_colors():
    colors = {tray_image(s).getpixel((32, 8)) for s in ("ok", "waiting", "muted")}
    assert len(colors) == 3
    # Совместимость со старым вызовом bool.
    assert tray_image(True).getpixel((32, 8)) == tray_image("muted").getpixel((32, 8))


# --- заряд ---


def test_battery_alerts_once_per_threshold():
    alerts = tx.BatteryAlerts()
    assert alerts.observe(50) is None
    assert alerts.observe(20) is not None
    assert alerts.observe(19) is None  # порог 20 уже сработал
    assert "9 %" in alerts.observe(9)
    assert alerts.observe(8) is None
    assert alerts.observe(None) is None


def test_battery_alerts_rearm_after_charging():
    alerts = tx.BatteryAlerts()
    alerts.observe(15)
    alerts.observe(80)  # зарядили
    assert alerts.observe(18) is not None


def test_battery_first_seen_low_alerts_once():
    alerts = tx.BatteryAlerts()
    assert alerts.observe(13) is not None  # 20 сразу, одним уведомлением
    assert alerts.observe(12) is None


def test_reconnect_notice_only_on_appearance():
    notice = tx.ReconnectNotice()
    assert notice.observe(status(device=ONLINE_PC)) is None  # старт трея — не событие
    assert notice.observe(status(device=OFF_BY_HAND)) is None  # выключил — молчим
    assert notice.observe(status(device=ONLINE_PC)) == "Кулон подключён · 13 %"
    assert notice.observe(status(device=ONLINE_PC)) is None


# --- сторож моста ---


def test_bridge_not_restarted_when_pendant_switched_off():
    assert not tx.bridge_hung(
        status(device=OFF_BY_HAND), alive=True, started_at=0.0, now=1000.0
    )


def test_bridge_restarted_when_silent():
    assert tx.bridge_hung(
        status(device=BRIDGE_SILENT), alive=True, started_at=0.0, now=1000.0
    )
    # От ПК ни одного отчёта за минуту — тоже завис.
    assert tx.bridge_hung(
        status(device=ONLINE_PHONE), alive=True, started_at=0.0, now=1000.0
    )
    assert tx.bridge_hung(status(), alive=True, started_at=0.0, now=1000.0)


def test_bridge_grace_period_after_start():
    assert not tx.bridge_hung(status(), alive=True, started_at=990.0, now=1000.0)
    assert not tx.bridge_hung(status(), alive=False, started_at=0.0, now=1000.0)


# --- итог батча ---

BATCH_OUT = """возвращено в очередь: 0
склеено обрывков на стыках: 3
расшифровано реплик: 41
ошибок расшифровки: 0
собрано разговоров: 2
задач выгружено: 1
"""


def test_batch_summary_picks_key_lines():
    text = tx.batch_summary(BATCH_OUT)
    assert text == (
        "Расшифровка готова: расшифровано реплик 41, собрано разговоров 2, "
        "задач выгружено 1"
    )


def test_batch_summary_shows_errors_and_handles_garbage():
    assert "ошибок расшифровки 2" in tx.batch_summary("ошибок расшифровки: 2\n")
    assert "batch.log" in tx.batch_summary("Traceback ...")


# --- процессы на подменах ---


class FakeProc:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = None
        self.killed = False
        self.output = kwargs.pop("_output", "")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1

    def communicate(self):
        self.returncode = 0
        return self.output.encode("utf-8"), None


def test_bridge_process_command_and_lifecycle(tmp_path, monkeypatch):
    started = []

    def popen(cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        started.append(proc)
        return proc

    killed = []
    monkeypatch.setattr(tx, "kill_tree", lambda p: (killed.append(p), p.terminate()))
    bridge = tx.BridgeProcess(
        "http://127.0.0.1:8770",
        tmp_path / "logs" / "bridge.log",
        address="AA:BB",
        popen=popen,
        clock=lambda: 100.0,
    )
    bridge.start()
    bridge.start()  # повторный старт живого моста — ничего не делает
    assert len(started) == 1 and bridge.alive and bridge.started_at == 100.0
    cmd = started[0].cmd
    assert cmd[1:3] == ["-m", "uzel_bridge"]
    assert cmd[cmd.index("--uid") + 1] == "pendant-pc"
    assert cmd[cmd.index("--reconnect-max") + 1] == "10.0"
    assert cmd[cmd.index("--address") + 1] == "AA:BB"
    assert "bridge" in started[0].kwargs["env"]["PYTHONPATH"]
    assert (tmp_path / "logs" / "bridge.log").exists()

    bridge.restart()
    assert len(started) == 2 and bridge.restarts == 1 and killed == [started[0]]
    bridge.stop()
    assert not bridge.alive and bridge.started_at is None


def test_batch_run_reports_summary_and_logs(tmp_path):
    calls = []

    def popen(cmd, **kwargs):
        calls.append(cmd[-1])
        out = BATCH_OUT if cmd[-1] == "batch" else "возвращено в очередь: 0\n"
        return FakeProc(cmd, _output=out, **kwargs)

    log_path = tmp_path / "batch.log"
    run = tx.BatchRun(log_path, popen=popen)
    done = threading.Event()
    result = []
    assert run.start(lambda s: (result.append(s), done.set()))
    assert done.wait(5)
    assert calls == ["recover", "batch"]
    assert "расшифровано реплик 41" in result[0]
    assert "вручную из трея" in log_path.read_text(encoding="utf-8")


def test_batch_run_failure_is_reported(tmp_path):
    def popen(cmd, **kwargs):
        raise OSError("нет python")

    run = tx.BatchRun(tmp_path / "batch.log", popen=popen)
    done = threading.Event()
    result = []
    run.start(lambda s: (result.append(s), done.set()))
    assert done.wait(5)
    assert result[0].startswith("Расшифровка с ошибкой")


def test_tray_state_file_roundtrip(tmp_path):
    path = tmp_path / "tray.json"
    assert tx.load_tray_state(path) == {}
    tx.save_tray_state(path, {"bridge": True})
    assert tx.load_tray_state(path) == {"bridge": True}
    path.write_text("{битый", encoding="utf-8")
    assert tx.load_tray_state(path) == {}


# --- трей целиком: сторож в apply() ---


class FakeBridge:
    def __init__(self):
        self.alive = True
        self.started_at = 0.0
        self.restarts = 0
        self.starts = 0

    def start(self):
        self.alive = True
        self.starts += 1

    def restart(self):
        self.restarts += 1

    def stop(self):
        self.alive = False


@pytest.fixture
def tray(settings):
    server = CoreServer(settings, AppOptions(port=1, window=False, tray=False))
    t = TrayController(server, threading.Event(), settings=settings)
    t.bridge = FakeBridge()
    t._state["bridge"] = True
    t.notes = []
    t._notify = t.notes.append
    return t


def test_tray_does_not_touch_bridge_when_pendant_off(tray):
    tray.apply(status(device=OFF_BY_HAND), now=1000.0)
    assert tray.bridge.restarts == 0
    assert not any("Мост" in n for n in tray.notes)


def test_tray_restarts_hung_bridge_but_not_in_a_loop(tray):
    tray.apply(status(device=BRIDGE_SILENT), now=1000.0)
    tray.apply(status(device=BRIDGE_SILENT), now=1010.0)  # сразу снова — ждём
    assert tray.bridge.restarts == 1
    assert tray.notes.count("Мост с ПК завис — перезапустил") == 1
    tray.apply(status(device=BRIDGE_SILENT), now=1100.0)
    assert tray.bridge.restarts == 2


def test_tray_raises_crashed_bridge(tray):
    tray.bridge.alive = False
    tray.apply(status(device=OFF_BY_HAND), now=1000.0)
    assert tray.bridge.starts == 1


def test_tray_ignores_bridge_when_switched_off(tray):
    tray._state["bridge"] = False
    tray.bridge.alive = False
    tray.apply(status(device=BRIDGE_SILENT), now=1000.0)
    assert tray.bridge.starts == 0 and tray.bridge.restarts == 0


def test_tray_battery_and_reconnect_notifications(tray):
    tray.apply(status(device=OFF_BY_HAND), now=1000.0)
    tray.apply(status(device=ONLINE_PC), now=1003.0)
    assert any("пора на зарядку" in n for n in tray.notes)
    assert "Кулон подключён · 13 %" in tray.notes


# --- CLI моста ---


def test_bridge_cli_reconnect_max():
    from uzel_bridge.cli import build_parser

    assert build_parser().parse_args([]).reconnect_max == 60.0
    assert build_parser().parse_args(["--reconnect-max", "10"]).reconnect_max == 10.0


# --- английский интерфейс (27.10.2026) ---


def test_tray_texts_in_english():
    from uzel import i18n
    from uzel.desktop import tray_tooltip
    from uzel.tray_extras import BatteryAlerts, device_line

    i18n.set_language("en")
    status = {
        "privacy": {"код": "allowed"},
        "queue": {"pending": 3},
        "device": {"known": True, "connected": True, "stale": False, "battery_percent": 80, "uid": "pendant-pc"},
    }
    assert tray_tooltip(status) == "UZEL · recording · 3 queued"
    assert device_line(status) == "Pendant: online · 80 % · via PC"
    assert tray_tooltip({"privacy": {"код": "paused", "причина": "x"}}) == "UZEL · off: x"
    assert BatteryAlerts().observe(9) == "Pendant battery 9 % — time to charge"


def test_batch_summary_reads_output_of_any_language():
    """Батч напечатал итог по-русски, а трей уже английский — и наоборот."""
    from uzel import i18n
    from uzel.tray_extras import batch_summary

    ru_output = "расшифровано реплик: 5\nсобрано разговоров: 2\nзадач выгружено: 1\nошибок расшифровки: 0\n"
    en_output = "utterances transcribed: 5\nconversations assembled: 2\ntasks exported: 1\ntranscription errors: 2\n"
    i18n.set_language("en")
    assert batch_summary(ru_output) == (
        "Transcription done: utterances transcribed 5, conversations assembled 2, tasks exported 1"
    )
    i18n.set_language("ru")
    assert batch_summary(en_output) == (
        "Расшифровка готова: расшифровано реплик 5, собрано разговоров 2, "
        "задач выгружено 1, ошибок расшифровки 2"
    )
