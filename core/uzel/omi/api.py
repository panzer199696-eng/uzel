"""Эндпоинты-приёмники вебхуков приложения Omi (P8, Developer Mode).

Приложение ждёт быстрый ``200 OK``: на 4xx/5xx оно ретраит и тормозит свою
транскрипцию, поэтому штатные отказы (заглушка приватности, пустое тело,
чужая частота) отдаются кодом 200 с ``{"accepted": false, "reason": …}``.

Соединение SQLite нельзя делить между потоками FastAPI — хранилище
открывается на каждый запрос и закрывается в ``finally``.
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from ..audio import PCM_ENCODING, float32_to_pcm16, pcm16_to_float32
from ..config import Settings
from ..privacy import PrivacyGate
from ..queue import DiskQueue
from ..storage import Storage
from .models import (
    ConversationPayload,
    DaySummaryPayload,
    TranscriptPayload,
    parse_time,
)
from .sessions import SessionClock
from .sink import OmiSink

log = logging.getLogger(__name__)

# uid приложение присылает всегда, но терять звук из-за его отсутствия нельзя.
DEFAULT_UID = "omi-app"


def build_omi_router(settings: Settings) -> APIRouter:
    def require_token(request: Request) -> None:
        """Токен нужен, если слушаем не только localhost."""
        expected = settings.omi_token
        if not expected:
            return
        provided = request.query_params.get("token")
        if not provided:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[len("Bearer ") :].strip()
        if provided != expected:
            raise HTTPException(status_code=401, detail="omi: token required")

    def session_clock(request: Request) -> SessionClock:
        """Один экземпляр на приложение: повторная пачка сегментов обязана
        попасть в ту же сессию, иначе привязка ко времени поплывёт."""
        clock = getattr(request.app.state, "omi_sessions", None)
        if clock is None:
            clock = SessionClock()
            request.app.state.omi_sessions = clock
        return clock

    router = APIRouter(prefix="/omi", dependencies=[Depends(require_token)])

    @router.get("/setup")
    async def omi_setup() -> dict:
        """Эндпоинт проверки настройки в Developer Mode."""
        return {"is_setup_completed": True}

    @router.post("/audio")
    async def omi_audio(
        request: Request,
        uid: str = Query(DEFAULT_UID, min_length=1),
        sample_rate: int = Query(None, ge=8000, le=48000),
    ) -> dict:
        """Realtime audio bytes: сырой PCM16 LE.

        Параметры с умолчаниями намеренно: приложение шлёт их всегда, но
        сборка, которая забыла один из них, получила бы 422 и ушла в ретраи —
        звук потерялся бы молча. Без частоты считаем частоту ядра.
        """
        if sample_rate is None:
            sample_rate = settings.sample_rate
        body = await request.body()
        if not body:
            return {"accepted": False, "reason": "empty"}

        privacy: PrivacyGate = request.app.state.privacy
        state = privacy.state()
        if state.muted:
            # Звук не должен доехать до диска.
            log.info("omi/audio: uid=%s отклонён приватностью (%s)", uid, state.code)
            return {"accepted": False, "reason": state.code}

        if sample_rate == settings.sample_rate:
            payload, source, rate = body, "omi-app", sample_rate
        elif sample_rate * 2 == settings.sample_rate:
            # Телефон шлёт 8 кГц — поднимаем вдвое линейной интерполяцией.
            samples = pcm16_to_float32(body)
            upsampled = np.interp(
                np.arange(0.0, float(len(samples)), 0.5),
                np.arange(float(len(samples))),
                samples,
            ).astype(np.float32)
            payload = float32_to_pcm16(upsampled)
            if not payload:
                return {"accepted": False, "reason": "empty"}
            source, rate = "omi-app-8k", settings.sample_rate
        else:
            log.warning(
                "omi/audio: uid=%s чужая частота %d Гц (ядро на %d)",
                uid,
                sample_rate,
                settings.sample_rate,
            )
            return {"accepted": False, "reason": "sample_rate"}

        queue: DiskQueue = request.app.state.queue
        meta = queue.put(
            payload,
            uid=uid,
            source=source,
            sample_rate=rate,
            encoding=PCM_ENCODING,
        )
        log.info(
            "omi/audio: uid=%s %d байт @%d Гц → %s",
            uid,
            meta.n_bytes,
            rate,
            meta.chunk_id,
        )
        return {"accepted": True, "chunk_id": meta.chunk_id, "n_bytes": meta.n_bytes}

    @router.post("/transcript")
    async def omi_transcript(
        request: Request,
        uid: str = Query(DEFAULT_UID, min_length=1),
        session_id: str | None = Query(None),
        clock: SessionClock = Depends(session_clock),
    ) -> dict:
        """Realtime transcript: голый список сегментов или объект."""
        privacy: PrivacyGate = request.app.state.privacy
        state = privacy.state()
        if state.muted:
            log.info(
                "omi/transcript: uid=%s отклонён приватностью (%s)",
                uid,
                state.code,
            )
            return {"accepted": False, "reason": state.code}

        try:
            data = json.loads(await request.body())
            payload = TranscriptPayload.normalize(data)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            # Вебхук не место для 500: тело чужое, а ответ должен быть быстрым
            # и однозначным, иначе приложение уходит в ретраи.
            log.warning("omi/transcript: uid=%s тело не разобрано", uid)
            return {"accepted": False, "reason": "bad_json"}
        effective_session = session_id or payload.session_id or uid

        now = time.time()
        clock.purge(now, settings.omi_session_ttl_s)
        storage = Storage(settings.db_path)
        try:
            sink = OmiSink(storage, settings, clock=clock, privacy=privacy)
            stats = sink.save_segments(
                payload.segments, uid=uid, session_id=effective_session, now=now
            )
        finally:
            storage.close()
        log.info(
            "omi/transcript: uid=%s сегментов=%d итог=%s",
            uid,
            len(payload.segments),
            stats,
        )
        return {"accepted": True, **stats}

    @router.post("/conversation")
    async def omi_conversation(
        request: Request,
        uid: str = Query(DEFAULT_UID, min_length=1),
    ) -> dict:
        """Conversation events: готовый разговор целиком.

        Приватность решается по времени записи, а не доставки (P11.2,
        находка 11): отказ по «сейчас» здесь не нужен — разговор мог
        записаться в стоп-зону и доехать после неё.
        """
        try:
            payload = ConversationPayload.model_validate_json(await request.body())
        except ValidationError:
            log.warning("omi/conversation: uid=%s тело не разобрано", uid)
            return {"accepted": False, "reason": "bad_json"}
        now = time.time()
        privacy: PrivacyGate = request.app.state.privacy
        storage = Storage(settings.db_path)
        try:
            sink = OmiSink(storage, settings, privacy=privacy)
            result = sink.save_conversation(payload, uid=uid, now=now)
        finally:
            storage.close()
        log.info(
            "omi/conversation: uid=%s сегментов=%d итог=%s",
            uid,
            len(payload.transcript_segments),
            result,
        )
        return {"accepted": True, **result}

    @router.post("/day-summary")
    async def omi_day_summary(
        request: Request,
        uid: str = Query(DEFAULT_UID, min_length=1),
    ) -> dict:
        """Days summary: сохраняем файлом как пришло.

        Отказ — если заглушено сейчас ИЛИ в момент сборки сводки
        (P11.2, находка 11): сводка, собранная в стоп-зоне, несёт тот же
        приватный день, что и реплики.
        """
        try:
            payload = DaySummaryPayload.model_validate_json(await request.body())
        except ValidationError:
            log.warning("omi/day-summary: uid=%s тело не разобрано", uid)
            return {"accepted": False, "reason": "bad_json"}
        privacy: PrivacyGate = request.app.state.privacy
        state = privacy.state()
        if state.muted:
            log.info(
                "omi/day-summary: uid=%s отклонён приватностью (%s)",
                uid,
                state.code,
            )
            return {"accepted": False, "reason": state.code}
        created = parse_time(payload.created_at)
        if created is not None:
            state = privacy.state(now=created)
            if state.muted:
                log.info(
                    "omi/day-summary: uid=%s отклонён приватностью (%s) на момент сборки",
                    uid,
                    state.code,
                )
                return {"accepted": False, "reason": state.code}
        now = time.time()
        storage = Storage(settings.db_path)
        try:
            sink = OmiSink(storage, settings)
            path = sink.save_day_summary(payload, uid=uid, now=now)
        finally:
            storage.close()
        log.info("omi/day-summary: uid=%s → %s", uid, path)
        return {"accepted": True, "path": str(path)}

    return router
