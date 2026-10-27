"""Роутер STT: FR-020, FR-021, FR-022, FR-072, A5."""

from __future__ import annotations

import numpy as np
import pytest
from uzel.stt.base import (
    AllBackendsFailed,
    CloudNotAllowed,
    LocalBackendFused,
    STTBackend,
    TranscriptionError,
    TranscriptionResult,
)
from uzel.stt.router import STTRouter, build_router

SAMPLES = np.zeros(16000, dtype=np.float32)


class FakeBackend(STTBackend):
    def __init__(self, name, *, is_cloud=False, ok=True, ready=True, boom=None,
                 text=None):
        self.name = name
        self.is_cloud = is_cloud
        self._ok = ok
        self._ready = ready
        self._boom = boom
        self._text = text
        self.calls = 0

    def available(self) -> bool:
        return self._ready

    def transcribe(self, samples, sample_rate, language):
        self.calls += 1
        if self._boom is not None:
            raise self._boom
        if not self._ok:
            raise TranscriptionError(f"{self.name} отказал")
        return TranscriptionResult(
            text=self._text if self._text is not None else f"текст от {self.name}",
            language=language,
            backend=self.name,
            audio_seconds=len(samples) / sample_rate,
            latency_s=0.0,
        )


# --- базовый порядок ---


def test_first_working_backend_wins():
    local = FakeBackend("local")
    cloud = FakeBackend("groq", is_cloud=True)

    result = STTRouter([local, cloud]).transcribe(SAMPLES, 16000)

    assert result.backend == "local"
    assert cloud.calls == 0


def test_fallback_between_two_local_backends_still_works():
    """Перебор между не-облачными бэкендами сохранён (FR-022)."""
    dead = FakeBackend("local-1", ok=False)
    alive = FakeBackend("local-2")

    result = STTRouter([dead, alive]).transcribe(SAMPLES, 16000)

    assert result.backend == "local-2"
    assert dead.calls == 1


def test_all_failures_raise_and_name_every_backend():
    router = STTRouter([FakeBackend("local", ok=False), FakeBackend("local-2", ok=False)])

    with pytest.raises(AllBackendsFailed) as err:
        router.transcribe(SAMPLES, 16000)

    assert "local" in str(err.value) and "local-2" in str(err.value)


# --- отказ локального не переливается в облако ---


def test_local_failure_does_not_spill_into_cloud():
    """FR-022 + приватность: отказ локального не открывает дорогу в Groq."""
    local = FakeBackend("local", ok=False)
    cloud = FakeBackend("groq", is_cloud=True)

    with pytest.raises(AllBackendsFailed) as err:
        STTRouter([local, cloud]).transcribe(SAMPLES, 16000, allow_cloud=True)

    assert local.calls == 1
    assert cloud.calls == 0
    assert "local" in str(err.value)
    assert "облако отсечено" in str(err.value)


def test_unconfigured_local_backend_blocks_cloud():
    local = FakeBackend("local", ready=False)
    cloud = FakeBackend("groq", is_cloud=True)

    with pytest.raises(AllBackendsFailed):
        STTRouter([local, cloud]).transcribe(SAMPLES, 16000, allow_cloud=True)

    assert local.calls == 0
    assert cloud.calls == 0


def test_unexpected_local_crash_blocks_cloud():
    local = FakeBackend("local", boom=MemoryError("нет памяти"))
    cloud = FakeBackend("groq", is_cloud=True)

    with pytest.raises(AllBackendsFailed):
        STTRouter([local, cloud]).transcribe(SAMPLES, 16000, allow_cloud=True)

    assert cloud.calls == 0


def test_default_call_never_touches_cloud():
    """Дефолт безопасен: без явного allow_cloud облако не зовётся."""
    local = FakeBackend("local")
    cloud = FakeBackend("groq", is_cloud=True)

    result = STTRouter([local, cloud]).transcribe(SAMPLES, 16000)

    assert result.backend == "local"
    assert cloud.calls == 0


def test_router_requires_at_least_one_backend():
    with pytest.raises(ValueError):
        STTRouter([])


# --- приватность (FR-072) ---


def test_private_segment_never_reaches_the_cloud():
    """FR-072: облако отсекается целиком, а не «по умолчанию»."""
    local = FakeBackend("local")
    cloud = FakeBackend("groq", is_cloud=True)

    result = STTRouter([local, cloud]).transcribe(SAMPLES, 16000, allow_cloud=False)

    assert result.backend == "local"
    assert cloud.calls == 0


def test_private_segment_fails_loudly_when_only_cloud_is_configured():
    cloud = FakeBackend("groq", is_cloud=True)

    with pytest.raises(CloudNotAllowed):
        STTRouter([cloud]).transcribe(SAMPLES, 16000, allow_cloud=False)

    assert cloud.calls == 0


def test_private_segment_is_not_sent_to_cloud_even_if_local_fails():
    local = FakeBackend("local", ok=False)
    cloud = FakeBackend("groq", is_cloud=True)

    with pytest.raises(AllBackendsFailed):
        STTRouter([local, cloud]).transcribe(SAMPLES, 16000, allow_cloud=False)

    assert cloud.calls == 0


def test_private_utterance_from_storage_stays_local(tmp_path):
    """A5: реплика с меткой приватности в базе расшифровывается только локально."""
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        utterance_id = storage.add_utterance(
            uid="seg-0001",
            started_at=1_000.0,
            ended_at=1_004.0,
            speech_seconds=4.0,
            is_private=True,
        )
        row = storage.get_utterance(utterance_id)
        assert row.is_private is True

        local = FakeBackend("local")
        cloud = FakeBackend("groq", is_cloud=True)
        router = STTRouter([local, cloud])

        result = router.transcribe(
            SAMPLES, 16000, language="ru", allow_cloud=not row.is_private
        )

        assert result.backend == "local"
        assert cloud.calls == 0


# --- предохранитель на N подряд отказов ---


def test_fuse_trips_after_streak_of_failures():
    local = FakeBackend("local", ok=False)
    cloud = FakeBackend("groq", is_cloud=True)
    router = STTRouter([local, cloud], failure_streak=3)

    for _ in range(3):
        with pytest.raises(AllBackendsFailed):
            router.transcribe(SAMPLES, 16000, allow_cloud=True)

    assert router.failures_in_a_row == 3
    calls = (local.calls, cloud.calls)

    with pytest.raises(LocalBackendFused) as err:
        router.transcribe(SAMPLES, 16000, allow_cloud=True)

    # Предохранитель — не повод уйти в облако: бэкенды не тронуты вообще.
    assert (local.calls, cloud.calls) == calls
    assert cloud.calls == 0
    assert "FR-022" in str(err.value)


def test_success_between_failures_resets_streak():
    local = FakeBackend("local", ok=False)
    router = STTRouter([local], failure_streak=3)

    for _ in range(2):
        with pytest.raises(AllBackendsFailed):
            router.transcribe(SAMPLES, 16000)
    assert router.failures_in_a_row == 2

    local._ok = True
    assert router.transcribe(SAMPLES, 16000).backend == "local"
    assert router.failures_in_a_row == 0

    local._ok = False
    for _ in range(3):
        with pytest.raises(AllBackendsFailed):
            router.transcribe(SAMPLES, 16000)
    with pytest.raises(LocalBackendFused):
        router.transcribe(SAMPLES, 16000)


def test_reset_fuse_clears_the_streak():
    local = FakeBackend("local", ok=False)
    router = STTRouter([local], failure_streak=2)

    for _ in range(2):
        with pytest.raises(AllBackendsFailed):
            router.transcribe(SAMPLES, 16000)
    with pytest.raises(LocalBackendFused):
        router.transcribe(SAMPLES, 16000)

    router.reset_fuse()
    assert router.failures_in_a_row == 0
    with pytest.raises(AllBackendsFailed):  # снова идёт к бэкенду, а не к предохранителю
        router.transcribe(SAMPLES, 16000)


# --- галлюцинации вырезаются после бэкенда ---


def test_router_strips_hallucination_without_counting_failure():
    backend = FakeBackend("local", text="Продолжение следует...")
    router = STTRouter([backend])

    result = router.transcribe(SAMPLES, 16000)

    assert result.text == ""
    assert result.raw["hallucination"] == "продолжение следует"
    assert router.failures_in_a_row == 0


def test_router_keeps_normal_text_untouched():
    backend = FakeBackend("local", text="ставим опалубку завтра")
    router = STTRouter([backend])

    result = router.transcribe(SAMPLES, 16000)

    assert result.text == "ставим опалубку завтра"
    assert "hallucination" not in result.raw


# --- сборка из настроек ---


def test_build_router_follows_configured_order(settings):
    """FR-021: локальный бэкенд идёт первым; облако — только явной настройкой."""
    assert [b.name for b in build_router(settings).backends] == ["local"]

    router = build_router(
        settings.model_copy(update={"stt_backends": ("local", "groq")})
    )
    assert [b.name for b in router.backends] == ["local", "groq"]
    assert router.backends[0].is_cloud is False


def test_build_router_passes_local_settings(settings):
    from uzel.stt.local_backend import LocalWhisperBackend

    settings = settings.model_copy(
        update={
            "local_beam_size": 7,
            "local_no_speech_threshold": 0.5,
            "local_logprob_threshold": -0.9,
            "local_compression_threshold": 2.1,
            "local_failure_streak": 5,
        }
    )

    router = build_router(settings)
    local = router.backends[0]

    assert isinstance(local, LocalWhisperBackend)
    assert local._beam_size == 7
    assert local._no_speech_threshold == 0.5
    assert local._logprob_threshold == -0.9
    assert local._compression_threshold == 2.1
    assert router._failure_streak == 5


def test_build_router_ignores_unknown_backend_names(settings):
    router = build_router(
        settings.model_copy(update={"stt_backends": ("local", "xxx")})
    )

    assert [b.name for b in router.backends] == ["local"]


def test_groq_backend_without_key_is_unavailable(settings):
    router = build_router(
        settings.model_copy(update={"stt_backends": ("local", "groq")})
    )
    groq = router.backends[1]

    assert groq.is_cloud is True
    assert groq.available() is False
