"""Приём кадров Opus ядром: ``POST /ingest/opus`` и декодирование фрагмента (P8)."""

from __future__ import annotations

import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from uzel.app import create_app
from uzel.opus_stream import (
    MAX_FRAME_BYTES,
    OPUS_FRAMES_ENCODING,
    count_frames,
    pack_frames,
    unpack_frames,
)
from uzel.queue import Chunk, ChunkMeta, DiskQueue
from uzel.worker import DecoderUnavailable, UndecodableChunk, decode_chunk
from uzel_bridge.opus_decoder import encode_opus_frames


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def opus_body(tone: np.ndarray) -> tuple[bytes, int]:
    """Секунда звука → (тело запроса с кадрами Opus, число кадров)."""
    pcm16 = (np.clip(tone, -1.0, 1.0) * 32767).astype(np.int16)
    frames = encode_opus_frames(pcm16)
    return pack_frames(frames), len(frames)


# --- формат потока кадров (§1) ---


def test_pack_unpack_roundtrip():
    frames = [b"\x01\x02", b"abc", b"x" * 300]

    payload = pack_frames(frames)

    assert unpack_frames(payload) == frames
    assert count_frames(payload) == 3


def test_pack_skips_empty_frames():
    assert pack_frames([b"", b"abc", b""]) == b"\x03\x00abc"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(pack_frames([b"abc", b"def"])[:-1], id="обрезан хвост"),
        pytest.param(pack_frames([b"abc"]) + b"\x07", id="висячий байт заголовка"),
        pytest.param(b"\x00\x00", id="нулевая длина"),
    ],
)
def test_unpack_rejects_broken_payload(payload):
    with pytest.raises(ValueError, match="обрезанн|нулев"):
        unpack_frames(payload)
    with pytest.raises(ValueError):
        count_frames(payload)


def test_pack_rejects_oversized_frame():
    with pytest.raises(ValueError, match="не помещается"):
        pack_frames([b"x" * (MAX_FRAME_BYTES + 1)])


# --- эндпоинт (§2) ---


def test_ingest_opus_stores_chunk(client, settings, tone):
    body, n_frames = opus_body(tone)

    response = client.post("/ingest/opus", params={"uid": "dev-1"}, content=body)

    assert response.status_code == 202
    data = response.json()
    assert data["frames"] == n_frames
    assert data["n_bytes"] == len(body)
    chunk_id = data["chunk_id"]
    assert response.headers["X-Uzel-Chunk-Id"] == chunk_id
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 1

    meta = ChunkMeta.from_json(
        (settings.queue_dir / "incoming" / f"{chunk_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta.encoding == OPUS_FRAMES_ENCODING
    assert meta.sample_rate == 16000  # целевая частота ПОСЛЕ декодирования
    assert meta.source == "android"
    assert meta.uid == "dev-1"


def test_ingest_opus_respects_privacy(client, tone):
    client.post("/privacy/pause")
    body, _ = opus_body(tone)

    response = client.post("/ingest/opus", params={"uid": "dev-1"}, content=body)

    assert response.status_code == 204
    assert response.headers["X-Uzel-Muted"] == "paused"
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 0


def test_ingest_opus_rejects_empty_body(client):
    response = client.post("/ingest/opus", params={"uid": "dev-1"}, content=b"")

    assert response.status_code == 400
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 0


def test_ingest_opus_rejects_garbage_body(client):
    response = client.post(
        "/ingest/opus", params={"uid": "dev-1"}, content=b"\xff\xff\x00"
    )

    assert response.status_code == 400
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 0


# --- декодирование воркером (§3) ---


def test_ingest_opus_decodes_to_a_second_of_audio(settings, tone):
    body, _ = opus_body(tone)

    with TestClient(create_app(settings)) as client:
        response = client.post("/ingest/opus", params={"uid": "dev-1"}, content=body)
        assert response.status_code == 202

    queue = DiskQueue(settings.queue_dir)
    chunks = queue.claim()
    assert len(chunks) == 1

    samples = decode_chunk(chunks[0], expected_sample_rate=16000)

    assert samples.dtype == np.float32
    duration_s = len(samples) / 16000
    assert 0.9 <= duration_s <= 1.1
    assert float(np.abs(samples).max()) > 0.01


def test_decode_chunk_opus_without_decoder(settings, tone, monkeypatch):
    # Договор изменён 23.09.2026: отсутствие декодера — это сломанное
    # окружение, а не битые данные. Раньше такой фрагмент уходил в done и
    # удалялся по TTL, то есть звук с телефона пропадал молча. Теперь
    # DecoderUnavailable, воркер возвращает фрагмент в очередь и встаёт.
    body, _ = opus_body(tone)
    monkeypatch.setitem(sys.modules, "uzel_bridge.opus_decoder", None)
    queue = DiskQueue(settings.queue_dir)
    meta = queue.put(
        body,
        uid="dev-1",
        source="android",
        sample_rate=16000,
        encoding=OPUS_FRAMES_ENCODING,
    )
    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )

    with pytest.raises(DecoderUnavailable, match="декодер Opus недоступен"):
        decode_chunk(chunk)


def test_decode_chunk_opus_broken_stream_is_undecodable(settings):
    queue = DiskQueue(settings.queue_dir)
    meta = queue.put(
        b"\x00\x00",  # нулевая длина кадра
        uid="dev-1",
        source="android",
        sample_rate=16000,
        encoding=OPUS_FRAMES_ENCODING,
    )
    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )

    with pytest.raises(UndecodableChunk, match="битый поток кадров"):
        decode_chunk(chunk)


def test_decode_chunk_still_rejects_unknown_encoding(settings):
    queue = DiskQueue(settings.queue_dir)
    meta = queue.put(
        b"whatever",
        uid="dev-1",
        source="android",
        sample_rate=16000,
        encoding="mp3",
    )
    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )

    with pytest.raises(UndecodableChunk, match="не поддерживается"):
        decode_chunk(chunk)
