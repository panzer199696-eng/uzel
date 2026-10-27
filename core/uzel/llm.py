"""Бэкенды больших моделей и роутер с фолбэком.

Тот же приём, что у STT, и по той же причине: единственный бэкенд рано или
поздно отваливается. Проверено 24.08.2026 — локальный агент-шлюз отвечает
HTTP 200, но его upstream отдаёт ``modelCode: does not exist`` на любой модели,
включая собственную модель шлюза. Без фолбэка конвейер встал бы целиком.

Приватность: облачные бэкенды отсекаются так же жёстко, как в STT. Отправлять
расшифровку приватного разговора в облако ради саммари — та же утечка, что
отправлять туда звук. С 19.09.2026 облачным считается и шлюз: локален у него
только порт, считает облачный провайдер. Настоящая локальная модель — Ollama на
этом ПК, она же первая в порядке фолбэка.
"""

from __future__ import annotations

import abc
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

Message = dict[str, str]


@dataclass(frozen=True)
class LlmResult:
    text: str
    backend: str
    model: str
    latency_s: float
    raw: dict[str, Any] = field(default_factory=dict)


class LlmError(RuntimeError):
    """Бэкенд не смог ответить."""


class LlmUnavailable(LlmError):
    """Бэкенд не настроен: нет ключа или адреса."""


class AllLlmBackendsFailed(LlmError):
    """Ни одна модель не ответила."""


class LlmCloudNotAllowed(LlmError):
    """Облако запрещено, локальных моделей нет."""


class LlmLocalFused(LlmError):
    """Локальные модели отказали N раз подряд — разбор остановлен.

    Не «ошибка разбора», а стоп-сигнал: поднимается мимо ``NluProcessor``,
    чтобы воркер остановил фазу разбора целиком, а разговоры остались
    ``open`` и дождались починки локальной модели (симметрия с
    ``LocalBackendFused`` у STT, FR-022).
    """


class LlmBackend(abc.ABC):
    name: str = "base"
    is_cloud: bool = False

    @abc.abstractmethod
    def available(self) -> bool: ...

    @abc.abstractmethod
    def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LlmResult: ...


class _OpenAiCompatibleBackend(LlmBackend):
    """Общая часть: и шлюз, и Groq говорят на диалекте OpenAI."""

    endpoint_suffix = "/chat/completions"

    def __init__(
        self, base_url: str, api_key: str | None, model: str, timeout_s: float
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self._base_url and self._api_key)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LlmResult:
        # json_mode сознательно игнорируется: ни шлюз, ни Groq этот параметр
        # диалекта OpenAI не поддерживают — строгий JSON у них выпрашивают
        # системной инструкцией (см. nlu.SYSTEM_PROMPT).
        import httpx

        if not self.available():
            raise LlmUnavailable(f"{self.name}: не настроен")

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self._base_url}{self.endpoint_suffix}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": list(messages),
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"{self.name}: сеть недоступна ({exc})") from exc

        if response.status_code != 200:
            raise LlmError(
                f"{self.name}: HTTP {response.status_code} — {response.text[:200]}"
            )

        body = response.json()
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{self.name}: непонятный ответ {str(body)[:200]}") from exc

        # Шлюз отдаёт HTTP 200 даже когда upstream отказал, а текст ошибки
        # кладёт прямо в content. Без этой проверки мусор уехал бы в разбор JSON.
        if choice.get("finish_reason") == "error":
            raise LlmError(f"{self.name}: модель вернула ошибку — {text[:200]}")

        return LlmResult(
            text=text.strip(),
            backend=self.name,
            model=self._model,
            latency_s=time.monotonic() - started,
            raw=body,
        )


class GatewayBackend(_OpenAiCompatibleBackend):
    """OpenAI-совместимый агент-шлюз на локальном порту. Считает облако.

    Локален у такого шлюза только порт: модель за ним — облачный провайдер
    (проверено 19.09.2026 на конфигурации шлюза). Расшифровка, отданная
    шлюзу, уезжает наружу ровно так же, как в Groq, поэтому ``is_cloud``
    здесь True. Это не ошибка конфигурации, а устройство шлюза: он агент,
    а не модель.
    """

    name = "gateway"
    is_cloud = True
    endpoint_suffix = "/v1/chat/completions"


class OllamaBackend(LlmBackend):
    """Локальная модель через Ollama. Данные не покидают этот ПК.

    Нативный API (``/api/chat``), а не OpenAI-совместимый слой: нужны
    ``format: "json"`` и ``keep_alive``, которых в совместимом слое нет.
    Первый — единственный способ заставить 7B-модель отвечать чистым JSON,
    второй — не выгружать модель из видеопамяти между репликами разбора.
    """

    name = "ollama"
    is_cloud = False

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float,
        *,
        keep_alive: str = "5m",
        num_ctx: int = 8192,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._probe_ok: bool | None = None
        self._probe_error: str | None = None

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LlmResult:
        import httpx

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self._num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        if self._model.startswith("qwen3"):
            # «размышления» qwen3 съедают num_predict и минуты, JSON от них не лучше
            payload["think"] = False

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout_s
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"{self.name}: сеть недоступна ({exc})") from exc

        if response.status_code != 200:
            raise LlmError(
                f"{self.name}: HTTP {response.status_code} — {response.text[:200]}"
            )

        body = response.json()
        try:
            text = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LlmError(f"{self.name}: непонятный ответ {str(body)[:200]}") from exc

        if not (text or "").strip():
            raise LlmError(f"{self.name}: пустой ответ модели")

        return LlmResult(
            text=text.strip(),
            backend=self.name,
            model=self._model,
            latency_s=time.monotonic() - started,
            raw=body,
        )

    def available(self) -> bool:
        """Ollama запущена и модель установлена. Результат кэшируется.

        Проверка — ``GET /api/tags``: сервис жив и список моделей известен.
        Ollama не запущена — False с причиной в ``probe_error``, исключение
        наружу не летит: недоступность модели не должна ронять процесс.
        """
        if self._probe_ok is not None:
            return self._probe_ok

        import httpx

        try:
            response = httpx.get(
                f"{self._base_url}/api/tags",
                timeout=min(self._timeout_s, 5.0),
            )
        except httpx.HTTPError as exc:
            self._probe_ok = False
            self._probe_error = f"Ollama не отвечает по {self._base_url}: {exc}"
            log.warning("ollama недоступна: %s", exc)
            return False

        if response.status_code != 200:
            self._probe_ok = False
            self._probe_error = f"HTTP {response.status_code}: {response.text[:200]}"
            return False

        names = [
            str(item.get("name") or "")
            for item in response.json().get("models", [])
            if isinstance(item, dict)
        ]
        if not self._model_listed(names):
            self._probe_ok = False
            self._probe_error = (
                f"модели {self._model!r} нет в Ollama (установлены: {names})"
            )
            return False

        self._probe_ok = True
        return True

    def _model_listed(self, names: list[str]) -> bool:
        """Точное совпадение плюс вариант «имя из настроек без тега»."""
        if self._model in names:
            return True
        return ":" not in self._model and any(
            name.split(":")[0] == self._model for name in names
        )

    def reset_probe(self) -> None:
        """Сбрасывает кэш проверки (нужен тестам и запуску Ollama на ходу)."""
        self._probe_ok = None
        self._probe_error = None

    @property
    def probe_error(self) -> str | None:
        """Текст последней неудачи проверки — в журнал и ``llm-check``."""
        return self._probe_error

    def unload(self) -> bool:
        """Выгружает модель из видеопамяти: ``keep_alive: 0``.

        Вспомогательная операция: любая ошибка — ``False``, батч продолжается.
        """
        import httpx

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": [], "keep_alive": 0},
                timeout=min(self._timeout_s, 30.0),
            )
        except httpx.HTTPError as exc:
            log.warning("выгрузка модели %s не удалась: %s", self._model, exc)
            return False

        if response.status_code != 200:
            log.warning(
                "выгрузка модели %s: HTTP %d — %s",
                self._model,
                response.status_code,
                response.text[:200],
            )
            return False

        log.info("модель %s выгружена из видеопамяти", self._model)
        return True


class GroqLlmBackend(_OpenAiCompatibleBackend):
    """Облачная модель на Groq. Быстрая, но наружу."""

    name = "groq-llm"
    is_cloud = True
    endpoint_suffix = "/chat/completions"


class LlmRouter:
    """Перебирает модели по порядку до первого успеха.

    Два предохранителя приватности — те же, что у ``STTRouter`` (P11,
    находка 7), потому что обещание одно и то же:

    1. отказ не-облачного бэкенда не «переливается» в облако — иначе не
       запущенная Ollama молча отправляет сутки разговоров в облачный шлюз;
    2. счётчик подряд идущих отказов останавливает разбор целиком:
       разговоры остаются ``open``, а не уходят в облако.
    """

    def __init__(self, backends: Sequence[LlmBackend], *, failure_streak: int = 3) -> None:
        if not backends:
            raise ValueError("роутеру нужен хотя бы один бэкенд")
        self._backends = list(backends)
        self._failure_streak = failure_streak
        self._failures = 0

    @property
    def backends(self) -> list[LlmBackend]:
        return list(self._backends)

    @property
    def failures_in_a_row(self) -> int:
        """Сколько вызовов подряд не справились не-облачные бэкенды."""
        return self._failures

    def reset_fuse(self) -> None:
        """Снимает предохранитель: следующие вызовы снова пойдут к моделям."""
        self._failures = 0

    def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        allow_cloud: bool = False,
        json_mode: bool = False,
    ) -> LlmResult:
        """Отвечает на сообщения, перебирая модели по порядку.

        ``allow_cloud`` по умолчанию ``False``: в сообщениях лежат тексты
        рабочих разговоров, и молчаливый дефолт «да» превращал бы любую
        ошибку конфигурации в утечку саммари в облако. Разрешение на облако
        передаётся явно и только там, где оно осознанное.

        Отказ не-облачного бэкенда (ненастроен, ошибка, падение) исключает
        облако из перебора этого вызова — перебор продолжается только среди
        не-облачных бэкендов.
        """
        if self._failures >= self._failure_streak:
            raise LlmLocalFused(
                f"локальные модели отказали {self._failures} раз подряд, "
                "разбор остановлен; разговоры остаются открытыми"
            )

        candidates = [b for b in self._backends if allow_cloud or not b.is_cloud]
        if not candidates:
            raise LlmCloudNotAllowed(
                "облако запрещено для этого разговора, локальных моделей нет"
            )

        failures: list[str] = []
        local_failed = False
        for backend in candidates:
            # Отказ локального уже случился — облако отсечено до его вызова.
            if local_failed and backend.is_cloud:
                continue
            if not backend.available():
                failures.append(f"{backend.name}: не настроен")
                log.debug("модель %s пропущена: не настроена", backend.name)
                if not backend.is_cloud:
                    local_failed = True
                continue
            try:
                result = backend.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            except LlmError as exc:
                failures.append(f"{backend.name}: {exc}")
                log.warning("модель %s не ответила: %s", backend.name, exc)
                if not backend.is_cloud:
                    local_failed = True
                continue
            except Exception as exc:
                failures.append(f"{backend.name}: непредвиденная ошибка {exc!r}")
                log.exception("модель %s упала неожиданно", backend.name)
                if not backend.is_cloud:
                    local_failed = True
                continue

            if not backend.is_cloud:
                self._failures = 0

            if failures:
                log.info(
                    "ответила %s после отказов: %s", backend.name, "; ".join(failures)
                )
            return result

        if local_failed:
            self._failures += 1
            if any(b.is_cloud for b in candidates):
                failures.append(
                    "облако отсечено из-за отказа локальной модели"
                )
        raise AllLlmBackendsFailed("; ".join(failures) or "нет доступных моделей")

    def unload(self) -> list[str]:
        """Выгружает модели бэкендов, которые это умеют (см. OllamaBackend).

        Воркер зовёт после разбора, чтобы освободить видеопамять под whisper
        следующего прогона. Ошибки глотаются: выгрузка — вспомогательная
        операция и ронять батч не имеет права.
        """
        unloaded: list[str] = []
        for backend in self._backends:
            unload = getattr(backend, "unload", None)
            if unload is None:
                continue
            try:
                if unload():
                    unloaded.append(backend.name)
            except Exception:
                log.exception("выгрузка модели %s не удалась", backend.name)
        return unloaded


def build_llm_router(settings) -> LlmRouter:
    factories = {
        "ollama": lambda: OllamaBackend(
            settings.ollama_url,
            settings.ollama_model,
            settings.ollama_timeout_s,
            keep_alive=settings.ollama_keep_alive,
            num_ctx=settings.ollama_num_ctx,
        ),
        "gateway": lambda: GatewayBackend(
            settings.gateway_url,
            settings.gateway_api_key,
            settings.gateway_model,
            settings.llm_timeout_s,
        ),
        "groq": lambda: GroqLlmBackend(
            settings.groq_llm_url,
            settings.groq_api_key,
            settings.groq_llm_model,
            settings.llm_timeout_s,
        ),
    }

    backends: list[LlmBackend] = []
    for name in settings.llm_backends:
        factory = factories.get(name)
        if factory is None:
            log.error("неизвестная модель в настройках: %s", name)
            continue
        backends.append(factory())
    return LlmRouter(backends, failure_streak=settings.llm_failure_streak)
