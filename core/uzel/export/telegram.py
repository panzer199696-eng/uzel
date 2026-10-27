"""Сводка в Telegram (FR-051).

Бот и получатель задаются настройками и по умолчанию не заданы: ядро не должно
писать кому-либо, пока это не включили осознанно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from .. import i18n
from ..i18n import t

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096


@dataclass(frozen=True)
class DaySummary:
    day: date
    conversations: int
    speech_seconds: float
    summaries: list[str]
    tasks: list[str]


def render_summary(
    summary: DaySummary, *, max_items: int = 8, lang: str | None = None
) -> str:
    """Короткая сводка. Длинный текст режется, а не отправляется обрезком."""
    lang = lang or i18n.get_language()
    date_text = summary.day.strftime(t("format.date", lang=lang))
    lines = [t("export.telegram.title", lang=lang, date=date_text), ""]

    if not summary.conversations:
        lines.append(t("export.telegram.empty", lang=lang))
        return "\n".join(lines)

    lines.append(
        t(
            "export.telegram.totals",
            lang=lang,
            conversations=summary.conversations,
            minutes=f"{summary.speech_seconds / 60:.0f}",
        )
    )
    lines.append("")

    if summary.summaries:
        lines.append(t("export.telegram.summaries", lang=lang))
        for item in summary.summaries[:max_items]:
            lines.append(f"• {_escape(item)}")
        if len(summary.summaries) > max_items:
            more = len(summary.summaries) - max_items
            lines.append(t("export.telegram.more", lang=lang, count=more))
        lines.append("")

    if summary.tasks:
        lines.append(t("export.telegram.tasks", lang=lang))
        for item in summary.tasks[:max_items]:
            lines.append(f"☐ {_escape(item)}")
        if len(summary.tasks) > max_items:
            more = len(summary.tasks) - max_items
            lines.append(t("export.telegram.more", lang=lang, count=more))

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 20].rstrip() + "\n…"
    return text


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    """Отправка сообщений ботом. Без токена молча ничего не делает."""

    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        *,
        timeout_s: float = 20.0,
        api_base: str = API_BASE,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout_s = timeout_s
        self._api_base = api_base.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str) -> bool:
        import httpx

        if not self.enabled:
            log.debug("Telegram не настроен, сообщение не отправлено")
            return False

        try:
            response = httpx.post(
                f"{self._api_base}/bot{self._token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout_s,
            )
        except Exception as exc:
            log.warning("Telegram недоступен: %s", exc)
            return False

        if response.status_code != 200:
            log.error(
                "Telegram отверг сообщение: HTTP %s %s",
                response.status_code,
                response.text[:200],
            )
            return False
        return True

    def send_day_summary(self, summary: DaySummary) -> bool:
        return self.send(render_summary(summary))


def build_notifier(settings) -> TelegramNotifier:
    return TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
