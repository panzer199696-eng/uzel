"""P12: пакет ставится в .venv (pip install -e), CLI работает без PYTHONPATH.

Тест не требует установки: проверяет, что точки входа из pyproject.toml
указывают на реально существующие функции, а поиск пакетов находит все три.
"""

import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_точки_входа_ведут_в_существующие_функции():
    scripts = PYPROJECT["project"]["scripts"]
    assert set(scripts) == {"uzel", "uzel-bridge", "uzel-mcp"}
    for имя, цель in scripts.items():
        модуль, функция = цель.split(":")
        assert callable(getattr(importlib.import_module(модуль), функция)), имя


def test_каталоги_пакетов_совпадают_с_pythonpath_тестов():
    # pytest и установленный пакет должны видеть одни и те же корни, иначе
    # тесты зелёные, а `uzel` из .venv не находит uzel_bridge (грабля P10).
    where = PYPROJECT["tool"]["setuptools"]["packages"]["find"]["where"]
    assert where == PYPROJECT["tool"]["pytest"]["ini_options"]["pythonpath"]
    for корень in where:
        assert (ROOT / корень).is_dir(), корень


def test_версия_совпадает_с_ядром():
    import uzel

    assert PYPROJECT["project"]["version"] == uzel.__version__


def test_модель_vad_попадает_в_данные_пакета():
    assert (ROOT / "core" / "uzel" / "models" / "silero_vad.onnx").exists()
    assert "models/*.onnx" in PYPROJECT["tool"]["setuptools"]["package-data"]["uzel"]
