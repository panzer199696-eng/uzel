"""Модели запросов приложения Omi (P8).

Приложение шлёт заметно больше полей, чем нужно УЗЛУ: ``extra="ignore"``
не даёт лишним полям ронять разбор, а ``populate_by_name`` позволяет
принимать и camelCase из документации Omi, и snake_case.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

_НАСТРОЙКА_МОДЕЛИ = ConfigDict(extra="ignore", populate_by_name=True)


class TranscriptSegment(BaseModel):
    """Сегмент транскрипта; времена — секунды от начала сессии."""

    model_config = _НАСТРОЙКА_МОДЕЛИ

    text: str = ""
    speaker: str | None = None
    speaker_id: int | None = Field(None, alias="speakerId")
    speaker_name: str | None = Field(None, alias="speakerName")
    is_user: bool = Field(False, alias="isUser")
    start: float = 0.0
    end: float = 0.0


class TranscriptPayload(BaseModel):
    model_config = _НАСТРОЙКА_МОДЕЛИ

    segments: list[TranscriptSegment] = []
    session_id: str | None = Field(None, alias="sessionId")

    @classmethod
    def normalize(cls, data: object) -> TranscriptPayload:
        """Realtime-транскрипт приходит и голым списком сегментов,
        и объектом с ключом ``segments`` — разбираем оба вида."""
        if isinstance(data, list):
            return cls(
                segments=[
                    TranscriptSegment.model_validate(item) for item in data
                ]
            )
        return cls.model_validate(data)


class ActionItem(BaseModel):
    model_config = _НАСТРОЙКА_МОДЕЛИ

    description: str = ""
    completed: bool = False


class StructuredPart(BaseModel):
    """Разбор разговора, сделанный облаком Omi."""

    model_config = _НАСТРОЙКА_МОДЕЛИ

    title: str = ""
    overview: str = ""
    emoji: str | None = None
    category: str | None = None
    action_items: list[ActionItem] = Field([], alias="actionItems")
    events: list[dict] = []


class ConversationPayload(BaseModel):
    """Готовый разговор из вебхука «Conversation events»."""

    model_config = _НАСТРОЙКА_МОДЕЛИ

    id: str | None = None
    created_at: str | None = Field(None, alias="createdAt")
    started_at: str | None = Field(None, alias="startedAt")
    finished_at: str | None = Field(None, alias="finishedAt")
    transcript_segments: list[TranscriptSegment] = Field([], alias="transcriptSegments")
    structured: StructuredPart = StructuredPart()
    discarded: bool = False


class DaySummaryPayload(BaseModel):
    """Сводка дня из приложения."""

    model_config = _НАСТРОЙКА_МОДЕЛИ

    uid: str | None = None
    created_at: str | None = Field(None, alias="createdAt")
    summary: str | None = None
    summary_json: dict | None = Field(None, alias="summaryJson")


def parse_time(value: str | None) -> float | None:
    """ISO 8601 (``+00:00`` или ``Z``) → unix-время.

    Мусор и ``None`` → ``None``: время без зоны считаем UTC — приложение
    обычно шлёт зону, но наивное время лучше не выбрасывать.
    """
    if not value:
        return None
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()
