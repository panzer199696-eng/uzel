"""Протокол кулона и декодер Opus: FR-002, FR-060, FR-061, FR-063."""

from __future__ import annotations

import numpy as np
import pytest
from uzel_bridge.opus_decoder import OpusDecoder, encode_opus_frames
from uzel_bridge.protocol import (
    HEADER_BYTES,
    PACKET_COUNTER_MODULO,
    AudioPacket,
    PacketTracker,
    codec_name,
    parse_packet,
)


def packet(index: int, internal: int = 0, payload: bytes = b"\xaa\xbb") -> bytes:
    return index.to_bytes(2, "little") + bytes([internal]) + payload


# --- разбор пакета ---


def test_parse_extracts_header_and_frame():
    result = parse_packet(packet(258, internal=7, payload=b"opus-frame"))

    assert result.packet_index == 258
    assert result.internal_index == 7
    assert result.payload == b"opus-frame"


def test_parse_rejects_packet_without_payload():
    assert parse_packet(b"\x01\x00\x00") is None
    assert parse_packet(b"\x01\x00") is None
    assert parse_packet(b"") is None
    assert parse_packet(None) is None


def test_parse_accepts_bytearray_from_bleak():
    result = parse_packet(bytearray(packet(1, payload=b"x")))

    assert isinstance(result.payload, bytes)


def test_header_is_three_bytes():
    assert HEADER_BYTES == 3


def test_codec_twenty_is_opus_as_measured_on_the_device():
    assert codec_name(20) == "Opus 16 кГц"
    assert "неизвестн" in codec_name(99)
    assert codec_name(None) == "неизвестен"


# --- учёт потерь ---


def test_tracker_counts_packets_without_losses():
    tracker = PacketTracker()
    for index in range(5):
        assert tracker.observe(parse_packet(packet(index))) == 0

    assert tracker.received == 5
    assert tracker.lost == 0
    assert tracker.loss_ratio == 0.0


def test_tracker_detects_a_gap():
    tracker = PacketTracker()
    tracker.observe(parse_packet(packet(0)))

    assert tracker.observe(parse_packet(packet(4))) == 3
    assert tracker.lost == 3


def test_tracker_handles_counter_wraparound():
    """uint16 переполняется — это не потеря 65 тысяч пакетов."""
    tracker = PacketTracker()
    tracker.observe(parse_packet(packet(PACKET_COUNTER_MODULO - 1)))

    assert tracker.observe(parse_packet(packet(0))) == 0
    assert tracker.lost == 0


def test_tracker_ignores_repeated_index():
    tracker = PacketTracker()
    tracker.observe(parse_packet(packet(5)))

    assert tracker.observe(parse_packet(packet(5))) == 0
    assert tracker.lost == 0


def test_tracker_reset_forgets_previous_connection():
    """После переподключения счётчик на устройстве начинается заново."""
    tracker = PacketTracker()
    tracker.observe(parse_packet(packet(60000)))

    tracker.reset()

    assert tracker.observe(parse_packet(packet(0))) == 0
    assert tracker.lost == 0


def test_tracker_accumulates_payload_bytes():
    tracker = PacketTracker()
    tracker.observe(AudioPacket(0, 0, b"12345"))
    tracker.observe(AudioPacket(1, 0, b"123"))

    assert tracker.payload_bytes == 8


def test_loss_ratio_is_share_of_expected_packets():
    tracker = PacketTracker()
    tracker.observe(parse_packet(packet(0)))
    tracker.observe(parse_packet(packet(2)))

    assert tracker.loss_ratio == pytest.approx(1 / 3)


# --- декодер Opus ---


@pytest.fixture
def speech_like() -> np.ndarray:
    """Секунда сигнала, который Opus кодирует без вырождения в тишину."""
    t = np.arange(16000, dtype=np.float32) / 16000.0
    wave = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.15 * np.sin(2 * np.pi * 700 * t)
    return (wave * 32767).astype(np.int16)


def test_roundtrip_returns_sixteen_kilohertz_pcm(speech_like):
    """libopus декодирует в 48 кГц — мост обязан вернуть 16 кГц."""
    frames = encode_opus_frames(speech_like)
    decoder = OpusDecoder(16000)

    decoded = np.concatenate([decoder.decode(frame) for frame in frames])

    assert decoded.dtype == np.int16
    # длительность сохранилась: считаем именно по частоте 16 кГц
    assert len(decoded) / 16000 == pytest.approx(len(speech_like) / 16000, abs=0.15)
    assert np.abs(decoded).max() > 1000  # это не тишина


def test_decoder_survives_corrupted_frame():
    decoder = OpusDecoder(16000)

    result = decoder.decode(b"\xff\xff\xff\xff\xff\xff\xff\xff")

    assert result.size == 0
    assert decoder.failed_frames == 1


def test_decoder_ignores_empty_frame():
    decoder = OpusDecoder(16000)

    assert decoder.decode(b"").size == 0
    assert decoder.failed_frames == 0


def test_decoder_counts_successful_frames(speech_like):
    frames = encode_opus_frames(speech_like)
    decoder = OpusDecoder(16000)

    for frame in frames:
        decoder.decode(frame)

    assert decoder.decoded_frames == len(frames)
