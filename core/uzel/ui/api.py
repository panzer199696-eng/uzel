"""JSON-API страницы окна (ТЗ P7).

Хранилище открывается на каждый запрос — как в MCP: долгоживущее соединение
SQLite в процессе UI не нужно. Ошибка выборки не должна ронять страницу:
возвращаем пустой результат и текстовую причину в поле ``error``, а не HTTP 500.

Принцип приватности: лента дня не содержит дословных реплик — только саммари,
факты и задачи. Дословный текст отдаёт только ``/conversation/{id}`` с явным
``with_utterances=true`` и ``/search`` — его для того и открывают.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from .. import __version__
from ..config import Settings
from ..storage import Storage

log = logging.getLogger(__name__)

# Верхняя граница выборки дня: сутки реальной работы в неё не упираются,
# а зацикленная страница не имеет права тянуть из базы неограниченный список.
DAY_LIMIT = 500


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/ui/api")

    # ---------- статус шапки ----------

    @router.get("/status")
    async def ui_status(request: Request) -> dict:
        """Всё для шапки окна. Дёргается каждые 3 с — обязан быть дешёвым:
        никаких Qdrant, whisper и Ollama, только локальные файлы и память."""
        queue = request.app.state.queue
        gate = request.app.state.privacy
        stop_file = settings.worker_stop_file.exists()

        return {
            "version": __version__,
            "privacy": gate.state().as_dict(),
            "queue": {
                "pending": queue.pending_count(),
                "processing": _count_chunks(settings.queue_dir / "processing"),
            },
            "device": request.app.state.device.as_dict(),
            "db": _safe_stats(settings),
            "worker": {
                "stop_file": stop_file,
                "batch_hint": (
                    "ночной батч остановлен стоп-файлом"
                    if stop_file
                    else "ночной батч не запрещён"
                ),
            },
            "quiet_windows": [window.describe() for window in gate.windows],
        }

    # ---------- лента дня ----------

    @router.get("/day")
    async def ui_day(date: str | None = Query(None)) -> dict:
        day = _parse_day(date)
        start, end = _day_bounds(day)
        empty = {
            "date": day.isoformat(),
            "conversations": [],
            "facts": [],
            "tasks": [],
            "totals": {"conversations": 0, "facts": 0, "tasks": 0, "speech": "0 с"},
        }
        try:
            with Storage(settings.db_path) as storage:
                rows = storage.conversations_between(start, end)
                fact_rows = storage.facts_between(start, end, limit=DAY_LIMIT)
                task_rows = storage.tasks_between(start, end, limit=DAY_LIMIT)

                facts_by_conv: dict[int, int] = {}
                facts = []
                for row in fact_rows:
                    facts.append(
                        {
                            "id": row["id"],
                            "text": row["text"],
                            "said": _fmt_hm(
                                row["said_at"]
                                if row["said_at"] is not None
                                else row["conversation_started_at"]
                            ),
                            "conversation_id": row["conversation_id"],
                        }
                    )
                    facts_by_conv[row["conversation_id"]] = (
                        facts_by_conv.get(row["conversation_id"], 0) + 1
                    )

                tasks_by_conv: dict[int, int] = {}
                tasks = []
                for row in task_rows:
                    tasks.append(_task_dict(row))
                    tasks_by_conv[row["conversation_id"]] = (
                        tasks_by_conv.get(row["conversation_id"], 0) + 1
                    )

                speech_seconds = 0.0
                conversations = []
                for row in rows:
                    speech_seconds += row["speech_seconds"] or 0.0
                    conversations.append(
                        _conversation_card(
                            storage,
                            row,
                            facts_by_conv.get(row["id"], 0),
                            tasks_by_conv.get(row["id"], 0),
                        )
                    )
        except Exception as exc:  # нет базы или схема чужая — страница жива
            log.warning("лента дня %s не собралась: %s", day, exc)
            return {**empty, "error": str(exc)}

        return {
            "date": day.isoformat(),
            "conversations": conversations,
            "facts": facts,
            "tasks": tasks,
            "totals": {
                "conversations": len(rows),
                "facts": len(facts),
                "tasks": len(tasks),
                "speech": _fmt_duration(speech_seconds),
            },
        }

    # ---------- разговор целиком ----------

    @router.get("/conversation/{conversation_id}")
    async def ui_conversation(
        conversation_id: int, with_utterances: bool = Query(False)
    ) -> dict:
        try:
            with Storage(settings.db_path) as storage:
                row = storage.get_conversation(conversation_id)
                if row is None:
                    raise HTTPException(
                        status_code=404, detail=f"разговора {conversation_id} нет"
                    )

                fact_rows = storage.fact_rows_of(conversation_id)
                task_rows = storage.tasks_of(conversation_id)
                card = _conversation_card(storage, row, len(fact_rows), len(task_rows))
                card["facts"] = [
                    {
                        "id": fact["id"],
                        "text": fact["text"],
                        "said": _fmt_hm(fact["said_at"])
                        if fact["said_at"] is not None
                        else None,
                        "conversation_id": fact["conversation_id"],
                    }
                    for fact in fact_rows
                ]
                card["tasks"] = [_task_dict(task) for task in task_rows]

                if with_utterances:
                    speakers = _speakers_of(storage, conversation_id)
                    # Владелец видит всё, включая приватное — с пометкой (P11.2).
                    card["transcript"] = [
                        {
                            "id": utterance.id,
                            "started": dt.datetime.fromtimestamp(
                                utterance.started_at
                            ).strftime("%H:%M:%S"),
                            "speaker": speakers.get(utterance.id),
                            "text": utterance.text,
                            "status": utterance.status,
                            "reject_reason": utterance.reject_reason,
                            # P11: приватное видно владельцу, но помечено —
                            # эта часть не уходит ни в облако, ни в MCP.
                            "is_private": utterance.is_private,
                        }
                        for utterance in storage.utterances_of(
                            conversation_id, include_private=True
                        )
                    ]
                return card
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("разговор %s не собрался: %s", conversation_id, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ---------- поиск ----------

    @router.get("/search")
    async def ui_search(
        q: str = Query(...),
        limit: int = Query(20, ge=1, le=100),
        semantic: bool = Query(True),
    ) -> dict:
        if not q.strip():
            raise HTTPException(status_code=400, detail="пустой поисковый запрос")

        try:
            with Storage(settings.db_path) as storage:
                hits: list[tuple[float | None, object]] = []

                if semantic:
                    try:
                        from ..vector import build_index

                        index = build_index(settings)
                        for hit in index.search(q, limit=limit):
                            row = storage.get_utterance(int(hit["id"]))
                            if row is not None:
                                hits.append((hit.get("score"), row))
                    except Exception:
                        hits = []  # смысловой поиск недоступен — откат ниже

                if hits:
                    origin = "смысловым поиском"
                else:
                    # Поиск владельца: приватное тоже находится — с пометкой
                    # в выдаче (P11.2).
                    hits = [
                        (None, row)
                        for row in storage.search_text(
                            q, limit=limit, include_private=True
                        )
                    ]
                    origin = "подстрочным поиском"

                results = []
                for score, row in hits:
                    results.append(
                        {
                            "utterance_id": row.id,
                            "conversation_id": row.conversation_id,
                            "started": dt.datetime.fromtimestamp(
                                row.started_at
                            ).strftime("%d.%m %H:%M"),
                            "speaker": _speaker_of(storage, row.id),
                            "text": (row.text or "").replace("\n", " "),
                            "score": round(score, 3) if score is not None else None,
                            # P11: приватное показывается, но с видимой пометкой.
                            "is_private": row.is_private,
                        }
                    )
        except Exception as exc:
            log.warning("поиск «%s» не собрался: %s", q, exc)
            return {
                "origin": "подстрочным поиском",
                "query": q,
                "results": [],
                "error": str(exc),
            }

        return {"origin": origin, "query": q, "results": results}

    # ---------- качество за день ----------

    @router.get("/quality")
    async def ui_quality(date: str | None = Query(None)) -> dict:
        day = _parse_day(date)
        start, end = _day_bounds(day)
        try:
            with Storage(settings.db_path) as storage:
                counts = storage.quality_counts(start=start, end=end)
        except Exception as exc:
            log.warning("отчёт качества за %s не собрался: %s", day, exc)
            counts = {
                "total": 0,
                "accepted": 0,
                "rejected": 0,
                "flagged": 0,
                "by_reason": {},
            }
            return {
                **_quality_payload(settings, day, counts),
                "error": str(exc),
            }

        return _quality_payload(settings, day, counts)

    return router


# ---------- общие помощники ----------


def _quality_payload(settings: Settings, day: dt.date, counts: dict) -> dict:
    from ..quality import REASON_TITLES

    by_reason = [
        {"code": code, "title": REASON_TITLES.get(code, code), "count": count}
        for code, count in sorted(
            counts["by_reason"].items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "date": day.isoformat(),
        "total": counts["total"],
        "accepted": counts["accepted"],
        "rejected": counts["rejected"],
        "flagged": counts["flagged"],
        "by_reason": by_reason,
        "enforce": settings.quality_enforce,
        "note": None if settings.quality_enforce else "фильтр помечает, но пропускает",
    }


def _parse_day(value: str | None) -> dt.date:
    """``None`` — сегодня, иначе ГГГГ-ММ-ДД. Остальное — HTTP 400."""
    if value is None:
        return dt.date.today()
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"дата должна быть в формате ГГГГ-ММ-ДД, получено: {value!r}",
        ) from exc


def _day_bounds(day: dt.date) -> tuple[float, float]:
    """Локальные сутки как полуинтервал ``[start, end)``."""
    start = dt.datetime.combine(day, dt.time.min).timestamp()
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp()
    return start, end


def _fmt_hm(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp).strftime("%H:%M")


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} с"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest:02d} мин"


def _count_chunks(directory) -> int:
    """Фрагменты в каталоге очереди — считаем файлы метаданных."""
    try:
        return sum(1 for _ in directory.glob("*.json"))
    except OSError:
        return 0


def _safe_stats(settings: Settings) -> dict:
    try:
        with Storage(settings.db_path) as storage:
            return storage.stats()
    except Exception as exc:
        log.warning("статистика базы не собралась: %s", exc)
        return {
            "conversations": 0,
            "utterances": 0,
            "facts": 0,
            "tasks": 0,
            "people": 0,
            "tags": 0,
            "error": str(exc),
        }


def _conversation_card(storage: Storage, row, facts: int, tasks: int) -> dict:
    """Карточка разговора для ленты: без дословных реплик."""
    started = dt.datetime.fromtimestamp(row["started_at"])
    ended = dt.datetime.fromtimestamp(row["ended_at"])
    duration = max(0.0, row["ended_at"] - row["started_at"])
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "started": started.strftime("%H:%M"),
        "ended": ended.strftime("%H:%M"),
        "duration": _fmt_duration(duration),
        "status": row["status"],
        "summary": row["summary"],
        "tags": storage.tags_of(row["id"]),
        "participants": _participants(storage, row["id"]),
        "utterances": len(storage.utterances_of(row["id"])),
        "facts": facts,
        "tasks": tasks,
    }


def _task_dict(task) -> dict:
    return {
        "id": task["id"],
        "text": task["text"],
        "assignee": task["assignee"],
        "due": task["due"],
        "status": task["status"],
        "exported": task["exported_at"] is not None,
        "conversation_id": task["conversation_id"],
    }


def _participants(storage: Storage, conversation_id: int) -> list[str]:
    """Участники — только из ``people`` через ``utterances.speaker_id``.

    Подход тот же, что в MCP (``_participants`` в tools.py).
    """
    with storage.transaction() as conn:
        rows = conn.execute(
            """SELECT DISTINCT p.name FROM people p
               JOIN utterances u ON u.speaker_id = p.id
               WHERE u.conversation_id=? ORDER BY p.name""",
            (conversation_id,),
        ).fetchall()
    return [row["name"] for row in rows]


def _speakers_of(storage: Storage, conversation_id: int) -> dict[int, str | None]:
    """id реплики → имя говорящего (или None, если не размечен)."""
    with storage.transaction() as conn:
        rows = conn.execute(
            """SELECT u.id AS utterance_id, p.name AS name
               FROM utterances u
               LEFT JOIN people p ON p.id = u.speaker_id
               WHERE u.conversation_id=?""",
            (conversation_id,),
        ).fetchall()
    return {row["utterance_id"]: row["name"] for row in rows}


def _speaker_of(storage: Storage, utterance_id: int) -> str | None:
    with storage.transaction() as conn:
        row = conn.execute(
            """SELECT p.name AS name FROM utterances u
               LEFT JOIN people p ON p.id = u.speaker_id WHERE u.id=?""",
            (utterance_id,),
        ).fetchone()
    return row["name"] if row and row["name"] else None
