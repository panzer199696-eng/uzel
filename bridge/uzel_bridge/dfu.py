"""Обновление прошивки кулона по воздуху (Nordic Legacy DFU через загрузчик Adafruit).

Как это устроено на кулоне (сверено с исходниками Adafruit_nRF52_Bootloader 0.9.0):

1. Прошивка УЗЛА по записи ``01 04`` в точку управления DFU (только по шифрованной
   связи) пишет ``GPREGRET = 0xA8`` и перезагружается.
2. Загрузчик поднимает свой BLE с SoftDevice S140 и рекламируется как ``AdaDFU``
   с адресом на единицу больше в младшем байте (``addr[0] += 1``, без переноса).
3. Принимает образ протоколом SDK 11: размеры → пакет инициализации → образ
   кусками по 20 байт с квитанциями каждые N пакетов → проверка → запуск.
4. Пакет инициализации: тип устройства 0x0052 (обязателен), ревизия, версия,
   список SoftDevice (0xFFFE — любой) и CRC16 образа — её загрузчик сверяет.

Загрузчик однобанковый: образ пишется поверх старой прошивки. Оборванная заливка
не «кирпич»: загрузчик остаётся в режиме DFU, а по кабелю (двойной RST → UF2)
кулон восстанавливается всегда.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

DFU_SERVICE_UUID = "00001530-1212-efde-1523-785feabcd123"
DFU_CONTROL_UUID = "00001531-1212-efde-1523-785feabcd123"
DFU_PACKET_UUID = "00001532-1212-efde-1523-785feabcd123"
BOOTLOADER_NAME = "AdaDFU"

OP_START = 0x01
OP_INIT = 0x02
OP_RECEIVE = 0x03
OP_VALIDATE = 0x04
OP_ACTIVATE = 0x05
OP_PRN_REQUEST = 0x08
OP_RESPONSE = 0x10
OP_PRN = 0x11
IMAGE_APPLICATION = 0x04

ADAFRUIT_DEVICE_TYPE = 0x0052
SOFTDEVICE_ANY = 0xFFFE
APP_START = 0x27000  # начало приложения за SoftDevice S140 7.3.0
PACKET_SIZE = 20  # ATT MTU загрузчика — 23
PRN_INTERVAL = 10

STATUS_TEXT = {
    1: "успех",
    2: "неверное состояние",
    3: "не поддерживается",
    4: "превышен размер данных",
    5: "ошибка CRC",
    6: "операция не удалась",
}

Progress = Callable[[int, int], None]


class DfuError(RuntimeError):
    """Загрузчик отказал или не ответил."""


def crc16_nordic(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC16 как ``crc16_compute`` из nRF5 SDK (CCITT, начальное 0xFFFF)."""
    for byte in data:
        crc = ((crc >> 8) | (crc << 8)) & 0xFFFF
        crc ^= byte
        crc ^= (crc & 0xFF) >> 4
        crc ^= (crc << 12) & 0xFFFF
        crc ^= ((crc & 0xFF) << 5) & 0xFFFF
    return crc


def init_packet(image: bytes, *, app_version: int = 0xFFFFFFFF) -> bytes:
    """Пакет инициализации SDK 11 в том виде, что проверяет загрузчик Adafruit."""
    header = struct.pack(
        "<HHIHH", ADAFRUIT_DEVICE_TYPE, 0xFFFF, app_version, 1, SOFTDEVICE_ANY
    )
    return header + struct.pack("<H", crc16_nordic(image))


def hex_to_image(text: str, start: int = APP_START) -> bytes:
    """Intel HEX → непрерывный образ с адреса ``start`` (дыры — 0xFF).

    Записи ниже ``start`` (SoftDevice, MBR) в образ приложения не входят и
    отбрасываются; запись, перекрывающая начало, — ошибка сборки, а не данные.
    """
    chunks: dict[int, bytes] = {}
    base = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"строка {lineno}: не Intel HEX")
        raw = bytes.fromhex(line[1:])
        length, addr, rtype = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        payload = raw[4 : 4 + length]
        if len(payload) != length or (sum(raw) & 0xFF) != 0:
            raise ValueError(f"строка {lineno}: битая запись")
        if rtype == 0x00:
            absolute = base + addr
            if absolute < start < absolute + length:
                raise ValueError(f"строка {lineno}: запись перекрывает начало образа")
            if absolute >= start:
                chunks[absolute] = payload
        elif rtype == 0x01:
            break
        elif rtype == 0x02:
            base = int.from_bytes(payload, "big") << 4
        elif rtype == 0x04:
            base = int.from_bytes(payload, "big") << 16
        # 0x03/0x05 — адрес старта, для образа не нужен
    if not chunks:
        raise ValueError(f"в файле нет данных с адреса 0x{start:X}")
    end = max(a + len(d) for a, d in chunks.items())
    image = bytearray(b"\xff" * (end - start))
    for addr, data in chunks.items():
        image[addr - start : addr - start + len(data)] = data
    return bytes(image)


def make_package(hex_path: Path, zip_path: Path) -> Path:
    """Zip-пакет для телефона (библиотека Nordic DFU): manifest.json + образ + пакет
    инициализации — тот же формат, что у ``adafruit-nrfutil dfu genpkg``."""
    import json
    import zipfile

    image = hex_to_image(Path(hex_path).read_text(encoding="ascii"))
    init = init_packet(image)
    manifest = {
        "manifest": {
            "application": {
                "bin_file": "application.bin",
                "dat_file": "application.dat",
                "init_packet_data": {
                    "application_version": 0xFFFFFFFF,
                    "device_revision": 0xFFFF,
                    "device_type": ADAFRUIT_DEVICE_TYPE,
                    "firmware_crc16": crc16_nordic(image),
                    "softdevice_req": [SOFTDEVICE_ANY],
                },
            },
            "dfu_version": 0.5,
        }
    }
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("application.bin", image)
        zf.writestr("application.dat", init)
    return zip_path


def bootloader_address(app_address: str) -> str:
    """Адрес загрузчика: младший байт +1 по модулю 256, как ``addr.addr[0] += 1``."""
    parts = app_address.split(":")
    if len(parts) != 6:
        raise ValueError(f"не MAC-адрес: {app_address}")
    parts[-1] = f"{(int(parts[-1], 16) + 1) & 0xFF:02X}"
    return ":".join(p.upper() for p in parts)


class LegacyDfu:
    """Протокол SDK 11 поверх подключённого к загрузчику клиента bleak."""

    def __init__(
        self,
        client,
        *,
        packet_size: int = PACKET_SIZE,
        prn: int = PRN_INTERVAL,
        timeout_s: float = 10.0,
        progress: Progress | None = None,
    ) -> None:
        self.client = client
        self.packet_size = packet_size
        self.prn = prn
        self.timeout_s = timeout_s
        self.progress = progress
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._receipts: asyncio.Queue[int] = asyncio.Queue()

    def _on_notify(self, _sender, data: bytearray) -> None:
        data = bytes(data)
        if data[:1] == bytes([OP_PRN]) and len(data) >= 5:
            self._receipts.put_nowait(int.from_bytes(data[1:5], "little"))
        elif data[:1] == bytes([OP_RESPONSE]) and len(data) >= 3:
            self._responses.put_nowait(data)
        else:
            log.debug("DFU: неизвестное уведомление %s", data.hex())

    async def _control(self, *payload: int) -> None:
        await self.client.write_gatt_char(DFU_CONTROL_UUID, bytes(payload), response=True)

    async def _packet(self, data: bytes) -> None:
        await self.client.write_gatt_char(DFU_PACKET_UUID, data, response=False)

    async def _expect(self, op: int, timeout_s: float | None = None) -> None:
        try:
            resp = await asyncio.wait_for(
                self._responses.get(), timeout_s or self.timeout_s
            )
        except asyncio.TimeoutError as exc:
            raise DfuError(f"загрузчик не ответил на операцию {op}") from exc
        if resp[1] != op:
            raise DfuError(f"ответ на операцию {resp[1]}, ждали {op}")
        if resp[2] != 1:
            text = STATUS_TEXT.get(resp[2], f"код {resp[2]}")
            raise DfuError(f"операция {op}: {text}")

    async def _send_stream(self, data: bytes, *, with_receipts: bool) -> None:
        total = len(data)
        sent_packets = 0
        for offset in range(0, total, self.packet_size):
            await self._packet(data[offset : offset + self.packet_size])
            sent_packets += 1
            if with_receipts and self.prn and sent_packets % self.prn == 0:
                sent = min(offset + self.packet_size, total)
                try:
                    got = await asyncio.wait_for(self._receipts.get(), self.timeout_s)
                except asyncio.TimeoutError as exc:
                    raise DfuError(f"нет квитанции после {sent} байт") from exc
                if got != sent:
                    raise DfuError(f"загрузчик принял {got} байт, отправлено {sent}")
                if self.progress:
                    self.progress(sent, total)
        if with_receipts and self.progress:  # прогресс — только по образу, не по пакету инициализации
            self.progress(total, total)

    async def run(self, image: bytes, init: bytes) -> None:
        await self.client.start_notify(DFU_CONTROL_UUID, self._on_notify)

        await self._control(OP_START, IMAGE_APPLICATION)
        await self._packet(struct.pack("<III", 0, 0, len(image)))
        await self._expect(OP_START, 30.0)  # загрузчик стирает место под образ

        await self._control(OP_INIT, 0x00)
        await self._send_stream(init, with_receipts=False)
        await self._control(OP_INIT, 0x01)
        await self._expect(OP_INIT)

        await self._control(OP_PRN_REQUEST, self.prn & 0xFF, self.prn >> 8)
        await self._control(OP_RECEIVE)
        await self._send_stream(image, with_receipts=True)
        await self._expect(OP_RECEIVE, 30.0)

        await self._control(OP_VALIDATE)
        await self._expect(OP_VALIDATE)
        try:
            await self._control(OP_ACTIVATE)
        except Exception as exc:  # загрузчик перезагружается и рвёт связь раньше ответа
            log.debug("активация: связь оборвалась, как и ожидалось (%s)", exc)


async def _find(address: str, name: str | None, timeout_s: float, scanner_factory):
    """Ждёт в эфире устройство с адресом или именем."""
    scanner = scanner_factory()

    def matches(device, adv) -> bool:
        local = (device.name or getattr(adv, "local_name", None) or "").strip()
        return device.address.upper() == address.upper() or (name is not None and local == name)

    return await scanner.find_device_by_filter(matches, timeout=timeout_s)


async def update_firmware(
    hex_path: Path,
    address: str,
    *,
    client_factory=None,
    scanner_factory=None,
    progress: Progress | None = None,
    wait_bootloader_s: float = 20.0,
) -> None:
    """Полный цикл: прошивка УЗЛА → загрузчик → заливка → запуск новой прошивки."""
    from .ble import ensure_paired
    from .protocol import DIS_FIRMWARE_REV_UUID

    if client_factory is None or scanner_factory is None:
        from bleak import BleakClient, BleakScanner

        client_factory = client_factory or BleakClient
        scanner_factory = scanner_factory or BleakScanner

    image = hex_to_image(Path(hex_path).read_text(encoding="ascii"))
    init = init_packet(image)
    log.info("образ %d байт, CRC16 %04X", len(image), crc16_nordic(image))

    # 1. Команда прошивке УЗЛА уйти в загрузчик (нужно шифрование → сопряжение)
    # Ошибка записи — ещё не отказ: кулон перезагружается через 300 мс и может
    # оборвать связь раньше, чем дойдёт ответ. Судим по появлению загрузчика.
    command_error: Exception | None = None
    try:
        async with client_factory(address) as app:
            await ensure_paired(app)
            await app.write_gatt_char(
                DFU_CONTROL_UUID, bytes([OP_START, IMAGE_APPLICATION]), response=True
            )
    except Exception as exc:
        command_error = exc
        log.info("связь оборвалась на команде DFU (%s) — проверяю, ушёл ли кулон в загрузчик", exc)
    else:
        log.info("кулон уходит в загрузчик")

    # 2. Загрузчик под адресом +1
    target = bootloader_address(address)
    device = await _find(target, BOOTLOADER_NAME, wait_bootloader_s, scanner_factory)
    if device is None:
        if command_error is not None:
            raise DfuError(
                f"кулон не принял команду DFU: {command_error}. "
                "Нужна прошивка УЗЛА и сопряжение с этим ПК"
            ) from command_error
        raise DfuError(
            f"загрузчик {target} не появился за {wait_bootloader_s:.0f} с. "
            "Кулон висит в загрузчике до выключения питания — выключите и включите"
        )
    log.info("загрузчик найден: %s", device.address)

    # 3. Заливка
    async with client_factory(device) as boot:
        await LegacyDfu(boot, progress=progress).run(image, init)
    log.info("образ принят, кулон перезапускается")

    # 4. Новая прошивка в эфире
    device = await _find(address, None, wait_bootloader_s, scanner_factory)
    if device is None:
        log.warning("после заливки кулон не появился за %.0f с", wait_bootloader_s)
        return
    async with client_factory(device) as app:
        try:
            rev = await app.read_gatt_char(DIS_FIRMWARE_REV_UUID)
            log.info("версия прошивки на кулоне: %s", bytes(rev).decode(errors="replace"))
        except Exception as exc:
            log.debug("версию прочитать не удалось: %s", exc)


if __name__ == "__main__":
    # Упаковка без зависимостей моста — её зовёт firmware/build.sh из WSL:
    #   python3 bridge/uzel_bridge/dfu.py package <in.hex> <out.zip>
    import sys

    if len(sys.argv) != 4 or sys.argv[1] != "package":
        sys.exit("использование: dfu.py package <in.hex> <out.zip>")
    print(make_package(Path(sys.argv[2]), Path(sys.argv[3])))
