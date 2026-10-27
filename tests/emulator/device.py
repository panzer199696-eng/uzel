"""Устройство: PCM 16 кГц → кадры Opus → пакеты протокола кулона (P10).

Эмулятор обязан быть неотличим от железа на уровне байтов: каждое уведомление —
3 байта заголовка (uint16 LE номер пакета + uint8 внутренний индекс) и один
цельный кадр Opus, и всё это обязан разбирать ``parse_packet`` из
``bridge/uzel_bridge/protocol.py``.
"""

from __future__ import annotations

import numpy as np

from .scenario import Scenario, SpeechMaterial

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 сэмплов на кадр
BIT_RATE = 24000  # как в замерах проекта: 2,81 КБ/с против 4,6 КБ/с у Omi
PACKET_COUNTER_MODULO = 1 << 16


class OpusStreamEncoder:
    """Стриминговый энкодер в кадры Opus по 20 мс.

    Грабля проекта: libopus всегда ОТДАЁТ 48 кГц при декодировании — вход
    кодируются нативные для Opus 16 кГц (проверено: кодирование на 16 кГц и
    декодер ``OpusDecoder`` дают исходную длительность с точностью до задержки
    кодека). Пересчёт частот нужен на выходе декодера, там он уже есть.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        bit_rate: int = BIT_RATE,
        frame_samples: int = FRAME_SAMPLES,
    ) -> None:
        import av

        self._av = av
        self._encoder = av.CodecContext.create("libopus", "w")
        self._encoder.sample_rate = sample_rate
        self._encoder.format = "s16"
        self._encoder.layout = "mono"
        self._encoder.bit_rate = bit_rate
        self._frame_samples = frame_samples
        self._next_pts = 0

    def encode(self, samples: np.ndarray) -> list[bytes]:
        """Кадр PCM (ровно ``frame_samples`` сэмплов) → кадры Opus."""
        frame = self._av.AudioFrame.from_ndarray(
            samples.astype(np.int16).reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = self._encoder.sample_rate
        frame.pts = self._next_pts
        self._next_pts += len(samples)
        return [bytes(packet) for packet in self._encoder.encode(frame)]


class EmulatedPendant:
    """Кулон в банке: кодирует поток сценария и нумерует пакеты как железо.

    Время устройства — условное: каждый выданный кадр это 20 мс потока, а
    ``skip()`` продвигает время без звука (разрыв связи, когда радио молчит).
    По этому же времени садится батарея.
    """

    def __init__(
        self,
        material: SpeechMaterial,
        *,
        codec: int = 20,
        battery_hours: float = 24.0,
        start_packet_index: int = 0,
        bit_rate: int = BIT_RATE,
    ) -> None:
        self.material = material
        self.scenario: Scenario = material.scenario
        self.codec = codec
        self.battery_hours = battery_hours
        self._encoder = OpusStreamEncoder(bit_rate=bit_rate)
        self._packet_index = start_packet_index % PACKET_COUNTER_MODULO
        self._internal_index = 0
        self._pcm = np.zeros(0, dtype=np.int16)
        self._pcm_is_speech = False
        self._frames_emitted = 0
        self._speech_frames = 0
        self._skipped_seconds = 0.0

    # --- поток ---

    def frames(self, count: int) -> list[bytes]:
        """Следующие ``count`` кадров Opus (без заголовков протокола)."""
        out: list[bytes] = []
        while len(out) < count:
            if len(self._pcm) < FRAME_SAMPLES:
                self._refill_pcm()
                if len(self._pcm) < FRAME_SAMPLES:
                    break  # материал исчерпан (не случается с бесконечным потоком)
            chunk = self._pcm[:FRAME_SAMPLES]
            self._pcm = self._pcm[FRAME_SAMPLES:]
            for frame in self._encoder.encode(chunk):
                out.append(frame)
                self._frames_emitted += 1
                if self._pcm_is_speech:
                    self._speech_frames += 1
        return out

    def notifications(self, count: int) -> list[bytes]:
        """Следующие ``count`` BLE-уведомлений: заголовок + один кадр Opus."""
        return [self._wrap(frame) for frame in self.frames(count)]

    def _refill_pcm(self) -> None:
        block, is_speech = self.material.block()
        self._pcm = np.concatenate([self._pcm, block])
        self._pcm_is_speech = is_speech

    def _wrap(self, frame: bytes) -> bytes:
        header = self._packet_index.to_bytes(2, "little") + bytes(
            [self._internal_index]
        )
        self._packet_index = (self._packet_index + 1) % PACKET_COUNTER_MODULO
        self._internal_index = (self._internal_index + 1) % 256
        return header + frame

    # --- состояние устройства ---

    def reset_link(self) -> None:
        """Новое BLE-соединение: счётчик пакетов на устройстве начинается заново."""
        self._packet_index = 0
        self._internal_index = 0

    def skip(self, seconds: float) -> None:
        """Продвигает время устройства без звука (радио молчит)."""
        self._skipped_seconds += seconds

    @property
    def emulated_seconds(self) -> float:
        """Условное время от старта: поток плюс паузы разрывов."""
        return self._frames_emitted * FRAME_MS / 1000.0 + self._skipped_seconds

    @property
    def battery_percent(self) -> int:
        """Заряд по времени устройства и собственной ёмкости батареи."""
        elapsed_h = self.emulated_seconds / 3600.0
        remaining = 1.0 - elapsed_h / self.battery_hours
        return int(round(max(0.0, min(1.0, remaining)) * 100.0))

    @property
    def packets_emitted(self) -> int:
        return self._frames_emitted

    @property
    def speech_seconds(self) -> float:
        return self._speech_frames * FRAME_MS / 1000.0

    @property
    def silence_seconds(self) -> float:
        stream = self._frames_emitted * FRAME_MS / 1000.0
        return stream - self.speech_seconds
