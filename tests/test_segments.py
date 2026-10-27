"""Склейка реплик и разговоров: FR-012, FR-013."""

from __future__ import annotations

import pytest
from uzel.segments import group_conversations, merge_utterances, shift
from uzel.vad import SpeechSegment


def seg(start: float, end: float) -> SpeechSegment:
    return SpeechSegment(start, end)


def test_merge_joins_segments_separated_by_short_pause():
    """FR-012: пауза короче 1,5 с — та же реплика."""
    result = merge_utterances([seg(0.0, 2.0), seg(3.0, 4.0)], gap_ms=1500)

    assert len(result) == 1
    assert result[0].start == 0.0
    assert result[0].end == 4.0
    assert len(result[0].parts) == 2


def test_merge_splits_on_long_pause():
    result = merge_utterances([seg(0.0, 2.0), seg(5.0, 6.0)], gap_ms=1500)

    assert len(result) == 2


def test_merge_boundary_pause_belongs_to_same_utterance():
    """Ровно на пороге — ещё одна реплика, а не две."""
    result = merge_utterances([seg(0.0, 2.0), seg(3.5, 4.0)], gap_ms=1500)

    assert len(result) == 1


def test_merge_sorts_unordered_input():
    result = merge_utterances([seg(5.0, 6.0), seg(0.0, 2.0)], gap_ms=1500)

    assert [round(u.start, 3) for u in result] == [0.0, 5.0]


def test_merge_handles_overlapping_segments():
    result = merge_utterances([seg(0.0, 3.0), seg(1.0, 2.0)], gap_ms=1500)

    assert len(result) == 1
    assert result[0].end == 3.0  # вложенный отрезок не укорачивает реплику


def test_speech_duration_counts_only_speech():
    result = merge_utterances([seg(0.0, 2.0), seg(3.0, 4.0)], gap_ms=1500)

    assert result[0].duration == pytest.approx(4.0)
    assert result[0].speech_duration == pytest.approx(3.0)


def test_merge_of_empty_input_is_empty():
    assert merge_utterances([], gap_ms=1500) == []


def test_group_joins_utterances_within_five_minutes():
    """FR-013: пауза меньше 5 минут — тот же разговор."""
    utterances = merge_utterances([seg(0.0, 10.0), seg(100.0, 110.0)], gap_ms=1500)

    result = group_conversations(utterances, gap_s=300.0)

    assert len(result) == 1
    assert result[0].duration == pytest.approx(110.0)


def test_group_splits_after_long_silence():
    utterances = merge_utterances([seg(0.0, 10.0), seg(700.0, 710.0)], gap_ms=1500)

    result = group_conversations(utterances, gap_s=300.0)

    assert len(result) == 2
    assert result[1].speech_duration == pytest.approx(10.0)


def test_group_of_empty_input_is_empty():
    assert group_conversations([], gap_s=300.0) == []


def test_shift_moves_segments_to_absolute_time():
    result = shift([seg(0.0, 1.0), seg(2.0, 3.0)], offset=1000.0)

    assert [(s.start, s.end) for s in result] == [(1000.0, 1001.0), (1002.0, 1003.0)]


def test_shift_lets_segments_from_two_chunks_form_one_utterance():
    """Речь на стыке принятых фрагментов не должна распадаться на две реплики."""
    first = shift([seg(9.0, 10.0)], offset=0.0)  # конец первого фрагмента
    second = shift([seg(0.0, 1.0)], offset=10.5)  # начало второго

    result = merge_utterances(first + second, gap_ms=1500)

    assert len(result) == 1
