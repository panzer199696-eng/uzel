"""HTTP-приёмник аудио (FR-001…FR-004).

Формат ``POST /ingest?sample_rate=16000&uid=…`` с сырым PCM в теле выбран
намеренно: он совпадает с «Realtime audio bytes» приложения Omi, поэтому ядро
обкатывается на живом звуке ещё до готовности своего моста.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import __version__
from .audio import PCM_ENCODING
from .config import Settings, get_settings
from .device import DeviceReport, DeviceStatus
from .logging_setup import setup_logging
from .opus_stream import OPUS_FRAMES_ENCODING, count_frames
from .privacy import PrivacyGate, build_gate
from .queue import DiskQueue
from .security import TokenAuthMiddleware, ensure_safe_listen_host

log = logging.getLogger(__name__)


class DeviceReportIn(BaseModel):
    """Отчёт моста о состоянии канала и кулона (FR-063)."""

    uid: str = Field(min_length=1)
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    codec: str | None = None
    connected: bool = True
    packets: int = Field(default=0, ge=0)
    lost: int = Field(default=0, ge=0)
    spooled: int = Field(default=0, ge=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    # P11, находка 1: не петлевой адрес без токена — не поднимаемся вовсе.
    # Тихо открытая наружу память хуже, чем ядро, которое не завелось.
    ensure_safe_listen_host(settings.host, settings.api_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        queue = DiskQueue(settings.queue_dir)
        restored = queue.recover()  # NFR-004: подхватываем прерванную работу
        app.state.queue = queue
        app.state.settings = settings
        app.state.privacy = build_gate(settings)
        app.state.device = DeviceStatus()
        state = app.state.privacy.state()
        if state.muted:
            log.warning("запись сейчас выключена: %s", state.reason)
        log.info(
            "УЗЕЛ %s готов: очередь %s, в ней %d фрагментов (восстановлено %d)",
            __version__,
            settings.queue_dir,
            queue.pending_count(),
            restored,
        )
        yield

    app = FastAPI(title="УЗЕЛ — ядро", version=__version__, lifespan=lifespan)

    # P11, находка 1: одна зависимость авторизации на ВСЁ приложение, а не
    # только на /omi. Токен задан — без него закрыто всё, кроме /health.
    app.add_middleware(TokenAuthMiddleware, token=settings.api_token)

    @app.get("/health")
    async def health() -> dict:
        # P11: /health открыт без токена (его проверяют мост и телефон),
        # поэтому отдаёт минимум — живость и версию. Счётчики и состояние
        # приватности — /ui/api/status, он за токеном.
        return {"status": "ok", "version": __version__}

    @app.get("/privacy")
    async def privacy_state(request: Request) -> dict:
        return request.app.state.privacy.state().as_dict()

    @app.post("/privacy/mute")
    async def privacy_mute(
        request: Request,
        minutes: float = Query(
            10.0, gt=0, le=24 * 60, description="на сколько заглушить"
        ),
    ) -> dict:
        """Ручная заглушка записи (FR-071)."""
        request.app.state.privacy.mute(minutes)
        return request.app.state.privacy.state().as_dict()

    @app.post("/privacy/pause")
    async def privacy_pause(request: Request) -> dict:
        """Бессрочный «приват» — до явного возобновления (FR-071)."""
        request.app.state.privacy.pause()
        return request.app.state.privacy.state().as_dict()

    @app.post("/privacy/unmute")
    async def privacy_unmute(request: Request) -> dict:
        request.app.state.privacy.unmute()
        return request.app.state.privacy.state().as_dict()

    @app.get("/device")
    async def device_state(request: Request) -> dict:
        """Что мост в последний раз рассказал о кулоне."""
        return request.app.state.device.as_dict()

    @app.post("/device/status")
    async def device_report(request: Request, report: DeviceReportIn) -> dict:
        status: DeviceStatus = request.app.state.device
        status.update(DeviceReport(**report.model_dump()))
        return status.as_dict()

    @app.post("/ingest", status_code=202)
    async def ingest(
        request: Request,
        response: Response,
        uid: str = Query(..., min_length=1, description="идентификатор устройства"),
        sample_rate: int = Query(16000, ge=8000, le=48000),
        source: str = Query("unknown", description="кто прислал: bridge-pc, omi-app"),
        device_time_ms: int | None = Query(
            None, description="время по часам устройства"
        ),
        spooled: bool = Query(
            False, description="фрагмент дослан из буфера моста, а не прямой"
        ),
    ):
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="пустое тело запроса")
        if sample_rate != settings.sample_rate:
            raise HTTPException(
                status_code=400,
                detail=f"ядро настроено на {settings.sample_rate} Гц, получено {sample_rate}",
            )

        # FR-070, FR-071: заглушённый звук отбрасывается ДО записи на диск.
        # Решение — по времени ЗАХВАТА, а не приёма (P11, находка 2): мост
        # копит фрагмент в буфере ночью и досылает утром, и по «сейчас» ночной
        # звук прошёл бы стоп-зону как разрешённый. Времени захвата нет —
        # остаётся «сейчас» (см. воркер: такой фрагмент из буфера помечается
        # приватным).
        privacy: PrivacyGate = request.app.state.privacy
        capture_s = device_time_ms / 1000.0 if device_time_ms is not None else None
        state = privacy.state(now=capture_s) if capture_s is not None else privacy.state()
        if state.muted:
            log.debug("фрагмент отброшен: %s", state.reason)
            # Причина только машинным кодом: заголовки HTTP — latin-1,
            # русский текст в них роняет ответ целиком.
            return Response(status_code=204, headers={"X-Uzel-Muted": state.code})

        queue: DiskQueue = request.app.state.queue
        meta = queue.put(
            payload,
            uid=uid,
            source=source,
            sample_rate=sample_rate,
            encoding=PCM_ENCODING,
            device_time_ms=device_time_ms,
            spooled=spooled,
        )
        response.headers["X-Uzel-Chunk-Id"] = meta.chunk_id
        return {"chunk_id": meta.chunk_id, "n_bytes": meta.n_bytes}

    @app.post("/ingest/opus", status_code=202)
    async def ingest_opus(
        request: Request,
        response: Response,
        uid: str = Query(..., min_length=1, description="идентификатор устройства"),
        source: str = Query("android", description="кто прислал: android, bridge-pc"),
        device_time_ms: int | None = Query(
            None, description="время по часам устройства"
        ),
        spooled: bool = Query(
            False, description="фрагмент дослан из буфера моста, а не прямой"
        ),
    ):
        """Приём кадров Opus от Android-клиента (P8): декодирует воркер, не приёмник."""
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="пустое тело запроса")

        # FR-070, FR-071: заглушённый звук отбрасывается ДО записи на диск —
        # по времени захвата, как и в /ingest (P11, находка 2).
        privacy: PrivacyGate = request.app.state.privacy
        capture_s = device_time_ms / 1000.0 if device_time_ms is not None else None
        state = privacy.state(now=capture_s) if capture_s is not None else privacy.state()
        if state.muted:
            log.debug("фрагмент opus отброшен: %s", state.reason)
            # Причина только машинным кодом: заголовки HTTP — latin-1.
            return Response(status_code=204, headers={"X-Uzel-Muted": state.code})

        try:
            frames = count_frames(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        queue: DiskQueue = request.app.state.queue
        meta = queue.put(
            payload,
            uid=uid,
            source=source,
            # Частота целевая — ПОСЛЕ декодирования: очередь и воркер
            # оперируют готовым PCM 16 кГц.
            sample_rate=settings.sample_rate,
            encoding=OPUS_FRAMES_ENCODING,
            device_time_ms=device_time_ms,
            spooled=spooled,
        )
        response.headers["X-Uzel-Chunk-Id"] = meta.chunk_id
        return {
            "chunk_id": meta.chunk_id,
            "n_bytes": meta.n_bytes,
            "frames": frames,
        }

    # Импорт внутри create_app: `uzel --help` не должен тянуть интерфейс.
    from .ui import mount_ui

    mount_ui(app, settings)

    # P8: приёмник вебхуков приложения Omi (Developer Mode). Импорт внутри
    # create_app — по той же причине: CLI не должен тянуть лишнее.
    if settings.omi_enabled:
        from .omi.api import build_omi_router

        app.include_router(build_omi_router(settings))

    return app


app = create_app()
