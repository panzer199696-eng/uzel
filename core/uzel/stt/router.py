"""Роутер расшифровки: порядок бэкендов, фолбэк, запрет облака.

Требования: FR-020 (несколько бэкендов), FR-021 (локальный по умолчанию),
FR-022 (фолбэк с записью в журнал, отрезок не теряется), FR-072 (для отрезка
с меткой приватности облако запрещено жёстко, а не флагом по умолчанию).

Два предохранителя приватности:

1. отказ не-облачного бэкенда не «переливается» в облако — иначе сломанная
   видеокарта молча отправляет сутки рабочих разговоров наружу;
2. счётчик подряд идущих отказов останавливает пачку целиком, реплики
   остаются ``pending`` (FR-022), а не уходят в облако.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from .base import (
    AllBackendsFailed,
    CloudNotAllowed,
    LocalBackendFused,
    STTBackend,
    TranscriptionError,
    TranscriptionResult,
)
from .postprocess import strip_hallucination

log = logging.getLogger(__name__)


class STTRouter:
    """Пробует бэкенды по порядку, пока один не справится."""

    def __init__(
        self, backends: Sequence[STTBackend], *, failure_streak: int = 3
    ) -> None:
        if not backends:
            raise ValueError("роутеру нужен хотя бы один бэкенд")
        self._backends = list(backends)
        self._failure_streak = failure_streak
        self._failures = 0

    @property
    def backends(self) -> list[STTBackend]:
        return list(self._backends)

    @property
    def failures_in_a_row(self) -> int:
        """Сколько вызовов подряд не справились не-облачные бэкенды."""
        return self._failures

    def reset_fuse(self) -> None:
        """Снимает предохранитель: следующие вызовы снова пойдут к бэкендам."""
        self._failures = 0

    def unload(self) -> list[str]:
        """Выгружает модели бэкендов, у которых это умеется (см. local_backend).

        Воркер зовёт между расшифровкой и разбором: 6 ГБ VRAM не держат
        whisper и локальную LLM одновременно. Ошибки глотаются — выгрузка
        не имеет права ронять батч.
        """
        unloaded: list[str] = []
        for backend in self._backends:
            unload = getattr(backend, "unload", None)
            if unload is None:
                continue
            try:
                if unload():
                    unloaded.append(backend.name)
            except Exception:
                log.exception("выгрузка бэкенда %s не удалась", backend.name)
        return unloaded

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        language: str | None = "ru",
        allow_cloud: bool = False,
    ) -> TranscriptionResult:
        """Расшифровывает отрезок.

        ``allow_cloud`` по умолчанию ``False``: право отправлять звук наружу
        даётся только явным решением вызывающего кода. Молчаливый дефолт
        «да» превращает любую ошибку конфигурации в утечку рабочих разговоров
        в облако (FR-021, FR-072).

        Отказ не-облачного бэкенда (ненастроен, ошибка расшифровки, падение)
        исключает облако из перебора этого вызова — перебор продолжает только
        среди не-облачных бэкендов.
        """
        if self._failures >= self._failure_streak:
            raise LocalBackendFused(
                f"локальный бэкенд отказал {self._failures} раз подряд, "
                "пачка остановлена; реплики остаются в очереди (FR-022)"
            )

        candidates = [b for b in self._backends if allow_cloud or not b.is_cloud]
        if not candidates:
            raise CloudNotAllowed(
                "облако запрещено для этого отрезка, локальных бэкендов нет"
            )

        failures: list[str] = []
        local_failed = False
        for backend in candidates:
            # Отказ локального уже случился — облако отсечено до его вызова.
            if local_failed and backend.is_cloud:
                continue
            if not backend.available():
                failures.append(f"{backend.name}: не настроен")
                log.debug("бэкенд %s пропущен: не настроен", backend.name)
                if not backend.is_cloud:
                    local_failed = True
                continue
            try:
                result = backend.transcribe(samples, sample_rate, language)
            except TranscriptionError as exc:
                failures.append(f"{backend.name}: {exc}")
                log.warning("бэкенд %s не справился: %s", backend.name, exc)
                if not backend.is_cloud:
                    local_failed = True
                continue
            except Exception as exc:  # неожиданная ошибка не должна ронять конвейер
                failures.append(f"{backend.name}: непредвиденная ошибка {exc!r}")
                log.exception("бэкенд %s упал неожиданно", backend.name)
                if not backend.is_cloud:
                    local_failed = True
                continue

            if not backend.is_cloud:
                self._failures = 0

            if len(failures):
                log.info(
                    "расшифровано бэкендом %s после отказов: %s",
                    backend.name,
                    "; ".join(failures),
                )
            return self._strip_hallucination(result, backend.name)

        if local_failed:
            self._failures += 1
            if any(b.is_cloud for b in candidates):
                failures.append(
                    "облако отсечено из-за отказа локального бэкенда"
                )
        raise AllBackendsFailed("; ".join(failures) or "нет доступных бэкендов")

    @staticmethod
    def _strip_hallucination(
        result: TranscriptionResult, backend_name: str
    ) -> TranscriptionResult:
        """Вырезает субтитровые галлюцинации после любого бэкенда.

        Обнуление текста — не отказ бэкенда: счётчик предохранителя не растёт.
        """
        text, reason = strip_hallucination(result.text)
        if reason is None:
            return result
        log.info(
            "галлюцинация «%s» вырезана (бэкенд %s)", reason, backend_name
        )
        return replace(
            result, text="", raw={**result.raw, "hallucination": reason}
        )


def build_router(settings) -> STTRouter:
    """Собирает роутер по настройкам (порядок из ``stt_backends``)."""
    from .groq_backend import GroqBackend
    from .local_backend import LocalWhisperBackend

    factories = {
        "local": lambda: LocalWhisperBackend(
            model_size=settings.local_model,
            device=settings.local_device,
            compute_type=settings.local_compute_type,
            beam_size=settings.local_beam_size,
            no_speech_threshold=settings.local_no_speech_threshold,
            logprob_threshold=settings.local_logprob_threshold,
            compression_threshold=settings.local_compression_threshold,
            model_dir=settings.local_model_dir,
        ),
        "groq": lambda: GroqBackend(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            timeout_s=settings.groq_timeout_s,
        ),
    }

    backends: list[STTBackend] = []
    for name in settings.stt_backends:
        factory = factories.get(name)
        if factory is None:
            log.error("неизвестный бэкенд STT в настройках: %s", name)
            continue
        backends.append(factory())
    return STTRouter(backends, failure_streak=settings.local_failure_streak)
