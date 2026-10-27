"""Локальный бэкенд: faster-whisper (FR-020, FR-021, NFR-001, NFR-002).

Бесплатный путь без интернета. Импорт ленивый: без обращения к бэкенду ни
faster-whisper, ни CUDA не трогаются, поэтому тесты и приём аудио работают на
машине без GPU.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import sysconfig
import threading
import time
from pathlib import Path

import numpy as np

from .base import (
    BackendUnavailable,
    STTBackend,
    TranscriptionError,
    TranscriptionResult,
)

log = logging.getLogger(__name__)


def _site_roots() -> list[Path]:
    """Корни site-packages текущего интерпретатора, без дубликатов.

    Путь ищется от интерпретатора, а не хардкодом: окружение может быть
    виртуальным, переносимым, лежать где угодно.
    """
    roots = [
        Path(sysconfig.get_paths()["purelib"]),
        Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _prepare_cuda_path() -> list[str]:
    """Добавляет каталоги CUDA-библиотек колёс nvidia-* в PATH процесса.

    ``ctranslate2`` ищет ``cublas64_12.dll`` и ``cudnn*.dll`` по PATH;
    ``os.add_dll_directory()`` на это не влияет. Возвращает список
    добавленных каталогов. Идемпотентна: повторный вызов ничего не дублирует.
    """
    added: list[str] = []
    known = {part for part in os.environ.get("PATH", "").split(os.pathsep) if part}
    for root in _site_roots():
        if not root.is_dir():
            continue
        for bin_dir in sorted(root.glob("nvidia/*/bin")):
            path = str(bin_dir)
            if not bin_dir.is_dir() or path in known:
                continue
            current = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{path}{os.pathsep}{current}" if current else path
            known.add(path)
            added.append(path)
    if added:
        log.debug("каталоги CUDA добавлены в PATH: %s", added)
    return added


# Один раз при импорте: дальше любой импорт faster_whisper найдёт CUDA-библиотеки.
_prepare_cuda_path()


def resolve_device(requested: str = "auto") -> str:
    """``auto`` → cuda, если видеокарта доступна ctranslate2, иначе cpu."""
    if requested != "auto":
        return requested
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception as exc:  # ctranslate2 не собран с CUDA либо нет драйвера
        log.debug("CUDA недоступна: %s", exc)
    return "cpu"


class LocalWhisperBackend(STTBackend):
    name = "local"
    is_cloud = False

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "int8_float16",
        *,
        beam_size: int = 5,
        no_speech_threshold: float = 0.6,
        logprob_threshold: float = -1.0,
        compression_threshold: float = 2.4,
        model_dir: Path | None = None,
    ) -> None:
        self._model_size = model_size
        self._requested_device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._no_speech_threshold = no_speech_threshold
        self._logprob_threshold = logprob_threshold
        self._compression_threshold = compression_threshold
        self._model_dir = model_dir
        self._model = None
        self._lock = threading.Lock()
        self._probe_ok: bool | None = None
        self._probe_error: str | None = None

    def available(self) -> bool:
        """Честная проверка: пробный прогон энкодера, а не импортируемость.

        Проверять только ``import faster_whisper`` недостаточно: DLL CUDA
        падает позже, уже в расшифровке — и тогда роутер молча уводит каждую
        реплику в облако. Результат кэшируется на всё время процесса.
        """
        if self._probe_ok is not None:
            return self._probe_ok
        try:
            model = self._ensure_model()
            audio = np.zeros(16000, dtype=np.float32)  # секунда тишины
            features = model.feature_extractor(audio)
            features = features[:, : model.feature_extractor.nb_max_frames]
            model.encode(features)
        except Exception as exc:
            self._probe_ok = False
            self._probe_error = str(exc) or repr(exc)
            log.warning("локальный бэкенд не прошёл пробный прогон: %s", exc)
            return False
        self._probe_ok = True
        return True

    def reset_probe(self) -> None:
        """Сбрасывает кэш пробного прогона (нужен тестам и смене окружения)."""
        self._probe_ok = None
        self._probe_error = None

    def unload(self) -> bool:
        """Отпускает модель и освобождает видеопамять.

        Ночной батч работает в два такта: 6 ГБ VRAM не держат whisper и
        локальную LLM одновременно. ctranslate2 освобождает память при
        уничтожении объекта, поэтому важно и обнулить ссылку, и позвать сборщик.
        """
        with self._lock:
            if self._model is None:
                return False
            self._model = None
        gc.collect()
        log.info("faster-whisper выгружен из видеопамяти")
        # Пробный прогон available() не сбрасываем: модель на диске та же.
        return True

    @property
    def probe_error(self) -> str | None:
        """Текст последней неудачи пробного прогона — в журнал роутера."""
        return self._probe_error

    @property
    def device(self) -> str:
        return resolve_device(self._requested_device)

    def _model_ref(self) -> str:
        """Ссылка для ``WhisperModel``: локальная папка, если есть, иначе имя.

        Папка ``model_dir / model_size`` избавляет от выхода в сеть при
        загрузке модели.
        """
        if self._model_dir is not None:
            candidate = Path(self._model_dir) / self._model_size
            if candidate.exists():
                return str(candidate)
        return self._model_size

    def _ensure_model(self):
        """Модель грузится один раз и переиспользуется: загрузка дорогая."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                # PATH могли подчистить после импорта модуля — обновляем.
                _prepare_cuda_path()
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise BackendUnavailable("faster-whisper не установлен") from exc

                device = self.device
                model_ref = self._model_ref()
                log.info(
                    "загрузка faster-whisper %s на %s (%s)",
                    model_ref,
                    device,
                    self._compute_type,
                )
                try:
                    self._model = WhisperModel(
                        model_ref, device=device, compute_type=self._compute_type
                    )
                except Exception as exc:
                    raise BackendUnavailable(
                        f"не удалось загрузить модель {model_ref} на {device}: {exc}"
                    ) from exc
        return self._model

    def transcribe(
        self, samples: np.ndarray, sample_rate: int, language: str | None
    ) -> TranscriptionResult:
        """``language=None`` — язык определяет whisper (UZEL_STT_LANGUAGE=auto)."""
        if sample_rate != 16000:
            raise TranscriptionError(
                f"faster-whisper ждёт 16 кГц, получено {sample_rate}"
            )

        model = self._ensure_model()
        audio_seconds = len(samples) / float(sample_rate)
        started = time.monotonic()

        try:
            # VAD у нас свой и уже отработал — фильтр модели выключен намеренно.
            # initial_prompt не задаётся: модель отдаёт его эхом на тишине.
            segments, info = model.transcribe(
                samples.astype(np.float32),
                beam_size=self._beam_size,
                condition_on_previous_text=False,  # главный источник каскадных повторов
                word_timestamps=True,
                hallucination_silence_threshold=2.0,  # работает только с word_timestamps
                no_speech_threshold=self._no_speech_threshold,
                log_prob_threshold=self._logprob_threshold,
                compression_ratio_threshold=self._compression_threshold,
                vad_filter=False,
                language=language,
            )
            text, metrics = self._collect_segments(segments)
        except Exception as exc:
            raise TranscriptionError(f"local: {exc}") from exc

        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None) or language or "",
            backend=self.name,
            audio_seconds=audio_seconds,
            latency_s=time.monotonic() - started,
            raw={
                "model": self._model_size,
                "device": self.device,
                "compute_type": self._compute_type,
                "beam_size": self._beam_size,
                # Уверенность определения языка: по ней воркер решает, можно ли
                # одолжить этот язык коротким репликам (stt/language.py).
                "language_probability": getattr(info, "language_probability", None),
                **metrics,
            },
        )

    def _collect_segments(self, segments) -> tuple[str, dict]:
        """Собирает текст из сегментов, отбрасывая тишину и галлюцинации.

        Метрики каждого сегмента суммируются: без них пороги против
        галлюцинаций невозможно откалибровать, а журнал FR-022 нечем наполнить.
        """
        pieces: list[str] = []
        dropped = {"no_speech": 0, "low_logprob": 0, "repetition": 0, "empty": 0}
        total = 0
        sum_no_speech = sum_logprob = sum_ratio = 0.0

        for segment in segments:
            total += 1
            no_speech = float(segment.no_speech_prob)
            logprob = float(segment.avg_logprob)
            ratio = float(segment.compression_ratio)
            sum_no_speech += no_speech
            sum_logprob += logprob
            sum_ratio += ratio

            reason: str | None = None
            if no_speech > self._no_speech_threshold:
                reason = "no_speech"
            elif logprob < self._logprob_threshold:
                reason = "low_logprob"
            elif ratio > self._compression_threshold:
                reason = "repetition"
            elif not segment.text.strip():
                reason = "empty"

            if reason is not None:
                dropped[reason] += 1
                log.debug(
                    "сегмент %.2f–%.2f отброшен (%s): %r",
                    float(segment.start),
                    float(segment.end),
                    reason,
                    segment.text,
                )
                continue
            pieces.append(segment.text)

        kept = len(pieces)
        metrics = {
            "segments_total": total,
            "segments_kept": kept,
            "segments_dropped": total - kept,
            "dropped_reasons": {
                name: count for name, count in dropped.items() if count
            },
            "avg_no_speech_prob": round(sum_no_speech / total, 4) if total else None,
            "avg_logprob": round(sum_logprob / total, 4) if total else None,
            "avg_compression_ratio": round(sum_ratio / total, 4) if total else None,
        }
        return "".join(pieces).strip(), metrics
