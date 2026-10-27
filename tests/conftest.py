from __future__ import annotations

import os
import tempfile

# До импорта uzel.config: путь к .env вычисляется при импорте. Без этого тесты
# читают боевой ~/uzel-data/.env — с UZEL_API_TOKEN в нём 75 тестов ловят 401.
# Путь вне проекта: test_config проверяет, что .env не в зоне синка Drive.
os.environ["UZEL_ENV_FILE"] = os.path.join(
    tempfile.gettempdir(), "uzel-tests-нет-такого.env"
)

import numpy as np
import pytest
from uzel.config import Settings


@pytest.fixture(autouse=True)
def без_переменных_окружения(monkeypatch) -> None:
    """Убирает UZEL_* из окружения на время теста.

    Переменная окружения сильнее и .env, и умолчаний. 20.09.2026 унаследованная
    UZEL_DATA_DIR увела свежую установку в боевой каталог — тесты не должны
    зависеть от того, что человек выставил в своей оболочке. Тесты, которым
    переменная нужна, ставят её сами через monkeypatch уже после этой фикстуры.
    """
    for имя in [k for k in os.environ if k.startswith("UZEL_")]:
        monkeypatch.delenv(имя, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    r"""Настройки с изолированным каталогом данных и своим vault.

    ``vault_dir`` обязателен: без него тесты воркера писали заметки в боевое
    хранилище Obsidian (``Проекты\Узел\Логи``) — найдено 18.09.2026.
    """
    return Settings(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        groq_api_key=None,
    )


@pytest.fixture
def tone() -> np.ndarray:
    """Секунда синуса 16 кГц — детерминированный звук для дымовых проверок."""
    t = np.arange(16000, dtype=np.float32) / 16000.0
    return (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
