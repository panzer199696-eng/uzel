"""Тесты эмулятора кулона (P10): инструмент обязан быть проверен сам.

Сети и поднятого ядра тесты не требуют: HTTP мокается через ``poster``,
BLE — через ``FakeBleakClient`` и шов ``client_factory`` в ``OmiBleSource``.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from tests.emulator.device import (
    PACKET_COUNTER_MODULO,
    EmulatedPendant,
    OpusStreamEncoder,
)
from tests.emulator.run import main as run_main
from tests.emulator.run import parse_duration
from tests.emulator.scenario import FIXTURE_PATH, Scenario, SpeechMaterial
from tests.emulator.transport import FakeBleakClient, HttpTransport
from uzel.opus_stream import pack_frames, unpack_frames
from uzel_bridge.opus_decoder import OpusDecoder
from uzel_bridge.protocol import PacketTracker, parse_packet


def make_pendant(scenario: Scenario, **kwargs) -> EmulatedPendant:
    return EmulatedPendant(SpeechMaterial(scenario, FIXTURE_PATH), **kwargs)


# --- устройство: пакеты протокола ---


def test_notifications_parse_as_real_packets():
    pendant = make_pendant(Scenario(seed=1))

    packets = [parse_packet(p) for p in pendant.notifications(8)]

    assert all(p is not None for p in packets)
    assert [p.packet_index for p in packets] == list(range(8))
    assert all(len(p.payload) > 0 for p in packets)  # в пакете есть кадр Opus


def test_packet_counter_wraps_around_uint16():
    """Номер пакета — uint16: после 65535 идёт 0, и это не потеря."""
    pendant = make_pendant(
        Scenario(seed=2), start_packet_index=PACKET_COUNTER_MODULO - 3
    )

    packets = [parse_packet(p) for p in pendant.notifications(6)]

    assert [p.packet_index for p in packets] == [65533, 65534, 65535, 0, 1, 2]

    tracker = PacketTracker()
    assert [tracker.observe(p) for p in packets] == [0] * 6


def test_codec_value_is_opus_by_default_and_changeable():
    pendant = make_pendant(Scenario(seed=3))
    assert pendant.codec == 20

    pendant = make_pendant(Scenario(seed=3), codec=21)
    assert pendant.codec == 21


# --- потери радио ---


def test_tracker_sees_losses_of_the_right_order():
    """PacketTracker на потоке с drop_rate насчитывает примерно те же потери."""
    scenario = Scenario(drop_rate=0.2, seed=5, disconnect_every_s=0)
    pendant = make_pendant(scenario)

    tracker = PacketTracker()
    for packet in pendant.notifications(2000):
        if scenario.should_drop():
            continue
        tracker.observe(parse_packet(packet))

    assert tracker.received > 1000
    assert 0.1 < tracker.loss_ratio < 0.3  # порядок тот же, разброс допустим


# --- формат тела для ядра ---


def test_pack_frames_roundtrip_on_emulator_output():
    pendant = make_pendant(Scenario(seed=6))

    frames = pendant.frames(30)

    assert frames == unpack_frames(pack_frames(frames))


# --- кодек: кадры реально декодируются ---


def test_opus_frames_decode_to_expected_duration():
    """Кадры декодируются в PCM 16 кГц, длительность не «плывёт втрое»."""
    scenario = Scenario(speech_ratio=1.0, seed=7)
    pendant = make_pendant(scenario)

    frames = pendant.frames(100)  # 100 кадров × 20 мс = 2 с
    decoder = OpusDecoder(16000)
    decoded = np.concatenate([decoder.decode(f) for f in frames])

    assert decoder.failed_frames == 0
    assert len(decoded) / 16000 == pytest.approx(2.0, abs=0.2)  # не 6 секунд
    assert np.abs(decoded).max() > 1000  # и это не тишина


def test_stream_encoder_accepts_exact_frame_chunks(tone):
    """Энкодер ест PCM-кадры проектной длины и отдаёт кадры Opus."""
    encoder = OpusStreamEncoder()
    pcm = (np.clip(tone, -1.0, 1.0) * 32767).astype(np.int16)

    frames = []
    for start in range(0, 16000 - 319, 320):
        frames.extend(encoder.encode(pcm[start : start + 320]))

    assert len(frames) == 50
    assert all(len(f) > 0 for f in frames)


# --- воспроизводимость ---


def test_same_seed_gives_identical_stream():
    def stream() -> tuple[list[bytes], list[bool]]:
        scenario = Scenario(drop_rate=0.1, seed=11)
        pendant = make_pendant(scenario)
        packets = pendant.notifications(150)
        drops = [scenario.should_drop() for _ in range(300)]
        return packets, drops

    first_packets, first_drops = stream()
    second_packets, second_drops = stream()

    assert first_packets == second_packets
    assert first_drops == second_drops


def test_different_seed_gives_different_stream():
    first = make_pendant(Scenario(seed=12)).notifications(150)
    second = make_pendant(Scenario(seed=13)).notifications(150)

    assert first != second


# --- тишина ---


def test_silence_blocks_are_really_quiet():
    """Тишина по сценарию — настоящая: энергия близка к нулю, не речь."""
    scenario = Scenario(speech_ratio=0.0, seed=14)
    material = SpeechMaterial(scenario, FIXTURE_PATH)

    quiet = 0
    for _ in range(6):
        block, is_speech = material.block()
        assert not is_speech
        rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        assert rms < 150  # около −47 дБFS и тише — это не повтор речи
        quiet += 1

    assert quiet == 6


def test_speech_ratio_shapes_the_stream_share():
    scenario = Scenario(speech_ratio=0.25, seed=15)
    material = SpeechMaterial(scenario, FIXTURE_PATH)

    blocks = [material.block()[1] for _ in range(200)]

    share = sum(blocks) / len(blocks)
    assert 0.1 < share < 0.4  # 25 % с статистическим разбросом


# --- батарея и разрывы ---


def test_battery_drains_over_emulated_time():
    pendant = make_pendant(Scenario(seed=16), battery_hours=2.0)

    assert pendant.battery_percent == 100
    pendant.skip(3600.0)  # час без звука — батарея всё равно тратится
    assert pendant.battery_percent == 50
    pendant.skip(3600.0)
    assert pendant.battery_percent == 0


def test_disconnection_schedule_is_regular():
    scenario = Scenario(disconnect_every_s=30.0, disconnect_for_s=10.0, seed=17)

    assert scenario.is_connected(0.0)
    assert scenario.is_connected(29.9)
    assert not scenario.is_connected(31.0)
    assert not scenario.is_connected(39.9)
    assert scenario.is_connected(41.0)  # переподключились
    assert not scenario.is_connected(71.0)  # и снова разрыв


def test_no_disconnects_when_disabled():
    scenario = Scenario(disconnect_every_s=0, seed=18)
    assert all(scenario.is_connected(t) for t in (0.0, 1e6, 1e9))


# --- HTTP-транспорт ---


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


def test_transport_counts_204_as_muted_not_error():
    """Ответ 204 (заглушка приватности) — нормальный исход, свой счётчик."""
    calls = []

    def poster(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(204, headers={"X-Uzel-Muted": "manual"})

    transport = HttpTransport("http://ядpo", uid="тест", poster=poster)

    sent = transport.send_frames([b"\x01\x02", b"\x03"], device_time_ms=12345)

    assert sent is True
    assert transport.muted == 1
    assert transport.accepted == 0
    assert transport.errors == 0
    assert transport.last_mute_code == "manual"
    url, kwargs = calls[0]
    assert url.endswith("/ingest/opus")
    assert kwargs["params"]["uid"] == "тест"
    assert kwargs["params"]["device_time_ms"] == 12345
    assert kwargs["content"] == pack_frames([b"\x01\x02", b"\x03"])


def test_transport_counts_202_and_errors():
    transport = HttpTransport("http://ядро", uid="тест", poster=lambda url, **kw: FakeResponse(202, text="{}"))

    assert transport.send_frames([b"\x01"]) is True
    assert transport.accepted == 1
    assert transport.frames_sent == 1

    def broken_poster(url, **kwargs):
        raise ConnectionError("нет сети")

    transport._post = broken_poster
    assert transport.send_frames([b"\x02"]) is False
    assert transport.errors == 1
    assert transport.frames_sent == 1  # неудачная попытка не считается отправкой
    assert transport.send_frames([]) is True  # пустая порция — не запрос


# --- мост целиком на FakeBleakClient ---


async def test_bridge_code_runs_over_fake_client():
    """OmiBleSource на подменённом клиенте: подписка, кодек, батарея, разрывы."""
    from uzel_bridge.ble import OmiBleSource

    scenario = Scenario(
        drop_rate=0.3,
        disconnect_every_s=20.0,  # условные секунды: поток идёт быстро
        disconnect_for_s=1.0,
        battery_hours=100.0,
        seed=19,
    )
    pendant = make_pendant(scenario)

    def factory(address, *, disconnected_callback):
        return FakeBleakClient(
            address,
            pendant=pendant,
            scenario=scenario,
            disconnected_callback=disconnected_callback,
        )

    source = OmiBleSource("fake-address", client_factory=factory, reconnect_initial_s=0.05)

    received: list = []

    async def wait_for(condition, timeout_s: float = 30.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while not condition():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("мост не дождался состояния прогона")
            await asyncio.sleep(0.01)

    stop = asyncio.Event()
    task = asyncio.create_task(source.run(lambda packet: received.append(packet), stop))
    try:
        # две сессии и хотя бы один разрыв между ними
        await wait_for(lambda: source.stats.connects >= 2 and source.stats.disconnects >= 1)
        await wait_for(lambda: source.stats.tracker.received > 100)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30.0)

    assert source.stats.codec == 20
    assert source.stats.battery_percent is not None
    assert source.stats.tracker.lost > 0  # потери радио мост увидел сам
    assert received  # обработчику дошли пакеты


# --- CLI-прогон целиком ---


def test_run_cli_writes_report(tmp_path):
    report_path = tmp_path / "прогон" / "поток.json"
    answers = iter([FakeResponse(202, text="{}")] * 1000)
    poster = lambda url, **kwargs: next(answers)  # noqa: E731

    code = run_main(
        [
            "--hours", "0.03",          # ~108 условных секунд
            "--speech-ratio", "0.3",
            "--drop-rate", "0.2",
            "--disconnect-every", "20s",
            "--disconnect-for", "2s",
            "--seed", "21",
            "--speed", "100000",        # реального времени почти не тратим
            "--report", str(report_path),
            "--quiet",
        ],
        poster=poster,
    )

    assert code == 0
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["завершение"] == "конец времени"
    assert data["отправлено_пакетов"] > 0
    assert data["потеряно_пакетов"] > 0
    assert data["разрывов"] >= 1
    assert data["секунд_речи"] > 0
    assert data["секунд_тишины"] > 0
    assert data["ответов_202"] > 0
    assert data["ответов_204"] == 0
    assert data["ошибок_http"] == 0
    assert 0 <= data["батарея_процент"] <= 100


def test_run_cli_respects_stop_file(tmp_path):
    report_path = tmp_path / "поток.json"
    (tmp_path / "STOP").write_text("", encoding="utf-8")  # стоп-файл уже лежит

    code = run_main(
        [
            "--hours", "0.02",
            "--report", str(report_path),
            "--speed", "100000",
            "--quiet",
        ],
        poster=lambda url, **kwargs: FakeResponse(202, text="{}"),
    )

    assert code == 0
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["завершение"] == "стоп-файл"


def test_duration_parsing():
    assert parse_duration("30m") == 1800.0
    assert parse_duration("90s") == 90.0
    assert parse_duration("1.5h") == 5400.0
    assert parse_duration("45") == 45.0
    with pytest.raises(Exception):
        parse_duration("сразу")


# --- долгие паузы и часы устройства (найдено долгим прогоном 23.09) ---


def test_long_pause_is_pure_silence_and_ends():
    """Без долгой тишины пауза длиннее ~90 с не выпадает, и 12 часов потока
    склеивались в один разговор. Пауза обязана быть сплошной тишиной."""
    scenario = Scenario(speech_ratio=1.0, pause_every_s=40.0, pause_for_s=20.0, seed=22)
    material = SpeechMaterial(scenario, FIXTURE_PATH)

    kinds = [material.block()[1] for _ in range(30)]  # блоки по 4 с → 120 с

    assert kinds[:10] == [True] * 10  # 0–40 с — речь
    assert kinds[10:15] == [False] * 5  # 40–60 с — долгая тишина
    assert kinds[15:25] == [True] * 10  # 60–100 с — снова речь
    assert kinds[25:30] == [False] * 5


def test_no_pauses_keeps_stream_byte_identical():
    """Паузы выключены — генератор речи не тронут: старые прогоны воспроизводимы."""
    plain = SpeechMaterial(Scenario(seed=23), FIXTURE_PATH)
    zero = SpeechMaterial(Scenario(seed=23, pause_every_s=0, pause_for_s=0), FIXTURE_PATH)

    assert [plain.block()[1] for _ in range(100)] == [zero.block()[1] for _ in range(100)]


def test_device_time_is_epoch_like_real_clients(tmp_path):
    """Мост на ПК шлёт time.time()*1000, Android — currentTimeMillis().
    Эмулятор слал секунды от старта — ядро после P11 ставило репликам 1970 год
    и решало стоп-зоны по чужому времени."""
    from datetime import datetime

    sent: list[int] = []

    def poster(url, **kwargs):
        sent.append(kwargs["params"]["device_time_ms"])
        return FakeResponse(202, text="{}")

    run_main(
        [
            "--hours", "0.01",
            "--clock-start", "2026-09-23T23:59:30",
            "--disconnect-every", "0",
            "--speed", "100000",
            "--report", str(tmp_path / "поток.json"),
            "--quiet",
        ],
        poster=poster,
    )

    start_ms = datetime(2026, 9, 23, 23, 59, 30).timestamp() * 1000
    assert sent, "эмулятор ничего не отправил"
    assert all(start_ms < t <= start_ms + 36_000 + 10_000 for t in sent)
    assert sent == sorted(sent)
    # Часы идут по условному времени — поток пересекает полночь.
    assert datetime.fromtimestamp(sent[-1] / 1000).day == 24


def test_device_time_defaults_to_now(tmp_path):
    import time as _time

    sent: list[int] = []

    def poster(url, **kwargs):
        sent.append(kwargs["params"]["device_time_ms"])
        return FakeResponse(202, text="{}")

    before = _time.time() * 1000
    run_main(
        ["--hours", "0.002", "--speed", "100000", "--disconnect-every", "0",
         "--report", str(tmp_path / "поток.json"), "--quiet"],
        poster=poster,
    )

    assert sent and abs(sent[0] - before) < 60_000
