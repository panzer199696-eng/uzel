"""Приватность на приёмнике: FR-070, FR-071.

Ключевая проверка — заглушённый звук не попадает на диск вовсе, а не «пишется
и удаляется потом».
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from uzel.app import create_app

PCM = b"\x00\x01" * 800


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_recording_state_is_visible_but_not_through_health(client):
    """Состояние записи отдаёт /privacy, а /health о нём молчит.

    Договор изменён 23.09.2026 (P11, находка 1): /health — единственный
    эндпоинт без токена, поэтому он отдаёт только живость и версию. Раньше
    любой в локальной сети видел там, пишется ли сейчас звук.
    """
    assert client.get("/privacy").json()["запись"] == "идёт"

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert "privacy" not in health
    assert "pending" not in health


def test_muted_ingest_is_discarded_before_touching_disk(settings, client):
    client.post("/privacy/mute", params={"minutes": 30})

    response = client.post("/ingest", params={"uid": "dev-1"}, content=PCM)

    assert response.status_code == 204
    assert response.headers["X-Uzel-Muted"] == "manual"
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 0
    # на диске не должно остаться ни одного файла с этим звуком
    assert list((settings.queue_dir / "incoming").glob("*")) == []


def test_unmute_restores_recording(client):
    client.post("/privacy/mute", params={"minutes": 30})
    client.post("/privacy/unmute")

    response = client.post("/ingest", params={"uid": "dev-1"}, content=PCM)

    assert response.status_code == 202
    assert client.get("/ui/api/status").json()["queue"]["pending"] == 1


def test_privacy_endpoint_shows_reason_and_deadline(client):
    body = client.post("/privacy/mute", params={"minutes": 15}).json()

    assert body["запись"] == "выключена"
    assert body["причина"] == "ручная заглушка"
    assert body["до"] is not None
    assert client.get("/privacy").json()["запись"] == "выключена"


def test_mute_requires_positive_duration(client):
    assert client.post("/privacy/mute", params={"minutes": 0}).status_code == 422
    assert client.post("/privacy/mute", params={"minutes": -5}).status_code == 422


def test_mute_survives_core_restart(settings):
    """Перезапуск ядра не имеет права включить запись обратно."""
    with TestClient(create_app(settings)) as client:
        client.post("/privacy/mute", params={"minutes": 60})

    with TestClient(create_app(settings)) as revived:
        assert revived.get("/privacy").json()["запись"] == "выключена"
        assert (
            revived.post("/ingest", params={"uid": "dev-1"}, content=PCM).status_code
            == 204
        )


def test_schedule_window_mutes_ingest(settings):
    """Стоп-зона по расписанию перекрывает приём без ручного вмешательства."""
    always = settings.model_copy(update={"quiet_windows": ("00:00-23:59",)})
    with TestClient(create_app(always)) as client:
        response = client.post("/ingest", params={"uid": "dev-1"}, content=PCM)

        assert response.status_code == 204
        assert response.headers["X-Uzel-Muted"] == "schedule"
        assert "стоп-зона" in client.get("/privacy").json()["причина"]
