"""Воркер конвейера: дешёвый проход, ночной батч, восстановление (пакет P3).

Без сети, без GPU, без настоящих моделей: VAD и роутеры — подделки с тем же
контрактом. Настоящий VAD и Opus — только в ``test_worker_e2e.py``.
"""

from __future__ import annotations

import itertools
import json
import math
from datetime import datetime

import numpy as np
import pytest
from uzel.audio import float32_to_pcm16, write_wav
from uzel.config import Settings
from uzel.llm import LlmBackend, LlmError, LlmResult, LlmRouter
from uzel.nlu import NluResult, NluTask, store_result
from uzel.privacy import build_gate
from uzel.queue import Chunk, DiskQueue
from uzel.storage import Storage
from uzel.stt.base import LocalBackendFused, TranscriptionError, TranscriptionResult
from uzel.vector import NullIndex
from uzel.worker import UndecodableChunk, Worker, decode_chunk

# Фиксированное «время приёма»: детерминизм, на нём стоит защита от дублей.
BASE_NS = int(datetime(2026, 9, 17, 12, 0, 0).timestamp() * 1e9)
BASE_S = BASE_NS / 1e9

GOOD_PAYLOAD = {
    "summary": "Обсудили заявку ООО «Ромашка» и сроки поставки.",
    "facts": ["Заявка подаётся 30 августа"],
    "tasks": [{"text": "подготовить КП", "assignee": "Иван", "due": "2026-08-30"}],
    "topics": ["закупки"],
    "people": ["Иван"],
}

_counter = itertools.count()


# --- подделки ---


class FakeVad:
    """Тот же контракт, что у SileroVAD: сплошная речь или сплошная тишина."""

    def __init__(self, prob: float = 1.0) -> None:
        self.prob = prob

    @property
    def window_seconds(self) -> float:
        return 512 / 16000

    def reset(self) -> None:
        pass

    def window_probabilities(self, samples: np.ndarray) -> np.ndarray:
        windows = math.ceil(len(samples) / 512)
        return np.full(windows, self.prob, dtype=np.float32)


class FakeStt:
    """Роутер STT без модели: пишет вызовы, умеет бросать заданное исключение."""

    def __init__(
        self, text="Пётр подаёт заявку на тридцатое", error=None, on_call=None
    ):
        self.text = text
        self.error = error
        self.on_call = on_call
        self.calls: list[dict] = []

    def transcribe(self, samples, sample_rate, *, language, allow_cloud):
        self.calls.append(
            {"samples": len(samples), "language": language, "allow_cloud": allow_cloud}
        )
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return TranscriptionResult(
            text=self.text,
            language=language,
            backend="fake",
            audio_seconds=len(samples) / sample_rate,
            latency_s=0.001,
        )


class RepeatLlm(LlmBackend):
    """Всегда один и тот же JSON: цикл тестов может звать сколько угодно раз."""

    name = "fake"
    is_cloud = False

    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(
        self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
    ) -> LlmResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LlmResult(
            text=json.dumps(self.payload),
            backend=self.name,
            model="fake",
            latency_s=0.0,
        )


class FakeClock:
    """Последовательность моментов; последний повторяется до бесконечности."""

    def __init__(self, *moments: float) -> None:
        self._moments = list(moments)
        self._step = 0

    def __call__(self) -> float:
        value = self._moments[min(self._step, len(self._moments) - 1)]
        self._step += 1
        return value


class RecordingIndex:
    """Индекс-подделка: пишет вызовы upsert — тест смотрит, кто в него попал."""

    def __init__(self) -> None:
        self.upserts: list[int] = []

    def upsert(self, utterance_id, text, payload) -> bool:
        self.upserts.append(utterance_id)
        return True


class PromptLlm(LlmBackend):
    """Пишет промпты целиком: тест проверяет, что приватное до модели не доехало."""

    name = "fake"
    is_cloud = False

    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[list[dict]] = []

    def available(self) -> bool:
        return True

    def complete(self, messages, **kwargs) -> LlmResult:
        self.prompts.append(list(messages))
        return LlmResult(
            text=json.dumps(self.payload),
            backend=self.name,
            model="fake",
            latency_s=0.0,
        )


# --- помощники ---


def speech(seconds: float = 1.0) -> np.ndarray:
    """Синтетический звук: содержимое не важно, VAD подделан."""
    return np.zeros(int(seconds * 16000), dtype=np.float32)


def put_chunk(
    settings: Settings,
    samples: np.ndarray,
    *,
    encoding: str = "pcm_s16le",
    sample_rate: int = 16000,
    received_ns: int | None = None,
    uid: str = "dev-1",
):
    queue = DiskQueue(settings.queue_dir)
    return queue.put(
        float32_to_pcm16(samples),
        uid=uid,
        source="test",
        sample_rate=sample_rate,
        encoding=encoding,
        received_ns=received_ns if received_ns is not None else BASE_NS,
    )


def make_worker(
    settings: Settings,
    *,
    vad=None,
    stt=None,
    llm=None,
    clock=None,
    index=None,
    **kwargs,
):
    if llm is None:
        llm = RepeatLlm(GOOD_PAYLOAD)
    if isinstance(llm, LlmBackend):
        llm = LlmRouter([llm])
    return Worker(
        settings,
        vad=vad if vad is not None else FakeVad(),
        stt_router=stt if stt is not None else FakeStt(),
        llm_router=llm,
        index=index if index is not None else NullIndex(),
        clock=clock if clock is not None else (lambda: BASE_S + 10_000),
        **kwargs,
    )


def add_pending(
    settings: Settings,
    storage: Storage,
    *,
    started_at: float,
    seconds: float = 1.0,
    uid: str = "dev-1",
    is_private: bool = False,
) -> int:
    """Реплика прямо в базе, как после дешёвого прохода."""
    path = settings.audio_dir / f"utt-{next(_counter):04d}.wav"
    write_wav(path, speech(seconds), 16000)
    return storage.add_utterance(
        uid=uid,
        started_at=started_at,
        ended_at=started_at + seconds,
        speech_seconds=seconds,
        is_private=is_private,
        audio_path=str(path),
    )


def relaxed(settings: Settings, **extra) -> Settings:
    """Пороги разговора, которые проходит короткий тестовый материал.

    Сквозная проверка тестирует провод, а не пороги: 4 секунды речи фильтр
    уровня разговора по умолчанию обязан отсеивать, и это правильно.
    """
    return settings.model_copy(
        update={
            "quality_conversation_min_chars": 1,
            "quality_conversation_min_speech_s": 0.0,
            "quality_conversation_min_utterances": 1,
            **extra,
        }
    )


def utterance_count(settings: Settings) -> int:
    with Storage(settings.db_path) as storage:
        return storage.stats()["utterances"]


def done_chunk_count(settings: Settings) -> int:
    return len(list((settings.queue_dir / "done").glob("*.json")))


# --- decode_chunk ---


def test_decode_chunk_accepts_pcm(settings):
    meta = put_chunk(settings, speech(0.5))

    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )
    samples = decode_chunk(chunk, expected_sample_rate=16000)

    assert samples.dtype == np.float32
    assert len(samples) == 8000


def test_decode_chunk_rejects_opus(settings):
    meta = put_chunk(settings, speech(0.5), encoding="opus")

    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )
    with pytest.raises(UndecodableChunk):
        decode_chunk(chunk)


def test_decode_chunk_rejects_foreign_sample_rate(settings):
    meta = put_chunk(settings, speech(0.5), sample_rate=8000)

    chunk = Chunk(
        meta=meta, path=settings.queue_dir / "incoming" / f"{meta.chunk_id}.pcm"
    )
    with pytest.raises(UndecodableChunk):
        decode_chunk(chunk, expected_sample_rate=16000)


# --- каркас воркера ---


def test_worker_init_loads_nothing_heavy(settings):
    import sys

    before = set(sys.modules)
    worker = Worker(settings)
    try:
        fresh = set(sys.modules) - before
        assert "onnxruntime" not in fresh
        assert "faster_whisper" not in fresh
        assert "fastembed" not in fresh
    finally:
        worker.close()


def test_worker_settings_defaults(settings):
    assert settings.worker_batch_size == 8
    assert settings.worker_time_limit_s == 3600.0
    assert settings.worker_poll_interval_s == 5.0
    assert settings.worker_claim_limit == 32
    assert settings.worker_stop_file == settings.data_dir / "worker.stop"
    # Конвейер не пишет в корневой файл задач владельца.
    assert settings.tasks_note == r"Проекты\Узел\Входящее — задачи.md"


# --- дешёвый проход ---


def test_ingest_creates_utterances_and_completes_chunk(settings):
    put_chunk(settings, speech(1.0))

    with make_worker(settings) as worker:
        stats = worker.ingest_once()

    assert stats.chunks == 1
    assert stats.utterances >= 1
    assert utterance_count(settings) == stats.utterances
    assert DiskQueue(settings.queue_dir).pending_count() == 0
    assert done_chunk_count(settings) == 1


def test_ingest_silent_chunk_completes_without_utterances(settings):
    put_chunk(settings, speech(1.0))

    with make_worker(settings, vad=FakeVad(prob=0.0)) as worker:
        stats = worker.ingest_once()

    assert stats.silent == 1
    assert stats.utterances == 0
    assert DiskQueue(settings.queue_dir).pending_count() == 0
    assert done_chunk_count(settings) == 1


def test_ingest_undecodable_chunk_goes_to_done(settings):
    put_chunk(settings, speech(1.0), encoding="opus")

    with make_worker(settings) as worker:
        stats = worker.ingest_once()

    assert stats.undecodable == 1
    assert stats.utterances == 0
    # данные не теряются: лежат в done до TTL
    assert DiskQueue(settings.queue_dir).pending_count() == 0
    assert done_chunk_count(settings) == 1


def test_repeated_chunk_does_not_duplicate_utterances(settings):
    # «упали» между add_utterance и complete — фрагмент вернулся и обработан заново
    put_chunk(settings, speech(1.0), received_ns=BASE_NS)
    put_chunk(settings, speech(1.0), received_ns=BASE_NS)

    with make_worker(settings) as worker:
        stats = worker.ingest_once()

    assert stats.chunks == 2
    assert stats.duplicates == stats.utterances
    assert stats.utterances >= 1
    assert utterance_count(settings) == stats.utterances


def test_recover_returns_claimed_chunks_after_crash(settings):
    put_chunk(settings, speech(1.0))
    queue = DiskQueue(settings.queue_dir)
    claimed = queue.claim(1)  # упали между claim и complete
    assert len(claimed) == 1
    assert queue.pending_count() == 0

    with make_worker(settings) as worker:
        assert worker.recover() == 1
        stats = worker.ingest_once()

    assert stats.chunks == 1
    assert stats.duplicates == 0
    assert utterance_count(settings) == stats.utterances  # ровно один раз


def test_ingest_failure_returns_chunk_to_queue(settings):
    put_chunk(settings, speech(1.0))

    class ExplodingVad(FakeVad):
        def window_probabilities(self, samples):
            raise RuntimeError("VAD упал")

    with make_worker(settings, vad=ExplodingVad()) as worker:
        stats = worker.ingest_once()

    assert stats.failed == 1
    assert DiskQueue(settings.queue_dir).pending_count() == 1  # не потерялся
    assert utterance_count(settings) == 0


def test_utterance_captured_during_mute_is_private(settings):
    put_chunk(settings, speech(1.0))  # время захвата = BASE_S - 1
    gate = build_gate(settings)
    gate.mute(10, now=BASE_S - 30)  # заглушка захватывает момент записи

    with make_worker(settings, privacy=gate) as worker:
        worker.ingest_once()

    with Storage(settings.db_path) as storage:
        rows = storage.pending_utterances(limit=100)
        assert rows
        assert all(row.is_private for row in rows)


def test_run_ingest_stops_by_iterations(settings):
    put_chunk(settings, speech(1.0))

    with make_worker(settings) as worker:
        stats = worker.run_ingest(iterations=2, interval_s=0.0)

    assert stats.chunks == 1  # второй проход уже пуст
    assert DiskQueue(settings.queue_dir).pending_count() == 0


def test_run_ingest_stops_by_stop_file(settings):
    put_chunk(settings, speech(1.0))
    settings.worker_stop_file.write_text("стоп", encoding="utf-8")

    with make_worker(settings) as worker:
        stats = worker.run_ingest(interval_s=0.0)

    assert stats.chunks == 0  # стоп-файл был до первого прохода
    assert DiskQueue(settings.queue_dir).pending_count() == 1


# --- ночной батч: расшифровка ---


def test_batch_transcribes_in_batches_of_given_size(settings):
    with Storage(settings.db_path) as storage:
        for i in range(5):
            add_pending(settings, storage, started_at=BASE_S + i * 100)

    stt = FakeStt()
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=2, max_batches=1, close_after_s=0)

    assert stats.transcribed == 2
    assert stats.batches == 1
    assert stats.stopped_by is None
    with Storage(settings.db_path) as storage:
        assert len(storage.pending_utterances()) == 3


def test_batch_transcribes_newest_first(settings):
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S, seconds=1.0)
        add_pending(settings, storage, started_at=BASE_S + 100, seconds=2.0)
        add_pending(settings, storage, started_at=BASE_S + 200, seconds=3.0)

    stt = FakeStt()
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.transcribed == 3
    assert [call["samples"] for call in stt.calls] == [48000, 32000, 16000]


def test_stop_file_halts_batch_between_batches(settings):
    with Storage(settings.db_path) as storage:
        for i in range(5):
            add_pending(settings, storage, started_at=BASE_S + i * 100)

    def plant_stop_file():
        settings.worker_stop_file.write_text("стоп", encoding="utf-8")

    stt = FakeStt(on_call=plant_stop_file)
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=2, close_after_s=0)

    settings.worker_stop_file.unlink(missing_ok=True)
    assert stats.stopped_by == "stop-file"
    assert stats.transcribed == 2  # начатая пачка доведена до конца
    with Storage(settings.db_path) as storage:
        assert len(storage.pending_utterances()) == 3


def test_time_limit_halts_batch_between_batches(settings):
    with Storage(settings.db_path) as storage:
        for i in range(5):
            add_pending(settings, storage, started_at=BASE_S + i * 100)

    clock = FakeClock(0.0, 5.0, 100.0)
    with make_worker(settings, clock=clock) as worker:
        stats = worker.run_batch(batch_size=2, time_limit_s=10, close_after_s=0)

    assert stats.stopped_by == "time-limit"
    assert stats.transcribed == 2
    with Storage(settings.db_path) as storage:
        assert len(storage.pending_utterances()) == 3


def test_batch_purges_expired_audio_at_the_end(settings):
    """P11.2, находка 7: TTL исполняется конвейером, а не только `uzel purge`.

    Замерено 23.09: ~600 МБ звука в сутки — без очистки в батче он копился
    бессрочно. Очистка идёт по настоящим часам (TTL в сутках), поэтому
    «свежая» реплика — от time.time(), а «старая» — BASE_S (17.09).
    """
    import time as real_time
    from pathlib import Path

    settings = settings.model_copy(update={"audio_ttl_days": 7})
    fresh_at = real_time.time() - 60
    with Storage(settings.db_path) as storage:
        old = add_pending(settings, storage, started_at=BASE_S - 86_400)
        fresh = add_pending(settings, storage, started_at=fresh_at)
        old_path = Path(storage.get_utterance(old).audio_path)
        fresh_path = Path(storage.get_utterance(fresh).audio_path)
    old_chunk = put_chunk(settings, speech(), received_ns=BASE_NS - 86_400 * 10**9)
    queue = DiskQueue(settings.queue_dir)
    queue.complete(queue.claim(1)[0])
    fresh_chunk = put_chunk(settings, speech(), received_ns=real_time.time_ns())
    queue.complete(queue.claim(1)[0])

    clock = lambda: fresh_at + 10_000
    with make_worker(settings, clock=clock) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0, export=False)

    assert stats.transcribed == 2  # сначала расшифровка, потом уборка
    assert stats.purged_audio == 1
    assert stats.purged_chunks == 1
    assert not old_path.exists() and fresh_path.exists()
    done = settings.queue_dir / "done"
    assert not (done / f"{old_chunk.chunk_id}.pcm").exists()
    assert (done / f"{fresh_chunk.chunk_id}.pcm").exists()
    with Storage(settings.db_path) as storage:
        assert storage.get_utterance(old).audio_path is None
        assert storage.get_utterance(old).text  # транскрипт остался


def test_fuse_stops_batch_and_keeps_utterances_pending(settings):
    with Storage(settings.db_path) as storage:
        for i in range(3):
            add_pending(settings, storage, started_at=BASE_S + i * 100)

    stt = FakeStt(error=LocalBackendFused("предохранитель"))
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.stopped_by == "stt-fused"
    assert stats.transcribed == 0
    assert all(not call["allow_cloud"] for call in stt.calls)  # в облако не ушло
    with Storage(settings.db_path) as storage:
        assert len(storage.pending_utterances()) == 3  # ничего не потеряно


def test_transcription_error_marks_utterance_failed(settings):
    with Storage(settings.db_path) as storage:
        first = add_pending(settings, storage, started_at=BASE_S)
        second = add_pending(settings, storage, started_at=BASE_S + 100)
        # у второй реплики нет файла аудио — тоже путь в failed
        with storage.transaction() as conn:
            conn.execute("UPDATE utterances SET audio_path=NULL WHERE id=?", (second,))

    stt = FakeStt(error=TranscriptionError("бэкенд не справился"))
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.failed == 2  # ошибка бэкенда и отсутствующий файл
    assert stats.transcribed == 0
    with Storage(settings.db_path) as storage:
        # failed остаётся в базе для ретрая следующим прогоном (FR-022)
        assert storage.get_utterance(first).status == "failed"
        assert storage.get_utterance(second).status == "failed"


def test_private_utterance_never_goes_to_cloud(settings):
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S, seconds=1.0)
        add_pending(
            settings, storage, started_at=BASE_S + 100, seconds=2.0, is_private=True
        )

    stt = FakeStt()
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0, allow_cloud=True)

    assert stats.transcribed == 2
    by_size = {call["samples"]: call["allow_cloud"] for call in stt.calls}
    assert by_size[32000] is False  # приватная — только локально (FR-072)
    assert by_size[16000] is True  # обычной облако разрешено вызывающим


# --- приватные реплики не покидают базу (P11.2, находка 5) ---

PRIV_MARK = "секретныйприватныймаркер2026"


def test_batch_transcribes_private_but_skips_semantic_index(settings):
    """Приватная реплика расшифровывается как обычно, но в смысловой
    индекс (Qdrant) не уходит: текст остаётся только в SQLite."""
    with Storage(settings.db_path) as storage:
        public = add_pending(settings, storage, started_at=BASE_S, seconds=1.0)
        private = add_pending(
            settings, storage, started_at=BASE_S + 5, seconds=2.0, is_private=True
        )

    index = RecordingIndex()
    with make_worker(settings, index=index) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0, export=False)

    assert stats.transcribed == 2  # расшифрована, как и раньше
    with Storage(settings.db_path) as storage:
        assert storage.get_utterance(private).text  # транскрипт в базе есть
    assert index.upserts == [public]  # индекс видел только публичную


def test_private_text_never_reaches_nlu(settings):
    """Транскрипт для NLU собирается без приватных реплик: они не должны
    доехать до модели и высыпаться в факты и задачи."""
    settings = relaxed(settings)
    llm = PromptLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        public = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            public,
            text="обычная реплика про заявку и поставку кирпича",
            language="ru",
            backend="fake",
        )
        private = add_pending(
            settings, storage, started_at=BASE_S + 5, seconds=2.0, is_private=True
        )
        storage.set_transcript(
            private,
            text=f"приватная реплика {PRIV_MARK}",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0, export=False)

    assert stats.analysed == 1
    prompts = "\n".join(str(message) for call in llm.prompts for message in call)
    assert PRIV_MARK not in prompts
    assert "обычная реплика про заявку" in prompts



def test_user_role_from_settings_reaches_nlu_prompt(settings):
    settings = relaxed(settings, user_role="диспетчер склада")
    llm = PromptLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        utt = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(utt, text="реплика про отгрузку", language="ru", backend="fake")

    with make_worker(settings, llm=llm) as worker:
        worker.run_batch(batch_size=10, close_after_s=0, export=False)

    assert "Владелец микрофона: диспетчер склада." in llm.prompts[0][0]["content"]

def test_day_note_and_raw_transcript_exclude_private(settings, tmp_path):
    """Заметка дня и сырая расшифровка не содержат приватного текста."""
    settings = relaxed(settings, vault_dir=tmp_path / "vault", export_transcript=True)
    with Storage(settings.db_path) as storage:
        public = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            public,
            text="обычная реплика про заявку и поставку кирпича",
            language="ru",
            backend="fake",
        )
        private = add_pending(
            settings, storage, started_at=BASE_S + 5, seconds=2.0, is_private=True
        )
        storage.set_transcript(
            private,
            text=f"приватная реплика {PRIV_MARK}",
            language="ru",
            backend="fake",
        )

    with make_worker(settings) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    note = (settings.vault_days_dir / f"{day.isoformat()} — день.md").read_text(
        encoding="utf-8"
    )
    raw = (settings.vault_raw_dir / f"{day.isoformat()} — расшифровка.md").read_text(
        encoding="utf-8"
    )
    assert stats.raw_notes == 1
    assert PRIV_MARK not in note
    assert PRIV_MARK not in raw
    assert "обычная реплика про заявку" in raw  # публичная — на месте


# --- ночной батч: разговоры, качество, NLU, экспорт ---


def test_grouping_merges_by_gap_and_keeps_fresh_tail(settings):
    clock = FakeClock(BASE_S + 1000)
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S - 20000)
        add_pending(settings, storage, started_at=BASE_S - 19900)
        add_pending(settings, storage, started_at=BASE_S)
        add_pending(settings, storage, started_at=BASE_S + 10)

    with make_worker(settings, clock=clock) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=3600)

    # старая пара закрыта в разговор, свежая осталась хвостом
    assert stats.conversations == 1
    with Storage(settings.db_path) as storage:
        assert len(storage.ungrouped_utterances()) == 2


def test_thin_conversation_is_closed_without_nlu(settings):
    llm = RepeatLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        for i in range(2):
            uid = add_pending(settings, storage, started_at=BASE_S + i * 100)
            storage.set_transcript(uid, text="а", language="ru", backend="fake")

    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.skipped == 1
    assert llm.calls == 0  # NLU не звался
    with Storage(settings.db_path) as storage:
        assert len(storage.conversations_with_status("thin")) == 1


def test_noise_conversation_is_closed_without_nlu(settings):
    llm = RepeatLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        for i in range(4):
            uid = add_pending(settings, storage, started_at=BASE_S + i * 100)
            storage.set_transcript(uid, text="а", language="ru", backend="fake")
        good = add_pending(settings, storage, started_at=BASE_S + 400, seconds=2.0)
        storage.set_transcript(
            good,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    settings = settings.model_copy(
        update={
            # одна принятая из пяти при доле отказов 0.8 — это шум
            "quality_conversation_min_utterances": 1,
            "quality_conversation_min_chars": 1,
            "quality_conversation_min_speech_s": 0.0,
            "quality_conversation_noise_ratio": 0.7,
            "quality_conversation_noise_max_accepted": 10,
        }
    )
    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.skipped == 1
    assert llm.calls == 0
    with Storage(settings.db_path) as storage:
        assert len(storage.conversations_with_status("noise")) == 1


def test_nlu_failure_preserves_transcripts(settings):
    settings = relaxed(settings)
    llm = RepeatLlm(GOOD_PAYLOAD, error=LlmError("нет связи"))
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.nlu_failed == 1
    with Storage(settings.db_path) as storage:
        (row,) = storage.conversations_with_status("nlu_failed")
        texts = [r.text for r in storage.utterances_of(row["id"])]
        assert texts == ["Пётр подаёт заявку на поставку кирпича"]


def test_stop_file_halts_nlu_phase_between_conversations(settings):
    """Разбор моделью — самая долгая часть батча, стоп обязан работать и в ней."""
    settings = relaxed(settings)

    class StoppingLlm(RepeatLlm):
        def complete(
            self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
        ):
            settings.worker_stop_file.write_text("стоп", encoding="utf-8")
            return super().complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )

    llm = StoppingLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        # Разрыв больше conversation_gap_s — разговора получится два.
        for offset in (0.0, 5_000.0):
            uid = add_pending(
                settings, storage, started_at=BASE_S + offset, seconds=2.0
            )
            storage.set_transcript(
                uid,
                text="Пётр подаёт заявку на поставку кирпича",
                language="ru",
                backend="fake",
            )

    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    settings.worker_stop_file.unlink(missing_ok=True)
    assert stats.conversations == 2
    assert llm.calls == 1  # второй разговор до модели не дошёл
    assert stats.analysed == 1
    assert stats.stopped_by == "stop-file"
    with Storage(settings.db_path) as storage:
        assert len(storage.conversations_with_status("open")) == 1
        # разобранное не пропало: заметка за день записана (такт 4 отработал)
        assert stats.notes == 1


def test_time_limit_halts_nlu_phase_between_conversations(settings):
    settings = relaxed(settings)
    with Storage(settings.db_path) as storage:
        for offset in (0.0, 5_000.0):
            uid = add_pending(
                settings, storage, started_at=BASE_S + offset, seconds=2.0
            )
            storage.set_transcript(
                uid,
                text="Пётр подаёт заявку на поставку кирпича",
                language="ru",
                backend="fake",
            )

    # Часы двигает сам разбор: первый разговор «съедает» 1000 с при пределе 100.
    # Считать вызовы clock() нельзя — привязка к внутреннему устройству такта.
    class ShiftingClock:
        def __init__(self, value: float) -> None:
            self.value = value

        def __call__(self) -> float:
            return self.value

    clock = ShiftingClock(BASE_S + 10_000)

    class SlowLlm(RepeatLlm):
        def complete(
            self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
        ):
            clock.value += 1000.0
            return super().complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )

    llm = SlowLlm(GOOD_PAYLOAD)
    with make_worker(settings, llm=llm, clock=clock) as worker:
        stats = worker.run_batch(batch_size=10, time_limit_s=100, close_after_s=0)

    assert stats.conversations == 2
    assert stats.stopped_by == "time-limit"
    assert llm.calls == 1  # на второй разговор времени уже не осталось
    with Storage(settings.db_path) as storage:
        assert len(storage.conversations_with_status("open")) == 1


def test_export_writes_note_and_tasks_without_duplicates(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    llm = RepeatLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, llm=llm) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)
        again = worker.run_batch(batch_size=10, close_after_s=0)  # повторный прогон

    day = datetime.fromtimestamp(BASE_S).date()
    note = settings.vault_days_dir / f"{day.isoformat()} — день.md"
    assert note.exists()
    note_text = note.read_text(encoding="utf-8")
    assert "Обсудили заявку ООО «Ромашка»" in note_text
    assert "Заявка подаётся 30 августа" in note_text
    # дословных реплик в заметке дня нет (пункт 4.1 ТЗ 2026-08-24)
    assert "Пётр подаёт заявку на поставку кирпича" not in note_text
    assert not settings.vault_raw_dir.exists()  # расшифровка — только по флагу

    tasks_text = settings.tasks_note_path.read_text(encoding="utf-8")
    assert tasks_text.count("подготовить КП") == 1  # дублей нет
    assert "^uzel-" in tasks_text  # строка задачи несёт якорь
    assert again.exported_tasks == 0
    with Storage(settings.db_path) as storage:
        assert storage.unexported_tasks() == []


def test_day_note_has_no_utterance_text_from_db(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="уникальный дословный текст реплики про кирпич",
            language="ru",
            backend="fake",
        )

    with make_worker(settings) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    note = (settings.vault_days_dir / f"{day.isoformat()} — день.md").read_text(
        encoding="utf-8"
    )
    assert "уникальный дословный текст реплики" not in note


def test_fact_timecode_from_llm_lands_in_note(settings, tmp_path):
    """LLM отдаёт факт с «at», совпадающим с временем реплики — в заметке
    факт получает таймкод."""
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    payload = {
        **GOOD_PAYLOAD,
        "facts": [{"text": "Заявка подаётся 30 августа", "at": "12:00"}],
    }
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, llm=RepeatLlm(payload)) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    note = (settings.vault_days_dir / f"{day.isoformat()} — день.md").read_text(
        encoding="utf-8"
    )
    assert "- `[12:00]` Заявка подаётся 30 августа" in note


def test_only_known_people_reach_the_note(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    payload = {**GOOD_PAYLOAD, "people": ["Иван", "Незнакомец"]}
    with Storage(settings.db_path) as storage:
        storage.upsert_person("Иван")
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, llm=RepeatLlm(payload)) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    note = (settings.vault_days_dir / f"{day.isoformat()} — день.md").read_text(
        encoding="utf-8"
    )
    assert "[[Иван]]" in note
    assert "Незнакомец" not in note  # нет в people — нет битой ссылки в графе


def test_transcript_export_follows_the_flag(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault", export_transcript=True)
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    day = datetime.fromtimestamp(BASE_S).date()
    raw = settings.vault_raw_dir / f"{day.isoformat()} — расшифровка.md"
    assert stats.raw_notes == 1
    assert raw.exists()
    raw_text = raw.read_text(encoding="utf-8")
    assert "удалить_после" in raw_text
    assert "Пётр подаёт заявку на поставку кирпича" in raw_text


def test_export_day_rewrites_note_and_does_not_duplicate_tasks(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    day = datetime.fromtimestamp(BASE_S).date()
    with make_worker(settings) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)
        first = worker.export_day(day)
        second = worker.export_day(day)  # повторная перевыгрузка

    assert first["note"] == settings.vault_days_dir / f"{day.isoformat()} — день.md"
    assert first["note"].exists()
    assert first["raw"] is None  # без флага расшифровка не пишется
    # задачи уже выгружены батчем — перевыгрузка не создаёт их заново
    assert first["tasks"] == 0
    assert second["tasks"] == 0
    tasks_text = settings.tasks_note_path.read_text(encoding="utf-8")
    assert tasks_text.count("подготовить КП") == 1


def test_export_can_be_disabled(settings, tmp_path):
    settings = relaxed(settings, vault_dir=tmp_path / "vault")
    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0, export=False)

    assert stats.analysed == 1
    assert stats.notes == 0
    assert not settings.vault_days_dir.exists()
    assert not settings.tasks_note_path.exists()


# --- новые методы хранилища (задача 1 ТЗ) ---


def test_pending_utterances_order(settings):
    with Storage(settings.db_path) as storage:
        older = storage.add_utterance(
            uid="dev-1", started_at=100.0, ended_at=110.0, speech_seconds=1.0
        )
        newer = storage.add_utterance(
            uid="dev-1", started_at=200.0, ended_at=210.0, speech_seconds=1.0
        )

        assert [row.id for row in storage.pending_utterances()] == [older, newer]
        newest_first = [row.id for row in storage.pending_utterances(newest_first=True)]
        assert newest_first == [newer, older]


def test_find_utterance_by_exact_triple(settings):
    with Storage(settings.db_path) as storage:
        uid = storage.add_utterance(
            uid="dev-1", started_at=100.0, ended_at=110.0, speech_seconds=1.0
        )
        storage.add_utterance(
            uid="dev-1", started_at=100.5, ended_at=110.5, speech_seconds=1.0
        )

        found = storage.find_utterance(uid="dev-1", started_at=100.0, ended_at=110.0)
        assert found is not None and found.id == uid
        assert (
            storage.find_utterance(uid="dev-1", started_at=100.0, ended_at=111.0)
            is None
        )
        assert (
            storage.find_utterance(uid="dev-2", started_at=100.0, ended_at=110.0)
            is None
        )


def test_ungrouped_utterances_are_transcribed_and_free(settings):
    with Storage(settings.db_path) as storage:
        loose = storage.add_utterance(
            uid="dev-1", started_at=100.0, ended_at=110.0, speech_seconds=1.0
        )
        storage.set_transcript(loose, text="готово", language="ru", backend="fake")
        storage.add_utterance(  # ещё не расшифрована — не для группировки
            uid="dev-1", started_at=120.0, ended_at=130.0, speech_seconds=1.0
        )
        cid = storage.open_conversation(uid="dev-1", started_at=200.0)
        attached = storage.add_utterance(
            uid="dev-1", started_at=300.0, ended_at=310.0, speech_seconds=1.0
        )
        storage.attach_utterances(cid, [attached])
        storage.set_transcript(
            attached, text="в разговоре", language="ru", backend="fake"
        )

        rows = storage.ungrouped_utterances()
        assert [row.id for row in rows] == [loose]  # pending и прикреплённые не её


def test_conversations_with_status(settings):
    with Storage(settings.db_path) as storage:
        first = storage.open_conversation(uid="dev-1", started_at=100.0)
        second = storage.open_conversation(uid="dev-1", started_at=200.0)
        storage.close_conversation(first, status="thin")

        open_ids = [row["id"] for row in storage.conversations_with_status("open")]
        thin_ids = [row["id"] for row in storage.conversations_with_status("thin")]
        assert open_ids == [second]
        assert thin_ids == [first]


# --- идемпотентность разбора (задача 2 ТЗ) ---


def test_store_result_skips_known_facts(tmp_path):
    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=["факт один", "факт два"])

        store_result(storage, cid, result)
        store_result(storage, cid, result)  # повторный прогон после обрыва

        assert storage.facts_of(cid) == ["факт один", "факт два"]


def test_store_result_skips_known_tasks(tmp_path):
    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(
            summary="с",
            tasks=[NluTask("дело", assignee="Иван", due="2026-09-30")],
        )

        store_result(storage, cid, result)
        store_result(storage, cid, result)

        tasks = storage.tasks_of(cid)
        assert len(tasks) == 1
        assert tasks[0]["text"] == "дело"


def test_store_result_still_writes_on_first_run(tmp_path):
    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=["факт"], tasks=[NluTask("новое дело")])

        store_result(storage, cid, result)

        assert storage.facts_of(cid) == ["факт"]
        assert len(storage.tasks_of(cid)) == 1
        row = storage.conversations_between(0, 1e12)[0]
        assert row["status"] == "closed"
        assert row["summary"] == "с"


# --- два такта батча: выгрузка моделей между расшифровкой и разбором (ТЗ P5) ---


def test_batch_unloads_stt_before_nlu_and_llm_after(settings):
    """VRAM 6 ГБ: whisper вон до разбора, локальная LLM — после."""
    settings = relaxed(settings)
    events = []

    class RecordingStt(FakeStt):
        def transcribe(self, samples, sample_rate, *, language, allow_cloud):
            events.append("stt")
            return super().transcribe(
                samples, sample_rate, language=language, allow_cloud=allow_cloud
            )

        def unload(self):
            events.append("stt-unload")
            return ["local"]

    class RecordingLlm(RepeatLlm):
        def complete(self, messages, **kwargs):
            events.append("nlu")
            return super().complete(messages, **kwargs)

        def unload(self):
            events.append("llm-unload")
            return True

    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        # транскрипт не записан: расшифровка идёт в этом же прогоне, и порядок
        # «stt → выгрузка stt → nlu → выгрузка llm» виден целиком

    with make_worker(
        settings, stt=RecordingStt(), llm=RecordingLlm(GOOD_PAYLOAD)
    ) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.analysed == 1
    assert stats.unloaded == 2  # whisper и LLM
    assert events.index("stt-unload") > events.index("stt")  # после расшифровки
    assert events.index("stt-unload") < events.index("nlu")  # до разбора
    assert events.index("llm-unload") > events.index("nlu")  # разбор закончился


def test_batch_unload_can_be_disabled(settings):
    settings = relaxed(settings, worker_unload_between_phases=False)
    events = []

    class RecordingStt(FakeStt):
        def transcribe(self, samples, sample_rate, *, language, allow_cloud):
            events.append("stt")
            return super().transcribe(
                samples, sample_rate, language=language, allow_cloud=allow_cloud
            )

        def unload(self):
            events.append("stt-unload")
            return ["local"]

    with Storage(settings.db_path) as storage:
        uid = add_pending(settings, storage, started_at=BASE_S, seconds=2.0)
        storage.set_transcript(
            uid,
            text="Пётр подаёт заявку на поставку кирпича",
            language="ru",
            backend="fake",
        )

    with make_worker(settings, stt=RecordingStt()) as worker:
        stats = worker.run_batch(batch_size=10, close_after_s=0)

    assert stats.analysed == 1
    assert stats.unloaded == 0
    assert "stt-unload" not in events


def test_release_skips_uncreated_routers(settings):
    """Выгрузка не имеет права создавать роутеры: это грузило бы модели зря."""
    settings = relaxed(settings)
    worker = Worker(settings, vad=FakeVad(), index=NullIndex())
    with worker:
        assert worker._release_stt() == 0
        assert worker._release_llm() == 0
        assert worker._stt_router is None  # свойство не дёргалось
        assert worker._llm_router is None


def test_worker_passes_nlu_max_chars_to_processor(settings):
    """Воркер обязан передавать настройку: куски считаются по ней, не по константе."""
    settings = relaxed(settings, nlu_max_chars=80)
    llm = RepeatLlm(GOOD_PAYLOAD)
    with Storage(settings.db_path) as storage:
        for i in range(3):
            uid = add_pending(
                settings, storage, started_at=BASE_S + i * 60, seconds=2.0
            )
            storage.set_transcript(
                uid,
                text=f"[12:0{i}] реплика номер {i} про заявку и поставку кирпича",
                language="ru",
                backend="fake",
            )

    with make_worker(settings, llm=llm) as worker:
        worker.run_batch(batch_size=10, close_after_s=0)

    # 3 куска + сведение пересказов; при константе 24000 был бы один вызов
    assert llm.calls == 4


def test_whisper_unload_clears_loaded_model():
    from uzel.stt.local_backend import LocalWhisperBackend

    backend = LocalWhisperBackend(model_size="tiny")
    assert backend.unload() is False  # нечего выгружать — не ошибка

    backend._model = object()
    assert backend.unload() is True
    assert backend._model is None
    assert backend.unload() is False


def test_whisper_unload_keeps_probe_cache():
    """Модель на диске не изменилась — пробный прогон пересматривать нечего."""
    from uzel.stt.local_backend import LocalWhisperBackend

    backend = LocalWhisperBackend(model_size="tiny")
    backend._probe_ok = True
    backend._model = object()

    backend.unload()

    assert backend.available() is True  # из кэша, без новой загрузки


def test_ingest_stops_when_decoder_missing(settings, monkeypatch):
    """Нет модуля декодера — фрагмент возвращается в очередь, проход встаёт.

    Поймано долгим прогоном 23.09.2026: `uzel.cmd` не клал `bridge` в
    PYTHONPATH, импорт `uzel_bridge.opus_decoder` падал, и каждый фрагмент с
    телефона уходил в `done` как «неразбираемый». Телефон получал 202, владелец
    не получал ничего, через TTL звук удалялся. Сломано окружение, а не данные —
    значит останавливаемся, а не выбрасываем.
    """
    import sys

    from uzel.opus_stream import OPUS_FRAMES_ENCODING, pack_frames
    from uzel.queue import DiskQueue as _Queue

    # Кладём фрагмент мимо put_chunk: тот ждёт массив сэмплов, а здесь нужен
    # валидный поток кадров Opus — иначе разбор упадёт на формате, раньше
    # импорта декодера, и проверять будет нечего.
    _Queue(settings.queue_dir).put(
        pack_frames([b"\xfc\xff\xfe"]),
        uid="dev-1",
        source="test",
        sample_rate=16000,
        encoding=OPUS_FRAMES_ENCODING,
        received_ns=BASE_NS,
    )
    # None в sys.modules заставляет импорт падать с ImportError — ровно то,
    # что происходит при отсутствующем пути в PYTHONPATH.
    monkeypatch.setitem(sys.modules, "uzel_bridge.opus_decoder", None)

    with make_worker(settings) as worker:
        stats = worker.ingest_once()

    assert stats.stopped_by == "decoder-unavailable"
    assert stats.undecodable == 0  # это НЕ «битые данные»
    assert stats.failed == 1
    # Данные целы и ждут починки окружения, а не лежат в done до TTL.
    assert DiskQueue(settings.queue_dir).pending_count() == 1
    assert done_chunk_count(settings) == 0


def test_launcher_puts_bridge_on_pythonpath():
    """Штатный запускатор обязан видеть `bridge`, иначе телефон пишет в никуда.

    Скрипты приёмки добавляли этот путь сами (`ops/проверка-p3.py:36`), поэтому
    дефект не проявлялся ни в одном прогоне — проверяли не тем путём, которым
    ходит владелец.
    """
    from pathlib import Path

    launcher = Path(__file__).resolve().parent.parent / "uzel.cmd"
    line = next(
        row
        for row in launcher.read_text(encoding="utf-8").splitlines()
        if row.startswith('set "PYTHONPATH=')
    )
    assert "bridge" in line, (
        "в PYTHONPATH запускатора нет bridge — декодер Opus отвалится"
    )
