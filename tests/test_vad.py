"""VAD: FR-010, FR-011.

Логика границ проверяется на синтетических вероятностях — без модели и без
звука, поэтому проверки детерминированы. Отдельно дымовая проверка, что
ONNX-модель грузится и отдаёт вероятности в допустимом диапазоне.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from uzel.audio import read_wav
from uzel.config import Settings
from uzel.vad import SileroVAD, detect_speech, segments_from_probabilities

WINDOW = 0.032  # 512 сэмплов при 16 кГц


def probs(*blocks: tuple[float, int]) -> np.ndarray:
    """Собирает массив вероятностей из пар (значение, число окон)."""
    return np.concatenate(
        [np.full(count, value, dtype=np.float32) for value, count in blocks]
    )


def test_silence_gives_no_segments():
    assert segments_from_probabilities(probs((0.05, 100)), WINDOW) == []


def test_empty_input_gives_no_segments():
    assert segments_from_probabilities(np.zeros(0), WINDOW) == []


def test_single_speech_block_is_detected():
    # 1 с тишины, 2 с речи, 1 с тишины
    result = segments_from_probabilities(
        probs((0.05, 31), (0.9, 62), (0.05, 31)), WINDOW, speech_pad_ms=0
    )

    assert len(result) == 1
    assert result[0].start == pytest.approx(0.99, abs=0.05)
    assert result[0].duration == pytest.approx(2.0, abs=0.1)


def test_short_blip_is_dropped():
    """FR-011: всплеск короче порога — шум, а не речь."""
    result = segments_from_probabilities(
        probs((0.05, 30), (0.9, 5), (0.05, 30)),  # 160 мс «речи»
        WINDOW,
        min_speech_ms=400,
    )

    assert result == []


def test_short_pause_inside_phrase_does_not_split_it():
    """Провал короче min_silence_ms не рвёт фразу надвое."""
    result = segments_from_probabilities(
        probs((0.9, 40), (0.05, 5), (0.9, 40)),  # пауза 160 мс
        WINDOW,
        min_silence_ms=400,
        speech_pad_ms=0,
    )

    assert len(result) == 1


def test_long_pause_splits_into_two_segments():
    result = segments_from_probabilities(
        probs((0.9, 40), (0.05, 60), (0.9, 40)),  # пауза почти 2 с
        WINDOW,
        min_silence_ms=400,
        speech_pad_ms=0,
    )

    assert len(result) == 2


def test_padding_extends_and_merges_close_segments():
    plain = segments_from_probabilities(
        probs((0.9, 40), (0.05, 20), (0.9, 40)),
        WINDOW,
        min_silence_ms=400,
        speech_pad_ms=0,
    )
    padded = segments_from_probabilities(
        probs((0.9, 40), (0.05, 20), (0.9, 40)),
        WINDOW,
        min_silence_ms=400,
        speech_pad_ms=400,
    )

    assert len(plain) == 2
    assert len(padded) == 1  # поля перекрыли паузу 640 мс


def test_padding_never_goes_below_zero_or_past_the_end():
    result = segments_from_probabilities(
        probs((0.9, 30)), WINDOW, speech_pad_ms=1000, total_seconds=30 * WINDOW
    )

    assert result[0].start == 0.0
    assert result[0].end == pytest.approx(30 * WINDOW)


def test_speech_at_the_very_end_is_closed():
    result = segments_from_probabilities(
        probs((0.05, 20), (0.9, 40)), WINDOW, speech_pad_ms=0
    )

    assert len(result) == 1
    assert result[0].end == pytest.approx(60 * WINDOW, abs=0.05)


def test_segment_slice_extracts_matching_audio():
    from uzel.vad import SpeechSegment

    samples = np.arange(16000, dtype=np.float32)
    piece = SpeechSegment(0.5, 1.0).slice(samples, 16000)

    assert len(piece) == 8000
    assert piece[0] == 8000.0


# --- дымовая проверка самой модели ---


def test_silero_model_loads_and_returns_valid_probabilities(tone):
    model_path = Settings().vad_model_path
    if not model_path.exists():
        pytest.skip(f"модель VAD не скачана: {model_path}")

    vad = SileroVAD(model_path)
    result = vad.window_probabilities(tone)

    # хвост дополняется тишиной, поэтому окон округлённо вверх
    assert len(result) == math.ceil(len(tone) / vad.window)
    assert result.min() >= 0.0 and result.max() <= 1.0
    assert vad.window_seconds == pytest.approx(0.032)
    # чистый синус — не речь: модель обязана дать низкую вероятность
    assert result.max() < 0.5


def test_silero_hears_real_speech():
    """Регрессия: модель v5 ждёт окно вместе с контекстом.

    Без 64 сэмплов предыдущего окна вероятность речи не поднимается выше 0,13
    даже на отчётливой речи — VAD «не слышит» ничего, а конвейер молча выдаёт
    пустой транскрипт. Проверять только на настоящей речи: синтетический сигнал
    этот дефект не ловит.
    """
    model_path = Settings().vad_model_path
    fixture = Path(__file__).parent / "fixtures" / "речь-ru-16k.wav"
    if not model_path.exists() or not fixture.exists():
        pytest.skip("нет модели VAD или фикстуры с речью")

    samples, sr = read_wav(fixture, expected_sr=16000)
    vad = SileroVAD(model_path)

    probabilities = vad.window_probabilities(samples)
    assert probabilities.max() > 0.9, "модель не распознала речь как речь"

    segments = detect_speech(samples, sr, vad=vad)
    speech = sum(segment.duration for segment in segments)
    assert segments, "на записи речи VAD не нашёл ни одного отрезка"
    assert speech > 0.7 * (len(samples) / sr)


def test_silero_context_survives_window_boundaries():
    """Контекст должен переноситься между окнами, а не обнуляться."""
    model_path = Settings().vad_model_path
    fixture = Path(__file__).parent / "fixtures" / "речь-ru-16k.wav"
    if not model_path.exists() or not fixture.exists():
        pytest.skip("нет модели VAD или фикстуры с речью")

    samples, _ = read_wav(fixture, expected_sr=16000)
    vad = SileroVAD(model_path)

    whole = vad.window_probabilities(samples)
    vad.reset()
    half = len(samples) // 2 // vad.window * vad.window
    piecewise = np.concatenate(
        [
            vad.window_probabilities(samples[:half]),
            vad.window_probabilities(samples[half:]),
        ]
    )

    assert np.allclose(whole, piecewise, atol=1e-4)


def test_silero_state_reset_makes_runs_reproducible(tone):
    model_path = Settings().vad_model_path
    if not model_path.exists():
        pytest.skip("модель VAD не скачана")

    vad = SileroVAD(model_path)
    first = vad.window_probabilities(tone)
    vad.reset()
    second = vad.window_probabilities(tone)

    assert np.allclose(first, second)


def test_silero_rejects_unsupported_sample_rate():
    model_path = Settings().vad_model_path
    if not model_path.exists():
        pytest.skip("модель VAD не скачана")

    with pytest.raises(ValueError):
        SileroVAD(model_path, sample_rate=44100)
