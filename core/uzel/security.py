"""Авторизация HTTP-интерфейса ядра и защита от старта «в никуда» (P11).

Аудит 23.09.2026 нашёл, что токен висел только на роутере ``/omi``, а
``/ingest``, ``/privacy/*``, ``/ui/api/*`` были открыты всей локальной сети,
как только владелец по собственной документации ставил ``UZEL_HOST=0.0.0.0``.
Здесь две страховки:

1. :func:`ensure_safe_listen_host` — ядро **отказывается подниматься**, если
   слушает не петлевой адрес без токена. Тихо открытая наружу память хуже,
   чем ядро, которое не завелось.
2. :class:`TokenAuthMiddleware` — одна проверка на всё приложение (кроме
   ``/health``): пока токен задан, без него не работает ничего.

Токен принимается заголовком ``Authorization: Bearer …`` и параметром запроса
``?token=…`` — телефону параметр проще. Сравнение — ``secrets.compare_digest``.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Петлевые адреса: слушать их без токена можно — наружу они не видны.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class UnsafeHostError(RuntimeError):
    """Слушаем не петлевой адрес без токена — подниматься отказываемся."""


def is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def ensure_safe_listen_host(host: str | None, token: str | None) -> None:
    """Не петлевой host при пустом токене — ``UnsafeHostError`` с рецептом.

    Вызывается при сборке приложения и в CLI: ``--host`` тоже обязан
    проверяться, иначе обход защиты — один флаг запуска.
    """
    if is_loopback_host(host) or (token or "").strip():
        return
    raise UnsafeHostError(
        f"UZEL_HOST={host!r} слушает все интерфейсы, а UZEL_API_TOKEN не задан: "
        "вся память (реплики, поиск, приватность) будет открыта локальной сети. "
        "Задай в .env строку UZEL_API_TOKEN=<секрет> и передавай его мосту и "
        "телефону (параметр ?token=… или заголовок Authorization: Bearer …), "
        "либо оставь UZEL_HOST=127.0.0.1."
    )


def extract_token(headers: Headers, query_string: bytes) -> str | None:
    """Токен из ``Authorization: Bearer …`` или параметра ``token``."""
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        provided = auth[len("Bearer ") :].strip()
        if provided:
            return provided
    params = parse_qs(query_string.decode("latin-1"))
    values = params.get("token")
    if values and values[0]:
        return values[0]
    return None


def token_matches(provided: str | None, expected: str) -> bool:
    """Сравнение в постоянном времени: по ответу не угадать длину секрета."""
    return provided is not None and secrets.compare_digest(provided, expected)


class TokenAuthMiddleware:
    """Пропускает ``/health`` и запросы с верным токеном, остальным — 401.

    Токен не задан — пропускает всё: локальная работа без токена должна
    оставаться удобной, а наружу без токена ядро и не поднимется.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self._token = (token or "").strip() or None

    @property
    def enabled(self) -> bool:
        return self._token is not None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # /health — единственное исключение: живость и версия без секретов.
        if path == "/health" or path.startswith("/health/"):
            await self.app(scope, receive, send)
            return
        # Запрос с самого ПК (окно, трей, мост) — без токена. Токен закрывает
        # ядро от локальной сети; процесс на этом ПК и так читает базу файлом.
        # Без этого с UZEL_API_TOKEN окно и трей получали 401 и не работали.
        # Только прямое соединение: за обратным прокси так делать нельзя.
        client = scope.get("client")
        if client and is_loopback_host(client[0]):
            await self.app(scope, receive, send)
            return

        provided = extract_token(Headers(scope=scope), scope.get("query_string", b""))
        if token_matches(provided, self._token):  # type: ignore[arg-type]
            await self.app(scope, receive, send)
            return

        response = JSONResponse({"detail": "uzel: token required"}, status_code=401)
        await response(scope, receive, send)
