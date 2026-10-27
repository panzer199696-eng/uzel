"""Стоп-лист субтитровых галлюцинаций (FR-023).

Whisper на тишине охотно «диктует» подписи из обучающего корпуса субтитров:
«Продолжение следует», «Субтитры сделал …». Это не речь человека — такие
реплики вырезаются целиком. Но осторожно: настоящую речь порезать нельзя,
поэтому фраза вырезается только когда реплика почти целиком состоит из неё.

Фильтр вызывается роутером после любого бэкенда и бэкенд-независим.
"""

from __future__ import annotations

import re

# Фразы целиком: нормализованная реплика почти целиком состоит из такой фразы.
STOP_PHRASES: tuple[str, ...] = (
    "продолжение следует",
    "подписывайтесь на канал",
    "спасибо за просмотр",
    "ставьте лайки",
)

# Начала фраз, после которых идёт имя автора субтитров, а не речь.
STOP_PREFIXES: tuple[str, ...] = (
    "субтитры сделал",
    "субтитры делал",
    "редактор субтитров",
    "субтитры создавал",
)

# «спасибо за внимание» и «всем пока» сюда включать НЕЛЬЗЯ: это нормальный
# конец совещания — ровно то место, где звучат договорённости и сроки.

_PHRASE_SHARE = 0.8  # фраза должна занимать не меньше 80 % реплики
_TAIL_LIMIT = 40  # хвост после префикса — имя автора субтитров, не речь

_NO_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Приводит текст к виду, по которому ищутся стоп-фразы.

    Нижний регистр, ``ё → е``, без пунктуации и спецсимволов, пробелы
    схлопнуты в один, без полей по краям.
    """
    lowered = text.lower().replace("ё", "е")
    without_punct = _NO_WORD.sub(" ", lowered)
    return _SPACES.sub(" ", without_punct).strip()


def strip_hallucination(text: str) -> tuple[str, str | None]:
    """Вырезает субтитровую галлюцинацию целиком.

    Возвращает ``(текст, причина)``: при срабатывании — ``("", совпавшая
    фраза)``, иначе — исходный текст без изменений и ``None``. Пустой или
    пробельный вход даёт ``("", None)``.
    """
    if not text or not text.strip():
        return "", None

    normalised = normalise(text)

    for phrase in STOP_PHRASES:
        if phrase in normalised and len(phrase) >= _PHRASE_SHARE * len(normalised):
            return "", phrase

    for prefix in STOP_PREFIXES:
        if not normalised.startswith(prefix):
            continue
        tail = normalised[len(prefix) :].strip()
        if len(tail) <= _TAIL_LIMIT:
            return "", prefix

    return text, None
