"""Фильтр качества реплик: правила B1–B8 и уровень разговора (ТЗ 2026-08-24, пакет 2)."""

from __future__ import annotations

from uzel.config import Settings
from uzel.quality import (
    QualityFilter,
    Thresholds,
    check_conversation,
    check_utterance,
)

# Здоровая реплика: проходит все правила, на ней проверяем «не срабатывает».
HEALTHY = "приняли заявку по фундаменту, отгрузка арматуры в четверг"


def test_b1_empty_rejects_none_blank_and_spaces():
    for text in (None, "", "   "):
        verdict = check_utterance(text)
        assert not verdict.accepted
        assert verdict.reason == "empty"


def test_b2_too_short_rejects_aga():
    verdict = check_utterance("ага")

    assert not verdict.accepted
    assert verdict.reason == "too_short"


def test_b2_passes_short_but_wordy_reply():
    """«И», а не «ИЛИ»: символов мало, но слов достаточно — реплика проходит."""
    verdict = check_utterance("да, согласен, подпишем")

    assert verdict.accepted


def test_b2_too_short_rejects_single_punctuated_word():
    verdict = check_utterance("Хорошо.")

    assert not verdict.accepted
    assert verdict.reason == "too_short"


def test_b3_hallucination_rejects_subtitle_phrase():
    verdict = check_utterance("Продолжение следует...")

    assert not verdict.accepted
    assert verdict.reason == "hallucination"
    assert verdict.detail is not None and "продолжение следует" in verdict.detail


def test_b3_hallucination_keeps_working_replica_with_phrase_inside():
    """Фраза внутри длинной рабочей реплики — не галлюцинация."""
    text = (
        "по объекту на Ленина продолжение следует завтра после обеда, "
        "шифр сметы четырнадцать"
    )
    verdict = check_utterance(text)

    assert verdict.accepted


def test_b4_lang_noise_rejects_short_foreign_speech():
    verdict = check_utterance("hello world check", language="en")

    assert not verdict.accepted
    assert verdict.reason == "lang_noise"


def test_b4_lang_noise_passes_long_foreign_text():
    text = (
        "the structural engineer approved the foundation drawings for axis "
        "fourteen and requested additional reinforcement details before "
        "friday meeting"
    )
    assert len(text.strip()) >= 100  # длинный иностранный текст — рабочий

    verdict = check_utterance(text, language="en")

    assert verdict.accepted


def test_b4_lang_noise_never_fires_without_language():
    verdict = check_utterance(HEALTHY, language=None)

    assert verdict.accepted


def test_b5_too_sparse_rejects_thin_speech():
    verdict = check_utterance("смотрим заявку шесть", speech_seconds=30.0)

    assert not verdict.accepted
    assert verdict.reason == "too_sparse"
    assert verdict.detail is not None and "симв/с" in verdict.detail


def test_b5_too_sparse_does_not_fire_without_speech_seconds():
    verdict = check_utterance(HEALTHY, speech_seconds=0.0)

    assert verdict.accepted


def test_b6_too_dense_rejects_impossible_density():
    text = (
        "штукатурка стен по сетке пять миллиметров слой проверяем уровнем "
        "захватки седьмая и восьмая ось марка бетона в тридцать пять защита "
        "закладных от коррозии акты скрытых работ подписать завтра до обеда"
    )
    verdict = check_utterance(text, speech_seconds=1.0)

    assert not verdict.accepted
    assert verdict.reason == "too_dense"
    assert verdict.detail is not None and "симв/с" in verdict.detail


def test_b7_looped_rejects_repeated_phrase():
    """«так точно» повторён четыре раза подряд — классика зацикливания."""
    verdict = check_utterance("так точно так точно так точно так точно")

    assert not verdict.accepted
    assert verdict.reason == "looped"
    assert verdict.detail is not None and "так точно" in verdict.detail


def test_b7_looped_rejects_degraded_vocabulary():
    """20 слов, из них уникальных 5 — вырожденный словарь декодера."""
    text = "один два три четыре пять " * 4
    verdict = check_utterance(text.strip())

    assert not verdict.accepted
    assert verdict.reason == "looped"
    assert verdict.detail is not None and "уникальных" in verdict.detail


def test_b7_looped_passes_normal_speech():
    verdict = check_utterance(HEALTHY)

    assert verdict.accepted


def test_b7_looped_ignores_short_repeats():
    """Повтор всего три раза и слов меньше порога — признак доли не активен."""
    verdict = check_utterance("да да да")

    assert verdict.accepted


def test_b8_duplicate_rejects_quick_repeat():
    previous = "заявка по фундаменту подписана"

    verdict = check_utterance("заявка по фундаменту подписана", previous_text=previous,
                              gap_seconds=5.0)

    assert not verdict.accepted
    assert verdict.reason == "duplicate"


def test_b8_duplicate_passes_slow_repeat():
    verdict = check_utterance("заявка по фундаменту подписана",
                              previous_text="заявка по фундаменту подписана",
                              gap_seconds=120.0)

    assert verdict.accepted


def test_b8_duplicate_needs_previous_and_gap():
    verdict = check_utterance("заявка по фундаменту подписана",
                              previous_text="заявка по фундаменту подписана",
                              gap_seconds=None)

    assert verdict.accepted


def test_quality_filter_rejected_is_not_kept_as_previous():
    """Принято A, отклонено B — эталоном для B8 остаётся A, а не B."""
    gate = QualityFilter()

    first = gate.check("приняли заявку по фундаменту",
                       started_at=100.0, ended_at=110.0)
    rejected = gate.check("ага", started_at=115.0, ended_at=116.0)
    repeat = gate.check("приняли заявку по фундаменту",
                        started_at=121.0, ended_at=131.0)

    assert first.accepted
    assert not rejected.accepted and rejected.reason == "too_short"
    assert not repeat.accepted and repeat.reason == "duplicate"


def test_quality_filter_reset_forgets_previous():
    gate = QualityFilter()

    assert gate.check("приняли заявку по фундаменту",
                      started_at=100.0, ended_at=110.0).accepted
    gate.reset()

    assert gate.check("приняли заявку по фундаменту",
                      started_at=115.0, ended_at=125.0).accepted


def test_quality_filter_without_times_does_not_guess_gap():
    gate = QualityFilter()

    assert gate.check("приняли заявку по фундаменту").accepted
    assert gate.check("приняли заявку по фундаменту").accepted


def test_conversation_thin_by_min_utterances():
    verdict = check_conversation(accepted=1, rejected=0,
                                 accepted_chars=500, speech_seconds=120.0)

    assert verdict.level == "thin"
    assert not verdict.accepted


def test_conversation_thin_by_min_chars():
    verdict = check_conversation(accepted=5, rejected=0,
                                 accepted_chars=300, speech_seconds=120.0)

    assert verdict.level == "thin"


def test_conversation_thin_by_min_speech_seconds():
    verdict = check_conversation(accepted=5, rejected=0,
                                 accepted_chars=500, speech_seconds=30.0)

    assert verdict.level == "thin"


def test_conversation_noise_when_rejections_dominate():
    """7 отклонённых на 2 принятых: доля 0.78 > 0.7 при малом числе принятых."""
    verdict = check_conversation(accepted=2, rejected=7,
                                 accepted_chars=500, speech_seconds=70.0)

    assert verdict.level == "noise"


def test_conversation_thin_takes_precedence_over_noise():
    """Разговор под оба ярлыка получает thin — порядок проверки из ТЗ."""
    verdict = check_conversation(accepted=1, rejected=9,
                                 accepted_chars=500, speech_seconds=70.0)

    assert verdict.level == "thin"


def test_conversation_noise_not_fired_when_accepted_reaches_max():
    verdict = check_conversation(accepted=10, rejected=90,
                                 accepted_chars=2000, speech_seconds=300.0)

    assert verdict.level == "ok"


def test_conversation_ok_on_healthy_conversation():
    verdict = check_conversation(accepted=10, rejected=2,
                                 accepted_chars=2000, speech_seconds=300.0)

    assert verdict.level == "ok"
    assert verdict.accepted


def test_thresholds_from_settings_reads_quality_fields():
    thresholds = Thresholds.from_settings(Settings(quality_min_chars=40))

    assert thresholds.min_chars == 40


def test_thresholds_from_settings_matches_defaults():
    assert Thresholds.from_settings(Settings()) == Thresholds()


# --- языки (локализация, 27.10.2026) ---


def test_b3_hallucination_rejects_english_subtitle_phrase():
    verdict = check_utterance("Thank you for watching.", expected_language="en")

    assert verdict.reason == "hallucination"


def test_b4_lang_noise_is_off_without_expected_language():
    """UZEL_STT_LANGUAGE=auto и язык разговора неизвестен — B4 не судит."""
    verdict = check_utterance("hello world check", language="en", expected_language=None)

    assert verdict.accepted


def test_b4_lang_noise_works_for_english_expected():
    verdict = check_utterance("привет как дела", language="ru", expected_language="en")

    assert verdict.reason == "lang_noise"


def test_details_follow_interface_language():
    from uzel import i18n

    i18n.set_language("en")
    verdict = check_utterance("so so so so so so", expected_language="en")
    assert verdict.reason == "looped"
    assert "repeated 6 times in a row" in verdict.detail

    sparse = check_utterance("we check order six", speech_seconds=30.0, expected_language="en")
    assert sparse.reason == "too_sparse"
    assert "chars/s" in sparse.detail


def test_reason_titles_by_language():
    from uzel.quality import reason_title

    assert reason_title("looped", lang="ru") == "зацикливание"
    assert reason_title("looped", lang="en") == "looped"
    assert reason_title("непонятное", lang="en") == "непонятное"


def test_dominant_language_weighs_by_text_length():
    from uzel.quality import dominant_language

    rows = [("en", "a long english utterance about the concrete delivery"), ("ru", "да"), (None, "x")]
    assert dominant_language(rows) == "en"
    assert dominant_language([]) is None
