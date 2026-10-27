"""Сборка моста: пакеты → PCM → буфер → ядро.

Вынесено из ``cli`` отдельно, чтобы проверяться тестами без BLE: на вход можно
подать пакеты откуда угодно.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from .opus_decoder import OpusDecoder
from .protocol import AudioPacket
from .spool import Uploader

log = logging.getLogger(__name__)


class AudioPipeline:
    """Копит расшифрованный PCM и отдаёт его в ядро порциями по времени."""

    def __init__(
        self,
        uploader: Uploader,
        *,
        sample_rate: int = 16000,
        flush_seconds: float = 5.0,
        decoder: OpusDecoder | None = None,
    ) -> None:
        self.uploader = uploader
        self.sample_rate = sample_rate
        self.flush_samples = int(flush_seconds * sample_rate)
        self.decoder = decoder if decoder is not None else OpusDecoder(sample_rate)
        self._buffer: list[np.ndarray] = []
        self._buffered_samples = 0
        self.decoded_samples = 0

    @property
    def buffered_seconds(self) -> float:
        return self._buffered_samples / float(self.sample_rate)

    def feed(self, packet: AudioPacket) -> None:
        """Принимает один BLE-пакет; отправляет, когда накопилась порция."""
        pcm = self.decoder.decode(packet.payload)
        if pcm.size == 0:
            return
        self._buffer.append(pcm)
        self._buffered_samples += pcm.size
        self.decoded_samples += pcm.size
        if self._buffered_samples >= self.flush_samples:
            self.flush()

    def flush(self) -> bool:
        """Отдаёт накопленное. ``False`` — ушло в буфер на диске, не в ядро."""
        if not self._buffer:
            return True
        payload = np.concatenate(self._buffer).astype("<i2").tobytes()
        self._buffer.clear()
        self._buffered_samples = 0
        return self.uploader.submit(payload, device_time_ms=int(time.time() * 1000))
