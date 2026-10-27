"""Формат потока кадров Opus для ``POST /ingest/opus`` (P8, часть 2а).

Кадр Opus самоописания длины не имеет, поэтому в теле запроса кадры идут
с префиксом длины::

    [0:2]   uint16 LE — длина кадра N (N > 0)
    [2:2+N] байты кадра Opus
    … повтор до конца тела

Тот же формат — в ``.pcm``-файле фрагмента очереди: приёмник кладёт тело
как есть, декодирование — работа воркера.
"""

from __future__ import annotations

from collections.abc import Iterator, Iterable

# Значение `encoding` в метаданных очереди.
OPUS_FRAMES_ENCODING = "opus-frames/1"

# uint16 LE — больше в префикс не влезает.
MAX_FRAME_BYTES = 65535


def pack_frames(frames: Iterable[bytes]) -> bytes:
    """Кадры Opus → тело запроса. Пустые кадры пропускаются.

    Кадр длиннее ``MAX_FRAME_BYTES`` байт в формат не помещается — ``ValueError``.
    """
    parts: list[bytes] = []
    for frame in frames:
        if not frame:
            continue
        if len(frame) > MAX_FRAME_BYTES:
            raise ValueError(
                f"кадр Opus не помещается в формат: {len(frame)} байт "
                f"при максимуме {MAX_FRAME_BYTES}"
            )
        parts.append(len(frame).to_bytes(2, "little"))
        parts.append(frame)
    return b"".join(parts)


def _iter_frames(payload: bytes) -> Iterator[memoryview]:
    """Обходит кадры, отдавая срезы без копирования данных.

    Битый формат — ``ValueError`` с внятным текстом: обрезанный заголовок,
    нулевая длина или обрезанный кадр.
    """
    view = memoryview(payload)
    pos = 0
    total = len(view)
    while pos < total:
        if pos + 2 > total:
            raise ValueError(
                "обрезанный заголовок кадра: в конце тела не хватает байтов длины"
            )
        size = int.from_bytes(view[pos : pos + 2], "little")
        pos += 2
        if size == 0:
            raise ValueError("нулевая длина кадра: в формате допустимы только N > 0")
        if pos + size > total:
            raise ValueError(
                f"обрезанный кадр Opus: заявлено {size} байт, в теле осталось {total - pos}"
            )
        yield view[pos : pos + size]
        pos += size


def unpack_frames(payload: bytes) -> list[bytes]:
    """Тело запроса → кадры Opus. Битый формат — ``ValueError``."""
    return [bytes(frame) for frame in _iter_frames(payload)]


def count_frames(payload: bytes) -> int:
    """Число кадров в теле, без копирования данных. Битый формат — ``ValueError``."""
    return sum(1 for _ in _iter_frames(payload))
