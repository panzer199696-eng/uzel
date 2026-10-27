"""Командная строка ядра (FR-053 и обслуживание).

uzel search "заявка Ромашки"     поиск по памяти
uzel stats                       что накоплено
uzel quality-report              сводка фильтра качества
uzel privacy mute 30             заглушить запись
uzel purge                       удалить сырое аудио по TTL
uzel export [--day ГГГГ-ММ-ДД] [--with-transcript]
                                  перевыгрузка суток из базы
uzel show <id> [--private]     разговор целиком в терминал
uzel prune-raw [--ttl-days N] [--yes]
                                  TTL сырых расшифровок
uzel worker recover              вернуть прерванное в очередь
uzel worker ingest [--limit N] [--loop] [--interval S]
uzel worker batch [--batch-size N] [--time-limit S] [--max-batches N]
                   [--close-after S] [--no-export] [--with-transcript]
uzel llm-check [--probe] [--json]
                                  чем разбираются разговоры и не уходит ли в облако
uzel wer <аудио> <эталон>     замер WER на готовой записи
uzel приёмка-возвращения [homecoming] [--wer-dir] [--name] [--hypothesis]
                                   окружение, эталон, WER и вердикт по A1
uzel serve                       поднять ядро
uzel app [--host] [--port] [--no-window] [--no-tray] [--autoclose N]
                                  окно и трей на ПК
uzel autostart install|remove|status
                                  запуск вместе с системой

Справка, ошибки и итоги команд — на языке интерфейса (UZEL_LANG, i18n.py).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from . import i18n
from .config import PROJECT_ROOT, Settings, get_settings
from .i18n import t
from .logging_setup import setup_logging
from .storage import Storage


def _fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime(t("format.datetime"))


def _fmt_duration(seconds: float) -> str:
    if seconds >= 60:
        return t("unit.minutes", value=f"{seconds / 60:.0f}")
    return t("unit.seconds", value=f"{seconds:.0f}")


def cmd_search(args, settings: Settings) -> int:
    """Смысловой поиск, с откатом на подстрочный."""
    with Storage(settings.db_path) as storage:
        rows: list = []
        hidden = 0

        if not args.plain:
            from .vector import build_index

            index = build_index(settings)
            for hit in index.search(args.query, limit=args.limit):
                row = storage.get_utterance(int(hit["id"]))
                if row is None:
                    continue
                if row.is_private:
                    # Старые точки смыслового индекса могли остаться от
                    # времён до фильтра — приватное не печатаем (P11.2).
                    hidden += 1
                    continue
                rows.append((hit.get("score"), row))

        if not rows:
            if not args.plain:
                print(t("cli.search.semantic_empty"), file=sys.stderr)
            rows = [
                (None, row) for row in storage.search_text(args.query, limit=args.limit)
            ]

        if not rows:
            print(t("cli.nothing_found"))
            return 1

        if hidden:
            print(t("cli.hidden_private", n=hidden), file=sys.stderr)

        for score, row in rows:
            people = _speaker_name(storage, row.id)
            mark = f"{score:.2f}  " if score is not None else ""
            print(f"{mark}{_fmt_time(row.started_at)}  {people}")
            print(f"    {row.text}")
            print()
    return 0


def _speaker_name(storage: Storage, utterance_id: int) -> str:
    with storage.transaction() as conn:
        row = conn.execute(
            """SELECT p.name AS name FROM utterances u
               LEFT JOIN people p ON p.id = u.speaker_id WHERE u.id=?""",
            (utterance_id,),
        ).fetchone()
    return row["name"] if row and row["name"] else t("cli.speaker_unknown")


def cmd_stats(args, settings: Settings) -> int:
    with Storage(settings.db_path) as storage:
        stats = storage.stats()
        print(t("cli.stats.db", path=settings.db_path))
        for key in ("conversations", "utterances", "facts", "tasks", "people", "tags"):
            print(f"  {t(f'cli.stats.{key}'):12s} {stats[key]}")

        pending = storage.pending_utterances(limit=10_000)
        if pending:
            print("  " + t("cli.stats.pending", n=len(pending)))

    from .queue import DiskQueue

    queue = DiskQueue(settings.queue_dir)
    print(t("cli.stats.queue", n=queue.pending_count()))
    return 0


def cmd_quality_report(args, settings: Settings) -> int:
    """Сводка фильтра качества — материал для калибровки порогов."""
    from .quality import reason_title

    start = end = None
    if args.day is not None:
        try:
            day = datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(t("cli.bad_date", value=args.day), file=sys.stderr)
            return 2
        start = day.timestamp()
        end = (day + timedelta(days=1)).timestamp()

    with Storage(settings.db_path) as storage:
        counts = storage.quality_counts(start=start, end=end)
        total = counts["total"]
        if total == 0:
            print(t("cli.quality.no_utterances"))
        else:
            accepted = counts["accepted"]
            rejected = counts["rejected"]
            flagged = counts["flagged"]

            def share(part: int) -> str:
                return f" ({round(part / total * 100)} %)" if part else ""

            print(t("cli.quality.title", path=settings.db_path))
            print(t("cli.quality.period", period=args.day or t("cli.quality.all_time")))
            print(f"  {t('cli.quality.total'):<18} {total}")
            print(f"  {t('cli.quality.accepted'):<18} {accepted}{share(accepted)}")
            print(f"  {t('cli.quality.rejected'):<18} {rejected}{share(rejected)}")
            print(f"  {t('cli.quality.flagged'):<18} {flagged}{share(flagged)}")

            for reason, count in sorted(
                counts["by_reason"].items(), key=lambda item: (-item[1], item[0])
            ):
                print(f"    {reason_title(reason):<28} {count}")

            if args.limit > 0:
                examples = storage.rejected_utterances(
                    limit=args.limit, start=start, end=end
                )
                if examples:
                    print(t("cli.quality.examples", n=len(examples)))
                    for row in examples:
                        text = (row.text or "").replace("\n", " ")
                        print(
                            f"  {_fmt_time(row.started_at)}  {row.reject_reason}"
                            f"  {text[:80]}"
                        )

    mode = (
        t("cli.quality.enforce_on")
        if settings.quality_enforce
        else t("cli.quality.enforce_off")
    )
    print(t("cli.quality.mode", mode=mode, enforce=settings.quality_enforce))
    return 0


def cmd_privacy(args, settings: Settings) -> int:
    from .privacy import build_gate

    gate = build_gate(settings)
    if args.action == "mute":
        gate.mute(args.minutes)
    elif args.action == "unmute":
        gate.unmute()
    elif args.action == "pause":
        gate.pause()

    state = gate.state()
    for key, value in state.as_dict().items():
        if value is not None:
            print(f"{key}: {value}")
    if gate.windows:
        windows = "; ".join(window.describe() for window in gate.windows)
        print(t("cli.privacy.windows", windows=windows))
    return 0


def cmd_purge(args, settings: Settings) -> int:
    ttl = args.ttl_days if args.ttl_days is not None else settings.audio_ttl_days
    with Storage(settings.db_path) as storage:
        removed_audio = storage.purge_audio(ttl)

    from .queue import DiskQueue

    removed_chunks = DiskQueue(settings.queue_dir).purge_done(ttl)
    print(t("cli.purge.done", audio=removed_audio, chunks=removed_chunks))
    return 0


def cmd_export(args, settings: Settings) -> int:
    """Перевыгрузка суток из базы: заметка и (по флагу) расшифровка."""
    from .worker import Worker

    if args.day is not None:
        try:
            day = datetime.strptime(args.day, "%Y-%m-%d").date()
        except ValueError:
            print(t("cli.bad_date", value=args.day), file=sys.stderr)
            return 2
    else:
        day = date.today()

    with Worker(settings) as worker:
        result = worker.export_day(day, with_transcript=args.with_transcript)

    if result["note"] is None:
        print(t("cli.export.no_note", date=day.isoformat()))
    else:
        print(t("cli.export.note", path=result["note"]))
    if result["raw"] is not None:
        print(t("cli.export.raw", path=result["raw"]))
    print(t("cli.export.tasks", n=result["tasks"]))
    return 0


def cmd_show(args, settings: Settings) -> int:
    """Разговор целиком в терминал — дословный текст вместо vault."""
    with Storage(settings.db_path) as storage:
        conversation = storage.get_conversation(args.id)
        if conversation is None:
            print(t("cli.show.missing", id=args.id), file=sys.stderr)
            return 1

        started = datetime.fromtimestamp(conversation["started_at"])
        ended = datetime.fromtimestamp(conversation["ended_at"])
        duration = conversation["ended_at"] - conversation["started_at"]
        print(
            t(
                "cli.show.header",
                id=conversation["id"],
                date=started.strftime(t("format.date")),
            )
        )
        print(
            t(
                "cli.show.span",
                start=f"{started:%H:%M}",
                end=f"{ended:%H:%M}",
                duration=_fmt_duration(duration),
                status=conversation["status"],
            )
        )
        if conversation["summary"]:
            print()
            print(conversation["summary"])

        facts = storage.fact_rows_of(args.id)
        if facts:
            print()
            print(t("cli.show.facts"))
            for row in facts:
                mark = (
                    f"[{datetime.fromtimestamp(row['said_at']):%H:%M}] "
                    if row["said_at"] is not None
                    else ""
                )
                print(f"  {mark}{row['text']}")

        tasks = storage.tasks_of(args.id)
        if tasks:
            print()
            print(t("cli.show.tasks"))
            for row in tasks:
                parts = [row["text"]]
                if row["assignee"]:
                    parts.append(f"({row['assignee']})")
                if row["due"]:
                    parts.append(t("export.task.due", due=row["due"]))
                print("  " + " ".join(parts))

        all_rows = storage.utterances_of(args.id, include_private=True)
        # По умолчанию приватные скрыты: видеть их вправе только владелец
        # и только явно (P11.2).
        rows = [
            row
            for row in all_rows
            if (row.text or "").strip() and (args.private or not row.is_private)
        ]
        hidden = sum(
            1 for row in all_rows if row.is_private and (row.text or "").strip()
        )
        if rows:
            print()
            print(t("cli.show.utterances"))
            for row in rows:
                mark = t("cli.show.private_mark") if row.is_private else ""
                print(
                    f"  [{datetime.fromtimestamp(row.started_at):%H:%M}]"
                    f" {row.text}{mark}"
                )
        if hidden and not args.private:
            print()
            print(t("cli.show.hidden", n=hidden))
    return 0


def cmd_prune_raw(args, settings: Settings) -> int:
    """TTL сырых расшифровок: без --yes только показывает просроченное."""
    from .export.obsidian import expired_raw_notes

    ttl = args.ttl_days if args.ttl_days is not None else settings.raw_note_ttl_days
    expired = expired_raw_notes(settings.vault_raw_dir, ttl_days=ttl)
    if not expired:
        print(t("cli.prune.none"))
        return 0
    for path in expired:
        print(path)

    if not args.yes:
        print(t("cli.prune.hint"))
        return 0

    removed = 0
    for path in expired:
        try:
            path.unlink()
        except OSError as exc:
            print(t("cli.prune.failed", path=path, error=exc), file=sys.stderr)
            continue
        removed += 1
    print(t("cli.prune.removed", n=removed))
    return 0


def cmd_backup(args, settings: Settings) -> int:
    with Storage(settings.db_path) as storage:
        path = storage.backup(args.destination)
    print(t("cli.backup.done", path=path))
    return 0


def cmd_wer(args, settings: Settings) -> int:
    """Замер качества распознавания на эталоне (критерий A1)."""
    from pathlib import Path

    from .homecoming import NoSpeechFound, transcribe_for_wer
    from .wer import compute_wer, render_report

    audio_path = Path(args.audio)
    reference_path = Path(args.reference)
    for path in (audio_path, reference_path):
        if not path.exists():
            print(t("cli.file_missing", path=path), file=sys.stderr)
            return 2

    try:
        result = transcribe_for_wer(
            audio_path, settings, backend=args.backend, no_vad=args.no_vad
        )
    except NoSpeechFound:
        print(t("cli.wer.no_speech"), file=sys.stderr)
        return 1

    hypothesis = result.hypothesis
    backend_name = result.backend
    audio_seconds = result.audio_seconds
    processing_seconds = result.processing_seconds

    reference = reference_path.read_text(encoding="utf-8")
    report = compute_wer(reference, hypothesis)

    note = _wer_note(args, settings, backend_name)
    text = render_report(
        report,
        title=audio_path.stem,
        backend=backend_name,
        audio_seconds=audio_seconds,
        processing_seconds=processing_seconds,
        threshold=args.threshold,
        settings_note=note,
    )

    print()
    print(text)

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else reference_path.parent / t("cli.wer.out_dir")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_path = out_dir / f"{stamp}-{backend_name}.md"
    out_path.write_text(text, encoding="utf-8")
    (out_dir / f"{stamp}-{backend_name}{t('cli.wer.recognized_suffix')}").write_text(
        hypothesis, encoding="utf-8"
    )
    print(t("cli.wer.report", path=out_path))

    return 0 if report.passed(args.threshold) else 1


def _wer_note(args, settings: Settings, backend_name: str) -> str:
    """Строка настроек замера для отчёта WER."""
    return t(
        "cli.wer.note",
        vad=t("cli.wer.vad_off") if args.no_vad else t("cli.wer.vad_on"),
        model=settings.local_model if backend_name == "local" else settings.groq_model,
    )


def cmd_homecoming(args, settings: Settings) -> int:
    """Приёмка возвращения: окружение → эталон → замер WER → вердикт A1."""
    from datetime import datetime
    from pathlib import Path

    from . import homecoming
    from .homecoming import NoSpeechFound, find_reference, render_verdict
    from .wer import compute_wer, render_report

    wer_dir = Path(args.wer_dir)
    out_dir = Path(args.out_dir) if args.out_dir else wer_dir / t("cli.wer.out_dir")

    print(t("cli.homecoming.title"))
    print()

    # Шаг 1. Окружение: что готово, чего не хватает.
    print(t("cli.homecoming.step1"))
    blocking: list[str] = []
    if args.skip_env:
        print(t("cli.homecoming.skipped"))
    else:
        for check in homecoming.check_environment(settings):
            mark = "✅" if check.ok else ("❌" if check.blocking else "⚠️")
            print(f"{mark} {check.name} — {check.detail}")
            if check.blocking and not check.ok:
                blocking.append(f"{check.name}: {check.detail}")
    print()

    # Шаг 2. Эталон: без записи и вычитки дальше идти некуда.
    print(t("cli.homecoming.step2"))
    state = find_reference(wer_dir, args.name)
    if not (state.audio_ok and state.text_ok):
        script = PROJECT_ROOT / "tests" / "wer" / "текст-для-начитки.md"
        print(t("cli.homecoming.audio", mark="✅" if state.audio_ok else "❌", path=state.audio))
        print(t("cli.homecoming.text", mark="✅" if state.text_ok else "❌", path=state.text))
        print(t("cli.homecoming.missing"))
        print(t("cli.homecoming.todo1"))
        print(t("cli.homecoming.todo1b", script=script))
        print(t("cli.homecoming.todo2", path=state.audio))
        print(t("cli.homecoming.todo2b"))
        print(t("cli.homecoming.todo3", path=state.text))
        print(t("cli.homecoming.todo3b"))
        print(t("cli.homecoming.todo4"))
        if state.problems:
            print(t("cli.homecoming.problems"))
            for problem in state.problems:
                print(f"  — {problem}")
        return 2
    print(t("cli.homecoming.ready", audio=state.audio.name, text=state.text.name))
    print()

    # Готовый текст снимает зависимость от окружения: STT не нужен вовсе.
    if blocking and args.hypothesis is None:
        print(t("cli.homecoming.blocked"))
        for item in blocking:
            print(f"  — {item}")
        return 3

    # Шаг 3. Замер.
    print(t("cli.homecoming.step3"))
    if args.hypothesis is not None:
        hypothesis = Path(args.hypothesis).read_text(encoding="utf-8")
        backend_name = t("cli.homecoming.ready_text_backend")
        audio_seconds = 0.0
        processing_seconds = 0.0
        rtf = None
        note = t("cli.homecoming.ready_text_note")
    else:
        try:
            result = homecoming.transcribe_for_wer(
                state.audio, settings, backend=args.backend, no_vad=args.no_vad
            )
        except NoSpeechFound:
            print(t("cli.wer.no_speech"), file=sys.stderr)
            return 1
        except Exception as exc:  # модель не грузится и прочее — без трейсбэка
            print(t("cli.homecoming.stt_failed", error=exc), file=sys.stderr)
            return 3
        hypothesis = result.hypothesis
        backend_name = result.backend
        audio_seconds = result.audio_seconds
        processing_seconds = result.processing_seconds
        rtf = processing_seconds / audio_seconds if audio_seconds else None
        note = _wer_note(args, settings, backend_name)

    reference = state.text.read_text(encoding="utf-8")
    report = compute_wer(reference, hypothesis)
    text = render_report(
        report,
        title=state.audio.stem,
        backend=backend_name,
        audio_seconds=audio_seconds,
        processing_seconds=processing_seconds,
        threshold=args.threshold,
        settings_note=note,
    )
    verdict = render_verdict(report, threshold=args.threshold, rtf=rtf)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    base_name = t("cli.homecoming.file", stamp=stamp, backend=backend_name)
    out_path = out_dir / f"{base_name}.md"
    out_path.write_text(text + "\n" + verdict, encoding="utf-8")
    if args.hypothesis is None:
        (out_dir / f"{base_name}{t('cli.wer.recognized_suffix')}").write_text(
            hypothesis, encoding="utf-8"
        )

    print()
    print(verdict)
    print()
    print(t("cli.wer.report", path=out_path))

    return 0 if report.passed(args.threshold) else 1


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from .security import UnsafeHostError, ensure_safe_listen_host

    host = args.host or settings.host
    try:
        # --host обходил бы проверку create_app, поэтому проверяем и его.
        ensure_safe_listen_host(host, settings.api_token)
    except UnsafeHostError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    uvicorn.run(
        "uzel.app:app",
        host=host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def cmd_app(args, settings: Settings) -> int:
    """Окно и трей на ПК (ТЗ P7)."""
    from .desktop import AppOptions, run_app
    from .security import UnsafeHostError, ensure_safe_listen_host

    try:
        ensure_safe_listen_host(args.host or settings.host, settings.api_token)
    except UnsafeHostError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    options = AppOptions(
        host=args.host or settings.host,
        port=args.port if args.port is not None else settings.port,
        window=not args.no_window,
        tray=not args.no_tray,
        autoclose_s=args.autoclose,
    )
    return run_app(settings, options)


def cmd_autostart(args, settings: Settings) -> int:
    """Управление ярлыком автозагрузки (ТЗ P7)."""
    from . import autostart

    if args.action == "install":
        path = autostart.install()
        print(t("cli.autostart.created", path=path))
    elif args.action == "remove":
        if autostart.remove():
            print(t("cli.autostart.removed"))
        else:
            print(t("cli.autostart.not_set"))
    else:
        state = autostart.status()
        if state["installed"]:
            print(
                t(
                    "cli.autostart.set",
                    target=state["target"],
                    arguments=state["arguments"],
                )
            )
        else:
            print(t("cli.autostart.not_set"))
    return 0


# Код остановки батча → ключ каталога (cli.stop.*).
_STOP_REASONS = {
    "stop-file": "cli.stop.stop_file",
    "time-limit": "cli.stop.time_limit",
    "stt-fused": "cli.stop.stt_fused",
    "llm-fused": "cli.stop.llm_fused",
}

_INGEST_LINES = (
    "recovered", "chunks", "utterances", "duplicates", "silent", "failed", "undecodable",
)
# Порядок строк итога батча. Подписи — cli.batch.<поле>: по ним же трей
# собирает уведомление (tray_extras.batch_summary), на любом языке.
BATCH_LINES = (
    "recovered", "stitched", "transcribed", "failed", "batches", "conversations",
    "analysed", "skipped", "nlu_failed", "accepted", "rejected", "notes",
    "raw_notes", "exported_tasks", "unloaded", "purged_audio", "purged_chunks",
)


def _print_ingest(stats) -> None:
    for name in _INGEST_LINES:
        print(f"{t(f'cli.ingest.{name}')}: {getattr(stats, name)}")


def _print_batch(stats) -> None:
    for name in BATCH_LINES:
        print(f"{t(f'cli.batch.{name}')}: {getattr(stats, name)}")
    if stats.stopped_by is not None:
        key = _STOP_REASONS.get(stats.stopped_by)
        reason = t(key) if key else stats.stopped_by
        print(t("cli.batch.stopped", reason=reason))


def cmd_worker(args, settings: Settings) -> int:
    # Импорт внутри функции: `uzel --help` не должен тянуть onnxruntime.
    from .worker import Worker

    with Worker(settings) as worker:
        if args.action == "recover":
            print(t("cli.worker.recovered", n=worker.recover()))
        elif args.action == "ingest":
            if args.loop:
                stats = worker.run_ingest(interval_s=args.interval)
            else:
                stats = worker.ingest_once(limit=args.limit)
            _print_ingest(stats)
        elif args.action == "batch":
            stats = worker.run_batch(
                batch_size=args.batch_size,
                time_limit_s=args.time_limit,
                max_batches=args.max_batches,
                close_after_s=args.close_after,
                export=not args.no_export,
                # None → флаг расшифровки берётся из настроек.
                with_transcript=True if args.with_transcript else None,
            )
            _print_batch(stats)
    return 0


# Известные облачные бэкенды: имя настройки → ключ пояснения для человека.
_KNOWN_CLOUD_BACKENDS = {
    "gateway": "cli.llm.cloud_gateway",  # локальный порт, облачный счёт (llm.GatewayBackend)
    "groq": "cli.llm.cloud_groq",
}


def cmd_llm_check(args, settings: Settings) -> int:
    """Показывает модели разбора и то, уходит ли расшифровка в облако.

    Без ``--probe`` сеть дёргается не дальше ``available()``. Код возврата:
    0, если хотя бы один локальный бэкенд готов, иначе 1.
    """
    import json

    from .llm import build_llm_router
    from .privacy import BATCH_ALLOW_CLOUD

    router = build_llm_router(settings)
    entries: list[dict] = []
    for backend in router.backends:
        entry = {
            "name": backend.name,
            "is_cloud": backend.is_cloud,
            "model": getattr(backend, "model", ""),
            "enabled": True,
        }
        if backend.available():
            entry["ready"] = True
            if args.probe:
                try:
                    result = backend.complete(
                        [{"role": "user", "content": t("cli.llm.probe_question")}],
                        max_tokens=32,
                    )
                    entry["probe_latency_s"] = round(result.latency_s, 1)
                    entry["probe_reply"] = result.text[:60]
                except Exception as exc:  # отказ проверки не роняет команду
                    entry["probe_error"] = str(exc)[:200]
        else:
            entry["ready"] = False
            reason = getattr(backend, "probe_error", None)
            entry["probe_error"] = reason or t("cli.llm.not_configured")
        entries.append(entry)

    enabled_names = {entry["name"] for entry in entries}
    for name, note in _KNOWN_CLOUD_BACKENDS.items():
        if name in enabled_names or (name == "groq" and "groq-llm" in enabled_names):
            continue
        entries.append(
            {
                "name": name,
                "is_cloud": True,
                "enabled": False,
                "cloud_note": t(note),
            }
        )

    local_ready = any(
        entry["enabled"] and not entry["is_cloud"] and entry["ready"]
        for entry in entries
    )
    report = {
        "backends": entries,
        "local_ready": local_ready,
        # Из того же места, откуда батч берёт allow_cloud (P11.2, находка 13).
        "cloud_on_default_path": BATCH_ALLOW_CLOUD,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_llm_check(entries, local_ready)

    if not local_ready:
        print(t("cli.llm.no_local"), file=sys.stderr)
        return 1
    return 0


def _print_llm_check(entries: list[dict], local_ready: bool) -> None:
    first_enabled = True
    for entry in entries:
        if entry["enabled"]:
            kind = t("cli.llm.cloud") if entry["is_cloud"] else t("cli.llm.local")
            if entry["ready"]:
                status = t("cli.llm.ready")
                if "probe_latency_s" in entry:
                    status += t(
                        "cli.llm.probe",
                        seconds=entry["probe_latency_s"],
                        reply=entry["probe_reply"],
                    )
            else:
                status = t("cli.llm.not_ready", error=entry["probe_error"])
            prefix = t("cli.llm.prefix") if first_enabled else "  "
            first_enabled = False
            print(f"{prefix}{entry['name']} ({kind}), {entry['model']} — {status}")
        else:
            print(t("cli.llm.disabled", name=entry["name"], note=entry["cloud_note"]))
    if local_ready:
        from .privacy import cloud_policy_line

        print(
            cloud_policy_line(
                [e["name"] for e in entries if e["enabled"] and e["is_cloud"]]
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uzel", description=t("cli.description"))
    parser.add_argument("--log-level", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help=t("cli.help.search"))
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--plain", action="store_true", help=t("cli.help.search_plain"))
    search.set_defaults(func=cmd_search)

    stats = subparsers.add_parser("stats", help=t("cli.help.stats"))
    stats.set_defaults(func=cmd_stats)

    quality = subparsers.add_parser("quality-report", help=t("cli.help.quality"))
    quality.add_argument("--day", default=None, help=t("cli.help.day"))
    quality.add_argument(
        "--limit", type=int, default=0, help=t("cli.help.quality_limit")
    )
    quality.set_defaults(func=cmd_quality_report)

    privacy = subparsers.add_parser("privacy", help=t("cli.help.privacy"))
    privacy.add_argument(
        "action",
        choices=("status", "mute", "unmute", "pause"),
        nargs="?",
        default="status",
    )
    privacy.add_argument("minutes", type=float, nargs="?", default=10.0)
    privacy.set_defaults(func=cmd_privacy)

    purge = subparsers.add_parser("purge", help=t("cli.help.purge"))
    purge.add_argument("--ttl-days", type=int, default=None)
    purge.set_defaults(func=cmd_purge)

    export = subparsers.add_parser(
        "export", help=t("cli.help.export")
    )
    export.add_argument(
        "--day", default=None, help=t("cli.help.export_day")
    )
    export.add_argument(
        "--with-transcript",
        action="store_true",
        help=t("cli.help.with_transcript"),
    )
    export.set_defaults(func=cmd_export)

    show = subparsers.add_parser("show", help=t("cli.help.show"))
    show.add_argument("id", type=int)
    show.add_argument(
        "--private",
        action="store_true",
        help=t("cli.help.show_private"),
    )
    show.set_defaults(func=cmd_show)

    prune_raw = subparsers.add_parser(
        "prune-raw", help=t("cli.help.prune")
    )
    prune_raw.add_argument(
        "--ttl-days", type=int, default=None, help=t("cli.help.prune_ttl")
    )
    prune_raw.add_argument(
        "--yes", action="store_true", help=t("cli.help.prune_yes")
    )
    prune_raw.set_defaults(func=cmd_prune_raw)

    backup = subparsers.add_parser("backup", help=t("cli.help.backup"))
    backup.add_argument("destination")
    backup.set_defaults(func=cmd_backup)

    wer = subparsers.add_parser("wer", help=t("cli.help.wer"))
    wer.add_argument("audio", help=t("cli.help.wer_audio"))
    wer.add_argument("reference", help=t("cli.help.wer_reference"))
    wer.add_argument("--backend", choices=("local", "groq"), default=None)
    wer.add_argument("--threshold", type=float, default=0.15)
    wer.add_argument("--no-vad", action="store_true", help=t("cli.help.no_vad"))
    wer.add_argument("--out-dir", default=None)
    wer.set_defaults(func=cmd_wer)

    homecoming_cmd = subparsers.add_parser(
        "приёмка-возвращения",
        aliases=["homecoming"],  # ASCII-имя нужно для cmd.exe
        help=t("cli.help.homecoming"),
    )
    homecoming_cmd.add_argument(
        "--wer-dir",
        default=PROJECT_ROOT / "tests" / "wer",
        help=t("cli.help.homecoming_dir"),
    )
    homecoming_cmd.add_argument("--name", default="эталон-01", help=t("cli.help.homecoming_name"))
    homecoming_cmd.add_argument(
        "--hypothesis",
        default=None,
        help=t("cli.help.homecoming_hypothesis"),
    )
    homecoming_cmd.add_argument("--backend", choices=("local", "groq"), default=None)
    homecoming_cmd.add_argument("--threshold", type=float, default=0.15)
    homecoming_cmd.add_argument(
        "--no-vad", action="store_true", help=t("cli.help.no_vad")
    )
    homecoming_cmd.add_argument("--out-dir", default=None)
    homecoming_cmd.add_argument(
        "--skip-env", action="store_true", help=t("cli.help.skip_env")
    )
    homecoming_cmd.set_defaults(func=cmd_homecoming)

    serve = subparsers.add_parser("serve", help=t("cli.help.serve"))
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=cmd_serve)

    app = subparsers.add_parser("app", help=t("cli.help.app"))
    app.add_argument("--host", default=None, help=t("cli.help.host"))
    app.add_argument("--port", type=int, default=None, help=t("cli.help.port"))
    app.add_argument("--no-window", action="store_true", help=t("cli.help.no_window"))
    app.add_argument("--no-tray", action="store_true", help=t("cli.help.no_tray"))
    app.add_argument(
        "--autoclose",
        type=float,
        default=None,
        help=t("cli.help.autoclose"),
    )
    app.set_defaults(func=cmd_app)

    autostart = subparsers.add_parser("autostart", help=t("cli.help.autostart"))
    autostart.add_argument("action", choices=("install", "remove", "status"))
    autostart.set_defaults(func=cmd_autostart)

    worker = subparsers.add_parser("worker", help=t("cli.help.worker"))
    worker_sub = worker.add_subparsers(dest="action", required=True)

    worker_recover = worker_sub.add_parser(
        "recover", help=t("cli.help.recover")
    )
    worker_recover.set_defaults(func=cmd_worker)

    worker_ingest = worker_sub.add_parser(
        "ingest", help=t("cli.help.ingest")
    )
    worker_ingest.add_argument(
        "--limit", type=int, default=None, help=t("cli.help.ingest_limit")
    )
    worker_ingest.add_argument(
        "--loop", action="store_true", help=t("cli.help.ingest_loop")
    )
    worker_ingest.add_argument(
        "--interval", type=float, default=None, help=t("cli.help.ingest_interval")
    )
    worker_ingest.set_defaults(func=cmd_worker)

    worker_batch = worker_sub.add_parser(
        "batch", help=t("cli.help.batch")
    )
    worker_batch.add_argument("--batch-size", type=int, default=None)
    worker_batch.add_argument("--time-limit", type=float, default=None)
    worker_batch.add_argument("--max-batches", type=int, default=None)
    worker_batch.add_argument("--close-after", type=float, default=None)
    worker_batch.add_argument(
        "--no-export", action="store_true", help=t("cli.help.no_export")
    )
    worker_batch.add_argument(
        "--with-transcript",
        action="store_true",
        help=t("cli.help.batch_transcript"),
    )
    worker_batch.set_defaults(func=cmd_worker)

    llm_check = subparsers.add_parser(
        "llm-check", help=t("cli.help.llm_check")
    )
    llm_check.add_argument(
        "--probe",
        action="store_true",
        help=t("cli.help.probe"),
    )
    llm_check.add_argument(
        "--json", action="store_true", help=t("cli.help.json")
    )
    llm_check.set_defaults(func=cmd_llm_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Язык нужен уже справке argparse: выбор из окна сильнее UZEL_LANG.
    settings = get_settings()
    i18n.configure(settings)
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level or settings.log_level)
    return args.func(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
