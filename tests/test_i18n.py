"""Локализация: каталоги, выбор языка, откат на en, формы множественного числа."""

from __future__ import annotations

import json

import pytest
from uzel import i18n
from uzel.config import Settings

LANGS = i18n.available_languages()


def каталог(lang: str) -> dict:
    return json.loads((i18n.LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def параметры(value) -> set[str]:
    if isinstance(value, str):
        return i18n.placeholders(value)
    if isinstance(value, dict):
        found: set[str] = set()
        for form in value.values():
            found |= i18n.placeholders(form)
        return found
    return set()


def test_ru_and_en_are_shipped():
    assert {"ru", "en"} <= set(LANGS)


@pytest.mark.parametrize("lang", LANGS)
def test_all_catalogs_have_the_same_keys(lang):
    """Новый язык — файл с тем же набором ключей, что и английский."""
    эталон, проверяемый = каталог("en"), каталог(lang)
    assert sorted(set(эталон) - set(проверяемый)) == []
    assert sorted(set(проверяемый) - set(эталон)) == []


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_and_value_kinds_match_english(lang):
    эталон, проверяемый = каталог("en"), каталог(lang)
    for key, value in эталон.items():
        other = проверяемый[key]
        assert type(other) is type(value), key
        assert параметры(other) == параметры(value), key


@pytest.mark.parametrize("lang", LANGS)
def test_every_template_formats_with_its_own_parameters(lang):
    """Лишняя фигурная скобка в переводе уронила бы вывод — ловим здесь."""
    for key, value in каталог(lang).items():
        forms = value.values() if isinstance(value, dict) else [value]
        for form in forms:
            if isinstance(form, str):
                form.format(**{name: 1 for name in i18n.placeholders(form)})


@pytest.mark.parametrize("lang", LANGS)
def test_plural_values_have_forms_of_their_language(lang):
    нужные = {"one", "few", "many", "other"} if lang in ("ru", "uk", "be") else {"one", "other"}
    for key, value in каталог(lang).items():
        if isinstance(value, dict):
            assert нужные <= set(value), key


def test_resolve_auto_uses_system_language_when_translated(monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "ru")
    assert i18n.resolve("auto") == "ru"
    monkeypatch.setattr(i18n, "system_language", lambda: "de")
    assert i18n.resolve("auto") == "en"  # перевода нет — английский
    monkeypatch.setattr(i18n, "system_language", lambda: None)
    assert i18n.resolve("") == "en"


def test_resolve_normalizes_and_falls_back():
    assert i18n.resolve("ru-RU") == "ru"
    assert i18n.resolve("EN_us.UTF-8") == "en"
    assert i18n.resolve("xx") == "en"


def test_t_falls_back_to_english_then_to_key(monkeypatch):
    ru = dict(i18n.catalog("ru"))
    ru.pop("export.day.facts")
    monkeypatch.setattr(i18n, "catalog", lambda lang: ru if lang == "ru" else каталог(lang))
    assert i18n.t("export.day.facts", lang="ru") == "**Facts**"
    assert i18n.t("нет.такого.ключа", lang="ru") == "нет.такого.ключа"


def test_t_formats_parameters_and_survives_missing_one():
    assert i18n.t("export.day.title", lang="en", date="2026-10-27") == "# 2026-10-27 — day"
    # Забытый параметр — строка без подстановки, а не исключение.
    assert "{date}" in i18n.t("export.day.title", lang="en", other=1)


@pytest.mark.parametrize(
    ("n", "форма"),
    [(1, "one"), (2, "few"), (4, "few"), (5, "many"), (11, "many"), (12, "many"),
     (21, "one"), (22, "few"), (25, "many"), (111, "many")],
)
def test_russian_plural_rules(n, форма):
    assert i18n.plural_category("ru", n) == форма


def test_english_plural_rules():
    assert i18n.plural_category("en", 1) == "one"
    assert i18n.plural_category("en", 0) == "other"
    assert i18n.plural_category("en", 2) == "other"


def test_window_choice_beats_setting_and_auto_forgets_it(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, lang="ru")
    assert i18n.configure(settings) == "ru"

    i18n.save_lang_choice(tmp_path, "en")
    assert (tmp_path / "language.txt").read_text(encoding="utf-8").strip() == "en"
    assert i18n.configure(settings) == "en"
    assert i18n.get_language() == "en"

    i18n.save_lang_choice(tmp_path, "auto")
    assert not (tmp_path / "language.txt").exists()
    assert i18n.configure(settings) == "ru"


def test_language_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("UZEL_LANG", raising=False)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.lang == "auto"
    assert settings.stt_language == "auto"


def test_language_names_for_switcher():
    assert i18n.language_name("ru") == "Русский"
    assert i18n.language_name("en") == "English"


def test_strings_with_prefix_merges_fallback(monkeypatch):
    """Каталог ru пуст — строки страницы приходят из английского."""
    monkeypatch.setattr(
        i18n, "catalog", lambda lang: {} if lang == "ru" else {"ui.x": "X", "cli.y": "Y"}
    )
    assert i18n.strings_with_prefix("ui.", lang="ru") == {"ui.x": "X"}


def test_env_example_documents_language_settings(monkeypatch):
    """Образец .env разбирается и называет обе языковые настройки."""
    from uzel.config import PROJECT_ROOT

    monkeypatch.delenv("UZEL_LANG", raising=False)
    sample = PROJECT_ROOT / ".env.example"
    settings = Settings(_env_file=sample)
    assert settings.lang == "auto"
    assert settings.stt_language == "auto"
    text = sample.read_text(encoding="utf-8")
    assert "UZEL_LANG=auto" in text and "UZEL_STT_LANGUAGE=auto" in text
