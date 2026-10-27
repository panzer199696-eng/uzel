"""Приёмник аудио: FR-001, FR-004, NFR-005."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from uzel.app import create_app
from uzel.queue import ChunkMeta

PCM = b"\x00\x01" * 800  # 1600 байт = 50 мс при 16 кГц


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_health_is_alive_and_version_only(client):
    """P11: /health открыт без токена, поэтому отдаёт минимум — без счётчиков."""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["version"]
    assert "pending" not in body
    assert "privacy" not in body


def test_status_reports_queue_state(client):
    body = client.get("/ui/api/status").json()

    assert body["queue"]["pending"] == 0


def test_ingest_stores_chunk_and_returns_id(client, settings):
    response = client.post(
        "/ingest",
        params={"uid": "dev-1", "sample_rate": 16000, "source": "omi-app"},
        content=PCM,
    )

    assert response.status_code == 202
    chunk_id = response.json()["chunk_id"]
    assert response.json()["n_bytes"] == len(PCM)
    assert response.headers["X-Uzel-Chunk-Id"] == chunk_id
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 1

    meta = ChunkMeta.from_json(
        (settings.queue_dir / "incoming" / f"{chunk_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta.source == "omi-app"
    assert meta.uid == "dev-1"


def test_ingest_keeps_device_time(client, settings):
    response = client.post(
        "/ingest",
        params={"uid": "dev-1", "sample_rate": 16000, "device_time_ms": 123456},
        content=PCM,
    )
    chunk_id = response.json()["chunk_id"]

    meta = ChunkMeta.from_json(
        (settings.queue_dir / "incoming" / f"{chunk_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta.device_time_ms == 123456


def test_ingest_rejects_empty_body(client):
    response = client.post("/ingest", params={"uid": "dev-1"}, content=b"")

    assert response.status_code == 400
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 0


def test_ingest_rejects_foreign_sample_rate(client):
    response = client.post(
        "/ingest", params={"uid": "dev-1", "sample_rate": 44100}, content=PCM
    )

    assert response.status_code == 400
    assert "16000" in response.json()["detail"]


def test_ingest_requires_uid(client):
    assert client.post("/ingest", content=PCM).status_code == 422


def test_restart_recovers_interrupted_chunks(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/ingest", params={"uid": "dev-1"}, content=PCM)

    from uzel.queue import DiskQueue

    queue = DiskQueue(settings.queue_dir)
    queue.claim()  # имитируем падение во время обработки

    with TestClient(create_app(settings)) as client:
        assert client.get("/ui/api/status").json()["queue"]["pending"] == 1
