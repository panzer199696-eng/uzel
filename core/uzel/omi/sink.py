"""Запись вебхуков Omi в хранилище (P8). Ничего не знает про HTTP."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..privacy import PrivacyGate
from ..storage import Storage
from ..stt.language import whisper_language
from .models import (
    ConversationPayload,
    DaySummaryPayload,
    TranscriptSegment,
    parse_time,
)
from .sessions import SessionClock

log = logging.getLogger(__name__)


class OmiSink:
    """Транскрипты Omi → реплики УЗЛА.

    Текст в этих вебхуках сделан **в облаке Omi**, поэтому каждая реплика
    помечается бэкендом ``omi-cloud`` — по базе всегда видно, что
    расшифровка чужая.

    ``privacy`` решает по времени ЗАПИСИ сегмента, а не доставки вебхука
    (P11.2, находка 11): разговор, записанный в стоп-зону и доставленный
    позже, заглушкой и остаётся. ``None`` — проверок нет (старые тесты).
    """

    def __init__(
        self,
        storage: Storage,
        settings: Settings,
        *,
        clock: SessionClock | None = None,
        privacy: PrivacyGate | None = None,
    ) -> None:
        self.storage = storage
        self.settings = settings
        self._privacy = privacy
        # Часы передаются снаружи: один экземпляр на приложение, иначе
        # повторная пачка сегментов начнёт новую сессию и время поплывёт.
        self._clock = clock if clock is not None else SessionClock()

    def _maybe_set_speaker(self, utterance_id: int, segment: TranscriptSegment) -> None:
        # Неизвестное имя в people не заводим (правило P4): [[Имя]] без
        # заметки — битая ссылка в графе.
        if not segment.speaker_name:
            return
        person_id = self.storage.find_person(segment.speaker_name)
        if person_id is not None:
            self.storage.set_speaker(utterance_id, person_id)

    def _write_text(self, utterance_id: int, text: str) -> None:
        self.storage.set_transcript(
            utterance_id,
            text=text,
            # Язык Omi не сообщает: при auto — пусто, а не слово «auto».
            language=whisper_language(self.settings.stt_language),
            backend="omi-cloud",
        )

    def save_segments(
        self,
        segments: list[TranscriptSegment],
        *,
        uid: str,
        session_id: str,
        now: float,
    ) -> dict:
        """Realtime-транскрипт: создать, уточнить или распознать дубль."""
        stats = {"created": 0, "updated": 0, "duplicates": 0, "skipped": 0, "private": 0}
        live = [s for s in segments if (s.text or "").strip()]
        stats["skipped"] = len(segments) - len(live)
        if not live:
            return stats

        key = session_id or uid
        base = self._clock.base_for(key, last_end=max(s.end for s in live), now=now)
        for segment in live:
            if (
                self._privacy is not None
                and self._privacy.is_muted(base + segment.start)
            ):
                # Момент сегмента — в стоп-зоне: текст не сохраняется вовсе,
                # не «помечается и удаляется потом» (P11.2, находка 11).
                stats["private"] += 1
                continue
            text = segment.text.strip()
            external_id = f"omi:{key}:{segment.start:.2f}:{segment.speaker or ''}"
            existing = self.storage.find_utterance_by_external(external_id)
            if existing is not None:
                if (existing.text or "") == text:
                    stats["duplicates"] += 1
                else:
                    # Realtime-транскрипт уточняет уже сказанное —
                    # новую реплику плодить нельзя.
                    self._write_text(existing.id, text)
                    stats["updated"] += 1
                self._maybe_set_speaker(existing.id, segment)
                continue
            utterance_id = self.storage.add_utterance(
                uid=uid,
                started_at=base + segment.start,
                ended_at=base + segment.end,
                speech_seconds=max(0.0, segment.end - segment.start),
                external_id=external_id,
            )
            self._write_text(utterance_id, text)
            stats["created"] += 1
            self._maybe_set_speaker(utterance_id, segment)
        return stats

    def save_conversation(
        self, payload: ConversationPayload, *, uid: str, now: float
    ) -> dict:
        """Разговор целиком из вебхука «Conversation events».

        Приватность решается по времени записи (P11.2, находка 11):
        сегмент, чей момент ``started + segment.start`` попал в заглушку,
        не сохраняется вовсе; время записи не разобрано — все реплики
        помечаются приватными. В обоих случаях выжимка Omi (задачи, тег
        категории, саммари) не сохраняется: она построена по всему
        разговору, включая заглушённую часть.
        """
        if payload.discarded:
            return {"skipped": True, "reason": "discarded"}

        started = parse_time(payload.started_at)
        if started is None:
            started = parse_time(payload.created_at)
        # Время записи не доказано: разговор мог состояться в стоп-зоне —
        # ошибаемся в сторону приватности, как и воркер для буфера моста.
        time_known = started is not None
        if not time_known:
            duration = max((s.end for s in payload.transcript_segments), default=0.0)
            started = now - duration

        conversation_key = payload.id if payload.id else f"{started}:{uid}"
        external_id = f"omi:conv:{conversation_key}"
        existing_cid = self.storage.find_conversation_by_external(external_id)
        if existing_cid is not None:
            return {"duplicate": True, "conversation_id": existing_cid}

        live = [
            (index, segment)
            for index, segment in enumerate(payload.transcript_segments)
            if (segment.text or "").strip()
        ]
        kept: list[tuple[int, TranscriptSegment]] = []
        dropped = 0
        for index, segment in live:
            if (
                self._privacy is not None
                and time_known
                and self._privacy.is_muted(started + segment.start)
            ):
                # Звук стоп-зоны не должен доехать до диска.
                dropped += 1
                continue
            kept.append((index, segment))
        if live and not kept:
            log.info(
                "omi/conversation: %s целиком в стоп-зоне, сегментов=%d",
                conversation_key,
                dropped,
            )
            return {"accepted": False, "reason": "private"}
        # Время не доказано — реплики остаются в базе, но приватными.
        mark_private = self._privacy is not None and not time_known

        cid = self.storage.open_conversation(
            uid=uid, started_at=started, external_id=external_id
        )

        utterance_ids: list[int] = []
        for index, segment in kept:
            text = (segment.text or "").strip()
            utterance_id = self.storage.add_utterance(
                uid=uid,
                started_at=started + segment.start,
                ended_at=started + segment.end,
                speech_seconds=max(0.0, segment.end - segment.start),
                external_id=f"omi:conv:{conversation_key}:{index}",
                is_private=mark_private,
            )
            self._write_text(utterance_id, text)
            self._maybe_set_speaker(utterance_id, segment)
            utterance_ids.append(utterance_id)
        # attach сам пересчитает границы разговора и speech_seconds.
        self.storage.attach_utterances(cid, utterance_ids)

        # Выжимка Omi построена по всему разговору, в том числе по
        # заглушённой части: сохранить её значило бы вынести приватное
        # через саммари. Отсекли или пометили — не сохраняем.
        structured_dropped = dropped > 0 or mark_private

        tasks = 0
        if not structured_dropped:
            for item in payload.structured.action_items:
                if not (item.description or "").strip():
                    continue
                # completed игнорируем: у нас задача всё равно заводится,
                # закрывает её человек.
                self.storage.add_task(cid, item.description.strip())
                tasks += 1

            if payload.structured.category:
                self.storage.add_tag(payload.structured.category, conversation_id=cid)

        # События не сохраняем: сущности «событие» в УЗЛЕ нет.
        events_ignored = len(payload.structured.events)

        summary = None
        if not structured_dropped:
            parts = [
                part.strip()
                for part in (payload.structured.title, payload.structured.overview)
                if part and part.strip()
            ]
            # Заголовок часто уже с точкой или вопросом — второй точки не ставим.
            if parts:
                summary = parts[0]
                for part in parts[1:]:
                    summary += (
                        " " if summary.endswith((".", "!", "?", "…")) else ". "
                    ) + part

        finished = parse_time(payload.finished_at)
        if finished is None:
            finished = max(
                (started + s.end for s in payload.transcript_segments),
                default=started,
            )
        superseded = self._supersede_realtime(uid=uid, start=started, end=finished)

        # Заметка дня берёт только закрытые разговоры.
        self.storage.close_conversation(cid, summary=summary, status="closed")
        result = {
            "conversation_id": cid,
            "utterances": len(utterance_ids),
            "tasks": tasks,
            "events_ignored": events_ignored,
            "superseded": superseded,
        }
        if structured_dropped:
            result["private"] = dropped
            result["structured_dropped"] = True
        return result

    # Допуск ко времени: у потоковых сегментов оно вычислено от «сейчас»
    # и точным не бывает.
    ДОПУСК_ВРЕМЕНИ_С = 300.0

    def _supersede_realtime(self, *, uid: str, start: float, end: float) -> int:
        """Гасит потоковые реплики, которые накрыл готовый разговор.

        Приложение Omi шлёт и потоковый транскрипт, и готовый разговор. Если
        в Developer Mode включены оба вебхука, одни и те же слова приезжают
        дважды, и в заметке дня разговор задвоится. Удалять ничего нельзя,
        поэтому потоковые реплики того же промежутка помечаются причиной
        ``omi_superseded`` и уходят в ``rejected``: текст остаётся в базе,
        но в группировку и в заметку он больше не попадает.
        """
        погашено = 0
        for row in self.storage.ungrouped_utterances():
            внешний = row.external_id or ""
            if row.uid != uid or row.stt_backend != "omi-cloud":
                continue
            if not внешний.startswith("omi:") or внешний.startswith("omi:conv:"):
                continue
            if (
                row.ended_at < start - self.ДОПУСК_ВРЕМЕНИ_С
                or row.started_at > end + self.ДОПУСК_ВРЕМЕНИ_С
            ):
                continue
            self.storage.mark_quality(row.id, reason="omi_superseded", enforce=True)
            погашено += 1
        if погашено:
            log.info(
                "разговор Omi накрыл %d потоковых реплик — помечены omi_superseded",
                погашено,
            )
        return погашено

    def save_day_summary(
        self, payload: DaySummaryPayload, *, uid: str, now: float
    ) -> Path:
        """Сводка дня — файлом «как пришло», без записи в vault.

        Заметку дня делает наш собственный экспорт: две заметки на один
        день не нужны.
        """
        moment = parse_time(payload.created_at) or now
        day = datetime.fromtimestamp(moment).strftime("%Y-%m-%d")
        path = self.settings.data_dir / "omi" / f"сводка-{uid}-{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
