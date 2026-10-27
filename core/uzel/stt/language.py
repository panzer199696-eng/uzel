"""Язык распознавания при ``UZEL_STT_LANGUAGE=auto`` (27.10.2026).

Whisper определяет язык по первым 30 с отрезка. На реплике в 1–3 с этого
мало: короткое русское «да, давай» уходит в украинский, болгарский или
сербский, и текст получается на чужом языке. Поэтому язык определяется не
по каждой реплике, а по прогону батча:

- реплика не короче ``MIN_DETECT_S`` — язык определяет whisper, и уверенное
  определение (вероятность от ``MIN_PROBABILITY``) становится языком прогона;
- короткая реплика получает язык прогона, а пока его нет — язык последних
  расшифровок из базы (``seed``); нет и его — решает whisper.

Воркер расшифровывает пачку длинными вперёд, чтобы язык прогона появился до
коротких. Явный код в настройке (``ru``, ``en``…) отключает всё это.
"""

from __future__ import annotations

AUTO = "auto"
# Короче — определению whisper не верим. 4 с — запас над типичной репликой
# «да, сейчас скину» (1–2 с) и всё ещё доля секунды от окна в 30 с.
MIN_DETECT_S = 4.0
MIN_PROBABILITY = 0.7


def whisper_language(setting: str | None) -> str | None:
    """Настройка → параметр ``language`` whisper: ``auto`` и пусто — ``None``."""
    code = (setting or "").strip().lower()
    return None if code in ("", AUTO) else code


def _code(language: str) -> str:
    return language.strip().lower()[:2]


class LanguagePicker:
    """Язык для каждой реплики прогона. Состояние живёт один батч."""

    def __init__(
        self,
        setting: str | None,
        *,
        seed: str | None = None,
        min_detect_s: float = MIN_DETECT_S,
        min_probability: float = MIN_PROBABILITY,
    ) -> None:
        self.fixed = whisper_language(setting)
        self.current = _code(seed) if seed else None
        self.min_detect_s = min_detect_s
        self.min_probability = min_probability

    @property
    def auto(self) -> bool:
        return self.fixed is None

    def choose(self, seconds: float) -> str | None:
        """Язык для реплики длиной ``seconds``; ``None`` — пусть определит whisper."""
        if self.fixed is not None:
            return self.fixed
        if seconds < self.min_detect_s:
            return self.current
        return None

    def observe(
        self, language: str | None, *, probability: float | None, seconds: float
    ) -> None:
        """Учитывает результат: уверенное определение на длинной реплике."""
        if self.fixed is not None or not language or seconds < self.min_detect_s:
            return
        if probability is not None and probability < self.min_probability:
            return
        self.current = _code(language)
