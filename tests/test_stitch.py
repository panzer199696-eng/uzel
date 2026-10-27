"""Склейка реплик через стыки фрагментов перед расшифровкой.

Телефон шлёт звук порциями по 5 с, дешёвый проход режет фразу на стыке —
ночной батч обязан свести обрывки в одну реплику до whisper.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from uzel.audio import read_wav
from uzel.segments import plan_stitches
from uzel.storage import Storage

from test_worker import BASE_S, FakeStt, add_pending, make_worker


def row(start, end, uid="u", private=False):
    return SimpleNamespace(uid=uid, started_at=start, ended_at=end, is_private=private)


# --- чистая функция ---


def test_joint_and_short_pause_are_stitched():
    rows = [row(0, 5), row(5.1, 10), row(11.4, 14)]
    groups = plan_stitches(rows, gap_s=1.5, max_s=30)
    assert [[r.started_at for r in g] for g in groups] == [[0, 5.1, 11.4]]


def test_long_pause_splits():
    assert plan_stitches([row(0, 5), row(7, 9)], gap_s=1.5, max_s=30) == []


def test_private_never_mixes_with_normal():
    assert plan_stitches([row(0, 5), row(5, 9, private=True)], gap_s=1.5) == []


def test_different_devices_never_mix():
    assert plan_stitches([row(0, 5, uid="a"), row(5, 9, uid="b")], gap_s=1.5) == []


def test_group_respects_max_length():
    rows = [row(i * 5, i * 5 + 5) for i in range(8)]  # 40 с сплошной речи
    groups = plan_stitches(rows, gap_s=1.5, max_s=30)
    assert [len(g) for g in groups] == [6, 2]
    assert all(g[-1].ended_at - g[0].started_at <= 30 for g in groups)


def test_unsorted_input_is_handled():
    groups = plan_stitches([row(5, 10), row(0, 5)], gap_s=1.5)
    assert [r.started_at for r in groups[0]] == [0, 5]


# --- батч ---


def test_batch_stitches_before_transcription(settings):
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S, seconds=5.0)
        add_pending(settings, storage, started_at=BASE_S + 5.5, seconds=3.0)
        add_pending(settings, storage, started_at=BASE_S + 100, seconds=2.0)

    stt = FakeStt()
    with make_worker(settings, stt=stt) as worker:
        stats = worker.run_batch(close_after_s=0)

    assert stats.stitched == 1
    assert stats.transcribed == 2
    # склеенная реплика: 5 с + 0,5 с тишины + 3 с
    assert sorted(call["samples"] for call in stt.calls) == [32000, 136000]
    with Storage(settings.db_path) as storage:
        stats_db = storage.stats()
    assert stats_db["utterances"] == 2


def test_stitched_audio_and_bounds(settings):
    with Storage(settings.db_path) as storage:
        first = add_pending(settings, storage, started_at=BASE_S, seconds=5.0)
        second = add_pending(settings, storage, started_at=BASE_S + 5.0, seconds=2.0)
        old_paths = [storage.get_utterance(i).audio_path for i in (first, second)]

    with make_worker(settings, stt=FakeStt()) as worker:
        worker._stitch_pending(type("S", (), {"stitched": 0})())

    with Storage(settings.db_path) as storage:
        kept = storage.get_utterance(first)
        assert storage.get_utterance(second) is None
    assert kept.started_at == BASE_S
    assert kept.ended_at == BASE_S + 7.0
    assert kept.speech_seconds == 7.0
    samples, _ = read_wav(kept.audio_path)
    assert len(samples) == 7 * 16000

    assert not any(Path(p).exists() for p in old_paths)


def test_second_batch_does_not_restitch(settings):
    with Storage(settings.db_path) as storage:
        add_pending(settings, storage, started_at=BASE_S, seconds=5.0)
        add_pending(settings, storage, started_at=BASE_S + 5.0, seconds=5.0)

    with make_worker(settings, stt=FakeStt()) as worker:
        assert worker.run_batch(close_after_s=0).stitched == 1
    with make_worker(settings, stt=FakeStt()) as worker:
        assert worker.run_batch(close_after_s=0).stitched == 0


def test_stitch_can_be_disabled(settings):
    off = settings.model_copy(update={"stitch_max_s": 0})
    with Storage(off.db_path) as storage:
        add_pending(off, storage, started_at=BASE_S, seconds=5.0)
        add_pending(off, storage, started_at=BASE_S + 5.0, seconds=5.0)

    with make_worker(off, stt=FakeStt()) as worker:
        stats = worker.run_batch(close_after_s=0)
    assert stats.stitched == 0
    assert stats.transcribed == 2


def test_broken_audio_leaves_group_as_is(settings):
    with Storage(settings.db_path) as storage:
        first = add_pending(settings, storage, started_at=BASE_S, seconds=5.0)
        second = add_pending(settings, storage, started_at=BASE_S + 5.0, seconds=5.0)

        Path(storage.get_utterance(second).audio_path).unlink()

    with make_worker(settings, stt=FakeStt()) as worker:
        worker._stitch_pending(type("S", (), {"stitched": 0})())

    with Storage(settings.db_path) as storage:
        assert storage.get_utterance(first) is not None
        assert storage.get_utterance(second) is not None
