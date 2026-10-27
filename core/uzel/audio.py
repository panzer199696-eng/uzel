"""Утилиты по звуку: PCM ⇄ float32, чтение и запись WAV."""

from __future__ import annotations

from pathlib import Path

import numpy as np

PCM_ENCODING = "pcm_s16le"
_INT16_SCALE = 32768.0


def pcm16_to_float32(payload: bytes) -> np.ndarray:
    """Сырой little-endian int16 → float32 в диапазоне [-1, 1]."""
    if len(payload) % 2:
        payload = payload[:-1]  # нечётный хвост — обрезанный сэмпл, отбрасываем
    if not payload:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(payload, dtype="<i2").astype(np.float32)
    return samples / _INT16_SCALE


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * (_INT16_SCALE - 1)).astype("<i2").tobytes()


def read_wav(path: Path | str, expected_sr: int | None = None) -> tuple[np.ndarray, int]:
    """WAV → (моно float32, частота). Многоканальный сводится усреднением."""
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    if expected_sr is not None and sr != expected_sr:
        raise ValueError(f"ожидалась частота {expected_sr} Гц, в файле {sr} Гц")
    return mono, sr


def write_wav(path: Path | str, samples: np.ndarray, sample_rate: int) -> None:
    import soundfile as sf

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples.astype(np.float32), sample_rate, subtype="PCM_16")


def duration_s(samples: np.ndarray, sample_rate: int) -> float:
    return len(samples) / float(sample_rate)
