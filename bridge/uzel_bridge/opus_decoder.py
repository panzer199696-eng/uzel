"""Декодер Opus → PCM 16 кГц (FR-002).

Берём PyAV: колёса под Windows несут ffmpeg с libopus внутри, отдельную DLL
ставить не надо (у opuslib на Windows это главная морока).

⚠️ libopus всегда отдаёт 48 кГц независимо от того, на какой частоте кодировали,
поэтому выход обязательно пересчитывается в 16 кГц штатным ресемплером ffmpeg.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000


class OpusDecoder:
    """Кадры Opus → моно PCM int16 16 кГц. Повреждённый кадр не роняет поток."""

    def __init__(self, sample_rate: int = TARGET_SAMPLE_RATE) -> None:
        import av
        from av.audio.resampler import AudioResampler

        self.sample_rate = sample_rate
        self._context = av.CodecContext.create("libopus", "r")
        self._context.format = "s16"
        self._context.layout = "mono"
        self._context.sample_rate = 48000  # libopus всегда декодирует в 48 кГц
        self._resampler = AudioResampler(format="s16", layout="mono", rate=sample_rate)
        self._av = av
        self.decoded_frames = 0
        self.failed_frames = 0

    def decode(self, frame: bytes) -> np.ndarray:
        """Один кадр Opus → PCM int16. Пустой массив, если кадр не разобрался."""
        if not frame:
            return np.zeros(0, dtype=np.int16)

        try:
            packets = self._context.decode(self._av.Packet(frame))
        except Exception as exc:
            self.failed_frames += 1
            log.debug("кадр Opus не разобрался: %s", exc)
            return np.zeros(0, dtype=np.int16)

        chunks: list[np.ndarray] = []
        for decoded in packets:
            for resampled in self._resampler.resample(decoded):
                chunks.append(resampled.to_ndarray().reshape(-1))

        self.decoded_frames += 1
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks).astype(np.int16)

    def flush(self) -> np.ndarray:
        """Забирает то, что осталось в ресемплере."""
        chunks = [
            frame.to_ndarray().reshape(-1) for frame in self._resampler.resample(None)
        ]
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks).astype(np.int16)


def encode_opus_frames(
    samples: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE, bit_rate: int = 24000
) -> list[bytes]:
    """Кодирует PCM int16 в кадры Opus. Нужно тестам и замерам трафика."""
    import av

    encoder = av.CodecContext.create("libopus", "w")
    encoder.sample_rate = sample_rate
    encoder.format = "s16"
    encoder.layout = "mono"
    encoder.bit_rate = bit_rate

    frame = av.AudioFrame.from_ndarray(
        samples.astype(np.int16).reshape(1, -1), format="s16", layout="mono"
    )
    frame.sample_rate = sample_rate
    frame.pts = 0

    packets = list(encoder.encode(frame)) + list(encoder.encode(None))
    return [bytes(packet) for packet in packets]
