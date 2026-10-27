"""Сквозная проверка на живом облаке: приём → VAD → Groq.

Требует сети и ключа, поэтому по умолчанию пропускается. Запуск::

    UZEL_RUN_INTEGRATION=1 pytest tests/test_integration_stt.py -v

Это дымовая проверка тракта, а НЕ замер WER: фикстура синтезирована TTS.
Настоящий замер (критерий A1) делается на записи с нагрудного микрофона —
см. tests/wer/README.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from uzel.audio import read_wav
from uzel.config import Settings
from uzel.stt.router import build_router
from uzel.vad import SileroVAD, detect_speech

FIXTURE = Path(__file__).parent / "fixtures" / "речь-ru-16k.wav"

pytestmark = pytest.mark.skipif(
    os.environ.get("UZEL_RUN_INTEGRATION") != "1",
    reason="нужны сеть и ключ Groq: UZEL_RUN_INTEGRATION=1",
)


@pytest.fixture
def live_settings() -> Settings:
    settings = Settings()
    if not settings.groq_api_key:
        pytest.skip("не задан UZEL_GROQ_API_KEY")
    return settings


def test_vad_drops_silence_before_stt(live_settings):
    """FR-010: тишина не должна доходить до расшифровки."""
    samples, sr = read_wav(FIXTURE, expected_sr=16000)
    padded = np.concatenate(
        [samples, np.zeros(5 * sr, dtype=np.float32), samples]
    )  # 5 секунд тишины между двумя фразами

    vad = SileroVAD(live_settings.vad_model_path)
    segments = detect_speech(padded, sr, vad=vad)
    speech = sum(segment.duration for segment in segments)

    assert len(segments) >= 2
    assert speech < 0.75 * (len(padded) / sr)  # тишина отрезана


def test_groq_transcribes_russian_speech(live_settings):
    samples, sr = read_wav(FIXTURE, expected_sr=16000)
    groq = next(b for b in build_router(live_settings).backends if b.name == "groq")

    result = groq.transcribe(samples, sr, live_settings.stt_language)

    assert result.backend == "groq"
    assert result.audio_seconds == pytest.approx(len(samples) / sr, abs=0.1)
    assert result.latency_s > 0
    text = result.text.lower()
    assert "акт" in text and "скрытых работ" in text
