"""Точка входа моста.

python -m uzel_bridge --scan                     поиск кулона в эфире
python -m uzel_bridge --uid dev-1                приём и отправка в ядро
python -m uzel_bridge --stats                    счётчики прошивки УЗЛА
python -m uzel_bridge --dfu firmware/out/uzel-fw-0.1.0.hex   обновление по воздуху
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from uzel.i18n import t

from .ble import OmiBleSource, discover
from .pipeline import AudioPipeline
from .spool import CoreClient, Spool, Uploader

log = logging.getLogger("uzel_bridge")


def default_spool_dir() -> Path:
    """Буфер сырого звука — рядом с данными ядра, вне проекта.

    P11.2, находка 8: прежнее относительное ``data/bridge-spool`` при запуске
    из корня проекта ложилось на Рабочий стол, а он синкается в Google Drive.
    """
    data_dir = os.environ.get("UZEL_DATA_DIR")
    base = Path(data_dir).expanduser() if data_dir else Path.home() / "uzel-data"
    return base / "bridge-spool"


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


async def cmd_scan(args) -> int:
    print(t("bridge.scanning"))
    devices = await discover(args.scan_timeout)
    if not devices:
        print(t("bridge.scan_empty"))
        return 1
    for device in devices:
        rssi = f"{device.rssi} dBm" if device.rssi is not None else "?"
        print(f"  {device.address}  {device.name or t('bridge.no_name'):20s}  {rssi}")
    return 0


async def _resolve(args) -> str | None:
    if args.address:
        return args.address
    devices = await discover(args.scan_timeout)
    if not devices:
        print(t("bridge.not_found"))
        return None
    return devices[0].address


async def cmd_stats(args) -> int:
    """Счётчики прошивки УЗЛА: звук, очередь, время кодирования, сопряжения."""
    from bleak import BleakClient

    from .ble import ensure_paired
    from .protocol import DIS_FIRMWARE_REV_UUID, UZEL_STATS_UUID, parse_stats

    address = await _resolve(args)
    if address is None:
        return 1
    async with BleakClient(address) as client:
        await ensure_paired(client)
        rev = await client.read_gatt_char(DIS_FIRMWARE_REV_UUID)
        revision = bytes(rev).decode(errors="replace")
        print(t("bridge.stats.header", address=address, revision=revision))
        try:
            stats = parse_stats(await client.read_gatt_char(UZEL_STATS_UUID))
        except Exception as exc:
            print(t("bridge.stats.none", error=exc))
            return 1
    for key, value in stats.items():
        print(f"  {key:28s} {value}")
    return 0


async def cmd_dfu(args) -> int:
    """Обновление прошивки по воздуху из .hex (firmware/out/uzel-fw-*.hex)."""
    from .dfu import DfuError, update_firmware

    address = await _resolve(args)
    if address is None:
        return 1

    last = [-1]

    def progress(done: int, total: int) -> None:
        percent = done * 100 // total
        if percent // 5 != last[0] // 5:
            print(t("bridge.dfu.progress", percent=percent, done=done, total=total), flush=True)
            last[0] = percent

    try:
        await update_firmware(Path(args.dfu), address, progress=progress)
    except DfuError as exc:
        print(t("bridge.dfu.failed", error=exc))
        return 1
    print(t("bridge.done"))
    return 0


async def cmd_run(args) -> int:
    spool = Spool(Path(args.spool_dir))
    client = CoreClient(
        args.core_url, uid=args.uid, source=args.source, sample_rate=args.sample_rate
    )
    uploader = Uploader(client, spool)
    pipeline = AudioPipeline(
        uploader, sample_rate=args.sample_rate, flush_seconds=args.flush_seconds
    )
    source = OmiBleSource(
        args.address,
        scan_timeout_s=args.scan_timeout,
        reconnect_max_s=args.reconnect_max,
    )

    if spool.count():
        log.info("в буфере с прошлого раза: %d фрагментов, досылаю", spool.count())
        uploader.flush()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            try:
                loop.add_signal_handler(getattr(signal, signame), stop.set)
            except NotImplementedError:
                pass  # Windows: обойдёмся KeyboardInterrupt

    async def report() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.report_seconds)
            except TimeoutError:
                pass
            stats = source.stats.as_dict()
            log.info(
                "пакетов %(пакетов)d, потеряно %(потеряно)d, батарея %(батарея_%)s%%, "
                "аудио %(байт_аудио)d Б | отправлено %(sent)d, в буфере %(spool)d",
                {**stats, "sent": uploader.sent, "spool": spool.count()},
            )
            # Тем же тактом — в ядро, чтобы окно на ПК показывало заряд
            # и разрывы, а не только журнал моста.
            client.report_status(stats, spooled=spool.count())

    reporter = asyncio.create_task(report())
    try:
        await source.run(pipeline.feed, stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        reporter.cancel()
        pipeline.flush()
        uploader.flush()
        log.info(
            "итог: %s | отправлено %d, осталось в буфере %d",
            source.stats.as_dict(),
            uploader.sent,
            spool.count(),
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uzel-bridge", description=t("bridge.description")
    )
    parser.add_argument(
        "--scan", action="store_true", help=t("bridge.help.scan")
    )
    parser.add_argument(
        "--stats", action="store_true", help=t("bridge.help.stats")
    )
    parser.add_argument(
        "--dfu",
        metavar="HEX",
        help=t("bridge.help.dfu"),
    )
    parser.add_argument("--address", help=t("bridge.help.address"))
    parser.add_argument("--uid", default="pendant-1", help=t("bridge.help.uid"))
    parser.add_argument(
        "--core-url", default="http://127.0.0.1:8770", help=t("bridge.help.core_url")
    )
    parser.add_argument("--source", default="bridge-pc")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument(
        "--spool-dir",
        default=str(default_spool_dir()),
        help=t("bridge.help.spool"),
    )
    parser.add_argument("--flush-seconds", type=float, default=5.0)
    parser.add_argument("--report-seconds", type=float, default=30.0)
    parser.add_argument("--scan-timeout", type=float, default=8.0)
    parser.add_argument(
        "--reconnect-max",
        type=float,
        default=60.0,
        help=t("bridge.help.reconnect"),
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.log_level)
    if args.scan:
        command = cmd_scan
    elif args.stats:
        command = cmd_stats
    elif args.dfu:
        command = cmd_dfu
    else:
        command = cmd_run
    try:
        return asyncio.run(command(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
