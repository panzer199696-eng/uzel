"""Стоп-режимы записи (FR-070, FR-071).

Главное свойство: заглушённый звук отбрасывается **до записи на диск**. Не
«не обрабатываем», не «помечаем и удаляем потом» — не сохраняется вовсе.
Иначе обещание «для рабочих переговоров можно» ничего не стоит.

Состояние ручной заглушки живёт в файле: перезапуск ядра не имеет права
случайно включить запись обратно.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

from . import i18n
from .i18n import t

log = logging.getLogger(__name__)

# Понедельник = 0, как в datetime.weekday(). Русские сокращения — под старым
# именем; в расписании понимаются сокращения ЛЮБОГО языка каталогов
# (пн / mon), а показываются — на языке интерфейса.
WEEKDAY_NAMES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _weekday_tokens() -> dict[str, int]:
    tokens = {name: index for index, name in enumerate(WEEKDAY_NAMES)}
    for names in i18n.data_all("privacy.weekdays"):
        for index, name in enumerate(names):
            tokens[str(name).lower()] = index
    return tokens


@dataclass(frozen=True)
class QuietWindow:
    """Интервал, в который запись выключена по расписанию."""

    start: dtime
    end: dtime
    weekdays: frozenset[int] = frozenset(range(7))

    def contains(self, moment: datetime) -> bool:
        if moment.weekday() not in self.weekdays:
            return False
        current = moment.time()
        if self.start <= self.end:
            return self.start <= current < self.end
        # окно через полночь, например 22:00–06:00
        return current >= self.start or current < self.end

    def describe(self) -> str:
        """Окно для человека на языке интерфейса: «10:00–12:00 пнвт»."""
        names = i18n.data("privacy.weekdays", default=list(WEEKDAY_NAMES))
        days = (
            t("privacy.weekday_sep").join(names[d] for d in sorted(self.weekdays))
            if len(self.weekdays) < 7
            else t("privacy.daily")
        )
        return t(
            "privacy.window",
            start=f"{self.start:%H:%M}",
            end=f"{self.end:%H:%M}",
            days=days,
        )


def parse_window(spec: str) -> QuietWindow:
    """Разбирает запись вида ``10:00-12:00`` или ``10:00-12:00/пн,вт,ср``."""
    spec = spec.strip()
    if not spec:
        raise ValueError("пустое описание окна")

    days_part = None
    if "/" in spec:
        spec, days_part = spec.split("/", 1)
        # Опечатка вроде «10:00-12:00/» не должна молча означать «ежедневно»:
        # для стоп-зоны ошибиться в сторону «пишем всегда» недопустимо.
        if not days_part.strip():
            raise ValueError("после «/» ожидается список дней недели")

    try:
        start_raw, end_raw = spec.split("-", 1)
        start = dtime.fromisoformat(start_raw.strip())
        end = dtime.fromisoformat(end_raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"не разобрал окно «{spec}», нужен формат ЧЧ:ММ-ЧЧ:ММ"
        ) from exc

    if days_part:
        weekdays = set()
        known = _weekday_tokens()
        for token in days_part.replace(" ", "").split(","):
            if not token:
                continue
            if token.lower() not in known:
                raise ValueError(f"неизвестный день недели «{token}»")
            weekdays.add(known[token.lower()])
        if not weekdays:
            raise ValueError("список дней недели пуст")
    else:
        weekdays = set(range(7))

    return QuietWindow(start=start, end=end, weekdays=frozenset(weekdays))


@dataclass(frozen=True)
class PrivacyState:
    muted: bool
    reason: str
    muted_until: float | None = None
    # Машинный код: заголовки HTTP допускают только latin-1, русская причина
    # в заголовке роняет ответ целиком. Человеку — reason, машине — code.
    code: str = "allowed"

    def as_dict(self) -> dict:
        return {
            # Ключи — машинные (их читают окно, трей и телефон), значения — для человека.
            "запись": t("privacy.recording.off") if self.muted else t("privacy.recording.on"),
            "причина": self.reason,
            "код": self.code,
            "до": (
                datetime.fromtimestamp(self.muted_until).isoformat(timespec="seconds")
                if self.muted_until
                else None
            ),
        }


class PrivacyGate:
    """Решает, писать ли звук прямо сейчас."""

    def __init__(
        self,
        state_path: Path | str,
        windows: tuple[QuietWindow, ...] = (),
        *,
        paused: bool = False,
        broken_windows: tuple[str, ...] = (),
    ) -> None:
        self.state_path = Path(state_path)
        self.windows = tuple(windows)
        # P11.2, находка 9: неразобранная стоп-зона не пропускается, а глушит
        # запись целиком, пока расписание не исправят.
        self.broken_windows = tuple(broken_windows)
        self._muted_until: float | None = None
        self._paused = paused
        # P11.2, находка 10: файл состояния есть, но не читается.
        self._corrupt = False
        self._stamp: tuple[int, float] | None = None
        self._load()

    # ---------- состояние на диске ----------

    def _file_stamp(self) -> tuple[int, float] | None:
        try:
            info = self.state_path.stat()
        except OSError:
            return None
        return (info.st_size, info.st_mtime_ns)

    def _refresh(self) -> None:
        """Подхватывает правку файла, сделанную другим процессом.

        Кнопку «приват» жмут в окне, а расписание читает воркер и CLI — это
        разные процессы над одним файлом. Без сверки живой сервер продолжал бы
        показывать «запись идёт» после `uzel privacy pause` в терминале.
        """
        if self._file_stamp() != self._stamp:
            self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            self._stamp = None
            self._corrupt = False
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ожидался объект JSON")
            until = data.get("muted_until")
            muted_until = float(until) if until else None
        except (OSError, ValueError, TypeError) as exc:  # JSONDecodeError ⊂ ValueError
            # Нечитаемое состояние могло означать «пауза»: считаем заглушённым.
            # Штамп запоминаем, чтобы подхватить исправление файла извне.
            log.error("состояние приватности не читается, запись заглушена: %s", exc)
            self._corrupt = True
            self._stamp = self._file_stamp()
            return
        self._corrupt = False
        self._stamp = self._file_stamp()
        self._muted_until = muted_until
        self._paused = bool(data.get("paused", self._paused))
        if self._muted_until and self._muted_until > time.time():
            log.info(
                "запись заглушена до %s (пережила перезапуск)",
                datetime.fromtimestamp(self._muted_until).isoformat(timespec="seconds"),
            )

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"muted_until": self._muted_until, "paused": self._paused},
            ensure_ascii=False,
        ).encode("utf-8")
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)
        self._stamp = self._file_stamp()
        # Явное действие владельца записало целый файл — поломка снята.
        self._corrupt = False

    # ---------- управление ----------

    def mute(self, minutes: float, *, now: float | None = None) -> float:
        """Заглушает запись на заданное число минут (FR-071)."""
        if minutes <= 0:
            raise ValueError("длительность заглушки должна быть положительной")
        now = time.time() if now is None else now
        self._refresh()  # чужую заглушку не укорачиваем
        self._muted_until = max(self._muted_until or 0.0, now + minutes * 60.0)
        self._save()
        log.info("запись заглушена на %.0f мин", minutes)
        return self._muted_until

    def unmute(self) -> None:
        self._muted_until = None
        self._paused = False
        self._save()
        log.info("запись возобновлена")

    def pause(self) -> None:
        """Бессрочная остановка — до явного возобновления."""
        self._paused = True
        self._save()

    # ---------- решение ----------

    def state(self, now: float | None = None) -> PrivacyState:
        now = time.time() if now is None else now
        self._refresh()

        if self.broken_windows:
            return PrivacyState(
                True,
                t("privacy.reason.bad_schedule", windows="; ".join(self.broken_windows)),
                code="bad_schedule",
            )

        if self._corrupt:
            return PrivacyState(
                True,
                t("privacy.reason.bad_state", name=self.state_path.name),
                code="bad_state",
            )

        if self._paused:
            return PrivacyState(True, t("privacy.reason.paused"), code="paused")

        if self._muted_until and now < self._muted_until:
            return PrivacyState(
                True,
                t("privacy.reason.manual"),
                muted_until=self._muted_until,
                code="manual",
            )

        moment = datetime.fromtimestamp(now)
        for window in self.windows:
            if window.contains(moment):
                return PrivacyState(
                    True,
                    t("privacy.reason.schedule", window=window.describe()),
                    code="schedule",
                )

        return PrivacyState(False, t("privacy.reason.allowed"), code="allowed")

    def is_muted(self, now: float | None = None) -> bool:
        return self.state(now).muted


# Право конвейера (ночной батч) отдавать звук и текст в облако. Отсюда его
# берёт и Worker.run_batch как умолчание, и строка для человека ниже:
# интерфейс не имеет права обещать то, чего код в этот момент не делает.
BATCH_ALLOW_CLOUD = False


def cloud_policy_line(cloud_backends: list[str] | tuple[str, ...] = ()) -> str:
    """Строка «куда уходят данные» из фактического состояния, не константой.

    P11.2, находка 13: раньше «облако на штатном пути: запрещено» было
    захардкожено в CLI и MCP и показывалось бы даже при включённом облаке.
    """
    if BATCH_ALLOW_CLOUD:
        return t("privacy.cloud.allowed")
    if cloud_backends:
        return t("privacy.cloud.forbidden_with", backends=", ".join(cloud_backends))
    return t("privacy.cloud.forbidden")


def build_gate(settings) -> PrivacyGate:
    windows = []
    broken = []
    for spec in settings.quiet_windows:
        try:
            windows.append(parse_window(spec))
        except ValueError as exc:
            log.error("стоп-зона «%s» не разобрана — запись заглушена: %s", spec, exc)
            broken.append(spec)
    return PrivacyGate(
        settings.data_dir / "privacy.json", tuple(windows), broken_windows=tuple(broken)
    )
