"""Дисковая очередь принятых фрагментов звука.

Требования: FR-003 (мост копит и досылает), FR-004 (метки источника и времени),
NFR-004 (перезапуск не теряет очередь).

Устройство каталога::

    data/queue/
      incoming/    принято, ждёт обработки
      processing/  взято в работу (при падении возвращается в incoming)
      done/        обработано, ждёт удаления по TTL

Запись атомарна: сначала ``.pcm``, затем ``.json``. Наличие ``.json`` — признак
целой записи, поэтому недописанный фрагмент читателю не виден.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_STATES = ("incoming", "processing", "done")


@dataclass(frozen=True)
class ChunkMeta:
    """Метаданные фрагмента (FR-004)."""

    chunk_id: str
    uid: str
    source: str
    seq: int
    sample_rate: int
    encoding: str
    n_bytes: int
    received_ns: int
    device_time_ms: int | None = None
    # Фрагмент дослан из буфера моста, а не пришёл напрямую (P11): без
    # device_time_ms такой фрагмент помечается приватным — сколько он ждал
    # в буфере, неизвестно, а ошибаться надо в сторону приватности.
    spooled: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> ChunkMeta:
        return cls(**json.loads(raw))


@dataclass(frozen=True)
class Chunk:
    """Фрагмент, взятый в работу: метаданные и путь к данным."""

    meta: ChunkMeta
    path: Path

    def read(self) -> bytes:
        return self.path.read_bytes()


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class DiskQueue:
    """Очередь фрагментов на диске. Потокобезопасна."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        for state in _STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True)
        self._seq = self._max_seq() + 1

    # ---------- служебное ----------

    def _dir(self, state: str) -> Path:
        return self.root / state

    def _max_seq(self) -> int:
        best = -1
        for state in _STATES:
            for meta_path in self._dir(state).glob("*.json"):
                parts = meta_path.stem.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    best = max(best, int(parts[1]))
        return best

    @staticmethod
    def _make_chunk_id(received_ns: int, seq: int) -> str:
        # Время в имени даёт сортировку по приёму, seq снимает коллизии
        # (у времени на Windows грубое разрешение), токен — от совпадения имён.
        return f"{received_ns:019d}_{seq:08d}_{secrets.token_hex(4)}"

    # ---------- запись ----------

    def put(
        self,
        payload: bytes,
        *,
        uid: str,
        source: str,
        sample_rate: int,
        encoding: str = "pcm_s16le",
        device_time_ms: int | None = None,
        received_ns: int | None = None,
        spooled: bool = False,
    ) -> ChunkMeta:
        """Кладёт фрагмент в очередь. Возвращает его метаданные."""
        if not payload:
            raise ValueError("пустой фрагмент")
        if not uid:
            raise ValueError("uid обязателен")

        with self._lock:
            seq = self._seq
            self._seq += 1

        stamp = time.time_ns() if received_ns is None else received_ns
        chunk_id = self._make_chunk_id(stamp, seq)
        meta = ChunkMeta(
            chunk_id=chunk_id,
            uid=uid,
            source=source,
            seq=seq,
            sample_rate=sample_rate,
            encoding=encoding,
            n_bytes=len(payload),
            received_ns=stamp,
            device_time_ms=device_time_ms,
            spooled=spooled,
        )

        base = self._dir("incoming") / chunk_id
        _atomic_write(base.with_suffix(".pcm"), payload)
        _atomic_write(base.with_suffix(".json"), meta.to_json().encode("utf-8"))
        return meta

    # ---------- чтение ----------

    def recover(self) -> int:
        """Возвращает в очередь всё, что осталось в ``processing`` после падения."""
        moved = 0
        for meta_path in sorted(self._dir("processing").glob("*.json")):
            data_path = meta_path.with_suffix(".pcm")
            if not data_path.exists():
                log.warning("осиротевшие метаданные без данных: %s", meta_path.name)
                meta_path.unlink()
                continue
            os.replace(data_path, self._dir("incoming") / data_path.name)
            os.replace(meta_path, self._dir("incoming") / meta_path.name)
            moved += 1
        if moved:
            log.info("восстановлено фрагментов из processing: %d", moved)
        return moved

    def pending_count(self) -> int:
        return sum(1 for _ in self._dir("incoming").glob("*.json"))

    def claim(self, limit: int | None = None) -> list[Chunk]:
        """Берёт фрагменты в работу в порядке приёма, перенося их в ``processing``."""
        claimed: list[Chunk] = []
        with self._lock:
            for meta_path in sorted(self._dir("incoming").glob("*.json")):
                if limit is not None and len(claimed) >= limit:
                    break
                data_path = meta_path.with_suffix(".pcm")
                if not data_path.exists():
                    continue  # данные ещё не доехали, возьмём в следующий раз
                try:
                    meta = ChunkMeta.from_json(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, TypeError) as exc:
                    log.error("битые метаданные %s: %s", meta_path.name, exc)
                    continue
                target_data = self._dir("processing") / data_path.name
                target_meta = self._dir("processing") / meta_path.name
                try:
                    os.replace(data_path, target_data)
                    os.replace(meta_path, target_meta)
                except OSError as exc:  # забрал другой поток
                    log.debug("не удалось взять %s: %s", meta_path.name, exc)
                    continue
                claimed.append(Chunk(meta=meta, path=target_data))
        return claimed

    # ---------- завершение ----------

    def complete(self, chunk: Chunk) -> None:
        """Помечает фрагмент обработанным (переносит в ``done``)."""
        self._move_from_processing(chunk, "done")

    def fail(self, chunk: Chunk) -> None:
        """Возвращает фрагмент в очередь: обработка не удалась."""
        self._move_from_processing(chunk, "incoming")

    def _move_from_processing(self, chunk: Chunk, state: str) -> None:
        for suffix in (".pcm", ".json"):
            src = self._dir("processing") / f"{chunk.meta.chunk_id}{suffix}"
            if src.exists():
                os.replace(src, self._dir(state) / src.name)

    def purge_done(self, ttl_days: int) -> int:
        """Удаляет обработанное старше TTL (FR-042). Возвращает число удалённых."""
        if ttl_days <= 0:
            return 0
        cutoff_ns = time.time_ns() - ttl_days * 86_400 * 1_000_000_000
        removed = 0
        for meta_path in self._dir("done").glob("*.json"):
            try:
                received_ns = int(meta_path.stem.split("_")[0])
            except (ValueError, IndexError):
                continue
            if received_ns < cutoff_ns:
                meta_path.with_suffix(".pcm").unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                removed += 1
        if removed:
            log.info("удалено по TTL: %d фрагментов", removed)
        return removed
