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
