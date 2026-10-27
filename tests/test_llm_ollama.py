"""OllamaBackend: локальная модель, нативный API, кэш доступности.

Сеть в тестах не дёргается: httpx.post/httpx.get подменяются на поддельные
ответы с тем же контрактом (статус, json(), text).
"""

from __future__ import annotations

import httpx
import pytest
from uzel.llm import (
    AllLlmBackendsFailed,
    LlmBackend,
    LlmError,
    LlmResult,
    LlmRouter,
    OllamaBackend,
    build_llm_router,
)


class FakeResponse:
    """Минимум контракта httpx.Response, нужного бэкенду."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("в ответе нет JSON")
        return self._json


def tags_response(*names):
    return FakeResponse(json_data={"models": [{"name": name} for name in names]})


@pytest.fixture
def backend():
    return OllamaBackend(
        "http://127.0.0.1:11434", "qwen2.5:7b-instruct-q4_K_M", 600.0
    )


# --- complete: тело запроса ---


def test_qwen3_thinking_is_off(monkeypatch):
    """qwen3 по умолчанию «размышляет»: рассуждения съедают num_predict и время,
    JSON от них не лучше (замер 23.10.2026 на боевых разговорах)."""
    captured = {}

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        captured.update(body=json)
        return FakeResponse(json_data={"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    OllamaBackend("http://127.0.0.1:11434", "qwen3:8b", 600.0).complete(
        [{"role": "user", "content": "x"}], json_mode=True
    )
    assert captured["body"]["think"] is False and captured["body"]["format"] == "json"


def test_complete_sends_native_fields(monkeypatch, backend):
    captured = {}

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        captured.update(url=url, body=json, timeout=timeout)
        return FakeResponse(json_data={"message": {"content": "ответ модели"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = backend.complete(
        [{"role": "user", "content": "привет"}], temperature=0.3, max_tokens=512
    )

    body = captured["body"]
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert body["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert body["messages"] == [{"role": "user", "content": "привет"}]
    assert body["stream"] is False
    assert body["keep_alive"] == "5m"
    assert body["options"] == {"temperature": 0.3, "num_predict": 512, "num_ctx": 8192}
    assert "format" not in body  # без json_mode нативный флаг не отправляется
    assert "think" not in body  # qwen2.5 «размышлений» не умеет — флаг не шлём
    assert isinstance(result, LlmResult)
    assert result.text == "ответ модели"
    assert result.backend == "ollama"
    assert result.model == "qwen2.5:7b-instruct-q4_K_M"


def test_complete_json_mode_adds_format(monkeypatch, backend):
    captured = {}

    def fake_post(url, *, json=None, **kwargs):
        captured["body"] = json
        return FakeResponse(json_data={"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    backend.complete([{"role": "user", "content": "x"}], json_mode=True)

    assert captured["body"]["format"] == "json"


def test_complete_without_json_mode_sends_no_format(monkeypatch, backend):
    captured = {}

    def fake_post(url, *, json=None, **kwargs):
        captured["body"] = json
        return FakeResponse(json_data={"message": {"content": "текст"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    backend.complete([{"role": "user", "content": "x"}])

    assert "format" not in captured["body"]


def test_complete_empty_content_is_error(monkeypatch, backend):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: FakeResponse(json_data={"message": {"content": "  "}}),
    )

    with pytest.raises(LlmError, match="пустой"):
        backend.complete([{"role": "user", "content": "x"}])


def test_complete_broken_body_is_error(monkeypatch, backend):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: FakeResponse(json_data={"result": "?"})
    )

    with pytest.raises(LlmError, match="непонятный ответ"):
        backend.complete([{"role": "user", "content": "x"}])


def test_complete_http_error_carries_body(monkeypatch, backend):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: FakeResponse(status_code=500, text="внутренняя ошибка"),
    )

    with pytest.raises(LlmError, match="HTTP 500"):
        backend.complete([{"role": "user", "content": "x"}])


def test_complete_network_failure_is_llm_error(monkeypatch, backend):
    def fake_post(*a, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LlmError, match="сеть недоступна"):
        backend.complete([{"role": "user", "content": "x"}])


# --- available: /api/tags с кэшем ---


def test_available_true_when_model_listed(monkeypatch, backend):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: tags_response(
            "llama3:latest", "qwen2.5:7b-instruct-q4_K_M"
        ),
    )

    assert backend.available() is True
    assert backend.probe_error is None


def test_available_false_when_other_models_only(monkeypatch, backend):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: tags_response("llama3:latest")
    )

    assert backend.available() is False


def test_available_matches_by_name_without_tag(monkeypatch):
    """Имя из настроек без тега находит вариант с тегом того же семейства."""
    backend = OllamaBackend("http://127.0.0.1:11434", "qwen2.5", 600.0)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: tags_response("qwen2.5:7b-instruct-q4_K_M")
    )

    assert backend.available() is True


def test_available_swallows_connection_error(monkeypatch, backend):
    def fake_get(*a, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)

    assert backend.available() is False
    assert backend.probe_error  # причина записана, наружу не брошена


def test_available_false_on_http_error(monkeypatch, backend):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: FakeResponse(status_code=503, text="нет")
    )

    assert backend.available() is False
    assert backend.probe_error


def test_available_is_cached_until_reset(monkeypatch, backend):
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return tags_response("qwen2.5:7b-instruct-q4_K_M")

    monkeypatch.setattr(httpx, "get", fake_get)

    assert backend.available() is True
    assert backend.available() is True
    assert len(calls) == 1  # второй вызов из кэша

    backend.reset_probe()
    assert backend.available() is True
    assert len(calls) == 2  # кэш снят — снова запрос


# --- unload ---


def test_unload_sends_keep_alive_zero(monkeypatch, backend):
    captured = {}

    def fake_post(url, *, json=None, **kwargs):
        captured["body"] = json
        return FakeResponse(json_data={"model": "qwen2.5:7b-instruct-q4_K_M"})

    monkeypatch.setattr(httpx, "post", fake_post)

    assert backend.unload() is True
    assert captured["body"] == {
        "model": "qwen2.5:7b-instruct-q4_K_M",
        "messages": [],
        "keep_alive": 0,
    }


def test_unload_returns_false_on_network_error(monkeypatch, backend):
    def fake_post(*a, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert backend.unload() is False  # выгрузка не роняет батч


def test_unload_returns_false_on_http_error(monkeypatch, backend):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: FakeResponse(status_code=404, text="нет")
    )

    assert backend.unload() is False


# --- главная дыра пакета: агент-шлюз считается облаком ---


def test_gateway_is_cloud():
    """Регресс-тест: облачный провайдер за шлюзом, проверено 19.09.2026.

    ``is_cloud=False`` снимал со шлюза все проверки приватности, хотя его
    локальный порт пересылал расшифровку в облако. Детали — docstring
    ``GatewayBackend``.
    """
    from uzel.llm import GatewayBackend

    assert GatewayBackend.is_cloud is True


# --- роутер ---


class FakeOllama(LlmBackend):
    name = "ollama"
    is_cloud = False

    def __init__(self, *, error=None):
        self.error = error
        self.calls = 0
        self.json_modes = []

    def available(self) -> bool:
        return True

    def complete(self, messages, *, temperature=0.2, max_tokens=2048,
                 json_mode=False) -> LlmResult:
        self.calls += 1
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return LlmResult(text="ок", backend="ollama", model="q", latency_s=0.0)


class FakeGateway(LlmBackend):
    name = "gateway"
    is_cloud = True

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, messages, *, temperature=0.2, max_tokens=2048,
                 json_mode=False) -> LlmResult:
        self.calls += 1
        return LlmResult(text="облачный ответ", backend="gateway", model="z", latency_s=0.0)


def test_router_with_cloud_gateway_never_calls_it():
    ollama = FakeOllama()
    gateway = FakeGateway()
    router = LlmRouter([ollama, gateway])

    result = router.complete([{"role": "user", "content": "x"}], allow_cloud=False)

    assert result.backend == "ollama"
    assert gateway.calls == 0


def test_router_failure_does_not_spill_into_cloud():
    dead = FakeOllama(error=LlmError("модель не загружена"))
    gateway = FakeGateway()
    router = LlmRouter([dead, gateway])

    with pytest.raises(AllLlmBackendsFailed):
        router.complete([{"role": "user", "content": "x"}], allow_cloud=False)

    assert gateway.calls == 0  # отказ локального не уводит разбор в облако


def test_router_passes_json_mode_through():
    ollama = FakeOllama()

    LlmRouter([ollama]).complete(
        [{"role": "user", "content": "x"}], json_mode=True
    )

    assert ollama.json_modes == [True]


def test_router_unload_calls_backends_that_can(monkeypatch, backend):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: FakeResponse(json_data={"model": "qwen2.5:7b-instruct-q4_K_M"}),
    )
    gateway = FakeGateway()  # unload не умеет — и не должен падать от этого
    router = LlmRouter([backend, gateway])

    assert router.unload() == ["ollama"]


def test_router_unload_swallows_backend_errors():
    class ExplodingUnload(FakeOllama):
        def unload(self):
            raise RuntimeError("выгрузка сломалась")

    router = LlmRouter([ExplodingUnload()])

    assert router.unload() == []


def test_build_llm_router_default_is_single_local_ollama(settings):
    router = build_llm_router(settings)

    assert [b.name for b in router.backends] == ["ollama"]
    assert router.backends[0].is_cloud is False
