"""Долгий прогон эмулятора: часы жизни кулона одним процессом (P10).

Запуск::

    .venv\\Scripts\\python.exe -m tests.emulator.run --hours 12 \\
        --speech-ratio 0.25 --drop-rate 0.01 --disconnect-every 30m \\
        --target http://127.0.0.1:8770 --uid эмулятор-1 \\
        --report ops\\прогон-p10\\поток.json

Прогресс печатается без буферизации (грабля проекта: прогресс, невидимый до
конца прогона). Останов — Ctrl+C или стоп-файл ``STOP`` рядом с отчётом; в
обоих случаях отчёт дописывается. Ускорение ``--speed 60`` — условный час за
реальную минуту.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from uzel_bridge.protocol import parse_packet

from tests.emulator.device import FRAME_MS, EmulatedPendant
from tests.emulator.scenario import FIXTURE_PATH, Scenario, SpeechMaterial
from tests.emulator.transport import HttpTransport

BATCH_FRAMES = 250  # 5 условных секунд аудио на один HTTP-запрос
REPORT_EVERY_S = 30.0  # переписывать отчёт не реже, чем раз в реальных 30 с


def parse_duration(text: str) -> float:
    """«30m», «90s», «1.5h», «45» → секунды (голое число — секунды)."""
    value = text.strip().lower()
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0}
    if value and value[-1] in multipliers:
        number = value[:-1]
        factor = multipliers[value[-1]]
    else:
        number, factor = value, 1.0
    try:
        seconds = float(number) * factor
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"не разобрать длительность {text!r}: пишите как 30m, 90s или 1.5h"
        ) from None
    if seconds < 0:
        raise argparse.ArgumentTypeError(
            f"длительность не может быть отрицательной: {text}"
        )
    return seconds


def parse_clock_start(text: str) -> float:
    """``2026-09-23T23:30`` (местное время) → эпоха в секундах."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"не понимаю время {text!r}, нужно ГГГГ-ММ-ДДTЧЧ:ММ"
        ) from exc


def format_clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


class RunReport:
    """Счётчики прогона и выгрузка их в JSON по ходу и в конце."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.path = Path(args.report)
        self.data: dict = {
            "параметры": {
                "часов": args.hours,
                "доля_речи": args.speech_ratio,
                "доля_потерь": args.drop_rate,
                "разрыв_каждых_с": args.disconnect_every,
                "разрыв_длится_с": args.disconnect_for,
                "батарея_часов": args.battery_hours,
                "пауза_каждых_с": args.pause_every,
                "пауза_длится_с": args.pause_for,
                "часы_старт": args.clock_start,
                "seed": args.seed,
                "ускорение": args.speed,
                "uid": args.uid,
                "target": args.target,
            },
            "завершение": None,
            "условное_время_с": 0.0,
            "отправлено_пакетов": 0,
            "потеряно_пакетов": 0,
            "отправлено_кадров": 0,
            "байт_отправлено": 0,
            "секунд_речи": 0.0,
            "секунд_тишины": 0.0,
            "разрывов": 0,
            "ответов_202": 0,
            "ответов_204": 0,
            "ошибок_http": 0,
            "батарея_процент": 100,
            "реальное_время_с": 0.0,
        }
        self._started = time.monotonic()

    def save(self) -> None:
        """Атомарно перезаписывает отчёт: и по ходу, и в конце."""
        self.data["реальное_время_с"] = round(time.monotonic() - self._started, 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    def line(self) -> str:
        d = self.data
        return (
            f"{format_clock(d['условное_время_с'])} условного времени | "
            f"пакетов {d['отправлено_пакетов']}, потеряно {d['потеряно_пакетов']} | "
            f"202: {d['ответов_202']} 204: {d['ответов_204']} ошибки: {d['ошибок_http']} | "
            f"разрывов {d['разрывов']} | батарея {d['батарея_процент']} %"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests.emulator.run",
        description="Эмулятор кулона для долгого прогона против ядра УЗЕЛ",
    )
    parser.add_argument(
        "--hours", type=float, default=1.0, help="сколько условных часов жить"
    )
    parser.add_argument(
        "--speech-ratio", type=float, default=0.25, help="доля речи в потоке"
    )
    parser.add_argument(
        "--drop-rate", type=float, default=0.01, help="доля теряемых пакетов"
    )
    parser.add_argument(
        "--disconnect-every",
        type=parse_duration,
        default=1800.0,
        help="разрыв связи каждые … (30m, 90s, 1.5h; голое число — секунды)",
    )
    parser.add_argument(
        "--disconnect-for",
        type=parse_duration,
        default=10.0,
        help="длительность разрыва (те же единицы)",
    )
    parser.add_argument(
        "--pause-every",
        type=parse_duration,
        default=0.0,
        help="долгая тишина каждые … потока (0 — никогда); нужна, чтобы закрывались разговоры",
    )
    parser.add_argument(
        "--pause-for",
        type=parse_duration,
        default=0.0,
        help="длительность долгой тишины (больше conversation_gap_s = 5m)",
    )
    parser.add_argument(
        "--clock-start",
        type=parse_clock_start,
        default=None,
        help="часы устройства в начале прогона: ГГГГ-ММ-ДДTЧЧ:ММ (по умолчанию — сейчас)",
    )
    parser.add_argument(
        "--battery-hours",
        type=float,
        default=24.0,
        help="за сколько часов садится батарея",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="зерно генератора: один seed — один поток"
    )
    parser.add_argument("--target", default="http://127.0.0.1:8770", help="адрес ядра")
    parser.add_argument("--uid", default="эмулятор-1", help="идентификатор устройства")
    parser.add_argument(
        "--source",
        default="android",
        choices=["android", "bridge-pc"],
        help="кто шлёт в ядро",
    )
    parser.add_argument(
        "--report", default=r"ops\прогон-p10\поток.json", help="куда писать отчёт JSON"
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="ускорение: 60 — условный час за минуту",
    )
    parser.add_argument(
        "--fixture", default=str(FIXTURE_PATH), help="речевая фикстура WAV 16 кГц"
    )
    parser.add_argument(
        "--batch-frames",
        type=int,
        default=BATCH_FRAMES,
        help="кадров на один HTTP-запрос",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="не печатать прогресс в терминал"
    )
    return parser


def run_stream(args: argparse.Namespace, poster=None) -> dict:
    """Сам прогон. Возвращает итоговый отчёт (и дописывает его на диск)."""
    scenario = Scenario(
        speech_ratio=args.speech_ratio,
        drop_rate=args.drop_rate,
        disconnect_every_s=args.disconnect_every,
        disconnect_for_s=args.disconnect_for,
        battery_hours=args.battery_hours,
        pause_every_s=args.pause_every,
        pause_for_s=args.pause_for,
        seed=args.seed,
    )
    material = SpeechMaterial(scenario, args.fixture)
    pendant = EmulatedPendant(material, battery_hours=args.battery_hours)
    transport = HttpTransport(
        args.target, uid=args.uid, source=args.source, poster=poster
    )
    report = RunReport(args)
    stop_file = report.path.parent / "STOP"
    report.path.parent.mkdir(parents=True, exist_ok=True)

    batch_seconds = args.batch_frames * FRAME_MS / 1000.0
    target_seconds = args.hours * 3600.0
    finish_reason = "конец времени"
    last_save = time.monotonic()
    clock_start = time.time() if args.clock_start is None else args.clock_start

    def progress(force: bool = False) -> None:
        nonlocal last_save
        now = time.monotonic()
        if args.quiet:
            if force or now - last_save >= REPORT_EVERY_S:
                report.save()
                last_save = now
            return
        print(report.line(), flush=True)  # без буферизации — иначе прогресс не виден
        if force or now - last_save >= REPORT_EVERY_S:
            report.save()
            last_save = now

    try:
        while pendant.emulated_seconds < target_seconds:
            if stop_file.exists():
                finish_reason = "стоп-файл"
                break

            if not scenario.is_connected(pendant.emulated_seconds):
                # Разрыв связи: условное время идёт, звука нет, счётчик
                # устройства после переподключения начнётся заново.
                report.data["разрывов"] += 1
                pendant.reset_link()
                pendant.skip(scenario.disconnect_for_s)
                time.sleep(min(scenario.disconnect_for_s, 1.0) / max(args.speed, 1.0))
                continue

            packets = pendant.notifications(args.batch_frames)
            frames = []
            for packet in packets:
                if scenario.should_drop():
                    report.data["потеряно_пакетов"] += 1
                    continue
                parsed = parse_packet(packet)
                if parsed is not None and parsed.payload:
                    frames.append(parsed.payload)
            # device_time_ms — эпоха в мс, как у моста на ПК (time.time()) и
            # Android (currentTimeMillis): по нему ядро решает приватность и
            # ставит время реплик. Секунды от старта прогона давали 1970 год.
            transport.send_frames(
                frames,
                device_time_ms=int((clock_start + pendant.emulated_seconds) * 1000),
            )

            report.data["отправлено_пакетов"] += len(frames)
            report.data["отправлено_кадров"] += len(frames)
            report.data["байт_отправлено"] = transport.bytes_sent
            report.data["секунд_речи"] = round(pendant.speech_seconds, 1)
            report.data["секунд_тишины"] = round(pendant.silence_seconds, 1)
            report.data["условное_время_с"] = round(pendant.emulated_seconds, 1)
            report.data["ответов_202"] = transport.accepted
            report.data["ответов_204"] = transport.muted
            report.data["ошибок_http"] = transport.errors
            report.data["батарея_процент"] = pendant.battery_percent
            progress()

            # реальный темп: условное время, делённое на ускорение
            time.sleep(batch_seconds / max(args.speed, 0.001))
    except KeyboardInterrupt:
        finish_reason = "ctrl+c"
    finally:
        report.data["завершение"] = finish_reason
        report.data["батарея_процент"] = pendant.battery_percent
        progress(force=True)

    if report.data["потеряно_пакетов"] == 0 and args.drop_rate > 0:
        print(
            "предупреждение: потери заданы, но не выпало ни одного — "
            "короткий прогон или зёрна не хватило",
            file=sys.stderr,
            flush=True,
        )
    return report.data


def main(argv: list[str] | None = None, poster=None) -> int:
    args = build_parser().parse_args(argv)
    if args.speed <= 0:
        raise SystemExit("--speed должен быть больше нуля")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # кириллица в консоли Windows
        except (OSError, ValueError):
            pass
    report = run_stream(args, poster=poster)
    print(
        f"готово ({report['завершение']}): "
        f"пакетов {report['отправлено_пакетов']}, потеряно {report['потеряно_пакетов']}, "
        f"разрывов {report['разрывов']}, речь {report['секунд_речи']} с, "
        f"тишина {report['секунд_тишины']} с, батарея {report['батарея_процент']} %",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
