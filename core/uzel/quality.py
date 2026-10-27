"""Фильтр качества реплик (ТЗ 2026-08-24, пакет 2).

LLM-судья «стоит ли это помнить» здесь сознательно нет: он смещён против
обрывочной рабочей речи с числами и шифрами. Фильтр — только механические
правила B1–B8, проверяются строго по порядку от дешёвого к дорогому;
первое сработавшее правило и есть вердикт.

Пороги — гипотезы до калибровки на реальном материале: сними сводку
``uzel quality-report`` и подгоняй ``quality_*`` в настройках.

Правила общие для всех языков; от языка зависят только стоп-фразы B3 (каталоги
``locales``, все языки сразу) и ожидаемый язык B4. Пояснения к отказам —
на языке интерфейса.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .i18n import t
from .stt.postprocess import normalise, strip_hallucination

REASONS: tuple[str, ...] = (
    "empty", "too_short", "hallucination", "lang_noise",
    "too_sparse", "too_dense", "looped", "duplicate",
)


def reason_title(code: str, *, lang: str | None = None) -> str:
    """Название правила для человека; неизвестный код — как есть."""
    key = f"quality.reason.{code}"
    title = t(key, lang=lang)
    return code if title == key else title


@dataclass(frozen=True)
class Verdict:
    """Решение по одной реплике."""
    accepted: bool
    reason: str | None = None   # код правила при отказе: 'empty', 'too_short', ...
    detail: str | None = None   # подробность: совпавшая стоп-фраза, замеренная цифра


@dataclass(frozen=True)
class ConversationVerdict:
    """Решение по разговору целиком."""
    level: str                  # 'ok' | 'thin' | 'noise'
    reason: str | None = None
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.level == "ok"


@dataclass(frozen=True)
class Thresholds:
    """Пороги фильтра. Значения — гипотезы, калибруются на реальном материале."""
    min_chars: int = 12
    min_words: int = 3
    lang_min_chars: int = 100
    sparse_min_speech_s: float = 2.0
    min_chars_per_second: float = 3.0
    max_chars_per_second: float = 30.0
    loop_ngram_repeats: int = 4
    loop_min_words: int = 10
    unique_word_ratio: float = 0.35
    duplicate_gap_s: float = 30.0
    conversation_min_utterances: int = 2
    conversation_min_chars: int = 400
    conversation_min_speech_s: float = 60.0
    conversation_noise_ratio: float = 0.7
    conversation_noise_max_accepted: int = 10

    @classmethod
    def from_settings(cls, settings) -> "Thresholds":
        """Собирает пороги из полей ``quality_*`` объекта настроек."""
        return cls(
            min_chars=settings.quality_min_chars,
            min_words=settings.quality_min_words,
            lang_min_chars=settings.quality_lang_min_chars,
            sparse_min_speech_s=settings.quality_sparse_min_speech_s,
            min_chars_per_second=settings.quality_min_chars_per_second,
            max_chars_per_second=settings.quality_max_chars_per_second,
            loop_ngram_repeats=settings.quality_loop_ngram_repeats,
            loop_min_words=settings.quality_loop_min_words,
            unique_word_ratio=settings.quality_unique_word_ratio,
            duplicate_gap_s=settings.quality_duplicate_gap_s,
            conversation_min_utterances=(
                settings.quality_conversation_min_utterances
            ),
            conversation_min_chars=settings.quality_conversation_min_chars,
            conversation_min_speech_s=settings.quality_conversation_min_speech_s,
            conversation_noise_ratio=settings.quality_conversation_noise_ratio,
            conversation_noise_max_accepted=(
                settings.quality_conversation_noise_max_accepted
            ),
        )


def check_utterance(
    text: str | None,
    *,
    speech_seconds: float = 0.0,
    language: str | None = None,
    expected_language: str | None = "ru",
    previous_text: str | None = None,
    gap_seconds: float | None = None,
    thresholds: Thresholds | None = None,
) -> Verdict:
    """Проверяет реплику правилами B1–B8 по порядку, от дешёвого к дорогому.

    ``expected_language=None`` выключает B4: при ``UZEL_STT_LANGUAGE=auto``
    язык разговора бывает неизвестен, а «не знаю» — не повод выкидывать.
    """
    th = thresholds if thresholds is not None else Thresholds()

    # B1: пусто — None, пустая строка или одни пробелы.
    if text is None or not text.strip():
        return Verdict(False, "empty")

    stripped = text.strip()
    words = normalise(text).split()

    # B2: слишком коротко — «И» по символам, и по словам, а не «ИЛИ»:
    # короткая, но многословная реплика («да, согласен, подпишем») проходит.
    if len(stripped) < th.min_chars and len(words) < th.min_words:
        return Verdict(
            False,
            "too_short",
            t("quality.detail.too_short", chars=len(stripped), words=len(words)),
        )

    # B3: субтитровая галлюцинация — стоп-лист переиспользуется из STT.
    _, phrase = strip_hallucination(text)
    if phrase is not None:
        return Verdict(False, "hallucination", phrase)

    # B4: не тот язык. Язык неизвестен — не повод выкидывать.
    if (
        language is not None
        and expected_language is not None
        and _short_code(language) != _short_code(expected_language)
        and len(stripped) < th.lang_min_chars
    ):
        return Verdict(False, "lang_noise", f"language={language}")

    # B5/B6: плотность текста. При speech_seconds <= 0 делить не на что.
    if speech_seconds > 0:
        density = len(stripped) / speech_seconds
        if (
            speech_seconds >= th.sparse_min_speech_s
            and density < th.min_chars_per_second
        ):
            return Verdict(
                False, "too_sparse", t("quality.detail.density", density=f"{density:.1f}")
            )
        if density > th.max_chars_per_second:
            return Verdict(
                False, "too_dense", t("quality.detail.density", density=f"{density:.1f}")
            )

    # B7: зацикливание.
    loop_detail = _detect_loop(words, th)
    if loop_detail is not None:
        return Verdict(False, "looped", loop_detail)

    # B8: дубль подряд — тот же нормализованный текст вскоре после предыдущего.
    if (
        previous_text is not None
        and gap_seconds is not None
        and normalise(text) == normalise(previous_text)
        and gap_seconds < th.duplicate_gap_s
    ):
        return Verdict(
            False, "duplicate", t("quality.detail.duplicate", gap=f"{gap_seconds:.0f}")
        )

    return Verdict(True)


def _short_code(language: str) -> str:
    """Код языка до первых двух букв: «RU», «ru-RU» и «ru» — одно и то же."""
    return language.strip().lower()[:2]


def dominant_language(rows: Iterable[tuple[str | None, str | None]]) -> str | None:
    """Язык разговора по репликам ``(язык, текст)``: больше всего символов.

    Нужен B4 при ``UZEL_STT_LANGUAGE=auto``: ожидаемым языком становится язык
    самого разговора, а не настройка. Реплики без языка не голосуют.
    """
    weights: Counter[str] = Counter()
    for language, text in rows:
        if language and text:
            weights[_short_code(language)] += len(text.strip())
    if not weights:
        return None
    return weights.most_common(1)[0][0]


def _detect_loop(words: list[str], th: Thresholds) -> str | None:
    """Детектор зацикливания: два независимых признака.

    1. Блок из 1–3 слов (тот самый случай «3-грамма» из ТЗ), повторённый
       вплотную не менее ``loop_ngram_repeats`` раз: Whisper любит крутить
       «так точно так точно…» любой длины повтора.
    2. Слов больше ``loop_min_words``, а доля уникальных ниже
       ``unique_word_ratio`` — вырожденный словарь декодера.
    """
    total = len(words)

    for size in (1, 2, 3):
        for start in range(total - size):
            block = words[start : start + size]
            repeats = 1
            position = start + size
            while position + size <= total and words[position : position + size] == block:
                repeats += 1
                position += size
            if repeats >= th.loop_ngram_repeats:
                return t("quality.detail.loop", block=" ".join(block), repeats=repeats)

    if total > th.loop_min_words:
        ratio = len(set(words)) / total
        if ratio < th.unique_word_ratio:
            return t("quality.detail.unique", ratio=f"{ratio:.2f}", total=total)

    return None


class QualityFilter:
    """Держит предыдущую принятую реплику — B8 работает по потоку."""

    def __init__(
        self,
        thresholds: Thresholds | None = None,
        *,
        expected_language: str | None = "ru",
    ) -> None:
        self._thresholds = thresholds if thresholds is not None else Thresholds()
        self._expected_language = expected_language
        self._previous: str | None = None
        self._previous_ended_at: float | None = None

    def check(
        self, text: str | None, *, speech_seconds: float = 0.0,
        language: str | None = None, started_at: float | None = None,
        ended_at: float | None = None,
    ) -> Verdict:
        """Проверяет реплику; принятая становится «предыдущей» для следующей."""
        gap = (
            started_at - self._previous_ended_at
            if started_at is not None and self._previous_ended_at is not None
            else None
        )
        verdict = check_utterance(
            text,
            speech_seconds=speech_seconds,
            language=language,
            expected_language=self._expected_language,
            previous_text=self._previous,
            gap_seconds=gap,
            thresholds=self._thresholds,
        )
        # Отклонённая реплика не подменяет эталон для сравнения (B8).
        if verdict.accepted:
            self._previous = normalise(text) if text else None
            self._previous_ended_at = ended_at
        return verdict

    def reset(self) -> None:
        """Забыть предыдущую реплику (новый разговор)."""
        self._previous = None
        self._previous_ended_at = None


def check_conversation(
    *,
    accepted: int,
    rejected: int,
    accepted_chars: int,
    speech_seconds: float,
    thresholds: Thresholds | None = None,
) -> ConversationVerdict:
    """Ярлык разговора для отчёта: 'ok', 'thin' или 'noise'.

    Разговор может подходить под оба условия — проверяется сначала ``thin``,
    потом ``noise``, поэтому такой разговор получает ярлык ``thin``. На
    экспорт это не влияет: оба уровня одинаково не экспортируются, ярлык
    нужен только для отчёта.
    """
    th = thresholds if thresholds is not None else Thresholds()

    if accepted < th.conversation_min_utterances:
        return ConversationVerdict(
            "thin",
            "thin",
            t(
                "quality.detail.conv_utterances",
                accepted=accepted,
                minimum=th.conversation_min_utterances,
            ),
        )
    if accepted_chars < th.conversation_min_chars:
        return ConversationVerdict(
            "thin",
            "thin",
            t(
                "quality.detail.conv_chars",
                chars=accepted_chars,
                minimum=th.conversation_min_chars,
            ),
        )
    if speech_seconds < th.conversation_min_speech_s:
        return ConversationVerdict(
            "thin",
            "thin",
            t(
                "quality.detail.conv_speech",
                speech=f"{speech_seconds:.0f}",
                minimum=f"{th.conversation_min_speech_s:.0f}",
            ),
        )

    total = accepted + rejected
    noise_ratio = rejected / total if total else 0.0
    if (
        noise_ratio > th.conversation_noise_ratio
        and accepted < th.conversation_noise_max_accepted
    ):
        return ConversationVerdict(
            "noise", "noise", t("quality.detail.conv_noise", ratio=f"{noise_ratio:.2f}")
        )

    return ConversationVerdict("ok")
