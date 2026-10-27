"""Протокол кулона Omi/Friend по BLE (FR-060, FR-061).

Совместимость с Omi держим намеренно: тот же сервис и те же характеристики,
поэтому мост работает и с нашим железом, и с уже купленными кулонами Omi.

Каждое BLE-уведомление — это 3 байта заголовка и **один цельный кадр Opus**.
Собирать кадр из нескольких пакетов не нужно. Заголовок::

    [0:2]  номер пакета, uint16 LE  — по нему видно потери
    [2]    внутренний индекс, uint8
    [3:]   кадр Opus

Значения сверены с ``sdks/python/omi/constants.py`` (BasedHardware/omi, MIT).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

SERVICE_UUID = "19b10000-e8f2-537e-4f6c-d104768a1214"
AUDIO_DATA_UUID = "19b10001-e8f2-537e-4f6c-d104768a1214"
AUDIO_CODEC_UUID = "19b10002-e8f2-537e-4f6c-d104768a1214"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

HEADER_BYTES = 3
PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PACKET_COUNTER_MODULO = 1 << 16

# Значения характеристики кодека. У нашего кулона проверено 21.08.2026: 20.
CODECS = {
    0: "PCM 16 бит 16 кГц",
    1: "PCM 8 бит 16 кГц",
    10: "μ-law 8 бит 16 кГц",
    20: "Opus 16 кГц",
    21: "Opus 16 кГц (FEC)",
}
OPUS_CODECS = (20, 21)

# Имена, под которыми кулон рекламируется в эфире. «uzel» — своя прошивка с 0.1.0.
DEVICE_NAME_HINTS = ("omi", "friend", "uzel")

# Своя прошивка УЗЛА (firmware/, с 0.1.0): счётчики работы кулона, формат —
# firmware/src/stats.h. Читается только по шифрованной связи.
UZEL_STATS_UUID = "5a0e1001-7a3e-4b8e-9f2a-0d1e2f3a4b5c"
DIS_FIRMWARE_REV_UUID = "00002a26-0000-1000-8000-00805f9b34fb"

_STATS_FORMAT = "<BIIIIIIIHHHBB"
_STATS_FIELDS = (
    "версия_формата",
    "аптайм_с",
    "кадров_закодировано",
    "pcm_блоков_потеряно",
    "кадров_не_влезло_в_очередь",
    "кадров_отправлено",
    "кадров_не_отправлено",
    "отсчётов_через_лимитер",
    "кодирование_мкс_среднее",
    "кодирование_мкс_макс",
    "att_mtu",
    "кадров_в_очереди",
    "сопряжённых_устройств",
)


def parse_stats(data: bytes | bytearray) -> dict[str, int]:
    """Счётчики прошивки УЗЛА. Неизвестная версия формата — ``ValueError``."""
    size = struct.calcsize(_STATS_FORMAT)
    raw = bytes(data)
    if len(raw) < size or raw[0] != 1:
        raise ValueError(f"статистика кулона: {len(raw)} байт, версия {raw[:1].hex() or '?'}")
    return dict(zip(_STATS_FIELDS, struct.unpack(_STATS_FORMAT, raw[:size])))


@dataclass(frozen=True)
class AudioPacket:
    """Разобранное BLE-уведомление с аудио."""

    packet_index: int
    internal_index: int
    payload: bytes

    @property
    def is_empty(self) -> bool:
        return not self.payload


def parse_packet(data: bytes | bytearray) -> AudioPacket | None:
    """Разбирает уведомление. ``None``, если пакет короче заголовка."""
    if data is None or len(data) <= HEADER_BYTES:
        return None
    raw = bytes(data)
    return AudioPacket(
        packet_index=int.from_bytes(raw[0:2], "little"),
        internal_index=raw[2],
        payload=raw[HEADER_BYTES:],
    )


def codec_name(value: int | None) -> str:
    if value is None:
        return "неизвестен"
    return CODECS.get(value, f"неизвестный код {value}")


class PacketTracker:
    """Считает потери по счётчику пакетов с учётом переполнения uint16."""

    def __init__(self) -> None:
        self.received = 0
        self.lost = 0
        self.payload_bytes = 0
        self._last_index: int | None = None

    def observe(self, packet: AudioPacket) -> int:
        """Учитывает пакет. Возвращает, сколько пакетов потеряно перед ним."""
        self.received += 1
        self.payload_bytes += len(packet.payload)

        if self._last_index is None:
            self._last_index = packet.packet_index
            return 0

        gap = (packet.packet_index - self._last_index) % PACKET_COUNTER_MODULO
        self._last_index = packet.packet_index

        if gap <= 1:
            return 0  # gap == 0 — повтор того же номера, потерей не считаем
        lost = gap - 1
        self.lost += lost
        return lost

    def reset(self) -> None:
        """Сброс при переподключении: счётчик на устройстве начинается заново."""
        self._last_index = None

    @property
    def loss_ratio(self) -> float:
        total = self.received + self.lost
        return self.lost / total if total else 0.0
