"""Язык распознавания: UZEL_STT_LANGUAGE=auto и короткие реплики (27.10.2026).

На 1–3 с whisper путает языки (русская реплика «да, давай» уходит в
украинский или болгарский). Поэтому в режиме auto язык определяется по
длинным репликам прогона, а короткие его наследуют.
"""

from __future__ import annotations

from test_worker import BASE_S, FakeStt, add_pending, make_worker
from uzel.storage import Storage
from uzel.stt.base import TranscriptionResult
from uzel.stt.language import MIN_DETECT_S, LanguagePicker, whisper_language


def test_whisper_language_maps_auto_to_none():
    assert whisper_language("auto") is None
    assert whisper_language("") is None
    assert whisper_language(None) is None
    assert whisper_language(" RU ") == "ru"


def test_fixed_language_is_always_used():
    picker = LanguagePicker("ru")
    assert picker.choose(1.0) == "ru"
    assert picker.choose(30.0) == "ru"
    picker.observe("en", probability=0.99, seconds=30.0)
    assert picker.choose(1.0) == "ru"


def test_auto_detects_long_and_lends_language_to_short():
    picker = LanguagePicker("auto")
    assert picker.choose(MIN_DETECT_S + 1) is None  # длинная — пусть whisper решает
    assert picker.choose(1.0) is None  # языка прогона ещё нет и подсказки нет

    picker.observe("en", probability=0.95, seconds=MIN_DETECT_S + 1)
    assert picker.choose(1.5) == "en"
    assert picker.choose(MIN_DETECT_S + 1) is None


def test_auto_ignores_unreliable_detections():
    picker = LanguagePicker("auto", seed="ru")
    picker.observe("uk", probability=0.95, seconds=1.0)  # короткая — не верим
    picker.observe("bg", probability=0.40, seconds=10.0)  # неуверенно — не верим
    assert picker.choose(1.0) == "ru"
    picker.observe("en", probability=None, seconds=10.0)  # бэкенд без вероятности
    assert picker.choose(1.0) == "en"


class DetectingStt(FakeStt):
    """Длинная реплика «распознаётся» английской, короткая — как попросили."""

    def transcribe(self, samples, sample_rate, *, language, allow_cloud):
        seconds = len(samples) / sample_rate
        self.calls.append({"seconds": seconds, "language": language})
        detected = language or ("en" if seconds >= MIN_DETECT_S else "uk")
        return TranscriptionResult(
            text="text",
            language=detected,
            backend="fake",
            audio_seconds=seconds,
            latency_s=0.0,
            raw={"language_probability": 0.97},
        )


def test_batch_in_auto_mode_lends_language_of_long_utterances(settings):
    stt = DetectingStt()
    with Storage(settings.db_path) as storage:
        short = add_pending(settings, storage, started_at=BASE_S + 100, seconds=1.0)
        long = add_pending(settings, storage, started_at=BASE_S, seconds=6.0)

    with make_worker(settings, stt=stt) as worker:
        worker.run_batch(export=False)

    # Длинная — первой и с автоопределением, короткая — с языком длинной.
    assert [round(c["seconds"]) for c in stt.calls[:2]] == [6, 1]
    assert stt.calls[0]["language"] is None
    assert stt.calls[1]["language"] == "en"
    with Storage(settings.db_path) as storage:
        assert storage.get_utterance(short).language == "en"
        assert storage.get_utterance(long).language == "en"


def test_batch_seeds_language_from_history(settings):
    stt = DetectingStt()
    with Storage(settings.db_path) as storage:
        old = add_pending(settings, storage, started_at=BASE_S - 1000, seconds=1.0)
        storage.set_transcript(old, text="старое", language="ru", backend="local")
        add_pending(settings, storage, started_at=BASE_S, seconds=1.0)

    with make_worker(settings, stt=stt) as worker:
        worker.run_batch(export=False)

    assert stt.calls[0]["language"] == "ru"


def test_batch_with_fixed_language_keeps_order_and_language(settings):
    settings = settings.model_copy(update={"stt_language": "ru"})
    stt = DetectingStt()
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S + 100, seconds=1.0)
        add_pending(settings, storage, started_at=BASE_S, seconds=6.0)

    with make_worker(settings, stt=stt) as worker:
        worker.run_batch(export=False)

    assert [round(c["seconds"]) for c in stt.calls[:2]] == [1, 6]  # как раньше: свежие первыми
    assert {c["language"] for c in stt.calls} == {"ru"}
