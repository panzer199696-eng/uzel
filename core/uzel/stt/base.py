"""Общий контракт бэкендов расшифровки (FR-020…FR-022)."""

from __future__ import annotations

import abc
import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TranscriptionResult:
    """Результат расшифровки одного отрезка."""

    text: str
    language: str
    backend: str
    audio_seconds: float
    latency_s: float
    raw: dict[str, Any] = field(default_factory=dict)


class TranscriptionError(RuntimeError):
    """Бэкенд не смог расшифровать отрезок."""


class BackendUnavailable(TranscriptionError):
    """Бэкенд не настроен: нет ключа, нет модели, нет устройства."""


class AllBackendsFailed(TranscriptionError):
    """Ни один бэкенд не справился — отрезок остаётся в очереди (FR-022)."""


class LocalBackendFused(AllBackendsFailed):
    """Предохранитель: локальный бэкенд отказал N раз подряд, пачка остановлена.

    Это не повод уходить в облако: реплики остаются ``pending`` и ждут,
    пока локальный путь починят (FR-022).
    """


class CloudNotAllowed(TranscriptionError):
    """Облако запрещено для этого отрезка, а локальных бэкендов нет (FR-072)."""


def samples_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """float32 → WAV в памяти. Формат, который принимают облачные API."""
    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(
        buffer, samples.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16"
    )
    return buffer.getvalue()


class STTBackend(abc.ABC):
    """Бэкенд расшифровки.

    ``is_cloud`` — не косметика: по нему роутер жёстко отсекает облако для
    отрезков с меткой приватности (FR-072).
    """

    name: str = "base"
    is_cloud: bool = False

    @abc.abstractmethod
    def available(self) -> bool:
        """Готов ли бэкенд к работе (есть ключ, модель, устройство)."""

    @abc.abstractmethod
    def transcribe(
        self, samples: np.ndarray, sample_rate: int, language: str
    ) -> TranscriptionResult:
        """Расшифровывает отрезок. При неудаче поднимает ``TranscriptionError``."""
