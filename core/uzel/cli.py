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
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from .config import PROJECT_ROOT, Settings, get_settings
from .logging_setup import setup_logging
from .storage import Storage


def _fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")


def _fmt_duration(seconds: float) -> str:
    return f"{seconds / 60:.0f} мин" if seconds >= 60 else f"{seconds:.0f} с"


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
                print(
                    "Смысловой поиск ничего не дал, ищу по подстроке.", file=sys.stderr
                )
            rows = [
                (None, row) for row in storage.search_text(args.query, limit=args.limit)
            ]

        if not rows:
            print("Ничего не нашлось.")
            return 1

        if hidden:
            print(f"скрыто приватных: {hidden}", file=sys.stderr)

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
    return row["name"] if row and row["name"] else "говорящий не размечен"


def cmd_stats(args, settings: Settings) -> int:
    with Storage(settings.db_path) as storage:
        stats = storage.stats()
        print(f"База: {settings.db_path}")
        for key, title in (
            ("conversations", "разговоров"),
            ("utterances", "реплик"),
            ("facts", "фактов"),
            ("tasks", "задач"),
            ("people", "людей"),
            ("tags", "меток"),
        ):
            print(f"  {title:12s} {stats[key]}")

        pending = storage.pending_utterances(limit=10_000)
        if pending:
            print(f"  ждут расшифровки: {len(pending)}")

    from .queue import DiskQueue

    queue = DiskQueue(settings.queue_dir)
    print(f"Очередь приёма: {queue.pending_count()} фрагментов")
    return 0


def cmd_quality_report(args, settings: Settings) -> int:
    """Сводка фильтра качества — материал для калибровки порогов."""
    from .quality import REASON_TITLES

    start = end = None
    if args.day is not None:
        try:
            day = datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"Не разобрать дату: {args.day}", file=sys.stderr)
            return 2
        start = day.timestamp()
        end = (day + timedelta(days=1)).timestamp()

    with Storage(settings.db_path) as storage:
        counts = storage.quality_counts(start=start, end=end)
        total = counts["total"]
        if total == 0:
            print("За период реплик нет.")
        else:
            accepted = counts["accepted"]
            rejected = counts["rejected"]
            flagged = counts["flagged"]

            def share(part: int) -> str:
                return f" ({round(part / total * 100)} %)" if part else ""

            print(f"Качество: {settings.db_path}")
            print(f"Период: {args.day if args.day else 'всё время'}")
            print(f"  {'реплик всего':<18} {total}")
            print(f"  {'принято':<18} {accepted}{share(accepted)}")
            print(f"  {'отклонено':<18} {rejected}{share(rejected)}")
            print(f"  {'помечено':<18} {flagged}{share(flagged)}")

            for reason, count in sorted(
                counts["by_reason"].items(), key=lambda item: (-item[1], item[0])
            ):
                print(f"    {REASON_TITLES.get(reason, reason):<28} {count}")

            if args.limit > 0:
                examples = storage.rejected_utterances(
                    limit=args.limit, start=start, end=end
                )
                if examples:
                    print(f"Примеры (последние {len(examples)}):")
                    for row in examples:
                        text = (row.text or "").replace("\n", " ")
                        print(
                            f"  {_fmt_time(row.started_at)}  {row.reject_reason}"
                            f"  {text[:80]}"
                        )

    mode = (
        "отбрасывание включено"
        if settings.quality_enforce
        else "пометка без отбрасывания"
    )
    print(f"Режим: {mode} (quality_enforce={settings.quality_enforce})")
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
        print("стоп-зоны: " + "; ".join(window.describe() for window in gate.windows))
    return 0


def cmd_purge(args, settings: Settings) -> int:
    ttl = args.ttl_days if args.ttl_days is not None else settings.audio_ttl_days
    with Storage(settings.db_path) as storage:
        removed_audio = storage.purge_audio(ttl)

    from .queue import DiskQueue

    removed_chunks = DiskQueue(settings.queue_dir).purge_done(ttl)
    print(
        f"Удалено: записей аудио {removed_audio}, обработанных фрагментов {removed_chunks}"
    )
    return 0


def cmd_export(args, settings: Settings) -> int:
    """Перевыгрузка суток из базы: заметка и (по флагу) расшифровка."""
    from .worker import Worker

    if args.day is not None:
        try:
            day = datetime.strptime(args.day, "%Y-%m-%d").date()
        except ValueError:
            print(f"Не разобрать дату: {args.day}", file=sys.stderr)
            return 2
    else:
        day = date.today()

    with Worker(settings) as worker:
        result = worker.export_day(day, with_transcript=args.with_transcript)

    if result["note"] is None:
        print(f"За {day.isoformat()} закрытых разговоров нет — заметка не писалась.")
    else:
        print(f"Заметка дня: {result['note']}")
    if result["raw"] is not None:
        print(f"Расшифровка: {result['raw']}")
    print(f"Задач выгружено: {result['tasks']}")
    return 0


def cmd_show(args, settings: Settings) -> int:
    """Разговор целиком в терминал — дословный текст вместо vault."""
    with Storage(settings.db_path) as storage:
        conversation = storage.get_conversation(args.id)
        if conversation is None:
            print(f"Разговора {args.id} нет.", file=sys.stderr)
            return 1

        started = datetime.fromtimestamp(conversation["started_at"])
        ended = datetime.fromtimestamp(conversation["ended_at"])
        duration = conversation["ended_at"] - conversation["started_at"]
        print(f"Разговор {conversation['id']} · {started:%d.%m.%Y}")
        print(
            f"  {started:%H:%M}–{ended:%H:%M} ({_fmt_duration(duration)})"
            f", статус: {conversation['status']}"
        )
        if conversation["summary"]:
            print()
            print(conversation["summary"])

        facts = storage.fact_rows_of(args.id)
        if facts:
            print()
            print("Факты:")
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
            print("Задачи:")
            for row in tasks:
                parts = [row["text"]]
                if row["assignee"]:
                    parts.append(f"({row['assignee']})")
                if row["due"]:
                    parts.append(f"— до {row['due']}")
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
            print("Реплики:")
            for row in rows:
                mark = " [приват]" if row.is_private else ""
                print(
                    f"  [{datetime.fromtimestamp(row.started_at):%H:%M}]"
                    f" {row.text}{mark}"
                )
        if hidden and not args.private:
            print()
            print(f"(скрыто приватных реплик: {hidden}, показать — --private)")
    return 0


def cmd_prune_raw(args, settings: Settings) -> int:
    """TTL сырых расшифровок: без --yes только показывает просроченное."""
    from .export.obsidian import expired_raw_notes

    ttl = args.ttl_days if args.ttl_days is not None else settings.raw_note_ttl_days
    expired = expired_raw_notes(settings.vault_raw_dir, ttl_days=ttl)
    if not expired:
        print("Просроченных расшифровок нет.")
        return 0
    for path in expired:
        print(path)

    if not args.yes:
        print("удалить: добавьте --yes")
        return 0

    removed = 0
    for path in expired:
        try:
            path.unlink()
        except OSError as exc:
            print(f"не удалось удалить {path}: {exc}", file=sys.stderr)
            continue
        removed += 1
    print(f"Удалено файлов: {removed}")
    return 0


def cmd_backup(args, settings: Settings) -> int:
    with Storage(settings.db_path) as storage:
        path = storage.backup(args.destination)
    print(f"Копия базы: {path}")
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
            print(f"Не найден файл: {path}", file=sys.stderr)
            return 2

    try:
        result = transcribe_for_wer(
            audio_path, settings, backend=args.backend, no_vad=args.no_vad
        )
    except NoSpeechFound:
        print("VAD не нашёл речи — замерять нечего.", file=sys.stderr)
        return 1

    hypothesis = result.hypothesis
    backend_name = result.backend
    audio_seconds = result.audio_seconds
    processing_seconds = result.processing_seconds

    reference = reference_path.read_text(encoding="utf-8")
    report = compute_wer(reference, hypothesis)

    note = (
        f"VAD {'выключен' if args.no_vad else 'включён'}, "
        f"модель {settings.local_model if backend_name == 'local' else settings.groq_model}"
    )
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

    out_dir = Path(args.out_dir) if args.out_dir else reference_path.parent / "замеры"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_path = out_dir / f"{stamp}-{backend_name}.md"
    out_path.write_text(text, encoding="utf-8")
    (out_dir / f"{stamp}-{backend_name}-распознано.txt").write_text(
        hypothesis, encoding="utf-8"
    )
    print(f"Отчёт: {out_path}")

    return 0 if report.passed(args.threshold) else 1


def cmd_homecoming(args, settings: Settings) -> int:
    """Приёмка возвращения: окружение → эталон → замер WER → вердикт A1."""
    from datetime import datetime
    from pathlib import Path

    from . import homecoming
    from .homecoming import NoSpeechFound, find_reference, render_verdict
    from .wer import compute_wer, render_report

    wer_dir = Path(args.wer_dir)
    out_dir = Path(args.out_dir) if args.out_dir else wer_dir / "замеры"

    print("Приёмка возвращения — гейт A1")
    print()

    # Шаг 1. Окружение: что готово, чего не хватает.
    print("Шаг 1. Окружение")
    blocking: list[str] = []
    if args.skip_env:
        print("пропущено флагом --skip-env")
    else:
        for check in homecoming.check_environment(settings):
            mark = "✅" if check.ok else ("❌" if check.blocking else "⚠️")
            print(f"{mark} {check.name} — {check.detail}")
            if check.blocking and not check.ok:
                blocking.append(f"{check.name}: {check.detail}")
    print()

    # Шаг 2. Эталон: без записи и вычитки дальше идти некуда.
    print("Шаг 2. Эталон")
    state = find_reference(wer_dir, args.name)
    if not (state.audio_ok and state.text_ok):
        script = PROJECT_ROOT / "tests" / "wer" / "текст-для-начитки.md"
        print(f"  {'✅' if state.audio_ok else '❌'} аудио: {state.audio}")
        print(f"  {'✅' if state.text_ok else '❌'} текст: {state.text}")
        print("Эталона нет или он не готов. Что сделать:")
        print("  1. Запиши 3 минуты речи: кулон на груди, как носишь обычно,")
        print(f"     обычный темп; текст начитки — {script}")
        print(f"  2. Положи аудио сюда: {state.audio}")
        print(
            "     (16 кГц моно WAV; сконвертировать: "
            "ffmpeg -i <файл> -ac 1 -ar 16000 <имя>.wav)"
        )
        print(f"  3. Расшифруй дословно руками сюда: {state.text}")
        print("     (черновик можно получить моделью, вычитка обязательна)")
        print("  4. После этого запусти эту же команду ещё раз.")
        if state.problems:
            print("Проблемы:")
            for problem in state.problems:
                print(f"  — {problem}")
        return 2
    print(f"Эталон на месте: {state.audio.name} + {state.text.name}")
    print()

    # Готовый текст снимает зависимость от окружения: STT не нужен вовсе.
    if blocking and args.hypothesis is None:
        print(
            "Без этого WER не посчитать (или передай --hypothesis с готовым текстом):"
        )
        for item in blocking:
            print(f"  — {item}")
        return 3

    # Шаг 3. Замер.
    print("Шаг 3. Замер")
    if args.hypothesis is not None:
        hypothesis = Path(args.hypothesis).read_text(encoding="utf-8")
        backend_name = "готовый-текст"
        audio_seconds = 0.0
        processing_seconds = 0.0
        rtf = None
        note = "готовый распознанный текст из файла, STT не запускался"
    else:
        try:
            result = homecoming.transcribe_for_wer(
                state.audio, settings, backend=args.backend, no_vad=args.no_vad
            )
        except NoSpeechFound:
            print("VAD не нашёл речи — замерять нечего.", file=sys.stderr)
            return 1
        except Exception as exc:  # модель не грузится и прочее — без трейсбэка
            print(f"Расшифровка не удалась: {exc}", file=sys.stderr)
            return 3
        hypothesis = result.hypothesis
        backend_name = result.backend
        audio_seconds = result.audio_seconds
        processing_seconds = result.processing_seconds
        rtf = processing_seconds / audio_seconds if audio_seconds else None
        note = (
            f"VAD {'выключен' if args.no_vad else 'включён'}, "
            f"модель {settings.local_model if backend_name == 'local' else settings.groq_model}"
        )

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
    out_path = out_dir / f"{stamp}-приёмка-{backend_name}.md"
    out_path.write_text(text + "\n" + verdict, encoding="utf-8")
    if args.hypothesis is None:
        (out_dir / f"{stamp}-приёмка-{backend_name}-распознано.txt").write_text(
            hypothesis, encoding="utf-8"
        )

    print()
    print(verdict)
    print()
    print(f"Отчёт: {out_path}")

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
        print(f"ярлык создан: {path}")
    elif args.action == "remove":
        if autostart.remove():
            print("ярлык удалён")
        else:
            print("автозапуск не настроен")
    else:
        state = autostart.status()
        if state["installed"]:
            print(f"автозапуск настроен: {state['target']} {state['arguments']}")
        else:
            print("автозапуск не настроен")
    return 0


_STOP_REASONS = {
    "stop-file": "стоп-файл",
    "time-limit": "предел времени",
    "stt-fused": "предохранитель STT (локальный бэкенд отказал подряд)",
    "llm-fused": "предохранитель разбора (локальные модели отказали подряд)",
}


def _print_ingest(stats) -> None:
    for title, value in (
        ("восстановлено фрагментов", stats.recovered),
        ("обработано фрагментов", stats.chunks),
        ("создано реплик", stats.utterances),
        ("дублей реплик", stats.duplicates),
        ("фрагментов без речи", stats.silent),
        ("фрагментов с ошибкой", stats.failed),
        ("фрагментов с чужой кодировкой", stats.undecodable),
    ):
        print(f"{title}: {value}")


def _print_batch(stats) -> None:
    for title, value in (
        ("восстановлено фрагментов", stats.recovered),
        ("склеено обрывков на стыках", stats.stitched),
        ("расшифровано реплик", stats.transcribed),
        ("ошибок расшифровки", stats.failed),
        ("пачек", stats.batches),
        ("собрано разговоров", stats.conversations),
        ("разобрано NLU", stats.analysed),
        ("отсеяно фильтром разговора", stats.skipped),
        ("сбоев NLU", stats.nlu_failed),
        ("реплик принято", stats.accepted),
        ("реплик отклонено", stats.rejected),
        ("заметок дня записано", stats.notes),
        ("расшифровок записано", stats.raw_notes),
        ("задач выгружено", stats.exported_tasks),
        ("моделей выгружено из VRAM", stats.unloaded),
        ("удалено аудио по TTL", stats.purged_audio),
        ("удалено фрагментов очереди по TTL", stats.purged_chunks),
    ):
        print(f"{title}: {value}")
    if stats.stopped_by is not None:
        print(
            f"батч остановлен: {_STOP_REASONS.get(stats.stopped_by, stats.stopped_by)}"
        )


def cmd_worker(args, settings: Settings) -> int:
    # Импорт внутри функции: `uzel --help` не должен тянуть onnxruntime.
    from .worker import Worker

    with Worker(settings) as worker:
        if args.action == "recover":
            print(f"возвращено в очередь: {worker.recover()}")
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


_LLM_PROBE_QUESTION = "Ответь одним словом: столица России?"

# Известные облачные бэкенды: имя настройки → пояснение для человека.
_KNOWN_CLOUD_BACKENDS = {
    "gateway": "облачный провайдер шлюза",  # локальный порт, облачный счёт (llm.GatewayBackend)
    "groq": "США",
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
                        [{"role": "user", "content": _LLM_PROBE_QUESTION}],
                        max_tokens=32,
                    )
                    entry["probe_latency_s"] = round(result.latency_s, 1)
                    entry["probe_reply"] = result.text[:60]
                except Exception as exc:  # отказ проверки не роняет команду
                    entry["probe_error"] = str(exc)[:200]
        else:
            entry["ready"] = False
            reason = getattr(backend, "probe_error", None)
            entry["probe_error"] = reason or "не настроен"
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
                "cloud_note": note,
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
        print(
            "предупреждение: среди включённых моделей нет локальной — "
            "штатный разбор невозможен, а облако запрещено",
            file=sys.stderr,
        )
        return 1
    return 0


def _print_llm_check(entries: list[dict], local_ready: bool) -> None:
    first_enabled = True
    for entry in entries:
        if entry["enabled"]:
            kind = "ОБЛАКО" if entry["is_cloud"] else "локально"
            if entry["ready"]:
                status = "готова"
                if "probe_latency_s" in entry:
                    status += f" · {entry['probe_latency_s']} с: {entry['probe_reply']}"
            else:
                status = f"НЕ готова: {entry['probe_error']}"
            prefix = "модель разбора: " if first_enabled else "  "
            first_enabled = False
            print(f"{prefix}{entry['name']} ({kind}), {entry['model']} — {status}")
        else:
            print(
                f"  {entry['name']} (ОБЛАКО, {entry['cloud_note']})"
                " — выключена в настройках"
            )
    if local_ready:
        from .privacy import cloud_policy_line

        print(
            cloud_policy_line(
                [e["name"] for e in entries if e["enabled"] and e["is_cloud"]]
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uzel", description="Ядро УЗЕЛ")
    parser.add_argument("--log-level", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="поиск по памяти")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--plain", action="store_true", help="только подстрочный поиск")
    search.set_defaults(func=cmd_search)

    stats = subparsers.add_parser("stats", help="что накоплено")
    stats.set_defaults(func=cmd_stats)

    quality = subparsers.add_parser("quality-report", help="сводка фильтра качества")
    quality.add_argument("--day", default=None, help="сутки ГГГГ-ММ-ДД")
    quality.add_argument(
        "--limit", type=int, default=0, help="показать N примеров отклонённых реплик"
    )
    quality.set_defaults(func=cmd_quality_report)

    privacy = subparsers.add_parser("privacy", help="стоп-режимы записи")
    privacy.add_argument(
        "action",
        choices=("status", "mute", "unmute", "pause"),
        nargs="?",
        default="status",
    )
    privacy.add_argument("minutes", type=float, nargs="?", default=10.0)
    privacy.set_defaults(func=cmd_privacy)

    purge = subparsers.add_parser("purge", help="удалить сырое аудио по TTL")
    purge.add_argument("--ttl-days", type=int, default=None)
    purge.set_defaults(func=cmd_purge)

    export = subparsers.add_parser(
        "export", help="перевыгрузка суток из базы без расшифровки и NLU"
    )
    export.add_argument(
        "--day", default=None, help="сутки ГГГГ-ММ-ДД; без значения — сегодня"
    )
    export.add_argument(
        "--with-transcript",
        action="store_true",
        help="дополнительно файл сырой расшифровки",
    )
    export.set_defaults(func=cmd_export)

    show = subparsers.add_parser("show", help="разговор целиком в терминал")
    show.add_argument("id", type=int)
    show.add_argument(
        "--private",
        action="store_true",
        help="показать и приватные реплики с пометкой [приват]",
    )
    show.set_defaults(func=cmd_show)

    prune_raw = subparsers.add_parser(
        "prune-raw", help="TTL сырых расшифровок в Obsidian"
    )
    prune_raw.add_argument(
        "--ttl-days", type=int, default=None, help="по умолчанию из настроек"
    )
    prune_raw.add_argument(
        "--yes", action="store_true", help="действительно удалить файлы"
    )
    prune_raw.set_defaults(func=cmd_prune_raw)

    backup = subparsers.add_parser("backup", help="копия базы")
    backup.add_argument("destination")
    backup.set_defaults(func=cmd_backup)

    wer = subparsers.add_parser("wer", help="замер качества распознавания на эталоне")
    wer.add_argument("audio", help="WAV 16 кГц моно")
    wer.add_argument("reference", help="выверенный вручную транскрипт")
    wer.add_argument("--backend", choices=("local", "groq"), default=None)
    wer.add_argument("--threshold", type=float, default=0.15)
    wer.add_argument("--no-vad", action="store_true", help="без отсечения тишины")
    wer.add_argument("--out-dir", default=None)
    wer.set_defaults(func=cmd_wer)

    homecoming_cmd = subparsers.add_parser(
        "приёмка-возвращения",
        aliases=["homecoming"],  # ASCII-имя нужно для cmd.exe
        help="окружение, эталон, WER и вердикт по A1 (одной командой)",
    )
    homecoming_cmd.add_argument(
        "--wer-dir",
        default=PROJECT_ROOT / "tests" / "wer",
        help="папка с эталоном (по умолчанию tests/wer)",
    )
    homecoming_cmd.add_argument("--name", default="эталон-01", help="имя эталона")
    homecoming_cmd.add_argument(
        "--hypothesis",
        default=None,
        help="готовый распознанный текст: STT не запускается, RTF не считается",
    )
    homecoming_cmd.add_argument("--backend", choices=("local", "groq"), default=None)
    homecoming_cmd.add_argument("--threshold", type=float, default=0.15)
    homecoming_cmd.add_argument(
        "--no-vad", action="store_true", help="без отсечения тишины"
    )
    homecoming_cmd.add_argument("--out-dir", default=None)
    homecoming_cmd.add_argument(
        "--skip-env", action="store_true", help="не проверять окружение"
    )
    homecoming_cmd.set_defaults(func=cmd_homecoming)

    serve = subparsers.add_parser("serve", help="поднять ядро")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=cmd_serve)

    app = subparsers.add_parser("app", help="окно и трей на ПК")
    app.add_argument("--host", default=None, help="адрес ядра")
    app.add_argument("--port", type=int, default=None, help="порт ядра")
    app.add_argument("--no-window", action="store_true", help="только сервер и трей")
    app.add_argument("--no-tray", action="store_true", help="без трея")
    app.add_argument(
        "--autoclose",
        type=float,
        default=None,
        help="закрыть окно через N секунд (для проверок)",
    )
    app.set_defaults(func=cmd_app)

    autostart = subparsers.add_parser("autostart", help="запуск вместе с системой")
    autostart.add_argument("action", choices=("install", "remove", "status"))
    autostart.set_defaults(func=cmd_autostart)

    worker = subparsers.add_parser("worker", help="воркер конвейера")
    worker_sub = worker.add_subparsers(dest="action", required=True)

    worker_recover = worker_sub.add_parser(
        "recover", help="вернуть прерванное в очередь"
    )
    worker_recover.set_defaults(func=cmd_worker)

    worker_ingest = worker_sub.add_parser(
        "ingest", help="дешёвый проход: очередь → VAD → реплики"
    )
    worker_ingest.add_argument(
        "--limit", type=int, default=None, help="сколько фрагментов взять за раз"
    )
    worker_ingest.add_argument(
        "--loop", action="store_true", help="крутить цикл до стоп-файла"
    )
    worker_ingest.add_argument(
        "--interval", type=float, default=None, help="пауза между проходами, с"
    )
    worker_ingest.set_defaults(func=cmd_worker)

    worker_batch = worker_sub.add_parser(
        "batch", help="ночной батч: расшифровка → разговоры → NLU → экспорт"
    )
    worker_batch.add_argument("--batch-size", type=int, default=None)
    worker_batch.add_argument("--time-limit", type=float, default=None)
    worker_batch.add_argument("--max-batches", type=int, default=None)
    worker_batch.add_argument("--close-after", type=float, default=None)
    worker_batch.add_argument(
        "--no-export", action="store_true", help="не писать в Obsidian"
    )
    worker_batch.add_argument(
        "--with-transcript",
        action="store_true",
        help="писать файлы сырой расшифровки поверх настройки export_transcript",
    )
    worker_batch.set_defaults(func=cmd_worker)

    llm_check = subparsers.add_parser(
        "llm-check", help="чем разбираются разговоры и не уходит ли в облако"
    )
    llm_check.add_argument(
        "--probe",
        action="store_true",
        help="реальный короткий запрос к каждой доступной модели",
    )
    llm_check.add_argument(
        "--json", action="store_true", help="те же сведения машинным форматом"
    )
    llm_check.set_defaults(func=cmd_llm_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(args.log_level or settings.log_level)
    return args.func(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
