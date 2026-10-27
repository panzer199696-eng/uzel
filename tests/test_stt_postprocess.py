"""Стоп-лист субтитровых галлюцинаций (FR-023, A4)."""

from __future__ import annotations

from uzel.stt.postprocess import STOP_PHRASES, STOP_PREFIXES, normalise, strip_hallucination


def test_normalise_lowercases_strips_punct_and_collapses_spaces():
    assert normalise("  Привет,   мир!  ") == "привет мир"


def test_normalise_unifies_yo_with_e():
    assert normalise("Ёлка и Жёлтый флаг") == "елка и желтый флаг"


def test_full_phrase_with_punctuation_is_cut():
    assert strip_hallucination("Продолжение следует...") == ("", "продолжение следует")


def test_full_phrase_case_insensitive_without_dot_is_cut():
    assert strip_hallucination("ПРОДОЛЖЕНИЕ СЛЕДУЕТ") == ("", "продолжение следует")


def test_phrase_with_yo_is_matched_after_normalisation():
    assert strip_hallucination("Продолжение слёдует") == ("", "продолжение следует")


def test_prefix_with_author_name_is_cut():
    assert strip_hallucination("Субтитры сделал DimaTorzok") == ("", "субтитры сделал")


def test_meeting_thanks_are_kept():
    """«Спасибо за внимание» — нормальный конец совещания, не галлюцинация."""
    text = "Спасибо за внимание, до встречи"
    assert strip_hallucination(text) == (text, None)


def test_goodbye_is_kept():
    text = "Всем пока"
    assert strip_hallucination(text) == (text, None)


def test_long_utterance_with_embedded_phrase_is_untouched():
    """Фраза занимает меньше 80 % реплики — резать настоящую речь нельзя."""
    text = (
        "Мы закрыли заявку ООО «Ромашка» и обсудили поставку бетона на объект, "
        "спасибо за просмотр остального не нужно, встретимся завтра у прораба"
    )
    result, reason = strip_hallucination(text)

    assert reason is None
    assert result == text


def test_empty_and_blank_input_returns_empty_without_reason():
    assert strip_hallucination("") == ("", None)
    assert strip_hallucination("   \n\t ") == ("", None)


def test_stop_lists_contents():
    """Стоп-листы содержат именно отобранные фразы и не разрастаются."""
    assert set(STOP_PHRASES) == {
        "продолжение следует",
        "подписывайтесь на канал",
        "спасибо за просмотр",
        "ставьте лайки",
    }
    assert set(STOP_PREFIXES) == {
        "субтитры сделал",
        "субтитры делал",
        "редактор субтитров",
        "субтитры создавал",
    }


# --- английские галлюцинации (локализация, 27.10.2026) ---


def test_english_subtitle_phrases_are_cut_whatever_the_interface_language():
    """Стоп-листы всех языков работают всегда: язык интерфейса тут ни при чём."""
    assert strip_hallucination("Thank you for watching!") == ("", "thank you for watching")
    assert strip_hallucination("Please subscribe.") == ("", "please subscribe")
    assert strip_hallucination("Like and subscribe!") == ("", "like and subscribe")


def test_english_subtitle_credits_are_cut_by_prefix():
    text = "Subtitles by the Amara.org community"
    assert strip_hallucination(text) == ("", "subtitles by")


def test_english_working_speech_with_phrase_inside_is_untouched():
    text = (
        "We closed the purchase order for the concrete and, thanks for watching "
        "the site yesterday, the delivery is booked for Friday morning"
    )
    assert strip_hallucination(text) == (text, None)


def test_stop_lists_by_language_cover_ru_and_en():
    from uzel.stt.postprocess import stop_lists

    lists = stop_lists()
    assert set(lists["ru"][0]) == set(STOP_PHRASES)
    assert "thank you for watching" in lists["en"][0]
    assert "subtitles by" in lists["en"][1]
