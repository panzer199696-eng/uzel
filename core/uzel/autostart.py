"""Запуск УЗЛА вместе с системой (ТЗ P7).

Windows, ярлык в папке автозагрузки — не планировщик: у него беда с GUI.
``pythonw.exe``, а не ``python.exe``: иначе при каждом входе в систему
вылезает чёрное окно консоли.

Параметр ``directory`` существует именно для тестов: pytest не имеет права
создавать ярлык в настоящей автозагрузке пользователя, тесты передают
``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

SHORTCUT_NAME = "УЗЕЛ.lnk"


def _shell():
    """COM-объект WScript.Shell. Импорт внутри: без Windows модуля нет."""
    try:
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "ярлык автозагрузки требует Windows с pywin32 (win32com.client)"
        ) from exc
    return win32com.client.Dispatch("WScript.Shell")


def startup_dir() -> Path:
    """%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup."""
    import os

    root = os.environ.get("APPDATA")
    if not root:
        raise RuntimeError(
            "не нашёл %APPDATA%: автозагрузка доступна только на Windows"
        )
    return Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path(directory: Path | None = None) -> Path:
    return (directory if directory is not None else startup_dir()) / SHORTCUT_NAME


def install(directory: Path | None = None) -> Path:
    """Создаёт ярлык на ``ops/запуск-приложения.py``. Возвращает путь ярлыка."""
    project_root = Path(__file__).resolve().parents[2]

    link = shortcut_path(directory)
    link.parent.mkdir(parents=True, exist_ok=True)
    shortcut = _shell().CreateShortCut(str(link))
    shortcut.Targetpath = str(project_root / ".venv" / "Scripts" / "pythonw.exe")
    # --hidden: при входе в систему окно не лезет на экран, УЗЕЛ сидит в трее.
    shortcut.Arguments = f'"{project_root / "ops" / "запуск-приложения.py"}" --hidden'
    shortcut.WorkingDirectory = str(project_root)
    from .i18n import t

    shortcut.Description = t("tray.autostart_description")
    shortcut.save()
    return link


def remove(directory: Path | None = None) -> bool:
    """Удаляет ярлык, если он есть. True — удалили, False — его не было."""
    link = shortcut_path(directory)
    try:
        link.unlink()
    except FileNotFoundError:
        return False
    return True


def status(directory: Path | None = None) -> dict:
    """Читает существующий ярлык тем же WScript.Shell."""
    link = shortcut_path(directory)
    if not link.exists():
        return {
            "installed": False,
            "path": str(link),
            "target": None,
            "arguments": None,
        }
    shortcut = _shell().CreateShortCut(str(link))
    return {
        "installed": True,
        "path": str(link),
        "target": shortcut.Targetpath,
        "arguments": shortcut.Arguments,
    }
