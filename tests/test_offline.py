"""Критерий A6 (specs/spec.md): цикл работает при выключенном интернете.

Логика доказательства: на сокеты ставится сторож — любое соединение куда-то,
кроме loopback, роняет тест. Под сторожем прогоняется полный цикл воркера
с поддельными локальными STT и LLM. Отдельный тест закрывает главную дыру
пакета P5: настройка только на шлюз при запрете облака не отправляет
наружу ни байта — разговор честно закрывается ``nlu_failed``.
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime

import httpx
import numpy as np
import pytest
from uzel.audio import float32_to_pcm16, write_wav
from uzel.config import Settings
from uzel.llm import LlmBackend, LlmResult, LlmRouter
from uzel.queue import DiskQueue
from uzel.stt.base import TranscriptionResult
from uzel.storage import Storage
from uzel.vector import NullIndex
from uzel.worker import Worker

BASE_NS = int(datetime(2026, 9, 19, 12, 0, 0).timestamp() * 1e9)

GOOD_PAYLOAD = {
    "summary": "Обсудили заявку ООО «Ромашка» и сроки поставки.",
    "facts": ["Заявка подаётся 30 августа"],
    "tasks": [{"text": "подготовить КП", "assignee": "Иван", "due": "2026-08-30"}],
    "topics": ["закупки"],
    "people": ["Иван"],
}

_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}

_counter = itertools.count()


# --- сторож сокетов ---


@pytest.fixture
def offline(monkeypatch):
    """Любое соединение вне loopback поднимает AssertionError."""
    import socket

    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def assert_local(address) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        if host not in _ALLOWED_HOSTS:
            raise AssertionError(f"ушли в сеть: {address}")

    def guarded_connect(self, address):
        assert_local(address)
        return real_connect(self, address)

    def guarded_create(address, *args, **kwargs):
        assert_local(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)


# --- подделки: всё локальное, сеть не нужна ---


class OfflineVad:
    """Сплошная речь: содержимое звука для проверки A6 не важно."""

    @property
    def window_seconds(self) -> float:
        return 512 / 16000

    def reset(self) -> None:
        pass

    def window_probabilities(self, samples: np.ndarray) -> np.ndarray:
        return np.full(math.ceil(len(samples) / 512), 1.0, dtype=np.float32)


class OfflineStt:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, samples, sample_rate, *, language, allow_cloud):
        self.calls += 1
        return TranscriptionResult(
            text="Пётр подаёт заявку на поставку кирпича марки М150",
            language=language,
            backend="fake",
            audio_seconds=len(samples) / sample_rate,
            latency_s=0.001,
        )


class OfflineLlm(LlmBackend):
    name = "fake"
    is_cloud = False

    def __init__(self, payload) -> None:
        import json

        self._text = json.dumps(payload)
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(
        self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
    ) -> LlmResult:
        self.calls += 1
        return LlmResult(
            text=self._text, backend=self.name, model="fake", latency_s=0.0
        )


# --- помощники ---


def offline_thresholds(**extra) -> dict:
    """Пороги разговора, которые проходит короткий тестовый материал."""
    return {
        "quality_conversation_min_chars": 1,
        "quality_conversation_min_speech_s": 0.0,
        "quality_conversation_min_utterances": 1,
        **extra,
    }


def add_transcribed_utterance(settings: Settings, storage: Storage, started_at: float):
    path = settings.audio_dir / f"utt-{next(_counter):04d}.wav"
    write_wav(path, np.zeros(32000, dtype=np.float32), 16000)
    uid = storage.add_utterance(
        uid="dev-1",
        started_at=started_at,
        ended_at=started_at + 2.0,
        speech_seconds=2.0,
        audio_path=str(path),
    )
    storage.set_transcript(
        uid,
        text="Пётр подаёт заявку на поставку кирпича",
        language="ru",
        backend="fake",
    )
    return uid


# --- сам критерий A6 ---


def test_full_cycle_runs_with_internet_cut_off(settings, offline):
    """Фрагмент в очереди → разговор с саммари, фактами, задачами и заметкой."""
    settings = settings.model_copy(update=offline_thresholds())

    # Две секунды: короче реплику бракует порог «символов в секунду» (B-правила).
    samples = np.zeros(32000, dtype=np.float32)
    DiskQueue(settings.queue_dir).put(
        float32_to_pcm16(samples),
        uid="dev-1",
        source="test",
        sample_rate=16000,
        encoding="pcm_s16le",
        received_ns=BASE_NS,
    )

    llm = OfflineLlm(GOOD_PAYLOAD)
    worker = Worker(
        settings,
        vad=OfflineVad(),
        stt_router=OfflineStt(),
        llm_router=LlmRouter([llm]),
        index=NullIndex(),
        clock=lambda: BASE_NS / 1e9 + 10_000,  # «сейчас» — позже реплики
    )
    with worker:
        ingest = worker.ingest_once()
        batch = worker.run_batch(close_after_s=0)

    assert ingest.utterances >= 1
    assert batch.transcribed >= 1
    assert batch.conversations == 1
    assert batch.analysed == 1
    assert batch.notes == 1
    assert batch.exported_tasks == 1

    day = datetime.fromtimestamp(BASE_NS / 1e9).date()
    note = settings.vault_days_dir / f"{day.isoformat()} — день.md"
    assert note.exists()
    note_text = note.read_text(encoding="utf-8")
    assert "Обсудили заявку ООО «Ромашка»" in note_text
    assert "Заявка подаётся 30 августа" in note_text

    with Storage(settings.db_path) as storage:
        (row,) = storage.conversations_with_status("closed")
        assert row["summary"].startswith("Обсудили")
        assert storage.facts_of(row["id"]) == ["Заявка подаётся 30 августа"]
        tasks = storage.tasks_of(row["id"])
        assert [t["text"] for t in tasks] == ["подготовить КП"]


def test_gateway_only_setup_leaks_nothing_when_cloud_forbidden(
    settings, offline, monkeypatch
):
    """Шлюз считается облаком: без разрешения наружу не уходит ни байта."""

    def no_http(*args, **kwargs):
        raise AssertionError("ушли в сеть: HTTP-запрос при запрещённом облаке")

    monkeypatch.setattr(httpx, "post", no_http)
    monkeypatch.setattr(httpx, "get", no_http)
    settings = settings.model_copy(
        update=offline_thresholds(
            llm_backends=("gateway",),
            gateway_api_key="ключ-для-теста",  # доступна — и всё равно не зовётся
        )
    )
    with Storage(settings.db_path) as storage:
        add_transcribed_utterance(settings, storage, started_at=BASE_NS / 1e9)

    worker = Worker(
        settings,
        vad=OfflineVad(),
        stt_router=OfflineStt(),
        index=NullIndex(),
        clock=lambda: BASE_NS / 1e9 + 10_000,  # «сейчас» — позже реплики
    )
    with worker:
        batch = worker.run_batch(close_after_s=0)

    assert batch.nlu_failed == 1
    with Storage(settings.db_path) as storage:
        (row,) = storage.conversations_with_status("nlu_failed")
        # расшифровка не потеряна: сбой модели не стоит нам транскрипта
        texts = [r.text for r in storage.utterances_of(row["id"])]
        assert texts == ["Пётр подаёт заявку на поставку кирпича"]
