"""Локальный бэкенд STT: блокер CUDA, пробный прогон, фильтр сегментов.

Всё на моках — GPU и настоящая модель в тестах не трогаются, набор обязан
проходить на машине без видеокарты. Боевая проверка на железе живёт отдельно:
``ops/проверка-p1.py``.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from uzel.stt import local_backend
from uzel.stt.base import TranscriptionError
from uzel.stt.local_backend import LocalWhisperBackend, _prepare_cuda_path

# --- _prepare_cuda_path (задача 1) ---


def _pin_path(monkeypatch):
    """Заставляет monkeypatch восстановить PATH после теста."""
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))


def test_prepare_cuda_path_adds_existing_nvidia_bin(monkeypatch, tmp_path):
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    _pin_path(monkeypatch)
    monkeypatch.setattr(local_backend, "_site_roots", lambda: [tmp_path])

    added = _prepare_cuda_path()

    assert str(bin_dir) in added
    assert str(bin_dir) in os.environ["PATH"].split(os.pathsep)


def test_prepare_cuda_path_is_idempotent(monkeypatch, tmp_path):
    (tmp_path / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    _pin_path(monkeypatch)
    monkeypatch.setattr(local_backend, "_site_roots", lambda: [tmp_path])

    _prepare_cuda_path()
    length = len(os.environ["PATH"])

    assert _prepare_cuda_path() == []
    assert len(os.environ["PATH"]) == length


def test_prepare_cuda_path_skips_missing_dirs(monkeypatch, tmp_path):
    _pin_path(monkeypatch)
    # Корень есть, но каталогов nvidia/*/bin в нём нет.
    monkeypatch.setattr(local_backend, "_site_roots", lambda: [tmp_path])
    assert _prepare_cuda_path() == []

    # И корня нет — тоже молча ничего не добавляем.
    monkeypatch.setattr(local_backend, "_site_roots", lambda: [tmp_path / "нет"])
    assert _prepare_cuda_path() == []


# --- пробный прогон available() (задача 2) ---


class _FakeFeatureExtractor:
    nb_max_frames = 3000

    def __call__(self, audio):
        assert len(audio) == 16000
        return np.zeros((1, 3000, 80), dtype=np.float32)


class _FakeModel:
    def __init__(self, model_ref, device=None, compute_type=None):
        self.model_ref = model_ref
        self.encode_calls = 0
        self.feature_extractor = _FakeFeatureExtractor()

    def encode(self, features):
        self.encode_calls += 1


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    """Подменяет faster_whisper фабрикой моделей; возвращает созданные."""
    created: list[_FakeModel] = []

    def factory(model_ref, device=None, compute_type=None):
        model = _FakeModel(model_ref, device=device, compute_type=compute_type)
        created.append(model)
        return model

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return created


def test_available_runs_probe_and_caches_success(fake_faster_whisper):
    backend = LocalWhisperBackend()

    assert backend.available() is True
    assert backend.available() is True

    assert len(fake_faster_whisper) == 1  # модель грузилась ровно один раз
    assert fake_faster_whisper[0].encode_calls == 1  # прогон не повторяется
    assert backend.probe_error is None


def test_available_false_when_model_load_fails(monkeypatch):
    attempts: list[int] = []

    def broken_factory(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("cublas64_12.dll не найдена")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = broken_factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    backend = LocalWhisperBackend()

    assert backend.available() is False
    assert backend.available() is False  # второй вызов не грузит модель заново
    assert len(attempts) == 1
    assert backend.probe_error is not None
    assert "cublas" in backend.probe_error


def test_available_false_when_encode_fails(monkeypatch):
    class _BrokenOnEncode(_FakeModel):
        def encode(self, features):
            raise RuntimeError("CUDA error: out of memory")

    def factory(model_ref, device=None, compute_type=None):
        return _BrokenOnEncode(model_ref)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    backend = LocalWhisperBackend()

    assert backend.available() is False
    assert backend.probe_error is not None


def test_reset_probe_forces_new_attempt(monkeypatch):
    attempts: list[int] = []

    def broken_factory(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("нет DLL")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = broken_factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    backend = LocalWhisperBackend()
    backend.available()
    backend.reset_probe()
    backend.available()

    assert len(attempts) == 2


# --- разрешение модели по папке (задача 7) ---


def test_existing_model_dir_passes_folder_path(fake_faster_whisper, tmp_path):
    model_dir = tmp_path / "models-whisper"
    (model_dir / "large-v3").mkdir(parents=True)

    backend = LocalWhisperBackend(model_dir=model_dir)
    backend.available()

    assert fake_faster_whisper[0].model_ref == str(model_dir / "large-v3")


def test_missing_model_dir_falls_back_to_model_name(fake_faster_whisper, tmp_path):
    backend = LocalWhisperBackend(model_dir=tmp_path / "нет такой папки")
    backend.available()

    assert fake_faster_whisper[0].model_ref == backend._model_size


# --- расшифровка: параметры и фильтр сегментов (задачи 4 и 5) ---


def _segment(start, end, text, *, logprob=-0.2, ratio=1.1, no_speech=0.05):
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        avg_logprob=logprob,
        compression_ratio=ratio,
        no_speech_prob=no_speech,
    )


def _install_model(backend: LocalWhisperBackend, segments, transcribe_kwargs) -> None:
    def fake_transcribe(audio, **kwargs):
        transcribe_kwargs.update(kwargs)
        return iter(segments), SimpleNamespace(language="ru")

    backend._model = SimpleNamespace(transcribe=fake_transcribe)


def test_transcribe_drops_suspicious_segments_and_reports_metrics():
    segments = [
        _segment(0.0, 2.0, " поставим бетон завтра "),
        _segment(2.0, 4.0, "продолжение следует", no_speech=0.9),  # no_speech
        _segment(4.0, 6.0, "бред на тишина", logprob=-2.5),  # low_logprob
        _segment(6.0, 8.0, " повтор повтор повтор ", ratio=3.9),  # repetition
        _segment(8.0, 9.0, "   "),  # empty
    ]
    kwargs: dict = {}
    backend = LocalWhisperBackend()
    _install_model(backend, segments, kwargs)

    result = backend.transcribe(np.zeros(16000, dtype=np.float32), 16000, "ru")

    assert result.text == "поставим бетон завтра"
    raw = result.raw
    assert raw["segments_total"] == 5
    assert raw["segments_kept"] == 1
    assert raw["segments_dropped"] == 4
    assert raw["dropped_reasons"] == {
        "no_speech": 1,
        "low_logprob": 1,
        "repetition": 1,
        "empty": 1,
    }
    assert raw["avg_no_speech_prob"] == round((0.05 + 0.9 + 0.05 + 0.05 + 0.05) / 5, 4)
    assert raw["avg_logprob"] == round((-0.2 - 0.2 - 2.5 - 0.2 - 0.2) / 5, 4)
    assert raw["avg_compression_ratio"] == round((1.1 * 3 + 3.9 + 1.1) / 5, 4)
    assert raw["beam_size"] == 5


def test_transcribe_no_segments_averages_are_none():
    kwargs: dict = {}
    backend = LocalWhisperBackend()
    _install_model(backend, [], kwargs)

    result = backend.transcribe(np.zeros(16000, dtype=np.float32), 16000, "ru")

    assert result.text == ""
    assert result.raw["segments_total"] == 0
    assert result.raw["avg_logprob"] is None
    assert result.raw["dropped_reasons"] == {}


def test_transcribe_passes_antihallucination_parameters():
    kwargs: dict = {}
    backend = LocalWhisperBackend(
        beam_size=3,
        no_speech_threshold=0.5,
        logprob_threshold=-0.8,
        compression_threshold=2.0,
    )
    _install_model(backend, [_segment(0.0, 1.0, " текст ")], kwargs)

    backend.transcribe(np.zeros(16000, dtype=np.float32), 16000, "ru")

    assert kwargs["beam_size"] == 3
    assert kwargs["no_speech_threshold"] == 0.5
    assert kwargs["log_prob_threshold"] == -0.8
    assert kwargs["compression_ratio_threshold"] == 2.0
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["word_timestamps"] is True
    assert kwargs["hallucination_silence_threshold"] == 2.0
    assert kwargs["vad_filter"] is False
    assert kwargs["language"] == "ru"
    assert "initial_prompt" not in kwargs


def test_transcribe_rejects_wrong_sample_rate():
    backend = LocalWhisperBackend()

    with pytest.raises(TranscriptionError):
        backend.transcribe(np.zeros(48000, dtype=np.float32), 48000, "ru")


def test_thresholds_are_strict_boundaries():
    """Значение ровно на пороге — ещё не повод выбрасывать сегмент."""
    segments = [
        _segment(0.0, 1.0, "на границе", no_speech=0.6, logprob=-1.0, ratio=2.4),
    ]
    kwargs: dict = {}
    backend = LocalWhisperBackend(
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_threshold=2.4,
    )
    _install_model(backend, segments, kwargs)

    result = backend.transcribe(np.zeros(16000, dtype=np.float32), 16000, "ru")

    assert result.text == "на границе"
    assert result.raw["segments_dropped"] == 0


def test_model_dir_typing_accepts_path_or_none(tmp_path):
    """Конструктор принимает Path и None без приведения к строке."""
    backend = LocalWhisperBackend(model_dir=Path(tmp_path))
    assert backend._model_dir == Path(tmp_path)
    backend = LocalWhisperBackend(model_dir=None)
    assert backend._model_dir is None
