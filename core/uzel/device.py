"""Последний отчёт моста о кулоне (FR-063) — для живого статуса в окне.

Хранится **только в памяти процесса**: это сиюминутное состояние канала, а не
данные. Перезапуск ядра его теряет, и это правильно — мост пришлёт новый через
полминуты, а показывать заряд двухдневной давности как текущий нельзя.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .i18n import t

# Мост отчитывается раз в 30 с. Втрое больше — значит связь оборвалась,
# а не «отчёт чуть задержался».
STALE_AFTER_S = 90.0


@dataclass
class DeviceReport:
    """Что мост рассказал о кулоне в последний раз."""

    uid: str
    battery_percent: int | None = None
    codec: str | None = None
    connected: bool = True
    packets: int = 0
    lost: int = 0
    spooled: int = 0
    reported_at: float = field(default_factory=time.time)

    def as_dict(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        ago = now - self.reported_at
        return {
            "uid": self.uid,
            "battery_percent": self.battery_percent,
            "codec": self.codec,
            "connected": self.connected,
            "packets": self.packets,
            "lost": self.lost,
            "spooled": self.spooled,
            "ago_s": round(ago, 1),
            # Мост молчит дольше срока — считаем, что связи нет, даже если
            # в последнем отчёте стояло connected=true.
            "stale": ago > STALE_AFTER_S,
        }


class DeviceStatus:
    """Держатель последнего отчёта. Пуст, пока мост не вышел на связь."""

    def __init__(self) -> None:
        self._last: DeviceReport | None = None

    def update(self, report: DeviceReport) -> DeviceReport:
        self._last = report
        return report

    @property
    def last(self) -> DeviceReport | None:
        return self._last

    def as_dict(self, *, now: float | None = None) -> dict:
        if self._last is None:
            return {"known": False, "reason": t("device.reason.never")}
        data = self._last.as_dict(now=now)
        data["known"] = True
        if data["stale"]:
            data["reason"] = t("device.reason.silent")
        elif not data["connected"]:
            data["reason"] = t("device.reason.pendant_off")
        else:
            data["reason"] = t("device.reason.online")
        return data
