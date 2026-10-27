"""Дисковая очередь: FR-003, FR-004, FR-042, NFR-004."""

from __future__ import annotations

import time

import pytest
from uzel.queue import ChunkMeta, DiskQueue


@pytest.fixture
def queue(tmp_path) -> DiskQueue:
    return DiskQueue(tmp_path / "queue")


def test_put_creates_visible_record_with_metadata(queue):
    meta = queue.put(
        b"\x01\x02" * 100, uid="dev-1", source="bridge-pc", sample_rate=16000
    )

    assert queue.pending_count() == 1
    assert meta.uid == "dev-1"
    assert meta.source == "bridge-pc"
    assert meta.n_bytes == 200
    assert meta.encoding == "pcm_s16le"
    assert meta.received_ns > 0

    stored = ChunkMeta.from_json(
        (queue.root / "incoming" / f"{meta.chunk_id}.json").read_text(encoding="utf-8")
    )
    assert stored == meta


def test_put_rejects_empty_payload_and_missing_uid(queue):
    with pytest.raises(ValueError):
        queue.put(b"", uid="dev-1", source="test", sample_rate=16000)
    with pytest.raises(ValueError):
        queue.put(b"data", uid="", source="test", sample_rate=16000)


def test_claim_moves_to_processing_and_complete_moves_to_done(queue):
    meta = queue.put(b"abcd", uid="dev-1", source="test", sample_rate=16000)

    claimed = queue.claim()
    assert len(claimed) == 1
    assert claimed[0].meta.chunk_id == meta.chunk_id
    assert claimed[0].read() == b"abcd"
    assert queue.pending_count() == 0
    assert (queue.root / "processing" / f"{meta.chunk_id}.pcm").exists()

    queue.complete(claimed[0])
    assert (queue.root / "done" / f"{meta.chunk_id}.pcm").exists()
    assert not (queue.root / "processing" / f"{meta.chunk_id}.pcm").exists()


def test_fail_returns_chunk_to_queue(queue):
    queue.put(b"abcd", uid="dev-1", source="test", sample_rate=16000)
    chunk = queue.claim()[0]

    queue.fail(chunk)

    assert queue.pending_count() == 1
    assert len(queue.claim()) == 1


def test_claim_respects_limit_and_receive_order(queue):
    for i in range(5):
        queue.put(bytes([i]) * 4, uid="dev-1", source="test", sample_rate=16000)

    first = queue.claim(limit=2)
    assert [c.meta.seq for c in first] == [0, 1]

    rest = queue.claim()
    assert [c.meta.seq for c in rest] == [2, 3, 4]


def test_recover_returns_interrupted_work(queue):
    queue.put(b"abcd", uid="dev-1", source="test", sample_rate=16000)
    queue.claim()  # взяли в работу и «упали», не завершив
    assert queue.pending_count() == 0

    restored = queue.recover()

    assert restored == 1
    assert queue.pending_count() == 1


def test_queue_survives_restart_without_reusing_seq(tmp_path):
    root = tmp_path / "queue"
    first = DiskQueue(root)
    first.put(b"ab", uid="dev-1", source="test", sample_rate=16000)
    first.put(b"cd", uid="dev-1", source="test", sample_rate=16000)

    second = DiskQueue(root)  # перезапуск ядра
    meta = second.put(b"ef", uid="dev-1", source="test", sample_rate=16000)

    assert meta.seq == 2
    assert second.pending_count() == 3
    assert len({c.meta.chunk_id for c in second.claim()}) == 3


def test_incomplete_record_is_invisible(queue):
    """Данные без метаданных читателю не видны: запись ещё не завершена."""
    (queue.root / "incoming" / "0000000000000000001_00000000_dead.pcm").write_bytes(
        b"xx"
    )

    assert queue.pending_count() == 0
    assert queue.claim() == []


def test_purge_done_removes_only_expired(queue):
    fresh = queue.put(b"ab", uid="dev-1", source="test", sample_rate=16000)
    old_ns = time.time_ns() - 30 * 86_400 * 1_000_000_000
    old = queue.put(
        b"cd", uid="dev-1", source="test", sample_rate=16000, received_ns=old_ns
    )
    for chunk in queue.claim():
        queue.complete(chunk)

    removed = queue.purge_done(ttl_days=7)

    assert removed == 1
    assert (queue.root / "done" / f"{fresh.chunk_id}.pcm").exists()
    assert not (queue.root / "done" / f"{old.chunk_id}.pcm").exists()


def test_purge_done_disabled_when_ttl_not_positive(queue):
    queue.put(b"ab", uid="dev-1", source="test", sample_rate=16000)
    for chunk in queue.claim():
        queue.complete(chunk)

    assert queue.purge_done(ttl_days=0) == 0
