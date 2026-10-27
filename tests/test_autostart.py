"""Автозапуск через ярлык (ТЗ P7). Ярлык создаётся только в tmp_path."""

from __future__ import annotations

import sys

import pytest
from uzel import autostart

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="ярлыки автозагрузки — только Windows"
)


def test_install_creates_shortcut(tmp_path):
    link = autostart.install(tmp_path)

    assert link.exists()
    assert link.name == "УЗЕЛ.lnk"
    assert link.parent == tmp_path


def test_status_after_install_reports_target_and_arguments(tmp_path):
    autostart.install(tmp_path)

    state = autostart.status(tmp_path)

    assert state["installed"] is True
    assert "pythonw" in state["target"]
    assert "запуск-приложения.py" in state["arguments"]
    assert "--hidden" in state["arguments"]  # из автозагрузки — сразу в трей


def test_remove_deletes_and_reports(tmp_path):
    autostart.install(tmp_path)

    assert autostart.remove(tmp_path) is True
    assert not autostart.shortcut_path(tmp_path).exists()
    assert autostart.remove(tmp_path) is False


def test_status_on_empty_directory(tmp_path):
    state = autostart.status(tmp_path)

    assert state["installed"] is False
    assert state["target"] is None
    assert state["arguments"] is None
