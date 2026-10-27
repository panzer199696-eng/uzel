"""BLE-источник аудио на bleak (FR-060, FR-062, FR-063).

📌 Пока кулон подключён к телефону, с ПК он не виден вообще: BLE-реклама при
активном подключении прекращается, и поиск по MAC даёт «устройство не найдено».
Это нормальная работа BLE, а не поломка — перед запуском моста кулон надо
отвязать от телефона.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import (
    AUDIO_CODEC_UUID,
    AUDIO_DATA_UUID,
    BATTERY_LEVEL_UUID,
    DEVICE_NAME_HINTS,
    OPUS_CODECS,
    SERVICE_UUID,
    AudioPacket,
    PacketTracker,
    codec_name,
    parse_packet,
)

log = logging.getLogger(__name__)

PacketHandler = Callable[[AudioPacket], None]


@dataclass
class DeviceInfo:
    address: str
    name: str | None = None
    rssi: int | None = None


@dataclass
class LinkStats:
    """Счётчики канала — исходные данные для метрики трафика (FR-063)."""

    connects: int = 0
    disconnects: int = 0
    battery_percent: int | None = None
    codec: int | None = None
    tracker: PacketTracker = field(default_factory=PacketTracker)

    @property
    def connected(self) -> bool:
        """Сессия открыта: подключений больше, чем разрывов."""
        return self.connects > self.disconnects

    def as_dict(self) -> dict:
        return {
            "подключено": self.connected,
            "подключений": self.connects,
            "разрывов": self.disconnects,
            "батарея_%": self.battery_percent,
            "кодек": codec_name(self.codec),
            "пакетов": self.tracker.received,
            "потеряно": self.tracker.lost,
            "потери_доля": round(self.tracker.loss_ratio, 4),
            "байт_аудио": self.tracker.payload_bytes,
        }


async def discover(timeout: float = 8.0) -> list[DeviceInfo]:
    """Ищет кулоны УЗЕЛ/Omi/Friend: по сервису, а при его отсутствии — по имени."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    result: list[DeviceInfo] = []
    for device, adv in found.values():
        uuids = {u.lower() for u in (adv.service_uuids or [])}
        name = (device.name or adv.local_name or "").strip()
        by_service = SERVICE_UUID.lower() in uuids
        by_name = any(hint in name.lower() for hint in DEVICE_NAME_HINTS)
        if by_service or by_name:
            result.append(
                DeviceInfo(address=device.address, name=name or None, rssi=adv.rssi)
            )
    return result


async def ensure_paired(client) -> bool:
    """Сопряжение с кулоном. Без него прошивка УЗЛА (0.1.0+) не отдаёт звук.

    Прошивка Omi сопряжение не поддерживает и работает без шифрования —
    отказ здесь не ошибка, а знак старой прошивки, поэтому сессия идёт дальше.
    Уже сопряжённый кулон Windows подтверждает сразу, без нового обмена ключами.
    """
    pair = getattr(client, "pair", None)
    if pair is None:  # эмулятор и клиенты без сопряжения
        return False
    try:
        paired = await pair()
    except Exception as exc:
        log.warning(
            "сопряжение не прошло: %s. Прошивка УЗЛА принимает новое устройство, "
            "только пока кулон подключён к USB (на зарядке)",
            exc,
        )
        return False
    if paired is False:
        log.warning("кулон отказал в сопряжении: положите его на зарядку и повторите")
        return False
    return True


class OmiBleSource:
    """Держит соединение с кулоном и отдаёт разобранные пакеты обработчику."""

    def __init__(
        self,
        address: str | None = None,
        *,
        reconnect_initial_s: float = 1.0,
        reconnect_max_s: float = 60.0,
        scan_timeout_s: float = 8.0,
        client_factory: Callable[..., object] | None = None,
    ) -> None:
        self.address = address
        self.reconnect_initial_s = reconnect_initial_s
        self.reconnect_max_s = reconnect_max_s
        self.scan_timeout_s = scan_timeout_s
        # Шов для эмулятора (tests/emulator): фабрика подменяет BleakClient,
        # остальной код моста работает как с настоящим железом. По умолчанию —
        # настоящий BleakClient, поведение не меняется.
        self.client_factory = client_factory
        self.stats = LinkStats()

    async def _resolve_address(self) -> str | None:
        if self.address:
            return self.address
        devices = await discover(self.scan_timeout_s)
        if not devices:
            return None
        device = devices[0]
        log.info("найден кулон %s (%s)", device.name or "без имени", device.address)
        return device.address

    async def _read_optional(self, client, uuid: str) -> bytes | None:
        try:
            return await client.read_gatt_char(uuid)
        except Exception as exc:
            log.debug("характеристика %s недоступна: %s", uuid, exc)
            return None

    async def _session(
        self, address: str, handler: PacketHandler, stop: asyncio.Event
    ) -> None:
        disconnected = asyncio.Event()

        def on_disconnect(_client) -> None:
            log.warning("кулон отключился")
            disconnected.set()

        async with self._make_client(address, on_disconnect) as client:
            self.stats.connects += 1
            self.stats.tracker.reset()  # счётчик пакетов на устройстве начинается заново
            log.info("подключено к %s", address)
            await ensure_paired(client)

            codec = await self._read_optional(client, AUDIO_CODEC_UUID)
            if codec:
                self.stats.codec = codec[0]
                log.info("кодек: %s", codec_name(self.stats.codec))
                if self.stats.codec not in OPUS_CODECS:
                    log.error(
                        "кулон отдаёт не Opus (%s) — мост рассчитан на Opus 16 кГц",
                        codec_name(self.stats.codec),
                    )

            battery = await self._read_optional(client, BATTERY_LEVEL_UUID)
            if battery:
                self.stats.battery_percent = battery[0]
                log.info("батарея: %d %%", self.stats.battery_percent)

            def on_notify(_sender, data: bytearray) -> None:
                packet = parse_packet(data)
                if packet is None or packet.is_empty:
                    return
                lost = self.stats.tracker.observe(packet)
                if lost:
                    log.warning("потеряно пакетов: %d", lost)
                handler(packet)

            await client.start_notify(AUDIO_DATA_UUID, on_notify)
            log.info("приём аудио пошёл")

            stopper = asyncio.create_task(stop.wait())
            dropper = asyncio.create_task(disconnected.wait())
            try:
                await asyncio.wait(
                    {stopper, dropper}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in (stopper, dropper):
                    task.cancel()
                if not disconnected.is_set():
                    try:
                        await client.stop_notify(AUDIO_DATA_UUID)
                    except Exception as exc:
                        log.debug("stop_notify не прошёл: %s", exc)

    def _make_client(self, address: str, on_disconnect) -> object:
        """Настоящий BleakClient или подмена из ``client_factory``."""
        if self.client_factory is not None:
            return self.client_factory(address, disconnected_callback=on_disconnect)
        from bleak import BleakClient

        return BleakClient(address, disconnected_callback=on_disconnect)

    async def run(
        self, handler: PacketHandler, stop: asyncio.Event | None = None
    ) -> None:
        """Крутит соединение до остановки, переподключаясь с ростом паузы."""
        stop = stop or asyncio.Event()
        delay = self.reconnect_initial_s

        while not stop.is_set():
            address = await self._resolve_address()
            if address is None:
                log.warning(
                    "кулон не найден. Если он подключён к телефону — отвяжите: "
                    "при активном соединении реклама BLE прекращается"
                )
            else:
                try:
                    await self._session(address, handler, stop)
                    delay = self.reconnect_initial_s  # успешная сессия сбрасывает паузу
                except Exception as exc:
                    log.warning("сессия оборвалась: %s", exc)
                finally:
                    self.stats.disconnects += 1

            if stop.is_set():
                break
            log.info("переподключение через %.0f с", delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.reconnect_max_s)
