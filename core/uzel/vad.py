"""Обнаружение речи (Silero VAD на onnxruntime).

Требования: FR-010 (тишина не доходит до STT), FR-011 (фрагменты короче порога
отбрасываются).

Модель ``silero_vad.onnx`` (v5) имеет вход ``input`` (B, N), ``state`` (2, B, 128)
и скалярный ``sr``; на выходе — вероятность речи и новое состояние. Для 16 кГц
окно строго 512 сэмплов, для 8 кГц — 256.

Разбор потока разделён надвое: обращение к модели (`SileroVAD`) и конечный
автомат (`segments_from_probabilities`). Второй — чистая функция, поэтому логика
границ проверяется тестами без модели и без звука.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

WINDOW_SAMPLES = {16000: 512, 8000: 256}

# ⚠️ Модель v5 ждёт окно ВМЕСТЕ с хвостом предыдущего: 64 сэмпла контекста для
# 16 кГц (32 для 8 кГц). Вход объявлен как [None, None] и молча принимает голые
# 512 сэмплов, но тогда вероятность речи не поднимается выше 0,13 даже на
# отчётливой речи — VAD «не слышит» ничего. Проверено 24.08.2026.
CONTEXT_SAMPLES = {16000: 64, 8000: 32}

_STATE_SHAPE = (2, 1, 128)


@dataclass(frozen=True)
class SpeechSegment:
    """Отрезок речи в секундах от начала поданного звука."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def slice(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        lo = max(0, int(self.start * sample_rate))
        hi = min(len(samples), int(self.end * sample_rate))
        return samples[lo:hi]


class SileroVAD:
    """Обёртка над ONNX-моделью: поток float32 → вероятности речи по окнам."""

    def __init__(self, model_path: Path | str, sample_rate: int = 16000) -> None:
        import onnxruntime as ort

        if sample_rate not in WINDOW_SAMPLES:
            raise ValueError(f"Silero VAD не поддерживает {sample_rate} Гц")
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"модель VAD не найдена: {model_path}")

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.sample_rate = sample_rate
        self.window = WINDOW_SAMPLES[sample_rate]
        self.context = CONTEXT_SAMPLES[sample_rate]
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(self.context, dtype=np.float32)

    @property
    def window_seconds(self) -> float:
        return self.window / float(self.sample_rate)

    def reset(self) -> None:
        """Сбрасывает состояние: обязательно между независимыми записями."""
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(self.context, dtype=np.float32)

    def window_probabilities(self, samples: np.ndarray) -> np.ndarray:
        """Вероятность речи для каждого окна. Хвост дополняется тишиной."""
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            return np.zeros(0, dtype=np.float32)

        remainder = samples.size % self.window
        if remainder:
            samples = np.pad(samples, (0, self.window - remainder))

        sr = np.array(self.sample_rate, dtype=np.int64)
        probs = np.empty(samples.size // self.window, dtype=np.float32)
        for i in range(probs.size):
            window = samples[i * self.window : (i + 1) * self.window]
            frame = np.concatenate((self._context, window)).reshape(1, -1)
            output, self._state = self._session.run(
                None, {"input": frame, "state": self._state, "sr": sr}
            )
            probs[i] = float(output[0][0])
            self._context = window[-self.context :]
        return probs


def segments_from_probabilities(
    probabilities: np.ndarray,
    window_seconds: float,
    *,
    threshold: float = 0.5,
    min_speech_ms: int = 400,
    min_silence_ms: int = 400,
    speech_pad_ms: int = 300,
    total_seconds: float | None = None,
) -> list[SpeechSegment]:
    """Вероятности по окнам → границы речи.

    Гистерезис: вход в речь по ``threshold``, выход — по ``threshold - 0.15``,
    чтобы короткий провал внутри фразы не рвал её надвое.
    """
    probabilities = np.asarray(probabilities, dtype=np.float32)
    if probabilities.size == 0:
        return []

    limit = (
        probabilities.size * window_seconds if total_seconds is None else total_seconds
    )
    exit_threshold = max(threshold - 0.15, 0.01)
    min_speech_windows = max(1, round(min_speech_ms / 1000.0 / window_seconds))
    min_silence_windows = max(1, round(min_silence_ms / 1000.0 / window_seconds))
    pad = speech_pad_ms / 1000.0

    raw: list[tuple[int, int]] = []
    triggered = False
    start = 0
    silence_start: int | None = None

    for i, prob in enumerate(probabilities):
        if prob >= threshold:
            silence_start = None
            if not triggered:
                triggered = True
                start = i
        elif triggered and prob < exit_threshold:
            if silence_start is None:
                silence_start = i
            elif i - silence_start >= min_silence_windows:
                if silence_start - start >= min_speech_windows:
                    raw.append((start, silence_start))
                triggered = False
                silence_start = None

    if triggered and probabilities.size - start >= min_speech_windows:
        raw.append((start, probabilities.size))

    segments: list[SpeechSegment] = []
    for lo, hi in raw:
        begin = max(0.0, lo * window_seconds - pad)
        finish = min(limit, hi * window_seconds + pad)
        if segments and begin <= segments[-1].end:
            # после расширения полями отрезки слиплись — это одна фраза
            segments[-1] = SpeechSegment(segments[-1].start, finish)
        else:
            segments.append(SpeechSegment(begin, finish))
    return segments


def detect_speech(
    samples: np.ndarray,
    sample_rate: int,
    *,
    vad: SileroVAD,
    threshold: float = 0.5,
    min_speech_ms: int = 400,
    min_silence_ms: int = 400,
    speech_pad_ms: int = 300,
) -> list[SpeechSegment]:
    """Полный проход: звук → отрезки речи. Состояние модели сбрасывается."""
    vad.reset()
    probs = vad.window_probabilities(samples)
    return segments_from_probabilities(
        probs,
        vad.window_seconds,
        threshold=threshold,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
        total_seconds=len(samples) / float(sample_rate),
    )
