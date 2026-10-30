"""Воркер конвейера (ТЗ 2026-08-24, пакет 3): провод между готовыми запчастями.

Два прохода, как в ТЗ:

- дешёвый постоянный (``ingest_once`` / ``run_ingest``): очередь → декод →
  VAD → реплики в базе. GPU не трогается вовсе, поэтому идёт всё время;
- ночной батч (``run_batch``): recover → расшифровка пачками → группировка в
  разговоры → фильтр уровня разговора → NLU → экспорт в Obsidian.

Всё тяжёлое (модель STT, VAD, fastembed) грузится лениво и ровно один раз:
импорт модуля не тянет ни onnxruntime, ни faster_whisper.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import numpy as np

from . import i18n
from . import privacy as privacy_policy
from . import segments
from .audio import PCM_ENCODING, pcm16_to_float32, read_wav, write_wav
from .export.report import write_report
from .export.obsidian import (
    ConversationView,
    FactView,
    append_tasks,
    archive_tasks,
    write_day_note,
    write_transcript_note,
)
from .i18n import t
from .llm import LlmLocalFused, LlmRouter
from .nlu import NluError, NluProcessor, store_result
from .opus_stream import OPUS_FRAMES_ENCODING, unpack_frames
from .privacy import PrivacyGate, build_gate
from .quality import QualityFilter, Thresholds, check_conversation, dominant_language
from .queue import Chunk, DiskQueue
from .segments import group_conversations, merge_utterances, shift
from .storage import Storage
from .stt.base import LocalBackendFused, TranscriptionError
from .stt.language import LanguagePicker, whisper_language
from .vad import detect_speech
from .vector import NullIndex

log = logging.getLogger(__name__)


def person_from_tag(tag: str) -> str | None:
    """Имя из метки «человек:Имя» на любом языке каталогов; не метка человека — None.

    Метки пишутся на языке интерфейса, а язык могли сменить: заметка дня
    обязана узнавать и старые «человек:», и новые «person:».
    """
    for template in i18n.data_all("tags.person"):
        prefix = template.split("{", 1)[0]
        if prefix and tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


class UndecodableChunk(ValueError):
    """Фрагмент нельзя декодировать: кодировка или частота не те.

    Возврат такого фрагмента в очередь был бы вечным кругом — повтор не меняет
    данных. Он уходит в ``done``: данные не теряются и лежат до TTL.
    """


class DecoderUnavailable(RuntimeError):
    """Декодера нет в окружении — сломаны пути, а не данные.

    Отличать от :class:`UndecodableChunk` обязательно. Поймано долгим прогоном
    23.09.2026: ``uzel.cmd`` клал в ``PYTHONPATH`` только ``core`` и
    ``integrations\\mcp``, поэтому ``uzel_bridge.opus_decoder`` не импортировался,
    и **каждый** фрагмент с телефона уходил в ``done`` как «неразбираемый».
    Телефон получал 202, владелец не получал ничего, а через TTL звук удалялся.

    Данные целы, повтор после починки окружения их расшифрует — значит фрагмент
    возвращается в очередь, а проход останавливается. Ровно тот же размен, что
    у предохранителя STT: остановка лучше тихой потери.
    """


def decode_chunk(chunk: Chunk, *, expected_sample_rate: int = 16000) -> np.ndarray:
    """Фрагмент из очереди → float32 моно. Чужое — ``UndecodableChunk``.

    ``pcm_s16le`` — родной формат: его шлют мост на ПК и приложение Omi.
    Кадры Opus (``opus-frames/1``) дошли до ядра с появлением Android-клиента
    (P8) — их декодируем здесь декодером моста, ядро не тянет PyAV на старте.
    """
    meta = chunk.meta
    if meta.encoding == OPUS_FRAMES_ENCODING:
        return _decode_opus_chunk(chunk, expected_sample_rate=expected_sample_rate)
    if meta.encoding != PCM_ENCODING:
        raise UndecodableChunk(f"кодировка {meta.encoding!r} не поддерживается ядром")
    if meta.sample_rate != expected_sample_rate:
        raise UndecodableChunk(
            f"частота {meta.sample_rate} Гц, ядро настроено на {expected_sample_rate} Гц"
        )
    return pcm16_to_float32(chunk.read())


def _decode_opus_chunk(chunk: Chunk, *, expected_sample_rate: int) -> np.ndarray:
    """Кадры Opus из фрагмента → float32 [-1, 1] на целевой частоте.

    Повреждённый кадр не роняет фрагмент: ``OpusDecoder.decode`` на битом
    кадре возвращает пустой массив — такие кадры пропускаются. Если после
    декодирования не осталось ни одного сэмпла или формат потока битый —
    ``UndecodableChunk``: повтор данных ничего не изменит.
    """
    try:
        from uzel_bridge.opus_decoder import OpusDecoder
    except ImportError as exc:  # сломаны пути окружения, а не данные
        raise DecoderUnavailable(f"декодер Opus недоступен: {exc}") from exc

    try:
        frames = unpack_frames(chunk.read())
    except ValueError as exc:
        raise UndecodableChunk(f"битый поток кадров Opus: {exc}") from exc

    decoder = OpusDecoder(expected_sample_rate)
    parts = [decoder.decode(frame) for frame in frames]
    tail = decoder.flush()  # хвост ресемплера 48 → 16 кГц
    if tail.size:
        parts.append(tail)
    nonempty = [part for part in parts if part.size]
    if not nonempty:
        raise UndecodableChunk(
            f"ни один кадр Opus фрагмента {chunk.meta.chunk_id} не декодировался"
        )
    pcm = np.concatenate(nonempty)
    return pcm.astype(np.float32) / 32768.0


@dataclass
class IngestStats:
    """Итог дешёвого прохода."""

    recovered: int = 0  # фрагментов возвращено из processing
    chunks: int = 0  # обработано фрагментов
    utterances: int = 0  # создано реплик
    duplicates: int = 0  # реплика уже была (повторная обработка фрагмента)
    silent: int = 0  # фрагментов без речи
    failed: int = 0  # фрагментов вернулось в очередь из-за ошибки
    undecodable: int = 0  # фрагментов отброшено: кодировку не разобрать
    stopped_by: str | None = None  # проход прерван: причина машинным кодом


@dataclass
class BatchStats:
    """Итог ночного батча."""

    recovered: int = 0
    stitched: int = 0  # реплик поглощено склейкой через стыки фрагментов
    transcribed: int = 0
    failed: int = 0  # расшифровка не удалась
    batches: int = 0
    conversations: int = 0  # собрано разговоров
    analysed: int = 0  # разговоров разобрано NLU
    skipped: int = 0  # разговоров отсеяно фильтром уровня разговора
    nlu_failed: int = 0
    accepted: int = 0  # реплик принято фильтром качества
    rejected: int = 0  # реплик отклонено
    notes: int = 0  # заметок дня записано
    raw_notes: int = 0  # файлов сырой расшифровки записано
    exported_tasks: int = 0
    expired_tasks: int = 0  # открытых задач снято по сроку давности
    unloaded: int = 0  # моделей выгружено из видеопамяти между тактами
    purged_audio: int = 0  # файлов звука реплик удалено по TTL
    purged_chunks: int = 0  # обработанных фрагментов очереди удалено по TTL
    stopped_by: str | None = None  # 'stop-file' | 'time-limit' | 'stt-fused' | None


def _merge_stats(total, part):
    """Складывает статистику итераций; строки (``stopped_by``) не суммируются."""
    changes = {}
    for field in fields(part):
        current = getattr(total, field.name)
        value = getattr(part, field.name)
        if isinstance(current, int) and isinstance(value, int):
            changes[field.name] = current + value
        elif current is None and value is not None:
            changes[field.name] = value
    return replace(total, **changes)


def _hhmm(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


class Worker:
    """Связывает очередь, VAD, STT, NLU и выгрузку в один конвейер.

    Все зависимости внедряются извне (для тестов); при ``None`` собираются
    лениво из настроек — конструирование воркера ничего не грузит.
    """

    def __init__(
        self,
        settings,
        *,
        storage=None,
        queue=None,
        privacy=None,
        stt_router=None,
        llm_router=None,
        vad=None,
        index=None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self._storage = storage
        self._queue = queue
        self._privacy = privacy
        self._stt_router = stt_router
        self._llm_router = llm_router
        self._vad = vad
        self._index = index

    # ---------- ленивые зависимости ----------

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            self._storage = Storage(self.settings.db_path)
        return self._storage

    @property
    def queue(self) -> DiskQueue:
        if self._queue is None:
            self._queue = DiskQueue(self.settings.queue_dir)
        return self._queue

    @property
    def privacy(self) -> PrivacyGate:
        if self._privacy is None:
            self._privacy = build_gate(self.settings)
        return self._privacy

    @property
    def vad(self):
        if self._vad is None:
            from .vad import SileroVAD

            self._vad = SileroVAD(
                self.settings.vad_model_path, self.settings.sample_rate
            )
        return self._vad

    @property
    def llm_router(self) -> LlmRouter:
        if self._llm_router is None:
            from .llm import build_llm_router

            self._llm_router = build_llm_router(self.settings)
        return self._llm_router

    @property
    def index(self):
        if self._index is None:
            if self.settings.vector_enabled:
                from .vector import build_index

                self._index = build_index(self.settings)
            else:
                self._index = NullIndex()
        return self._index

    def _ensure_stt(self):
        """Модель STT грузится здесь и ровно один раз за жизнь воркера."""
        if self._stt_router is None:
            from .stt.router import build_router

            self._stt_router = build_router(self.settings)
        return self._stt_router

    def _release_stt(self) -> int:
        """Выгружает модель STT, если роутер создан. Возвращает число моделей.

        Свойство ``self.stt_router`` здесь звать нельзя: несозданный роутер
        оно создало бы и загрузило модель зря. Работаем с приватным полем.
        """
        if self._stt_router is None:
            return 0
        unload = getattr(self._stt_router, "unload", None)
        if unload is None:
            return 0
        try:
            names = unload() or []
        except Exception:
            log.exception("выгрузка модели STT не удалась")
            return 0
        if names:
            log.info("модели STT выгружены из видеопамяти: %s", ", ".join(names))
        return len(names)

    def _release_llm(self) -> int:
        """Симметрично ``_release_stt``: выгрузка LLM после разбора."""
        if self._llm_router is None:
            return 0
        unload = getattr(self._llm_router, "unload", None)
        if unload is None:
            return 0
        try:
            names = unload() or []
        except Exception:
            log.exception("выгрузка модели разбора не удалась")
            return 0
        if names:
            log.info("модели разбора выгружены из видеопамяти: %s", ", ".join(names))
        return len(names)

    # ---------- жизненный цикл ----------

    def close(self) -> None:
        if self._storage is not None:
            self._storage.close()
            self._storage = None

    def __enter__(self) -> Worker:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---------- восстановление (критерий A4) ----------

    def recover(self) -> int:
        """Возвращает прерванное из ``processing`` в очередь.

        Реплики восстанавливать не нужно: прерванная расшифровка оставляет
        статус ``pending``, а ``pending_utterances`` берёт и ``'failed'`` —
        следующий прогон подберёт их сам.
        """
        moved = self.queue.recover()
        if moved:
            log.info("recover: возвращено фрагментов в очередь: %d", moved)
        return moved

    # ---------- дешёвый постоянный проход ----------

    def ingest_once(self, *, limit: int | None = None) -> IngestStats:
        """Один дешёвый проход: очередь → VAD → реплики. GPU не трогается."""
        settings = self.settings
        stats = IngestStats()
        claim_limit = limit if limit is not None else settings.worker_claim_limit

        for chunk in self.queue.claim(claim_limit):
            stats.chunks += 1
            try:
                samples = decode_chunk(chunk, expected_sample_rate=settings.sample_rate)
            except DecoderUnavailable as exc:
                # Окружение, а не данные: фрагмент цел и расшифруется после
                # починки. Возвращаем в очередь и останавливаем проход, иначе
                # за ночь в done уедет весь поток с телефона.
                log.error(
                    "проход остановлен: %s. Фрагмент %s возвращён в очередь",
                    exc,
                    chunk.meta.chunk_id,
                )
                self.queue.fail(chunk)
                stats.failed += 1
                stats.stopped_by = "decoder-unavailable"
                break
            except UndecodableChunk as exc:
                log.error("фрагмент %s не разобран: %s", chunk.meta.chunk_id, exc)
                self.queue.complete(chunk)
                stats.undecodable += 1
                continue

            # Приватность — по времени ЗАХВАТА, а не приёма (P11, находка 2):
            # фрагмент из буфера моста мог быть записан в стоп-зону, а доехать
            # после неё. device_time_ms — часы устройства в момент записи;
            # время захвата у одного и того же фрагмента постоянно, поэтому
            # защита от дублей (find_utterance по границам) становится только
            # надёжнее. Ошибаемся в сторону приватности.
            if chunk.meta.device_time_ms is not None:
                audio_end = chunk.meta.device_time_ms / 1000.0
                capture_known = True
            else:
                # received_ns — момент приёма: отстоит от захвата не больше,
                # чем шёл запрос. Для прямого потока это честно; для досланного
                # из буфера — нет, см. ниже.
                audio_end = chunk.meta.received_ns / 1e9
                capture_known = False
            audio_start = audio_end - len(samples) / settings.sample_rate

            try:
                speech = detect_speech(
                    samples,
                    settings.sample_rate,
                    vad=self.vad,
                    threshold=settings.vad_threshold,
                    min_speech_ms=settings.min_speech_ms,
                    min_silence_ms=settings.min_silence_ms,
                    speech_pad_ms=settings.speech_pad_ms,
                )
                if not speech:
                    stats.silent += 1
                    self.queue.complete(chunk)
                    continue

                utterances = merge_utterances(
                    shift(speech, audio_start),
                    gap_ms=settings.utterance_gap_ms,
                )

                # Приватность — по времени ЗАХВАТА, а не приёма (P11).
                is_private = self.privacy.is_muted(audio_start)
                if not capture_known and chunk.meta.spooled:
                    # Время захвата неизвестно, а фрагмент ехал через буфер
                    # моста: сколько он ждал — неизвестно, и ночь в буфере от
                    # утра неотличимы. Ошибаемся в сторону приватности.
                    is_private = True

                for index, utterance in enumerate(utterances):
                    duplicate = self.storage.find_utterance(
                        uid=chunk.meta.uid,
                        started_at=utterance.start,
                        ended_at=utterance.end,
                    )
                    if duplicate is not None:
                        # упали между add_utterance и queue.complete, фрагмент
                        # вернулся и обработан заново — реплика уже есть
                        stats.duplicates += 1
                        continue
                    lo = max(
                        0,
                        int((utterance.start - audio_start) * settings.sample_rate),
                    )
                    hi = min(
                        len(samples),
                        int((utterance.end - audio_start) * settings.sample_rate),
                    )
                    path = settings.audio_dir / f"{chunk.meta.chunk_id}-{index:03d}.wav"
                    write_wav(path, samples[lo:hi], settings.sample_rate)
                    self.storage.add_utterance(
                        uid=chunk.meta.uid,
                        started_at=utterance.start,
                        ended_at=utterance.end,
                        speech_seconds=utterance.speech_duration,
                        is_private=is_private,
                        audio_path=str(path),
                    )
                    stats.utterances += 1

                self.queue.complete(chunk)
            except Exception:
                stats.failed += 1
                self.queue.fail(chunk)
                log.exception(
                    "фрагмент %s вернулся в очередь из-за ошибки",
                    chunk.meta.chunk_id,
                )
        return stats

    def run_ingest(
        self, *, interval_s: float | None = None, iterations: int | None = None
    ) -> IngestStats:
        """Цикл дешёвых проходов.

        Выходит по ``iterations``, по появлению стоп-файла
        (``settings.worker_stop_file``) или по ``KeyboardInterrupt``.
        """
        interval = (
            self.settings.worker_poll_interval_s if interval_s is None else interval_s
        )
        total = IngestStats()
        done = 0
        stop_file = self.settings.worker_stop_file
        try:
            while not stop_file.exists():
                total = _merge_stats(total, self.ingest_once())
                done += 1
                if iterations is not None and done >= iterations:
                    break
                if interval > 0:
                    time.sleep(interval)
        except KeyboardInterrupt:
            log.info("дешёвый проход остановлен вручную")
        return total

    # ---------- ночной батч ----------

    def run_batch(
        self,
        *,
        batch_size=None,
        time_limit_s=None,
        max_batches=None,
        close_after_s: float | None = None,
        export: bool = True,
        allow_cloud: bool | None = None,
        with_transcript: bool | None = None,
    ) -> BatchStats:
        """Ночной батч: расшифровка → разговоры → качество → NLU → экспорт.

        ``allow_cloud=False`` — штатный путь; облако допускается только явным
        решением вызывающего, и даже тогда приватные реплики остаются
        локальными (FR-072). ``with_transcript=None`` — флаг сырой расшифровки
        берётся из настроек (``export_transcript``).
        """
        settings = self.settings
        if allow_cloud is None:
            # Одно место правды для конвейера и для строки в интерфейсе.
            allow_cloud = privacy_policy.BATCH_ALLOW_CLOUD
        stats = BatchStats()
        started = self.clock()
        size = settings.worker_batch_size if batch_size is None else batch_size
        limit_s = settings.worker_time_limit_s if time_limit_s is None else time_limit_s
        close_after = (
            settings.conversation_gap_s if close_after_s is None else close_after_s
        )

        # Такт 0: прерванное возвращается в очередь (критерий A4).
        stats.recovered = self.recover()

        # Такт 0.5: обрывки фраз на стыках фрагментов — в одну реплику.
        self._stitch_pending(stats)

        # Такт 1: расшифровка пачками, свежие первыми.
        self._transcribe_batches(
            stats,
            started=started,
            size=size,
            limit_s=limit_s,
            max_batches=max_batches,
            allow_cloud=allow_cloud,
        )

        # Такт 2: группировка расшифрованного в разговоры.
        stats.conversations += self._group_conversations(close_after_s=close_after)

        # Такт 2.5: whisper вон из видеопамяти. 6 ГБ не держат его и локальную
        # LLM одновременно, а разбор идёт сразу следом.
        if settings.worker_unload_between_phases:
            stats.unloaded += self._release_stt()

        # Такт 3: фильтр уровня разговора, NLU, сохранение.
        touched = self._analyse_open_conversations(
            stats,
            allow_cloud=allow_cloud,
            started=started,
            limit_s=limit_s,
        )

        # Конец такта 3: LLM вон из видеопамяти — следующий прогон батча
        # снова получит VRAM под whisper.
        if settings.worker_unload_between_phases:
            stats.unloaded += self._release_llm()

        # Такт 4: экспорт.
        if export:
            transcript_flag = (
                self.settings.export_transcript
                if with_transcript is None
                else with_transcript
            )
            self._export(touched, stats, with_transcript=transcript_flag)

        # Такт 5: сырой звук по TTL (FR-042). До P11.2 его звала только
        # `uzel purge`, и звук копился бессрочно: ~600 МБ в сутки (находка 7).
        self._purge(stats)
        return stats

    def _stitch_pending(self, stats: BatchStats) -> None:
        """Склеивает нерасшифрованные реплики, разрезанные стыком фрагментов.

        Дешёвый проход видит по одному фрагменту (с телефона — 5 с) и режет
        фразу на его границе; днём следующий фрагмент ещё не пришёл. Ночью
        весь день на месте: соседние реплики с паузой не длиннее
        ``utterance_gap_ms`` сводятся в одну, пауза между ними — тишина.
        Сбой одной группы не роняет батч: она расшифруется обрывками.
        """
        settings = self.settings
        if settings.stitch_max_s <= 0:
            return
        sr = settings.sample_rate
        rows = self.storage.pending_utterances(limit=1_000_000)
        groups = segments.plan_stitches(
            rows,
            gap_s=settings.utterance_gap_ms / 1000.0,
            max_s=settings.stitch_max_s,
        )
        for group in groups:
            try:
                pieces: list[np.ndarray] = []
                cursor = group[0].started_at
                for row in group:
                    samples, _ = read_wav(row.audio_path, expected_sr=sr)
                    gap = row.started_at - cursor
                    if gap > 0:
                        pieces.append(np.zeros(int(round(gap * sr)), dtype=np.float32))
                    pieces.append(samples)
                    cursor = max(cursor, row.ended_at)
                head = group[0]
                path = settings.audio_dir / f"stitch-{head.id}-{len(group)}.wav"
                write_wav(path, np.concatenate(pieces), sr)
                self.storage.stitch_utterances(
                    head.id,
                    [row.id for row in group[1:]],
                    ended_at=cursor,
                    speech_seconds=sum(row.speech_seconds for row in group),
                    audio_path=str(path),
                )
            except Exception:
                log.exception("склейка реплик %s не удалась", [r.id for r in group])
                continue
            for row in group:
                if row.audio_path and row.audio_path != str(path):
                    try:
                        Path(row.audio_path).unlink(missing_ok=True)
                    except OSError:
                        log.warning("не удалён звук склеенной реплики %s", row.id)
            stats.stitched += len(group) - 1
        if stats.stitched:
            log.info("склеено реплик через стыки фрагментов: %d", stats.stitched)

    def _purge(self, stats: BatchStats) -> None:
        """Очистка по TTL не роняет батч: расшифровка важнее уборки."""
        ttl = self.settings.audio_ttl_days
        try:
            stats.purged_audio += self.storage.purge_audio(ttl)
            stats.purged_chunks += self.queue.purge_done(ttl)
        except Exception:
            log.exception("очистка по TTL не удалась")

    def _transcribe_batches(
        self,
        stats: BatchStats,
        *,
        started: float,
        size: int,
        limit_s: float,
        max_batches: int | None,
        allow_cloud: bool,
    ) -> None:
        settings = self.settings
        fused = False
        # Язык распознавания на весь прогон: при auto короткие реплики берут
        # язык уверенно распознанных длинных (stt/language.py).
        picker = LanguagePicker(
            settings.stt_language,
            seed=self.storage.recent_language()
            if whisper_language(settings.stt_language) is None
            else None,
        )
        while True:
            # Стоп-файл и лимит времени проверяются МЕЖДУ пачками:
            # начатая пачка доводится до конца.
            if settings.worker_stop_file.exists():
                stats.stopped_by = "stop-file"
                break
            if self.clock() - started >= limit_s:
                stats.stopped_by = "time-limit"
                break

            rows = self.storage.pending_utterances(limit=size, newest_first=True)
            if not rows:
                break

            router = self._ensure_stt()
            progressed = False  # расшифрована ли хоть одна реплика пачки
            if picker.auto:
                # Длинные вперёд: их язык достанется коротким этой же пачки.
                # Сортировка устойчивая — при равной длине свежие по-прежнему первыми.
                rows = sorted(rows, key=lambda item: -(item.speech_seconds or 0.0))
            for row in rows:
                samples = self._load_utterance_audio(row, stats)
                if samples is None:
                    continue
                try:
                    # Приватная реплика не уходит в облако никогда (FR-072).
                    seconds = len(samples) / float(settings.sample_rate)
                    result = router.transcribe(
                        samples,
                        settings.sample_rate,
                        language=picker.choose(seconds),
                        allow_cloud=allow_cloud and not row.is_private,
                    )
                except LocalBackendFused:
                    # Реплики остаются pending — так и задумано (FR-022).
                    stats.stopped_by = "stt-fused"
                    fused = True
                    break
                except TranscriptionError as exc:
                    log.warning("реплика %d не расшифрована: %s", row.id, exc)
                    self.storage.mark_failed(row.id)
                    stats.failed += 1
                    continue

                self.storage.set_transcript(
                    row.id,
                    text=result.text,
                    language=result.language,
                    backend=result.backend,
                )
                picker.observe(
                    result.language,
                    probability=(result.raw or {}).get("language_probability"),
                    seconds=seconds,
                )
                stats.transcribed += 1
                progressed = True
                if row.is_private:
                    # Приватная реплика расшифрована, но в смысловой индекс
                    # не уходит: текст остаётся в SQLite и больше нигде
                    # (P11.2, находка 5).
                    continue
                try:
                    self.index.upsert(
                        row.id,
                        result.text,
                        payload={
                            "started_at": row.started_at,
                            "uid": row.uid,
                            "language": result.language,
                            "backend": result.backend,
                        },
                    )
                except Exception:
                    # Падение смыслового индекса не роняет конвейер.
                    log.exception("смысловой индекс не принял реплику %d", row.id)

            stats.batches += 1
            if fused:
                break
            if not progressed:
                # Пачка из одних отказов: pending_utterances берёт и 'failed',
                # поэтому без этого выхода цикл вечно брался бы за те же
                # реплики. Повторная попытка им уже была.
                log.warning("пачка без единой расшифровки — останавливаюсь")
                break
            if max_batches is not None and stats.batches >= max_batches:
                break

    def _load_utterance_audio(self, row, stats: BatchStats):
        """WAV реплики или отметка провала: без файла расшифровывать нечего."""
        if not row.audio_path:
            log.error("у реплики %d нет файла аудио", row.id)
            self.storage.mark_failed(row.id)
            stats.failed += 1
            return None
        try:
            samples, _ = read_wav(row.audio_path, expected_sr=self.settings.sample_rate)
        except (OSError, ValueError) as exc:
            log.error("аудио реплики %d не читается: %s", row.id, exc)
            self.storage.mark_failed(row.id)
            stats.failed += 1
            return None
        return samples

    def _group_conversations(self, *, close_after_s: float) -> int:
        """Собирает разговоры из расшифрованных реплик. Возвращает число новых.

        Хвост не закрывается: разговор, чей конец свежее ``close_after_s``, ещё
        может продолжиться — закрыть его сейчас значило бы разрезать один
        разговор надвое.
        """
        rows = self.storage.ungrouped_utterances()
        if not rows:
            return 0

        pairs = [
            (row, segments.Utterance(start=row.started_at, end=row.ended_at))
            for row in rows
        ]
        # group_conversations кладёт в Conversation те же объекты — соответствие
        # «реплика ↔ id» держим по идентичности объекта.
        by_identity = {id(utterance): row for row, utterance in pairs}
        created = 0
        for conversation in group_conversations(
            [utterance for _, utterance in pairs],
            gap_s=self.settings.conversation_gap_s,
        ):
            if self.clock() - conversation.end < close_after_s:
                continue
            member_rows = sorted(
                (by_identity[id(u)] for u in conversation.utterances),
                key=lambda row: row.started_at,
            )
            cid = self.storage.open_conversation(
                uid=member_rows[0].uid, started_at=conversation.start
            )
            # attach_utterances сам пересчитает границы и speech_seconds.
            self.storage.attach_utterances(cid, [row.id for row in member_rows])
            created += 1
        return created

    def _analyse_open_conversations(
        self,
        stats: BatchStats,
        *,
        allow_cloud: bool,
        started: float,
        limit_s: float,
    ) -> set[date]:
        """Такт 3 по каждому открытому разговору. Возвращает тронутые даты.

        Стоп-файл и предел времени проверяются и здесь, перед каждым разговором:
        разбор моделью — самая долгая часть батча, и батч, который «остановлен»,
        но продолжает молотить NLU до утра, остановленным не является.
        Разобранное при этом не пропадает — такт 4 выгрузит его как обычно.
        """
        thresholds = Thresholds.from_settings(self.settings)
        touched: set[date] = set()
        for row in self.storage.conversations_with_status("open"):
            if self.settings.worker_stop_file.exists():
                stats.stopped_by = "stop-file"
                break
            if self.clock() - started >= limit_s:
                stats.stopped_by = "time-limit"
                break
            cid = row["id"]
            try:
                self._analyse_conversation(
                    cid, thresholds, stats, allow_cloud=allow_cloud
                )
            except LlmLocalFused as exc:
                # Локальные модели отказали N раз подряд: разговор остаётся
                # открытым и дождётся починки, облако не «подхватывает»
                # разбор (P11, находка 7 — симметрия с STT-предохранителем).
                stats.stopped_by = "llm-fused"
                log.error("разбор остановлен: %s", exc)
                break
            except Exception:
                log.exception("разговор %d не должен ронять батч", cid)
            touched.add(datetime.fromtimestamp(row["started_at"]).date())
        return touched

    def _analyse_conversation(
        self, cid: int, thresholds: Thresholds, stats: BatchStats, *, allow_cloud: bool
    ) -> None:
        settings = self.settings
        # Фильтру качества и счётчикам нужны все реплики, включая приватные;
        # дальше приватные отсеиваются: до модели, фактов и заметок они
        # не доедут (P11.2, находка 5).
        rows = self.storage.utterances_of(
            cid, include_rejected=True, include_private=True
        )

        # Фильтр новый на каждый разговор: правило B8 «дубль подряд» работает
        # только на хронологии, поэтому качество считается здесь, а не на
        # такте 1, где порядок расшифровки обратный.
        # B4 «не тот язык»: при auto ожидаемый язык — язык самого разговора.
        expected = whisper_language(settings.stt_language)
        if expected is None:
            expected = dominant_language(
                (row.language, row.text) for row in rows if not row.reject_reason
            )
        utterance_filter = QualityFilter(thresholds, expected_language=expected)
        utterance_filter.reset()
        accepted_rows = []
        accepted = rejected = accepted_chars = 0
        speech_seconds = 0.0
        for row in rows:  # utterances_of отдаёт хронологически
            verdict = utterance_filter.check(
                row.text,
                speech_seconds=row.speech_seconds,
                language=row.language,
                started_at=row.started_at,
                ended_at=row.ended_at,
            )
            self.storage.mark_quality(
                row.id, reason=verdict.reason, enforce=settings.quality_enforce
            )
            if not verdict.accepted:
                rejected += 1
                continue
            accepted += 1
            if row.is_private:
                # Принятая приватная реплика остаётся в базе и в счётчиках,
                # но в транскрипт для NLU и в time_map не попадает.
                continue
            accepted_chars += len(row.text or "")
            speech_seconds += row.speech_seconds
            accepted_rows.append(row)
        stats.accepted += accepted
        stats.rejected += rejected

        conversation_verdict = check_conversation(
            # Вердикт разговора — по видимым репликам: если приватные
            # закрыли собой весь разговор, он уходит в thin сам собой.
            accepted=len(accepted_rows),
            rejected=rejected,
            accepted_chars=accepted_chars,
            speech_seconds=speech_seconds,
            thresholds=thresholds,
        )
        if conversation_verdict.level != "ok":
            self.storage.close_conversation(cid, status=conversation_verdict.level)
            stats.skipped += 1
            return

        transcript = "\n".join(
            f"[{_hhmm(row.started_at)}] {row.text}" for row in accepted_rows if row.text
        )
        # Карта «ЧЧ:ММ → абсолютное время» по тем же принятым репликам, из
        # которых собран транскрипт: факт с таймкодом получает точное said_at.
        # При одинаковых ЧЧ:ММ остаётся время первой реплики.
        time_map: dict[str, float] = {}
        for row in accepted_rows:
            time_map.setdefault(_hhmm(row.started_at), row.started_at)
        # FR-072 (P11, находка 3): хоть одна приватная реплика в разговоре —
        # разбор идёт ТОЛЬКО локально, независимо от allow_cloud. Решение
        # принимается здесь, до вызова: роутер получает уже готовый запрет,
        # а не угадывает сам.
        if any(row.is_private for row in rows):
            allow_cloud = False
        try:
            # дата записи — в промпт: иначе модель додумывает сроки «завтра» от потолка
            day = datetime.fromtimestamp(accepted_rows[0].started_at).date()
            result = NluProcessor(
                self.llm_router,
                max_chars=settings.nlu_max_chars,
                user_role=settings.user_role,
                user_vocab=settings.user_vocab,
            ).analyse(transcript, allow_cloud=allow_cloud, day=day)
        except NluError as exc:
            # Расшифровка остаётся: сбой модели не стоит нам транскрипта.
            log.error("разбор разговора %d не удался: %s", cid, exc)
            self.storage.close_conversation(cid, status="nlu_failed")
            stats.nlu_failed += 1
            return

        store_result(self.storage, cid, result, time_map=time_map)
        if result.kind:
            self.storage.add_tag(t("tags.kind", kind=result.kind), conversation_id=cid)
        for topic in result.topics:
            self.storage.add_tag(topic, conversation_id=cid)
        # Незнакомые имена не пишутся: [[Имя]] без заметки — битая ссылка в графе.
        for name in result.people:
            if self.storage.find_person(name) is not None:
                self.storage.add_tag(t("tags.person", name=name), conversation_id=cid)
        stats.analysed += 1

    def _export(
        self, touched: set[date], stats: BatchStats, *, with_transcript: bool = False
    ) -> None:
        """Такт 4: заметки дня и выгрузка задач. Ошибки не роняют батч."""
        for day in sorted(touched):
            try:
                result = self._export_day_note(day, with_transcript=with_transcript)
                if result["note"] is not None:
                    stats.notes += 1
                if result["raw"] is not None:
                    stats.raw_notes += 1
            except Exception:
                log.exception("заметка за %s не записана", day.isoformat())

        try:
            stats.exported_tasks += self._export_tasks()
        except Exception:
            log.exception("выгрузка задач не удалась")

        try:
            stats.expired_tasks += self._expire_tasks()
        except Exception:
            log.exception("снятие устаревших задач не удалось")

    def _expire_tasks(self, today: date | None = None) -> int:
        """Снимает застоявшиеся задачи в базе и уносит их строки во входящих вниз."""
        ids = self.storage.expire_tasks(
            today or date.today(), ttl_days=self.settings.task_ttl_days
        )
        if ids:
            archive_tasks(self.settings.tasks_note_path, ids)
        return len(ids)

    def _export_day_note(self, day: date, *, with_transcript: bool) -> dict:
        """Заметка суток и (по флагу) сырая расшифровка. Пути или ``None``."""
        views = self._day_views(day)
        if not views:
            # закрытых разговоров нет — пустой заметкой перезаписывать
            # день незачем
            return {"note": None, "raw": None, "tasks": 0}
        day_start = datetime.combine(day, dtime.min).timestamp()
        day_end = datetime.combine(day + timedelta(days=1), dtime.min).timestamp()
        counts = self.storage.quality_counts(start=day_start, end=day_end)
        # Помеченное, но пропущенное (flagged) реально прошло в конвейер —
        # считаем принятым; rejected как есть.
        accepted = counts["accepted"] + counts["flagged"]
        rejected = counts["rejected"]
        note = write_day_note(
            self.settings.vault_days_dir,
            day,
            views,
            accepted=accepted,
            rejected=rejected,
            flagged=counts["flagged"],
        )
        if self.settings.report_dir is not None:
            try:
                write_report(self.settings.report_dir, day, views)
            except Exception:
                # Отчёт — копия для человека: его сбой заметку не отменяет.
                log.exception("отчёт за %s не записан", day.isoformat())
        raw = None
        if with_transcript:
            raw = write_transcript_note(
                self.settings.vault_raw_dir,
                day,
                views,
                ttl_days=self.settings.raw_note_ttl_days,
            )
        return {"note": note, "raw": raw, "tasks": 0}

    def _export_tasks(self) -> int:
        """Выгружает невыгруженные задачи. Возвращает число записанных строк."""
        rows = self.storage.unexported_tasks()
        if not rows:
            return 0
        tasks: list[dict] = []
        for row in rows:
            task = dict(row)
            started = self.storage.conversation_started_at(task["conversation_id"])
            if started is not None:
                task["conversation_started_at"] = started
            tasks.append(task)
        fresh = append_tasks(self.settings.tasks_note_path, tasks)
        # Дедуп живёт в БД (tasks.exported_at): повторный прогон не пишет
        # задачи заново. Порядок строгий: сначала файл, потом пометка.
        self.storage.mark_tasks_exported([row["id"] for row in rows])
        return fresh

    def export_day(self, day: date, *, with_transcript: bool = False) -> dict:
        """Перевыгрузка суток из базы: заметка, расшифровка, новые задачи.

        Без расшифровки и NLU — для приёмки и для правки заметки после ручных
        исправлений в базе. Возвращает ``{"note", "raw", "tasks"}``.
        """
        result = self._export_day_note(day, with_transcript=with_transcript)
        try:
            result["tasks"] = self._export_tasks()
        except Exception:
            log.exception("выгрузка задач не удалась")
            result["tasks"] = 0
        return result

    def _day_views(self, day: date) -> list[ConversationView]:
        day_start = datetime.combine(day, dtime.min).timestamp()
        day_end = datetime.combine(day + timedelta(days=1), dtime.min).timestamp()
        views = []
        for row in self.storage.conversations_between(day_start, day_end):
            if row["status"] != "closed":
                continue
            cid = row["id"]
            # Метрики разговора по ТЗ: rejected — статус 'rejected' или причина,
            # accepted — остальные с непустым текстом.
            utterance_rows = self.storage.utterances_of(cid, include_rejected=True)

            def is_rejected(item) -> bool:
                return item.status == "rejected" or bool(item.reject_reason)

            rejected = sum(1 for item in utterance_rows if is_rejected(item))
            accepted = sum(
                1 for item in utterance_rows if not is_rejected(item) and item.text
            )
            topics: list[str] = []
            people: list[str] = []
            for tag in self.storage.tags_of(cid):
                name = person_from_tag(tag)
                if name is not None:
                    if name and name not in people:
                        people.append(name)
                elif tag not in topics:
                    topics.append(tag)
            views.append(
                ConversationView(
                    conversation_id=cid,
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    summary=row["summary"],
                    speech_seconds=row["speech_seconds"],
                    facts=[
                        FactView(text=r["text"], said_at=r["said_at"])
                        for r in self.storage.fact_rows_of(cid)
                    ],
                    tasks=[
                        {**dict(task), "conversation_started_at": row["started_at"]}
                        for task in self.storage.tasks_of(cid)
                    ],
                    topics=topics,
                    people=people,
                    # Говорящий сейчас не размечен — диаризация не в этом пакете.
                    utterances=[
                        (r.started_at, None, r.text)
                        for r in utterance_rows
                        if r.status != "rejected"
                    ],
                    accepted=accepted,
                    rejected=rejected,
                )
            )
        return views
