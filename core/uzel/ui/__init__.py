"""Интерфейс окна УЗЛА (ТЗ P7): страница ``/ui`` и её JSON-API.

Ядро уже на FastAPI, поэтому интерфейс живёт в нём же, а окно на ПК — это
pywebview вокруг этой страницы. Страница целиком оффлайн: свои HTML/CSS/JS
без CDN, шрифтов Google и сборщиков (критерий A6).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import Settings
from .api import build_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_ui(app: FastAPI, settings: Settings) -> None:
    """Регистрирует страницу, статику и маршруты API на приложении ядра."""
    app.include_router(build_router(settings))
    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")

    @app.get("/ui", include_in_schema=False)
    async def ui_page() -> HTMLResponse:
        # Версия в ссылках на статику: без неё окно после обновления УЗЛА
        # показывает старый app.js из кэша WebView2 и ведёт себя «как вчера».
        # Ловится это только вручную и очень долго.
        разметка = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(разметка.replace("{ВЕРСИЯ}", __version__))
