"""Интерфейс окна УЗЛА (ТЗ P7): страница ``/ui`` и её JSON-API.

Ядро уже на FastAPI, поэтому интерфейс живёт в нём же, а окно на ПК — это
pywebview вокруг этой страницы. Страница целиком оффлайн: свои HTML/CSS/JS
без CDN, шрифтов Google и сборщиков (критерий A6).

Страница собирается на сервере на языке интерфейса: метки ``{{t:ключ}}`` в
index.html заменяются строками каталога, а строки для app.js (все ключи
``ui.*``) кладутся в ``window.UZEL_I18N``. Поэтому уже первый ответ ``/ui``
на нужном языке, без мигания и без отдельного запроса словаря.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, i18n
from ..config import Settings
from ..i18n import t
from .api import build_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
_T_MARK = re.compile(r"\{\{t:([\w.]+)\}\}")


def render_page(settings: Settings) -> str:
    """index.html на текущем языке: строки, список языков, словарь для app.js."""
    lang = i18n.get_language()
    разметка = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    разметка = _T_MARK.sub(lambda match: html.escape(t(match.group(1))), разметка)

    выбор = i18n.read_lang_choice(settings.data_dir) or i18n.AUTO
    варианты = [(i18n.AUTO, t("ui.lang.auto"))] + [
        (code, i18n.language_name(code)) for code in i18n.available_languages()
    ]
    языки = "".join(
        f'<option value="{html.escape(code)}"'
        f'{" selected" if code == выбор else ""}>{html.escape(name)}</option>'
        for code, name in варианты
    )
    # «</» внутри JSON закрыл бы тег script — экранируем.
    словарь = json.dumps(
        {"lang": lang, "strings": i18n.strings_with_prefix("ui.")},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return (
        разметка.replace("{ВЕРСИЯ}", __version__)
        .replace("{ЯЗЫК}", lang)
        .replace("{ЯЗЫКИ}", языки)
        .replace("{СЛОВАРЬ}", словарь)
    )


def mount_ui(app: FastAPI, settings: Settings) -> None:
    """Регистрирует страницу, статику и маршруты API на приложении ядра."""
    app.include_router(build_router(settings))
    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")

    @app.get("/ui", include_in_schema=False)
    async def ui_page() -> HTMLResponse:
        # Версия в ссылках на статику: без неё окно после обновления УЗЛА
        # показывает старый app.js из кэша WebView2 и ведёт себя «как вчера».
        # Ловится это только вручную и очень долго.
        return HTMLResponse(render_page(settings))
