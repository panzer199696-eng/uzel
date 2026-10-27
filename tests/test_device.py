"""Отчёт моста о кулоне: заряд и разрывы для живого статуса (FR-063)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from uzel.app import create_app
from uzel.device import STALE_AFTER_S, DeviceReport, DeviceStatus


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_device_unknown_until_bridge_reports(client):
    body = client.get("/device").json()

    assert body["known"] is False
    assert "мост" in body["reason"]


def test_report_lands_in_status(client):
    response = client.post(
        "/device/status",
        json={
            "uid": "pendant-1",
            "battery_percent": 62,
            "codec": "opus",
            "connected": True,
            "packets": 1200,
            "lost": 3,
            "spooled": 0,
        },
    )

    assert response.status_code == 200
    body = client.get("/device").json()
    assert body["known"] is True
    assert body["battery_percent"] == 62
    assert body["lost"] == 3
    assert body["stale"] is False
    assert body["reason"] == "кулон на связи"


def test_bad_battery_is_rejected(client):
    response = client.post(
        "/device/status", json={"uid": "pendant-1", "battery_percent": 146}
    )

    assert response.status_code == 422


def test_silence_of_the_bridge_is_visible():
    """Старый отчёт не выдаётся за текущее состояние."""
    status = DeviceStatus()
    status.update(DeviceReport(uid="pendant-1", battery_percent=80, reported_at=1000.0))

    свежий = status.as_dict(now=1000.0 + STALE_AFTER_S - 1)
    протухший = status.as_dict(now=1000.0 + STALE_AFTER_S + 1)

    assert свежий["stale"] is False
    assert протухший["stale"] is True
    assert протухший["reason"] == "мост молчит"


def test_disconnected_pendant_is_named_as_such():
    status = DeviceStatus()
    status.update(DeviceReport(uid="pendant-1", connected=False, reported_at=1000.0))

    assert status.as_dict(now=1000.0)["reason"] == "мост на связи, кулон отключён"
