"""Буфер моста и отправка в ядро (FR-003, FR-062).

Мост не имеет права терять звук из-за того, что ядро недоступно: накопленное
ложится на диск и досылается по восстановлении, в исходном порядке.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpooledChunk:
    path: Path
    meta: dict

    def read(self) -> bytes:
        return self.path.read_bytes()


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Spool:
    """Очередь неотправленного на диске. Порядок — по времени накопления."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._seq = self._max_seq() + 1

    def _max_seq(self) -> int:
        best = -1
        for meta_path in self.root.glob("*.json"):
            parts = meta_path.stem.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                best = max(best, int(parts[1]))
        return best

    def add(self, payload: bytes, meta: dict | None = None) -> SpooledChunk:
        if not payload:
            raise ValueError("пустой фрагмент")
        stamp = time.time_ns()
        seq = self._seq
        self._seq += 1
        name = f"{stamp:019d}_{seq:08d}_{secrets.token_hex(4)}"

        full_meta = {
            "spooled_ns": stamp,
            "seq": seq,
            "n_bytes": len(payload),
            **(meta or {}),
        }
        base = self.root / name
        _atomic_write(base.with_suffix(".pcm"), payload)
        _atomic_write(
            base.with_suffix(".json"),
            json.dumps(full_meta, ensure_ascii=False).encode("utf-8"),
        )
        return SpooledChunk(path=base.with_suffix(".pcm"), meta=full_meta)

    def pending(self, limit: int | None = None) -> list[SpooledChunk]:
        result: list[SpooledChunk] = []
        for meta_path in sorted(self.root.glob("*.json")):
            if limit is not None and len(result) >= limit:
                break
            data_path = meta_path.with_suffix(".pcm")
            if not data_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    raise ValueError("ожидался объект JSON")
            except (UnicodeDecodeError, ValueError):  # JSONDecodeError ⊂ ValueError
                # P11.2, находка 8: пропуск навсегда осиротил бы звук — он
                # стоял бы первым в очереди до скончания века. Шлём без
                # метаданных: без времени захвата ядро пометит его приватным.
                log.error("битые метаданные %s, досылаю без них", meta_path.name)
                meta = {}
            result.append(SpooledChunk(path=data_path, meta=meta))
        return result

    def drop(self, chunk: SpooledChunk) -> None:
        chunk.path.unlink(missing_ok=True)
        chunk.path.with_suffix(".json").unlink(missing_ok=True)

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*.json"))

    def total_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.glob("*.pcm"))


class CoreClient:
    """Отправка в ядро по тому же контракту, что у приложения Omi (FR-001)."""

    def __init__(
        self,
        base_url: str,
        *,
        uid: str,
        source: str = "bridge-pc",
        sample_rate: int = 16000,
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.uid = uid
        self.source = source
        self.sample_rate = sample_rate
        self.timeout_s = timeout_s

    def send(
        self,
        payload: bytes,
        *,
        device_time_ms: int | None = None,
        spooled: bool = False,
    ) -> bool:
        import httpx

        params = {
            "uid": self.uid,
            "sample_rate": self.sample_rate,
            "source": self.source,
        }
        if device_time_ms is not None:
            params["device_time_ms"] = device_time_ms
        if spooled:
            # Без времени захвата ядро пометит такой фрагмент приватным
            # (P11): сколько он ждал в буфере — неизвестно.
            params["spooled"] = "true"

        try:
            response = httpx.post(
                f"{self.base_url}/ingest",
                params=params,
                content=payload,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self.timeout_s,
            )
        except Exception as exc:
            log.warning("ядро недоступно: %s", exc)
            return False

        if response.status_code >= 400:
            log.error(
                "ядро отвергло фрагмент: HTTP %s %s",
                response.status_code,
                response.text[:200],
            )
            # 4xx — фрагмент испорчен, повтор не поможет; 5xx — стоит повторить
            return 400 <= response.status_code < 500

        return True

    def report_status(self, stats: dict, *, spooled: int = 0) -> bool:
        """Сообщает ядру заряд и счётчики канала — для живого статуса в окне.

        Отчёт необязательный: его потеря не стоит ни одной задержки в приёме
        звука, поэтому ошибки только в журнал отладки и короткий таймаут.
        """
        import httpx

        payload = {
            "uid": self.uid,
            "battery_percent": stats.get("батарея_%"),
            "codec": stats.get("кодек"),
            "connected": bool(stats.get("подключено", True)),
            "packets": int(stats.get("пакетов", 0)),
            "lost": int(stats.get("потеряно", 0)),
            "spooled": int(spooled),
        }
        try:
            response = httpx.post(
                f"{self.base_url}/device/status", json=payload, timeout=5.0
            )
        except Exception as exc:
            log.debug("отчёт о состоянии не ушёл: %s", exc)
            return False
        if response.status_code >= 400:
            log.debug("ядро отвергло отчёт: HTTP %s", response.status_code)
            return False
        return True


class Uploader:
    """Пробует отправить сразу, при неудаче — копит на диске и досылает."""

    def __init__(self, client: CoreClient, spool: Spool) -> None:
        self.client = client
        self.spool = spool
        self.sent = 0
        self.spooled = 0

    def submit(self, payload: bytes, *, device_time_ms: int | None = None) -> bool:
        """Отправляет фрагмент. ``False`` — ушёл в буфер, а не в ядро."""
        if self.spool.count():
            # Пока есть незакрытый хвост, новое тоже идёт через буфер,
            # иначе порядок сломается.
            self.spool.add(payload, {"device_time_ms": device_time_ms})
            self.spooled += 1
            self.flush()
            return False

        if self.client.send(payload, device_time_ms=device_time_ms):
            self.sent += 1
            return True

        self.spool.add(payload, {"device_time_ms": device_time_ms})
        self.spooled += 1
        return False

    def flush(self, limit: int | None = None) -> int:
        """Досылает накопленное. Возвращает число отправленных фрагментов."""
        delivered = 0
        for chunk in self.spool.pending(limit):
            if not self.client.send(
                chunk.read(),
                device_time_ms=chunk.meta.get("device_time_ms"),
                spooled=True,
            ):
                break  # ядро всё ещё недоступно — порядок важнее скорости
            self.spool.drop(chunk)
            self.sent += 1
            delivered += 1
        if delivered:
            log.info(
                "дослано из буфера: %d фрагментов, осталось %d",
                delivered,
                self.spool.count(),
            )
        return delivered
