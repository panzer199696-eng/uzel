"""Обновление прошивки по воздуху (bridge/uzel_bridge/dfu.py) и сопряжение моста.

Загрузчик Adafruit имитируется по его исходникам 0.9.0: ответы ``10 op 01``,
квитанции ``11 <u32 байт>`` каждые N пакетов, CRC16 образа из пакета
инициализации сверяется на VALIDATE. Железо не нужно.
"""

from __future__ import annotations

import asyncio
import binascii
import random
import struct

import pytest

from uzel_bridge import dfu
from uzel_bridge.ble import ensure_paired
from uzel_bridge.protocol import DEVICE_NAME_HINTS, parse_stats

# Вымышленные адреса: приложение …:FE, загрузчик …:FF. Префикс без шестой
# группы, чтобы сканер публичной выгрузки не принимал их за настоящий MAC.
FAKE_MAC_PREFIX = "AA:BB:CC:DD:EE"
APP_ADDR = f"{FAKE_MAC_PREFIX}:FE"
BOOT_ADDR = "AA:BB:CC:DD:EE:FF"


def run(coro):
    return asyncio.run(coro)


# --- CRC, пакет инициализации, HEX, адрес ---------------------------------


def test_crc16_совпадает_с_ccitt_false():
    rnd = random.Random(1)
    for size in (0, 1, 20, 1000):
        data = bytes(rnd.randrange(256) for _ in range(size))
        assert dfu.crc16_nordic(data) == binascii.crc_hqx(data, 0xFFFF)


def test_пакет_инициализации_для_загрузчика_adafruit():
    image = b"\x01\x02\x03"
    packet = dfu.init_packet(image)
    dev_type, dev_rev, app_ver, sd_len, sd = struct.unpack("<HHIHH", packet[:12])
    assert dev_type == 0x0052  # иначе NRF_ERROR_FORBIDDEN
    assert (sd_len, sd) == (1, 0xFFFE)  # любой SoftDevice
    assert dev_rev == 0xFFFF and app_ver == 0xFFFFFFFF
    assert struct.unpack("<H", packet[12:])[0] == dfu.crc16_nordic(image)


def _hex_record(addr: int, rtype: int, payload: bytes) -> str:
    raw = bytes([len(payload), addr >> 8, addr & 0xFF, rtype]) + payload
    return ":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper()


def test_hex_в_образ_с_начала_приложения():
    lines = [
        _hex_record(0x0000, 0x04, b"\x00\x01"),  # база 0x10000
        _hex_record(0x0000, 0x00, b"\xaa" * 4),  # 0x10000 — SoftDevice, отбрасывается
        _hex_record(0x0000, 0x04, b"\x00\x02"),  # база 0x20000
        _hex_record(0x7000, 0x00, b"\x11\x22"),  # 0x27000
        _hex_record(0x7004, 0x00, b"\x33"),  # дыра в 2 байта → 0xFF
        _hex_record(0x0000, 0x01, b""),
    ]
    image = dfu.hex_to_image("\n".join(lines))
    assert image == b"\x11\x22\xff\xff\x33"


def test_hex_битая_контрольная_сумма():
    good = _hex_record(0x7000, 0x00, b"\x11")
    bad = good[:-2] + ("00" if good[-2:] != "00" else "01")
    with pytest.raises(ValueError, match="битая"):
        dfu.hex_to_image(_hex_record(0, 0x04, b"\x00\x02") + "\n" + bad)


def test_hex_без_данных_приложения():
    with pytest.raises(ValueError, match="нет данных"):
        dfu.hex_to_image(_hex_record(0x0000, 0x00, b"\x01") + "\n" + _hex_record(0, 0x01, b""))


def test_адрес_загрузчика_младший_байт_плюс_один_без_переноса():
    assert dfu.bootloader_address(APP_ADDR) == BOOT_ADDR
    assert dfu.bootloader_address(BOOT_ADDR.lower()) == f"{FAKE_MAC_PREFIX}:00"
    with pytest.raises(ValueError):
        dfu.bootloader_address("не адрес")


# --- Имитация загрузчика ---------------------------------------------------


class FakeBootloader:
    """Клиент bleak, за которым «загрузчик» SDK 11 в духе Adafruit 0.9.0."""

    def __init__(self, *, start_status=1, drop_receipt_after=None, corrupt=False):
        self.start_status = start_status
        self.drop_receipt_after = drop_receipt_after
        self.corrupt = corrupt
        self.callback = None
        self.state = "idle"
        self.expected = 0
        self.received = bytearray()
        self.init = bytearray()
        self.prn = 0
        self.packets = 0
        self.max_packet = 0
        self.activated = False

    async def start_notify(self, uuid, callback):
        assert uuid == dfu.DFU_CONTROL_UUID
        self.callback = callback

    def _notify(self, data: bytes) -> None:
        self.callback(None, bytearray(data))

    async def write_gatt_char(self, uuid, data, response=None):
        data = bytes(data)
        if uuid == dfu.DFU_PACKET_UUID:
            assert response is False
            self.max_packet = max(self.max_packet, len(data))
            self._on_packet(data)
            return
        assert uuid == dfu.DFU_CONTROL_UUID and response is True
        op = data[0]
        if op == dfu.OP_START:
            assert data[1] == dfu.IMAGE_APPLICATION
            self.state = "sizes"
        elif op == dfu.OP_INIT:
            self.state = "init" if data[1] == 0 else "idle"
            if data[1] == 1:
                self._notify(bytes([0x10, dfu.OP_INIT, 1]))
        elif op == dfu.OP_PRN_REQUEST:
            self.prn = data[1] | (data[2] << 8)
        elif op == dfu.OP_RECEIVE:
            self.state = "image"
        elif op == dfu.OP_VALIDATE:
            crc = struct.unpack("<H", bytes(self.init[-2:]))[0]
            ok = dfu.crc16_nordic(bytes(self.received)) == crc
            self._notify(bytes([0x10, dfu.OP_VALIDATE, 1 if ok else 5]))
        elif op == dfu.OP_ACTIVATE:
            self.activated = True
            raise OSError("связь оборвалась: загрузчик перезагружается")

    def _on_packet(self, data: bytes) -> None:
        if self.state == "sizes":
            self.expected = struct.unpack("<III", data)[2]
            self.state = "idle"
            self._notify(bytes([0x10, dfu.OP_START, self.start_status]))
        elif self.state == "init":
            self.init += data
        elif self.state == "image":
            self.received += data[:1] + bytes([data[1] ^ 0xFF]) + data[2:] if (
                self.corrupt and len(self.received) == 0 and len(data) > 1
            ) else data
            self.packets += 1
            if self.prn and self.packets % self.prn == 0:
                if self.drop_receipt_after is None or self.packets <= self.drop_receipt_after:
                    self._notify(bytes([0x11]) + struct.pack("<I", len(self.received)))
            if len(self.received) >= self.expected:
                self._notify(bytes([0x10, dfu.OP_RECEIVE, 1]))


def _image(size=1234):
    rnd = random.Random(7)
    return bytes(rnd.randrange(256) for _ in range(size))


def test_полная_заливка_проходит():
    image = _image()
    boot = FakeBootloader()
    seen = []
    run(dfu.LegacyDfu(boot, progress=lambda d, t: seen.append((d, t))).run(image, dfu.init_packet(image)))
    assert bytes(boot.received) == image
    assert boot.activated
    assert boot.max_packet <= dfu.PACKET_SIZE
    assert boot.prn == dfu.PRN_INTERVAL
    assert seen[-1] == (len(image), len(image))


def test_образ_кратный_квитанциям():
    image = _image(dfu.PACKET_SIZE * dfu.PRN_INTERVAL * 3)
    boot = FakeBootloader()
    run(dfu.LegacyDfu(boot).run(image, dfu.init_packet(image)))
    assert bytes(boot.received) == image


def test_загрузчик_отказал_на_старте():
    image = _image()
    boot = FakeBootloader(start_status=4)
    with pytest.raises(dfu.DfuError, match="превышен размер"):
        run(dfu.LegacyDfu(boot).run(image, dfu.init_packet(image)))


def test_битый_образ_ловится_по_crc():
    image = _image()
    boot = FakeBootloader(corrupt=True)
    with pytest.raises(dfu.DfuError, match="CRC"):
        run(dfu.LegacyDfu(boot).run(image, dfu.init_packet(image)))
    assert not boot.activated


def test_нет_квитанции_это_ошибка_а_не_зависание():
    image = _image()
    boot = FakeBootloader(drop_receipt_after=20)
    with pytest.raises(dfu.DfuError, match="нет квитанции"):
        run(dfu.LegacyDfu(boot, timeout_s=0.2).run(image, dfu.init_packet(image)))


# --- Сопряжение и статистика ------------------------------------------------


class PairingClient:
    def __init__(self, result=True, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def pair(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_сопряжение_успешно():
    client = PairingClient()
    assert run(ensure_paired(client)) is True
    assert client.calls == 1


def test_отказ_в_сопряжении_не_роняет_сессию():
    assert run(ensure_paired(PairingClient(error=OSError("Pairing failed")))) is False
    assert run(ensure_paired(PairingClient(result=False))) is False


def test_клиент_без_сопряжения_пропускается():
    assert run(ensure_paired(object())) is False


def test_статистика_прошивки():
    raw = struct.pack("<BIIIIIIIHHHBB", 1, 60, 3000, 0, 2, 2990, 1, 17, 4100, 7900, 247, 3, 2)
    stats = parse_stats(raw)
    assert stats["кадров_отправлено"] == 2990
    assert stats["att_mtu"] == 247
    assert stats["сопряжённых_устройств"] == 2
    assert len(raw) == 37  # совпадает с sizeof(uzel_stats_t) в firmware/src/stats.h


def test_статистика_неизвестного_формата():
    with pytest.raises(ValueError):
        parse_stats(b"\x02" + b"\x00" * 40)
    with pytest.raises(ValueError):
        parse_stats(b"\x01\x00")


def test_новое_имя_в_поиске():
    assert "uzel" in DEVICE_NAME_HINTS


def test_zip_пакет_для_телефона(tmp_path):
    import json
    import zipfile

    hex_text = "\n".join([
        _hex_record(0x0000, 0x04, b"\x00\x02"),
        _hex_record(0x7000, 0x00, bytes(range(16))),
        _hex_record(0x0000, 0x01, b""),
    ])
    src = tmp_path / "fw.hex"
    src.write_text(hex_text, encoding="ascii")
    out = dfu.make_package(src, tmp_path / "fw-dfu.zip")
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))["manifest"]
        app = manifest["application"]
        image = zf.read(app["bin_file"])
        init = zf.read(app["dat_file"])
    assert image == bytes(range(16))
    assert init == dfu.init_packet(image)
    assert app["init_packet_data"]["firmware_crc16"] == dfu.crc16_nordic(image)
    assert app["init_packet_data"]["device_type"] == 0x52
    assert manifest["dfu_version"] == 0.5


# --- Полный цикл update_firmware -------------------------------------------


class _Dev:
    def __init__(self, address, name):
        self.address, self.name = address, name


class _Adv:
    local_name = None


def _scanner_factory(devices):
    class Scanner:
        async def find_device_by_filter(self, flt, timeout=0):
            for dev in devices:
                if flt(dev, _Adv()):
                    return dev
            return None

    return Scanner


class _AppClient:
    """Прошивка УЗЛА: команда DFU рвёт связь до ответа (худший случай)."""

    def __init__(self, drop_on_dfu=True):
        self.drop_on_dfu = drop_on_dfu
        self.paired = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def pair(self):
        self.paired = True
        return True

    async def write_gatt_char(self, uuid, data, response=None):
        assert uuid == dfu.DFU_CONTROL_UUID and bytes(data) == b"\x01\x04"
        if self.drop_on_dfu:
            raise OSError("устройство отключилось")

    async def read_gatt_char(self, uuid):
        return b"0.1.0"


class _BootClient(FakeBootloader):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _hex_file(tmp_path, payload: bytes):
    text = "\n".join([
        _hex_record(0x0000, 0x04, b"\x00\x02"),
        _hex_record(0x7000, 0x00, payload),
        _hex_record(0x0000, 0x01, b""),
    ])
    path = tmp_path / "fw.hex"
    path.write_text(text, encoding="ascii")
    return path


def test_обрыв_на_команде_dfu_не_мешает_обновлению(tmp_path):
    app_addr = APP_ADDR
    boot = _BootClient()
    app = _AppClient(drop_on_dfu=True)

    def factory(target):
        return boot if getattr(target, "address", target) == BOOT_ADDR else app

    devices = [_Dev(BOOT_ADDR, "AdaDFU"), _Dev(app_addr, "UZEL")]
    run(dfu.update_firmware(
        _hex_file(tmp_path, bytes(range(40))), app_addr,
        client_factory=factory, scanner_factory=_scanner_factory(devices),
        wait_bootloader_s=0.1,
    ))
    assert app.paired
    assert bytes(boot.received) == bytes(range(40))
    assert boot.activated


def test_нет_загрузчика_после_отказа_команды(tmp_path):
    app = _AppClient(drop_on_dfu=True)
    with pytest.raises(dfu.DfuError, match="не принял команду"):
        run(dfu.update_firmware(
            _hex_file(tmp_path, b"\x01\x02"), APP_ADDR,
            client_factory=lambda t: app, scanner_factory=_scanner_factory([]),
            wait_bootloader_s=0.1,
        ))
