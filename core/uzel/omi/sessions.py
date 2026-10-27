"""Привязка realtime-сегментов Omi к абсолютному времени (P8).

Сегменты приходят со временем **от начала сессии** (``start``/``end`` в
секундах), а в базе нужно абсолютное. База сессии вычисляется один раз из
первой пачки: последний сегмент пачки считается кончившимся «сейчас».
Хранится в памяти процесса: перезапуск ядра начинает отсчёт заново — это
осознанно, привязка нужна только на время разговора.
"""

from __future__ import annotations

import threading


class SessionClock:
    """Помнит базовое время сессий транскрипта. Потокобезопасен."""

    def __init__(self) -> None:
        # ключ → (базовое время, момент последнего обращения)
        self._sessions: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def base_for(self, key: str, *, last_end: float, now: float) -> float:
        """Базовое время сессии: первый кадр считается кончившимся «сейчас»."""
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                base = now - last_end
                self._sessions[key] = (base, now)
                return base
            self._sessions[key] = (entry[0], now)
            return entry[0]

    def purge(self, now: float, ttl_s: float) -> None:
        """Забыть сессии, к которым не обращались дольше ``ttl_s``."""
        with self._lock:
            stale = [
                key
                for key, (_, seen) in self._sessions.items()
                if now - seen >= ttl_s
            ]
            for key in stale:
                del self._sessions[key]
