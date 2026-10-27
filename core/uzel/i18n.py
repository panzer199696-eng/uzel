"""Локализация: каталоги строк ``locales/<код>.json``, выбор языка, откат на en.

Язык интерфейса и вывода (сводки, факты, задачи, заметки, CLI, окно, трей)
выбирается так, по убыванию силы:

1. выбор в окне ``/ui`` — файл ``<UZEL_DATA_DIR>/language.txt``;
2. настройка ``UZEL_LANG`` (``auto`` | ``ru`` | ``en`` | …);
3. ``auto`` — язык системы, если для него есть каталог, иначе английский.

Строка ищется в каталоге текущего языка, затем в английском, затем
возвращается сам ключ: недопереведённый каталог не роняет программу.

Каталог — плоский JSON: ``"раздел.имя": "строка с {параметром}"``. Значение
может быть и не строкой: формы множественного числа (объект ``one/few/many/
other``) и языковые данные (списки слов для разбора, стоп-фразы whisper).
Новый язык — это новый файл ``locales/<код>.json`` с тем же набором ключей
(тест ``tests/test_i18n.py`` сверяет ключи и параметры).
"""

from __future__ import annotations

import json
import locale
import logging
import os
import string
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
FALLBACK_LANG = "en"
AUTO = "auto"
# Выбор языка из окна живёт рядом с данными, а не в браузере: его видят
# и трей, и CLI, и ночной батч.
LANG_FILE_NAME = "language.txt"

_lock = threading.Lock()
_current: str | None = None

# Языки, где у целых чисел три формы: 1 разговор, 2 разговора, 5 разговоров.
_SLAVIC_PLURAL = {"ru", "uk", "be"}


def normalize(code: str | None) -> str:
    """``ru-RU``, ``ru_RU.UTF-8``, ``RU`` → ``ru``; пусто → ``auto``."""
    text = (code or "").strip().lower()
    if not text:
        return AUTO
    text = text.split(".", 1)[0].replace("_", "-")
    return text.split("-", 1)[0] or AUTO


@lru_cache(maxsize=1)
def available_languages() -> tuple[str, ...]:
    """Коды языков, для которых есть каталог. Файлы на ``_`` — служебные."""
    return tuple(
        sorted(
            path.stem
            for path in LOCALES_DIR.glob("*.json")
            if not path.stem.startswith("_")
        )
    )


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, Any]:
    """Каталог языка целиком. Нет файла — пустой словарь (сработает откат)."""
    path = LOCALES_DIR / f"{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.error("каталог %s не читается: %s", path, exc)
        return {}


def language_name(lang: str) -> str:
    """Самоназвание языка для переключателя: «Русский», «English»."""
    value = catalog(lang).get("language.name")
    return value if isinstance(value, str) else lang


def _windows_ui_language() -> str | None:
    """Язык интерфейса Windows (а не формат дат): GetUserDefaultUILanguage."""
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except Exception:
        return None
    return locale.windows_locale.get(lcid)


def system_language() -> str | None:
    """Язык системы двумя буквами или ``None``, если не определить."""
    candidates: list[str | None] = []
    if sys.platform == "win32":
        candidates.append(_windows_ui_language())
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(name)
        if value:
            candidates.append(value.split(":", 1)[0])
    try:
        candidates.append(locale.getlocale()[0])
    except ValueError:
        pass
    for candidate in candidates:
        code = normalize(candidate)
        if code not in (AUTO, "c", "posix"):
            return code
    return None


def resolve(requested: str | None) -> str:
    """Запрошенный код → язык, для которого есть каталог.

    ``auto`` — язык системы, если он переведён, иначе английский. Неизвестный
    код — тоже английский, с предупреждением в журнал: опечатка в настройке
    не должна оставлять человека с ключами вместо текста.
    """
    code = normalize(requested)
    languages = available_languages()
    if code == AUTO:
        system = system_language()
        return system if system in languages else FALLBACK_LANG
    if code in languages:
        return code
    log.warning("нет каталога для языка %r — беру %s", requested, FALLBACK_LANG)
    return FALLBACK_LANG


# ------------------------------------------------------------ выбор из окна


def lang_file(data_dir: Path | str) -> Path:
    return Path(data_dir) / LANG_FILE_NAME


def read_lang_choice(data_dir: Path | str) -> str | None:
    """Выбор из окна или ``None``, если его нет (тогда решает ``UZEL_LANG``)."""
    try:
        text = lang_file(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    code = normalize(text)
    return None if code == AUTO else code


def save_lang_choice(data_dir: Path | str, code: str | None) -> None:
    """Запоминает выбор из окна. ``auto``/``None`` — забыть, решает настройка."""
    path = lang_file(data_dir)
    choice = normalize(code)
    if choice == AUTO:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(choice + "\n", encoding="utf-8")


# ------------------------------------------------------------ текущий язык


def configure(settings) -> str:
    """Выбирает язык процесса по настройкам: файл из окна сильнее ``UZEL_LANG``."""
    global _current
    requested = read_lang_choice(settings.data_dir) or getattr(settings, "lang", AUTO)
    with _lock:
        _current = resolve(requested)
    return _current


def set_language(code: str | None) -> str:
    """Принудительно задаёт язык процесса (переключатель окна, тесты)."""
    global _current
    with _lock:
        _current = resolve(code)
    return _current


def reset() -> None:
    """Забыть выбранный язык: следующий вызов снова прочитает настройки."""
    global _current
    with _lock:
        _current = None


def get_language() -> str:
    """Текущий язык. Первый вызов без ``configure`` читает настройки сам."""
    if _current is not None:
        return _current
    try:
        from .config import get_settings

        return configure(get_settings())
    except Exception as exc:  # настройки битые — язык всё равно нужен
        log.warning("язык по настройкам не определён: %s", exc)
        return set_language(AUTO)


# ------------------------------------------------------------ строки


def _lookup(key: str, lang: str | None) -> Any:
    lang = lang or get_language()
    for code in (lang, FALLBACK_LANG):
        value = catalog(code).get(key)
        if value is not None:
            return value
    return None


def _format(template: str, key: str, params: dict[str, Any]) -> str:
    """Каждая строка каталога — шаблон ``str.format``: ``{{``/``}}`` — скобки."""
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError) as exc:
        # Ошибка перевода не должна ронять вывод: строка без подстановки.
        log.error("строка %s не подставилась: %s", key, exc)
        return template


def t(key: str, /, *, lang: str | None = None, **params: Any) -> str:
    """Строка по ключу на текущем (или указанном) языке."""
    value = _lookup(key, lang)
    if not isinstance(value, str):
        return key
    return _format(value, key, params)


def plural_category(lang: str, n: int | float) -> str:
    """Категория CLDR для целого ``n``: ``one``, ``few``, ``many`` или ``other``."""
    if not float(n).is_integer():
        return "other"
    n = abs(int(n))
    if lang in _SLAVIC_PLURAL:
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"


def tn(key: str, n: int | float, /, *, lang: str | None = None, **params: Any) -> str:
    """Строка с формой множественного числа; ``{n}`` подставляется сам."""
    lang = lang or get_language()
    value = _lookup(key, lang)
    if isinstance(value, str):
        return _format(value, key, {"n": n, **params})
    if not isinstance(value, dict):
        return key
    # Форма берётся по правилам языка, в котором строка нашлась.
    found_in = lang if isinstance(catalog(lang).get(key), dict) else FALLBACK_LANG
    form = plural_category(found_in, n)
    template = value.get(form) or value.get("other") or next(iter(value.values()), key)
    return _format(str(template), key, {"n": n, **params})


def data(key: str, /, *, lang: str | None = None, default: Any = None) -> Any:
    """Нестроковое значение каталога (список, словарь) с откатом на en."""
    value = _lookup(key, lang)
    return default if value is None else value


def data_all(key: str) -> list[Any]:
    """Значения ключа из всех каталогов — для языковых данных, которые
    должны работать независимо от языка интерфейса (стоп-фразы whisper,
    словесная «пустота» в ответах модели)."""
    return [
        catalog(code)[key]
        for code in available_languages()
        if catalog(code).get(key) is not None
    ]


def strings_with_prefix(prefix: str, *, lang: str | None = None) -> dict[str, Any]:
    """Все ключи с префиксом (с откатом на en) — словарь для страницы ``/ui``."""
    lang = lang or get_language()
    merged = {
        key: value
        for key, value in catalog(FALLBACK_LANG).items()
        if key.startswith(prefix)
    }
    merged.update(
        {key: value for key, value in catalog(lang).items() if key.startswith(prefix)}
    )
    return merged


def placeholders(template: str) -> set[str]:
    """Имена параметров ``{…}`` в строке (``{{`` и ``}}`` — не параметры)."""
    return {
        name.split(".", 1)[0].split("[", 1)[0]
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    }
