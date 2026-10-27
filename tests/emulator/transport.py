"""Транспорты эмулятора: HTTP в ядро и подмена BleakClient для кода моста (P10).

``HttpTransport`` — основной путь долгого прогона: кадры уходят в
``POST /ingest/opus`` тем же форматом, что и Android-клиент.

``FakeBleakClient`` подменяет ``BleakClient`` через шов ``client_factory`` в
``OmiBleSource``: код моста честно подписывается на характеристику, читает
кодек и батарею, переживает разрывы и считает потери ``PacketTracker``-ом.
"""

from __future__ import annotations

import asyncio
import logging

from uzel_bridge.protocol import (
    AUDIO_CODEC_UUID,
    AUDIO_DATA_UUID,
    BATTERY_LEVEL_UUID,
)

log = logging.getLogger(__name__)


class HttpTransport:
    """Шлёт кадры Opus в ``POST /ingest/opus`` и считает ответы ядра.

    Ответ 204 — это заглушка приватности (``X-Uzel-Muted``): ядро приняло
    запрос и осознанно его не записало. Это нормальный исход, а не ошибка.
    """

    def __init__(
        self,
        base_url: str,
        *,
        uid: str,
        source: str = "android",
        timeout_s: float = 10.0,
        poster=None,
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.uid = uid
        self.source = source
        self.timeout_s = timeout_s
        self._post = poster if poster is not None else httpx.post
        self.accepted = 0   # HTTP 202 — ядро записало фрагмент
        self.muted = 0      # HTTP 204 — заглушка приватности
        self.errors = 0     # сеть или 4xx/5xx
        self.frames_sent = 0
        self.bytes_sent = 0
        self.last_mute_code: str | None = None
        self.last_error: str | None = None

    def send_frames(
        self, frames: list[bytes], *, device_time_ms: int | None = None
    ) -> bool:
        """Отправляет порцию кадров. ``True`` — ядро ответило 202 или 204."""
        from uzel.opus_stream import pack_frames

        if not frames:
            return True
        body = pack_frames(frames)
        params: dict = {"uid": self.uid, "source": self.source}
        if device_time_ms is not None:
            params["device_time_ms"] = int(device_time_ms)

        try:
            response = self._post(
                f"{self.base_url}/ingest/opus",
                params=params,
                content=body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self.timeout_s,
            )
        except Exception as exc:
            self.errors += 1
            self.last_error = str(exc)
            log.warning("ядро недоступно: %s", exc)
            return False

        if response.status_code == 202:
            self.accepted += 1
            ok = True
        elif response.status_code == 204:
            self.muted += 1
            self.last_mute_code = response.headers.get("X-Uzel-Muted")
            ok = True
        else:
            self.errors += 1
            self.last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            log.error("ядро отвергло фрагмент: %s", self.last_error)
            return False

        self.frames_sent += len(frames)
        self.bytes_sent += len(body)
        return ok


class FakeBleakClient:
    """BLE-клиент вокруг ``EmulatedPendant``: интерфейс как у ``BleakClient``.

    Подписка на характеристику превращается в поток настоящих пакетов
    протокола; пакеты, потерянные по сценарию, до обработчика не доходят —
    мост видит дыру в нумерации и засчитывает потери сам. Разрывы связи
    объявляются через ``disconnected_callback``, после чего ``OmiBleSource``
    переподключается и зовёт фабрику снова.
    """

    def __init__(
        self,
        address: str,
        *,
        pendant,
        scenario,
        disconnected_callback=None,
        notify_interval_s: float = 0.0,
        batch_frames: int = 25,
    ) -> None:
        self.address = address
        self.pendant = pendant
        self.scenario = scenario
        self.disconnected_callback = disconnected_callback
        self.notify_interval_s = notify_interval_s
        self.batch_frames = batch_frames
        self._notify_task: asyncio.Task | None = None
        self._connected = False
        self.delivered_packets = 0

    # --- протокол async with, как у BleakClient ---

    async def __aenter__(self):
        self._connected = True
        self.pendant.reset_link()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._connected = False
        await self._cancel_notify()
        return False

    def is_connected(self) -> bool:
        return self._connected

    # --- характеристики ---

    async def read_gatt_char(self, uuid: str) -> bytes | None:
        if uuid.lower() == AUDIO_CODEC_UUID:
            return bytes([self.pendant.codec])
        if uuid.lower() == BATTERY_LEVEL_UUID:
            return bytes([self.pendant.battery_percent])
        return None

    async def start_notify(self, uuid: str, callback) -> None:
        """Подписка на аудио: поток пакетов начинает течь в обработчик."""
        if self._notify_task is None:
            self._notify_task = asyncio.create_task(
                self._notify_loop(callback), name="fake-ble-notify"
            )

    async def stop_notify(self, uuid: str) -> None:
        await self._cancel_notify()

    async def _cancel_notify(self) -> None:
        if self._notify_task is not None:
            self._notify_task.cancel()
            try:
                await self._notify_task
            except asyncio.CancelledError:
                pass
            self._notify_task = None

    # --- поток ---

    async def _notify_loop(self, callback) -> None:
        while self._connected:
            if not self.scenario.is_connected(self.pendant.emulated_seconds):
                # Радио пропало: время разрыва уходит без звука, мост
                # узнаёт об этом штатным колбэком разрыва.
                self.pendant.skip(self.scenario.disconnect_for_s)
                self._connected = False
                if self.disconnected_callback is not None:
                    self.disconnected_callback(self)
                return
            for packet in self.pendant.notifications(self.batch_frames):
                if not self._connected:
                    return
                if self.scenario.should_drop():
                    continue  # пакет потерян в эфире — обработчик его не увидит
                self.delivered_packets += 1
                callback(None, bytearray(packet))
            if self.notify_interval_s > 0:
                await asyncio.sleep(self.notify_interval_s)
            else:
                await asyncio.sleep(0)
