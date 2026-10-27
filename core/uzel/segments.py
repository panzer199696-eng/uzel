"""Склейка речи в реплики и разговоры.

Требования: FR-012 (пауза < 1,5 с — одна реплика), FR-013 (пауза < 5 мин —
один разговор).

Время везде — секунды. Внутри одной записи это смещение от её начала, после
`shift` — абсолютное время приёма, поэтому склейка работает и через границы
принятых фрагментов.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .vad import SpeechSegment


@dataclass
class Utterance:
    """Непрерывная реплика: несколько отрезков речи с паузами короче порога."""

    start: float
    end: float
    parts: list[SpeechSegment] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def speech_duration(self) -> float:
        """Чистая длительность речи без внутренних пауз."""
        return sum(part.duration for part in self.parts)


@dataclass
class Conversation:
    """Разговор: реплики, разделённые паузами короче ``conversation_gap_s``."""

    start: float
    end: float
    utterances: list[Utterance] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def speech_duration(self) -> float:
        return sum(item.speech_duration for item in self.utterances)


def shift(segments: Iterable[SpeechSegment], offset: float) -> list[SpeechSegment]:
    """Сдвигает отрезки на ``offset`` секунд (перевод в абсолютное время)."""
    return [SpeechSegment(s.start + offset, s.end + offset) for s in segments]


def merge_utterances(
    segments: Sequence[SpeechSegment], gap_ms: int = 1500
) -> list[Utterance]:
    """Отрезки речи → реплики (FR-012). Вход не обязан быть отсортирован."""
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    if not ordered:
        return []

    gap = gap_ms / 1000.0
    result: list[Utterance] = []
    current = Utterance(ordered[0].start, ordered[0].end, [ordered[0]])

    for segment in ordered[1:]:
        if segment.start - current.end <= gap:
            current.parts.append(segment)
            current.end = max(current.end, segment.end)
        else:
            result.append(current)
            current = Utterance(segment.start, segment.end, [segment])

    result.append(current)
    return result


def group_conversations(
    utterances: Sequence[Utterance], gap_s: float = 300.0
) -> list[Conversation]:
    """Реплики → разговоры (FR-013)."""
    ordered = sorted(utterances, key=lambda u: (u.start, u.end))
    if not ordered:
        return []

    result: list[Conversation] = []
    current = Conversation(ordered[0].start, ordered[0].end, [ordered[0]])

    for item in ordered[1:]:
        if item.start - current.end <= gap_s:
            current.utterances.append(item)
            current.end = max(current.end, item.end)
        else:
            result.append(current)
            current = Conversation(item.start, item.end, [item])

    result.append(current)
    return result


def plan_stitches(
    rows: Sequence, *, gap_s: float = 1.5, max_s: float = 30.0
) -> list[list]:
    """Соседние реплики → группы на склейку (FR-012 через границы фрагментов).

    Телефон шлёт звук порциями по 5 с, и дешёвый проход режет речь на их
    стыках: фраза доезжает до расшифровки обрывками. Группа — реплики одного
    ``uid`` с одинаковой приватностью, паузы между которыми не длиннее
    ``gap_s``, а вся группа укладывается в ``max_s`` (окно whisper).
    Приватное с обычным не смешивается никогда. Возвращаются только группы
    из двух и более реплик — одиночки трогать незачем.

    От строки нужны ``uid``, ``started_at``, ``ended_at``, ``is_private``.
    """
    ordered = sorted(rows, key=lambda r: (r.uid, r.started_at, r.ended_at))
    groups: list[list] = []
    current: list = []
    for row in ordered:
        if current:
            head, last_end = current[0], max(r.ended_at for r in current)
            if (
                row.uid == head.uid
                and row.is_private == head.is_private
                and row.started_at - last_end <= gap_s
                and max(row.ended_at, last_end) - head.started_at <= max_s
            ):
                current.append(row)
                continue
            if len(current) > 1:
                groups.append(current)
        current = [row]
    if len(current) > 1:
        groups.append(current)
    return groups
