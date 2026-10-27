"""Сквозной тест пакета P3: «Opus на входе → заметка в Obsidian».

Цепочка ровно та, что работает в бою: кадры Opus → протокол кулона → мост →
``POST /ingest`` → очередь → воркер (VAD → реплики → STT → разговор → NLU) →
заметка дня и файл задач. Настоящие VAD и Opus; подделки только на STT и LLM —
модели весом в гигабайты в тестах не грузим.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from uzel.app import create_app
from uzel.audio import read_wav
from uzel.config import Settings
from uzel.llm import LlmBackend, LlmResult, LlmRouter
from uzel.stt.base import TranscriptionResult
from uzel.storage import Storage
from uzel.vector import NullIndex
from uzel.worker import Worker
from uzel_bridge.opus_decoder import encode_opus_frames
from uzel_bridge.pipeline import AudioPipeline
from uzel_bridge.protocol import parse_packet

FIXTURE = Path(__file__).parent / "fixtures" / "речь-ru-16k.wav"

GOOD_PAYLOAD = {
    "summary": "Обсудили заявку ООО «Ромашка» и сроки поставки.",
    "facts": ["Заявка подаётся 30 августа"],
    "tasks": [{"text": "подготовить КП", "assignee": "Иван", "due": "2026-08-30"}],
    "topics": ["закупки"],
    "people": ["Иван"],
}


class HttpUploader:
    """Отправляет порцию PCM в приёмник ядра — как spool, но через TestClient."""

    def __init__(self, client: TestClient, *, uid: str = "dev-1") -> None:
        self.client = client
        self.uid = uid
        self.accepted = 0
        self.muted = 0

    def submit(self, payload: bytes, *, device_time_ms: int | None = None) -> bool:
        response = self.client.post(
            "/ingest",
            params={"uid": self.uid, "sample_rate": 16000, "source": "bridge-pc"},
            content=payload,
        )
        if response.status_code == 202:
            self.accepted += 1
            return True
        self.muted += 1  # 204 — приёмник в стоп-режиме, звук отброшен до записи
        return False


class FakeStt:
    """Осмысленный русский текст вместо whisper."""

    def transcribe(self, samples, sample_rate, *, language, allow_cloud):
        return TranscriptionResult(
            text="Пётр подаёт заявку на поставку кирпича марки М150",
            language=language,
            backend="fake",
            audio_seconds=len(samples) / sample_rate,
            latency_s=0.01,
        )


class FakeLlm(LlmBackend):
    name = "fake"
    is_cloud = False

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(
        self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
    ) -> LlmResult:
        self.calls += 1
        return LlmResult(
            text=json.dumps(self.payload), backend=self.name, model="fake",
            latency_s=0.0,
        )


@pytest.fixture
def pipeline_settings(settings, tmp_path):
    """Настройки сквозного теста: пороги разговора ослаблены, vault — в tmp.

    Тест проверяет провод, а не пороги: 4 секунды речи фильтр уровня разговора
    по умолчанию обязан отсеивать. Писать в настоящий Obsidian тест не имеет
    права.
    """
    return settings.model_copy(
        update={
            "quality_conversation_min_chars": 1,
            "quality_conversation_min_speech_s": 0.0,
            "quality_conversation_min_utterances": 1,
            "vault_dir": tmp_path / "vault",
        }
    )


def test_opus_to_obsidian_note(pipeline_settings):
    model_path = Settings().vad_model_path
    if not model_path.exists() or not FIXTURE.exists():
        pytest.skip("нет модели VAD или фикстуры с речью")

    # 1. Настоящая речь, 16 кГц.
    samples, sample_rate = read_wav(FIXTURE, expected_sr=16000)

    # 2–3. PCM → кадры Opus → пакеты протокола кулона (3 байта заголовка + кадр).
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    frames = encode_opus_frames(pcm16)
    packets = [
        parsed
        for index, frame in enumerate(frames)
        if (parsed := parse_packet(index.to_bytes(2, "little") + b"\x00" + frame))
        is not None
    ]
    assert packets

    # 4. Пакеты → мост → приёмник ядра: проверяется и декодер, и POST /ingest.
    with TestClient(create_app(pipeline_settings)) as client:
        uploader = HttpUploader(client)
        pipeline = AudioPipeline(uploader, flush_seconds=5.0)
        for packet in packets:
            pipeline.feed(packet)
        assert pipeline.flush() is True
    assert uploader.accepted == 1
    assert uploader.muted == 0

    # 5. Дешёвый проход: очередь → настоящий VAD → реплики в базе.
    from uzel.vad import SileroVAD

    llm = FakeLlm(GOOD_PAYLOAD)
    worker = Worker(
        pipeline_settings,
        vad=SileroVAD(pipeline_settings.vad_model_path, 16000),
        stt_router=FakeStt(),
        llm_router=LlmRouter([llm]),
        index=NullIndex(),
    )
    with worker, Storage(pipeline_settings.db_path) as storage:
        ingest = worker.ingest_once()
        assert ingest.utterances >= 1
        utterances_before = storage.stats()["utterances"]

        # 6. Ночной батч: расшифровка → разговор → NLU.
        batch = worker.run_batch(close_after_s=0)
        assert batch.transcribed >= 1
        assert batch.conversations == 1
        assert batch.analysed == 1
        assert batch.notes == 1
        assert batch.exported_tasks == 1
        assert llm.calls == 1

        # 7. Заметка дня и файл задач на месте.
        (conversation,) = storage.conversations_between(0, datetime.now().timestamp() + 3600)
        day = datetime.fromtimestamp(conversation["started_at"]).date()
        note = pipeline_settings.vault_days_dir / f"{day.isoformat()} — день.md"
        assert note.exists()
        note_text = note.read_text(encoding="utf-8")
        assert "Обсудили заявку ООО «Ромашка»" in note_text
        assert "Заявка подаётся 30 августа" in note_text
        # Дословных реплик в заметке дня нет (пункт 4.1 ТЗ 2026-08-24) —
        # они остаются в базе.
        assert "Пётр подаёт заявку на поставку кирпича" not in note_text

        tasks_text = pipeline_settings.tasks_note_path.read_text(encoding="utf-8")
        assert tasks_text.count("подготовить КП") == 1

        # 8. Повторный прогон не дублирует ни реплики, ни задачи.
        rerun_ingest = worker.ingest_once()
        rerun_batch = worker.run_batch(close_after_s=0)
        assert rerun_ingest.chunks == 0  # очередь пуста
        assert rerun_batch.transcribed == 0
        assert rerun_batch.exported_tasks == 0
        assert storage.stats()["utterances"] == utterances_before
        assert (
            pipeline_settings.tasks_note_path.read_text(encoding="utf-8").count(
                "подготовить КП"
            )
            == 1
        )
