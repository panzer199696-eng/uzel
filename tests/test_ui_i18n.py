"""Окно /ui на двух языках и переключатель языка (27.10.2026).

Выбор языка в окне хранится в каталоге данных (language.txt), а не в
браузере: его видят трей, CLI и ночной батч.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from uzel import i18n
from uzel.app import create_app


def _client(settings, lang: str) -> TestClient:
    return TestClient(create_app(settings.model_copy(update={"lang": lang})))


def _dictionary(page: str) -> dict:
    match = re.search(r"window\.UZEL_I18N = (\{.*?\});</script>", page)
    assert match, "словаря для app.js на странице нет"
    return json.loads(match.group(1))


def test_ui_page_in_english(settings):
    with _client(settings, "en") as client:
        page = client.get("/ui").text

    assert '<html lang="en">' in page
    assert "<title>UZEL — Voice to notes</title>" in page
    assert "Connection and recording" in page and "Resume recording" in page
    assert "Голос в заметки" not in page and "Связь и запись" not in page
    assert "{{t:" not in page and "{ЯЗЫК" not in page and "{СЛОВАРЬ}" not in page
    data = _dictionary(page)
    assert data["lang"] == "en"
    assert data["strings"]["ui.rec.streaming.title"] == "The pendant is sending audio"
    assert all(key.startswith("ui.") for key in data["strings"])


def test_ui_page_in_russian(settings):
    with _client(settings, "ru") as client:
        page = client.get("/ui").text

    assert '<html lang="ru">' in page
    assert "<title>УЗЕЛ — Голос в заметки</title>" in page
    assert "Возобновить запись" in page
    assert _dictionary(page)["strings"]["ui.n.facts"]["few"] == "{n} факта"


def test_language_switch_is_saved_in_data_dir_and_applies(settings):
    with _client(settings, "ru") as client:
        state = client.get("/ui/api/language").json()
        assert state["current"] == "ru" and state["choice"] == "auto"
        assert {item["code"] for item in state["available"]} >= {"ru", "en"}
        assert 'value="auto" selected' in client.get("/ui").text

        response = client.post("/ui/api/language", params={"lang": "en"})
        assert response.status_code == 200
        assert response.json()["current"] == "en"
        assert (settings.data_dir / "language.txt").read_text(encoding="utf-8").strip() == "en"
        page = client.get("/ui").text
        assert '<html lang="en">' in page and 'value="en" selected' in page
        assert client.get("/ui/api/status").json()["worker"]["batch_hint"] == (
            "the night batch is not blocked"
        )
        assert client.get("/privacy").json()["причина"] == "allowed"

        back = client.post("/ui/api/language", params={"lang": "auto"}).json()
        assert back["current"] == "ru" and back["choice"] == "auto"
        assert not (settings.data_dir / "language.txt").exists()


def test_window_choice_survives_restart(settings):
    i18n.save_lang_choice(settings.data_dir, "en")
    with _client(settings, "ru") as client:
        assert '<html lang="en">' in client.get("/ui").text


def test_unknown_language_is_refused(settings):
    with _client(settings, "ru") as client:
        response = client.post("/ui/api/language", params={"lang": "xx"})
    assert response.status_code == 400
    assert not (settings.data_dir / "language.txt").exists()


@pytest.mark.parametrize(
    ("lang", "speech", "observe"),
    [("en", "0 s", "the filter flags but lets through"), ("ru", "0 с", "фильтр помечает, но пропускает")],
)
def test_api_texts_follow_language(settings, lang, speech, observe):
    with _client(settings, lang) as client:
        assert client.get("/ui/api/day").json()["totals"]["speech"] == speech
        assert client.get("/ui/api/quality").json()["note"] == observe
        assert client.get("/ui/api/day", params={"date": "вчера"}).status_code == 400
