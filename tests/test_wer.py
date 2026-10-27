"""Замер WER: критерий A1.

Сам измеритель должен быть проверен строже, чем то, что он меряет: ошибка здесь
даст красивую неправду о качестве распознавания.
"""

from __future__ import annotations

import pytest
from uzel.wer import (
    WerReport,
    collapse_numerals,
    compute_wer,
    normalise,
    render_report,
    words_to_number,
)

# --- нормализация ---


def test_case_and_punctuation_do_not_count_as_errors():
    assert normalise("Привет, мир!") == normalise("привет мир")


def test_yo_is_folded_to_ye():
    assert normalise("объём") == normalise("объем")


def test_dashes_are_unified():
    assert normalise("КС-2") == normalise("КС–2")


@pytest.mark.parametrize(
    "words,expected",
    [
        (["четыреста", "двенадцать"], 412),
        (["сто"], 100),
        (["двадцать", "один"], 21),
        (["две", "тысячи", "двадцать", "шесть"], 2026),
        (["тысяча"], 1000),
        (["ноль"], 0),
    ],
)
def test_numerals_are_assembled(words, expected):
    assert words_to_number(words) == expected


def test_words_to_number_rejects_non_numerals():
    assert words_to_number(["бетон"]) is None
    assert words_to_number([]) is None


def test_numerals_written_out_equal_digits():
    """«412» и «четыреста двенадцать» — одно слово, а не три ошибки."""
    assert normalise("объём четыреста двенадцать кубов") == normalise("объём 412 кубов")


def test_collapse_keeps_ordinary_words_intact():
    assert collapse_numerals(["бетон", "два", "куба"]) == ["бетон", "2", "куба"]


def test_numeral_run_is_collapsed_into_one_token():
    assert collapse_numerals(["сто", "двадцать", "три"]) == ["123"]


# --- подсчёт ---


def test_identical_texts_give_zero_wer():
    report = compute_wer(
        "акт освидетельствования скрытых работ", "акт освидетельствования скрытых работ"
    )

    assert report.wer == 0.0
    assert report.errors == 0
    assert report.accuracy == 1.0


def test_substitution_is_counted():
    report = compute_wer("акт освидетельствования", "акт свидетельствования")

    assert report.substitutions == 1
    assert report.wer == pytest.approx(0.5)
    assert report.examples == [("освидетельствования", "свидетельствования")]


def test_deletion_is_counted():
    report = compute_wer("один два три", "один три")

    assert report.deletions == 1
    assert report.wer == pytest.approx(1 / 3)


def test_insertion_is_counted():
    report = compute_wer("один три", "один два три")

    assert report.insertions == 1
    assert report.wer == pytest.approx(0.5)


def test_completely_wrong_hypothesis():
    report = compute_wer("один два", "три четыре")

    assert report.substitutions == 2
    assert report.wer == 1.0
    assert report.accuracy == 0.0


def test_empty_hypothesis_is_all_deletions():
    report = compute_wer("один два три", "")

    assert report.deletions == 3
    assert report.wer == 1.0


def test_empty_reference_does_not_divide_by_zero():
    report = compute_wer("", "что-то распозналось")

    assert report.reference_words == 0
    assert report.wer == 0.0


def test_examples_are_in_reading_order():
    report = compute_wer("один два три четыре", "один семь три восемь")

    assert report.examples == [("два", "семь"), ("четыре", "восемь")]


def test_examples_are_capped():
    reference = " ".join(f"слово{i}" for i in range(50))
    hypothesis = " ".join(f"другое{i}" for i in range(50))

    report = compute_wer(reference, hypothesis, max_examples=5)

    assert len(report.examples) == 5
    assert report.substitutions == 50  # сам счёт не урезан


# --- доменные слова ---


def test_domain_terms_are_counted_separately():
    report = compute_wer(
        "составлен АОСР по форме КС-2 согласно ГОСТ",
        "составлен АОСР по форме КС-2 согласно ГОСТ",
    )

    assert report.domain_total == 3
    assert report.domain_errors == 0
    assert report.domain_wer == 0.0


def test_domain_error_is_visible_even_when_overall_wer_is_low():
    """Общая цифра размазывает главное — ошибку в термине."""
    reference = (
        "составлен АОСР по устройству основания и это очень длинный текст дальше"
    )
    hypothesis = (
        "составлен ОСАГО по устройству основания и это очень длинный текст дальше"
    )

    report = compute_wer(reference, hypothesis)

    assert report.wer < 0.15  # общий порог пройден
    assert report.domain_wer == 1.0  # а термин потерян целиком


def test_missing_domain_word_counts_as_domain_error():
    report = compute_wer("нужен АОСР сегодня", "нужен сегодня")

    assert report.domain_errors == 1
    assert report.domain_total == 1


def test_domain_wer_is_zero_when_no_terms_present():
    report = compute_wer("обычный разговор ни о чём", "обычный разговор ни о чем")

    assert report.domain_total == 0
    assert report.domain_wer == 0.0


# --- порог и отчёт ---


def test_threshold_verdict():
    assert WerReport(100, 10, 0, 0).passed(0.15) is True
    assert WerReport(100, 15, 0, 0).passed(0.15) is True  # ровно на пороге
    assert WerReport(100, 16, 0, 0).passed(0.15) is False


def test_report_contains_verdict_and_numbers():
    reference = " ".join(f"слово{i}" for i in range(20))
    hypothesis = " ".join("иное" if i == 0 else f"слово{i}" for i in range(20))

    report = compute_wer(reference, hypothesis)
    text = render_report(
        report,
        title="эталон-01",
        backend="local",
        audio_seconds=1800,
        processing_seconds=600,
    )

    assert report.wer == pytest.approx(0.05)
    assert "✅ порог пройден" in text
    assert "5.0%" in text
    assert "×3.0" in text
    assert "| слово0 | иное |" in text  # в отчёте пары слов, не фразы


def test_report_marks_failure():
    text = render_report(
        WerReport(10, 5, 0, 0),
        title="плохой",
        backend="groq",
        audio_seconds=60,
        processing_seconds=30,
    )

    assert "НЕ пройден" in text


def test_report_warns_when_reference_has_no_domain_words():
    """Замер без рабочей лексики не проверяет главного — это надо сказать вслух."""
    report = compute_wer("просто разговор", "просто разговор")

    text = render_report(
        report,
        title="бытовой",
        backend="local",
        audio_seconds=60,
        processing_seconds=10,
    )

    assert "не встретилось ни одного доменного термина" in text


def test_report_survives_zero_processing_time():
    text = render_report(
        WerReport(10, 0, 0, 0),
        title="мгновенно",
        backend="local",
        audio_seconds=60,
        processing_seconds=0,
    )

    assert "×0.0" in text


# --- сквозной случай из жизни ---


def test_real_mistake_from_the_smoke_run():
    """То, что Groq выдал 24.08 на живой проверке."""
    reference = (
        "Составлен акт освидетельствования скрытых работ по устройству основания. "
        "Объём по форме КС-2 составил четыреста двенадцать кубометров."
    )
    hypothesis = (
        "Составлен акт о свидетельствовании скрытых работ по устройству основания. "
        "Объем по форме КС-2 составил 412 кубометров."
    )

    report = compute_wer(reference, hypothesis)

    # «412» против «четыреста двенадцать» и «объём» против «объем» ошибками
    # не считаются; реальная ошибка одна — разваленный термин
    assert report.reference_words == 15
    assert report.errors == 2  # «освидетельствования» → «о свидетельствовании»
    assert report.wer < 0.15  # общий порог формально пройден
    assert report.domain_errors == 1  # но термин потерян, и это видно отдельно
    assert report.domain_wer == pytest.approx(1 / 3)
