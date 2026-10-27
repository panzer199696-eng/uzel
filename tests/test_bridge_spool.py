"""Буфер моста и отправка в ядро: FR-003, FR-062."""

from __future__ import annotations

import numpy as np
import pytest
from uzel_bridge.pipeline import AudioPipeline
from uzel_bridge.protocol import AudioPacket
from uzel_bridge.spool import Spool, Uploader


class FakeClient:
    """Ядро, которое можно уронить и поднять по команде."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.received: list[bytes] = []
        # Помечен ли каждый отправленный фрагмент как «пролежавший в буфере»:
        # по этому признаку ядро решает приватность, когда времени захвата нет.
        self.spooled_flags: list[bool] = []

    def send(
        self, payload: bytes, *, device_time_ms=None, spooled: bool = False
    ) -> bool:
        if not self.alive:
            return False
        self.received.append(payload)
        self.spooled_flags.append(spooled)
        return True


class FakeDecoder:
    """Декодер без Opus: кадр из N байт даёт N сэмплов."""

    def __init__(self) -> None:
        self.decoded_frames = 0
        self.failed_frames = 0

    def decode(self, frame: bytes) -> np.ndarray:
        self.decoded_frames += 1
        return np.arange(len(frame), dtype=np.int16)


@pytest.fixture
def spool(tmp_path) -> Spool:
    return Spool(tmp_path / "spool")


# --- буфер ---


def test_spool_stores_and_returns_chunks_in_order(spool):
    for i in range(3):
        spool.add(bytes([i]) * 10, {"n": i})

    pending = spool.pending()

    assert [chunk.meta["n"] for chunk in pending] == [0, 1, 2]
    assert pending[0].read() == b"\x00" * 10
    assert spool.count() == 3
    assert spool.total_bytes() == 30


def test_spool_rejects_empty_payload(spool):
    with pytest.raises(ValueError):
        spool.add(b"")


def test_dropping_chunk_removes_both_files(spool):
    chunk = spool.add(b"data")

    spool.drop(chunk)

    assert spool.count() == 0
    assert not chunk.path.exists()
    assert not chunk.path.with_suffix(".json").exists()


def test_spool_continues_numbering_after_restart(tmp_path):
    root = tmp_path / "spool"
    Spool(root).add(b"first")

    chunk = Spool(root).add(b"second")

    assert chunk.meta["seq"] == 1
    assert Spool(root).count() == 2


def test_spool_skips_records_without_data(spool):
    (spool.root / "0000000000000000001_00000000_dead.json").write_text(
        "{}", encoding="utf-8"
    )

    assert spool.pending() == []


@pytest.mark.parametrize("broken", [b"{oops", b"[1]", b"\xff\xfe\x00"])
def test_broken_meta_does_not_orphan_audio(spool, broken):
    """P11.2, находка 8: битый .json не должен навсегда запереть звук в буфере."""
    chunk = spool.add(b"audio", {"device_time_ms": 123})
    chunk.path.with_suffix(".json").write_bytes(broken)
    client = FakeClient()

    assert Uploader(client, spool).flush() == 1
    assert client.received == [b"audio"]
    assert client.spooled_flags == [
        True
    ]  # без времени захвата — ядро пометит приватным
    assert spool.count() == 0


def test_default_spool_dir_lives_outside_project(monkeypatch, tmp_path):
    """P11.2, находка 8: сырой звук не на Рабочем столе, который синкает Drive."""
    from pathlib import Path

    from uzel_bridge.cli import build_parser

    project = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("UZEL_DATA_DIR", raising=False)
    default = Path(build_parser().parse_args([]).spool_dir)
    assert default.is_absolute()
    assert not default.is_relative_to(project)
    assert default == Path.home() / "uzel-data" / "bridge-spool"

    monkeypatch.setenv("UZEL_DATA_DIR", str(tmp_path))
    assert Path(build_parser().parse_args([]).spool_dir) == tmp_path / "bridge-spool"


# --- отправка ---


def test_uploader_sends_directly_when_core_is_alive(spool):
    client = FakeClient()
    uploader = Uploader(client, spool)

    assert uploader.submit(b"audio") is True
    assert client.received == [b"audio"]
    assert spool.count() == 0


def test_uploader_spools_when_core_is_down(spool):
    """FR-003: ядро недоступно — звук не теряется."""
    client = FakeClient(alive=False)
    uploader = Uploader(client, spool)

    assert uploader.submit(b"audio") is False
    assert spool.count() == 1
    assert uploader.spooled == 1


def test_uploader_delivers_backlog_when_core_returns(spool):
    client = FakeClient(alive=False)
    uploader = Uploader(client, spool)
    uploader.submit(b"one")
    uploader.submit(b"two")

    client.alive = True
    delivered = uploader.flush()

    assert delivered == 2
    assert client.received == [b"one", b"two"]  # порядок сохранён
    assert spool.count() == 0


def test_new_audio_waits_behind_the_backlog(spool):
    """Пока хвост не дослан, свежее тоже идёт через буфер — иначе порядок сломается."""
    client = FakeClient(alive=False)
    uploader = Uploader(client, spool)
    uploader.submit(b"first")

    client.alive = True
    uploader.submit(b"second")

    assert client.received == [b"first", b"second"]


def test_flush_stops_at_first_failure_and_keeps_the_rest(spool):
    client = FakeClient(alive=False)
    uploader = Uploader(client, spool)
    uploader.submit(b"one")
    uploader.submit(b"two")

    assert uploader.flush() == 0
    assert spool.count() == 2


def test_backlog_survives_bridge_restart(tmp_path):
    client = FakeClient(alive=False)
    Uploader(client, Spool(tmp_path / "spool")).submit(b"audio")

    client.alive = True
    revived = Uploader(client, Spool(tmp_path / "spool"))

    assert revived.flush() == 1
    assert client.received == [b"audio"]


# --- конвейер моста ---


def test_pipeline_flushes_once_the_buffer_is_full(spool):
    client = FakeClient()
    pipeline = AudioPipeline(
        Uploader(client, spool),
        sample_rate=100,
        flush_seconds=1.0,
        decoder=FakeDecoder(),
    )

    for _ in range(9):
        pipeline.feed(AudioPacket(0, 0, b"x" * 10))
    assert client.received == []  # 90 сэмплов из 100 — рано

    pipeline.feed(AudioPacket(0, 0, b"x" * 10))

    assert len(client.received) == 1
    assert len(client.received[0]) == 200  # 100 сэмплов по 2 байта


def test_pipeline_ignores_undecodable_packets(spool):
    class DeadDecoder(FakeDecoder):
        def decode(self, frame: bytes) -> np.ndarray:
            self.failed_frames += 1
            return np.zeros(0, dtype=np.int16)

    client = FakeClient()
    pipeline = AudioPipeline(
        Uploader(client, spool),
        sample_rate=100,
        flush_seconds=0.1,
        decoder=DeadDecoder(),
    )

    pipeline.feed(AudioPacket(0, 0, b"\xff\xff\xff\xff"))

    assert client.received == []
    assert pipeline.buffered_seconds == 0.0


def test_pipeline_flush_sends_remainder_on_shutdown(spool):
    client = FakeClient()
    pipeline = AudioPipeline(
        Uploader(client, spool),
        sample_rate=100,
        flush_seconds=10.0,
        decoder=FakeDecoder(),
    )
    pipeline.feed(AudioPacket(0, 0, b"x" * 10))

    assert pipeline.flush() is True
    assert len(client.received) == 1


def test_pipeline_flush_on_empty_buffer_is_a_noop(spool):
    client = FakeClient()
    pipeline = AudioPipeline(Uploader(client, spool), decoder=FakeDecoder())

    assert pipeline.flush() is True
    assert client.received == []


def test_pipeline_reports_buffered_seconds(spool):
    pipeline = AudioPipeline(
        Uploader(FakeClient(), spool),
        sample_rate=100,
        flush_seconds=10.0,
        decoder=FakeDecoder(),
    )

    pipeline.feed(AudioPacket(0, 0, b"x" * 50))

    assert pipeline.buffered_seconds == pytest.approx(0.5)
    assert pipeline.decoded_samples == 50


# --- отчёт о состоянии канала (FR-063) ---


def test_report_status_translates_bridge_counters(monkeypatch):
    """Ядро ждёт латинские ключи, мост считает в русских — сверяем перевод."""
    import httpx
    from uzel_bridge.spool import CoreClient

    отправлено = {}

    class Ответ:
        status_code = 200

    def fake_post(url, *, json, timeout):
        отправлено["url"] = url
        отправлено["json"] = json
        return Ответ()

    monkeypatch.setattr(httpx, "post", fake_post)
    client = CoreClient("http://127.0.0.1:8770", uid="pendant-1")

    ok = client.report_status(
        {
            "подключено": True,
            "батарея_%": 74,
            "кодек": "opus",
            "пакетов": 900,
            "потеряно": 5,
        },
        spooled=2,
    )

    assert ok is True
    assert отправлено["url"].endswith("/device/status")
    assert отправлено["json"] == {
        "uid": "pendant-1",
        "battery_percent": 74,
        "codec": "opus",
        "connected": True,
        "packets": 900,
        "lost": 5,
        "spooled": 2,
    }


def test_report_status_never_breaks_the_bridge(monkeypatch):
    """Ядро недоступно — отчёт теряется молча, приём звука не страдает."""
    import httpx
    from uzel_bridge.spool import CoreClient

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("ядро не поднято")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert CoreClient("http://127.0.0.1:8770", uid="x").report_status({}) is False
