"""API страницы окна (ТЗ P7): статус, лента дня, разговор, поиск, качество.

Лента дня принципиально не содержит дословных реплик — это проверяется
маркерным словом, которого нет ни в саммари, ни в фактах, ни в задачах.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uzel.app import create_app
from uzel.storage import Storage

MARKER = "контрольноемаркерноеслово2026"


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def _fill_day(settings) -> dict:
    """Разговор, факт, задача и реплика с маркером — на сегодня, в 12:00."""
    noon = dt.datetime.combine(dt.date.today(), dt.time(12, 0)).timestamp()
    with Storage(settings.db_path) as storage:
        person = storage.upsert_person("Пётр")
        conversation = storage.open_conversation(uid="dev-1", started_at=noon)
        utterance = storage.add_utterance(
            uid="dev-1",
            started_at=noon,
            ended_at=noon + 27 * 60,
            speech_seconds=41.0,
        )
        storage.set_transcript(
            utterance,
            text=f"обычная реплика с редким словом {MARKER} внутри",
            language="ru",
            backend="local",
        )
        storage.set_speaker(utterance, person)
        storage.attach_utterances(conversation, [utterance])
        storage.close_conversation(conversation, summary="обсудили поставку")
        storage.add_fact(conversation, "поставка 30 сентября", said_at=noon + 360)
        storage.add_task(
            conversation, "подготовить КП", assignee="Пётр", due="2026-09-30"
        )
        storage.add_tag("работа", conversation_id=conversation)
    return {"conversation": conversation, "utterance": utterance}


# ---------- страница и статика ----------


def test_ui_page_served_with_name(client):
    response = client.get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "УЗЕЛ" in response.text


def test_static_assets_served(client):
    for name, kind in (
        ("app.js", "javascript"),
        ("style.css", "css"),
        ("index.html", "text/html"),
    ):
        response = client.get(f"/ui/static/{name}")
        assert response.status_code == 200, name
        assert kind in response.headers["content-type"], name


def test_static_page_references_no_external_addresses():
    """Критерий A6: страница не тянет ничего из сети."""
    static_dir = Path(__file__).resolve().parents[1] / "core" / "uzel" / "ui" / "static"
    for name in ("index.html", "app.js", "style.css"):
        text = (static_dir / name).read_text(encoding="utf-8")
        for scheme in ("http://", "https://"):
            for chunk in text.split(scheme)[1:]:
                address = scheme + chunk
                assert "127.0.0.1" in address.split('"')[0], (name, address)


# ---------- /ui/api/status ----------


def test_status_on_empty_database(client):
    response = client.get("/ui/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["privacy"]["код"] == "allowed"
    assert body["queue"]["pending"] == 0
    assert body["queue"]["processing"] == 0
    assert body["device"]["known"] is False
    assert body["db"]["conversations"] == 0
    assert body["worker"]["stop_file"] is False


def test_status_reflects_mute(client, settings):
    assert client.post("/privacy/mute", params={"minutes": 30}).status_code == 200

    body = client.get("/ui/api/status").json()

    assert body["privacy"]["код"] == "manual"
    assert body["privacy"]["запись"] == "выключена"


def test_status_shows_stop_file_and_windows(client, settings):
    settings.worker_stop_file.write_text("", encoding="utf-8")

    body = client.get("/ui/api/status").json()

    assert body["worker"]["stop_file"] is True


def test_status_lists_quiet_windows(client, settings):
    # Стоп-зоны берутся из гейта ядра; перестраиваем приложение с окном.
    with TestClient(create_app(
        settings.model_copy(update={"quiet_windows": ("22:00-06:00",)})
    )) as other:
        body = other.get("/ui/api/status").json()

    assert body["quiet_windows"] == ["22:00–06:00 ежедневно"]


# ---------- /ui/api/day ----------


def test_day_feed_shows_conversation_fact_and_task(client, settings):
    prepared = _fill_day(settings)

    response = client.get("/ui/api/day")

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == dt.date.today().isoformat()

    (card,) = body["conversations"]
    assert card["id"] == prepared["conversation"]
    assert card["started"] == "12:00"
    assert card["status"] == "closed"
    assert card["summary"] == "обсудили поставку"
    assert card["tags"] == ["работа"]
    assert card["participants"] == ["Пётр"]
    assert card["utterances"] == 1
    assert card["facts"] == 1
    assert card["tasks"] == 1

    (fact,) = body["facts"]
    assert fact["text"] == "поставка 30 сентября"
    assert fact["conversation_id"] == prepared["conversation"]

    (task,) = body["tasks"]
    assert task["text"] == "подготовить КП"
    assert task["assignee"] == "Пётр"

    totals = body["totals"]
    assert totals["conversations"] == 1
    assert totals["facts"] == 1
    assert totals["tasks"] == 1
    assert totals["speech"]


def test_day_feed_has_no_verbatim_utterances(client, settings):
    """Ни одной дословной реплики в ленте дня — принцип приватности."""
    _fill_day(settings)

    response = client.get("/ui/api/day")

    assert MARKER not in response.text


def test_day_empty_shows_zero_totals(client):
    body = client.get("/ui/api/day").json()

    assert body["conversations"] == []
    assert body["totals"]["conversations"] == 0


def test_day_bad_date_is_400(client):
    response = client.get("/ui/api/day", params={"date": "кривая-дата"})

    assert response.status_code == 400
    assert "ГГГГ-ММ-ДД" in response.json()["detail"]


# ---------- /ui/api/conversation/{id} ----------


def test_conversation_without_and_with_utterances(client, settings):
    prepared = _fill_day(settings)
    url = f"/ui/api/conversation/{prepared['conversation']}"

    plain = client.get(url)
    assert plain.status_code == 200
    body = plain.json()
    assert body["summary"] == "обсудили поставку"
    assert "transcript" not in body
    assert MARKER not in plain.text
    assert [fact["text"] for fact in body["facts"]] == ["поставка 30 сентября"]
    assert [task["text"] for task in body["tasks"]] == ["подготовить КП"]

    full = client.get(url, params={"with_utterances": "true"})
    assert full.status_code == 200
    rows = full.json()["transcript"]
    assert len(rows) == 1
    assert rows[0]["speaker"] == "Пётр"
    assert MARKER in rows[0]["text"]
    assert rows[0]["status"] == "transcribed"


def test_conversation_missing_is_404(client):
    assert client.get("/ui/api/conversation/999").status_code == 404


# ---------- приватные реплики (P11.2: владелец видит всё, но с пометкой) ----------


def test_conversation_transcript_includes_private_marked(client, settings):
    """Карточка разговора показывает приватную реплику с ``is_private``."""
    noon = dt.datetime.combine(dt.date.today(), dt.time(14, 0)).timestamp()
    with Storage(settings.db_path) as storage:
        conversation = storage.open_conversation(uid="dev-1", started_at=noon)
        public = storage.add_utterance(
            uid="dev-1",
            started_at=noon,
            ended_at=noon + 30,
            speech_seconds=20.0,
            conversation_id=conversation,
        )
        private = storage.add_utterance(
            uid="dev-1",
            started_at=noon + 60,
            ended_at=noon + 90,
            speech_seconds=20.0,
            conversation_id=conversation,
            is_private=True,
        )
        storage.set_transcript(
            public, text="обычная реплика дня", language="ru", backend="local"
        )
        storage.set_transcript(
            private,
            text=f"приватная реплика {MARKER}",
            language="ru",
            backend="local",
        )
        storage.attach_utterances(conversation, [public, private])

    full = client.get(
        f"/ui/api/conversation/{conversation}", params={"with_utterances": "true"}
    )

    assert full.status_code == 200
    rows = full.json()["transcript"]
    assert len(rows) == 2
    by_flag = {row["is_private"]: row for row in rows}
    assert MARKER in by_flag[True]["text"]
    assert by_flag[False]["is_private"] is False


def test_search_includes_private_marked(client, settings):
    """Подстрочный поиск владельца находит приватное — с видимой пометкой."""
    noon = dt.datetime.combine(dt.date.today(), dt.time(15, 0)).timestamp()
    with Storage(settings.db_path) as storage:
        uid = storage.add_utterance(
            uid="dev-1",
            started_at=noon,
            ended_at=noon + 30,
            speech_seconds=20.0,
            is_private=True,
        )
        storage.set_transcript(
            uid,
            text=f"приватная реплика {MARKER}",
            language="ru",
            backend="local",
        )

    found = client.get(
        "/ui/api/search", params={"q": MARKER, "semantic": "false"}
    ).json()

    assert found["origin"] == "подстрочным поиском"
    (hit,) = found["results"]
    assert hit["utterance_id"] == uid
    assert hit["is_private"] is True
    assert MARKER in hit["text"]


# ---------- /ui/api/search ----------


def test_search_finds_by_substring(client, settings):
    _fill_day(settings)

    response = client.get(
        "/ui/api/search", params={"q": MARKER, "semantic": "false"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "подстрочным поиском"
    (hit,) = body["results"]
    assert hit["utterance_id"] > 0
    assert hit["speaker"] == "Пётр"
    assert MARKER in hit["text"]


def test_search_without_query_is_400(client):
    response = client.get("/ui/api/search", params={"q": ""})

    assert response.status_code == 400


def test_search_semantic_failure_falls_back_to_substring(client, settings, monkeypatch):
    """Смысловой поиск недоступен (нет Qdrant/модели) — откат на подстрочный."""
    _fill_day(settings)

    import uzel.vector as vector_module

    def broken_build_index(_settings):
        raise RuntimeError("нет индекса")

    monkeypatch.setattr(vector_module, "build_index", broken_build_index)

    response = client.get("/ui/api/search", params={"q": MARKER})

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "подстрочным поиском"
    assert len(body["results"]) == 1


# ---------- /ui/api/quality ----------


def test_quality_counts_accepted_and_rejected(client, settings):
    noon = dt.datetime.combine(dt.date.today(), dt.time(13, 0)).timestamp()
    with Storage(settings.db_path) as storage:
        accepted = storage.add_utterance(
            uid="dev-1", started_at=noon, ended_at=noon + 5, speech_seconds=4.0
        )
        storage.set_transcript(accepted, text="нормальная реплика", language="ru", backend="local")
        rejected = storage.add_utterance(
            uid="dev-1", started_at=noon + 10, ended_at=noon + 14, speech_seconds=3.0
        )
        storage.set_transcript(rejected, text="так так так так", language="ru", backend="local")
        storage.mark_quality(rejected, reason="looped", enforce=True)

    response = client.get("/ui/api/quality")

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == dt.date.today().isoformat()
    assert body["total"] == 2
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert body["enforce"] is settings.quality_enforce
    (reason,) = body["by_reason"]
    assert reason["code"] == "looped"
    assert reason["title"] == "зацикливание"
    assert reason["count"] == 1
    if not settings.quality_enforce:
        assert body["note"] == "фильтр помечает, но пропускает"


def test_static_links_carry_version(client):
    """Ссылки на статику помечены версией: иначе окно после обновления
    показывает старый app.js из кэша WebView2 и ведёт себя «как вчера»."""
    from uzel import __version__

    разметка = client.get("/ui").text

    assert f"/ui/static/app.js?v={__version__}" in разметка
    assert f"/ui/static/style.css?v={__version__}" in разметка
    assert "{ВЕРСИЯ}" not in разметка  # подстановка сработала


def test_ui_frontend_regressions():
    """Проверяем поведение UI, включая выключение записи из временной паузы.

    Node-проверки выполняют настоящий JS с mock DOM и API: для выключения
    записи из паузы не требуется сначала разрешать запись.
    """
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        pytest.skip("Node.js не найден: проверки интерфейса требуют Node.js")

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/test_ui_frontend.cjs"),
            str(root / "core/uzel/ui/static"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_loopback_client_needs_no_token(tmp_path):
    """Окно, трей и мост ходят с самого ПК: токен им не нужен (05.10.2026)."""
    from fastapi.testclient import TestClient
    from uzel.app import create_app
    from uzel.config import Settings

    settings = Settings(data_dir=tmp_path / "data", api_token="секрет-ascii-ok".encode().hex())
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/ui/api/status").status_code == 200
    with TestClient(app, client=("192.168.20.110", 50000)) as lan:
        assert lan.get("/ui/api/status").status_code == 401
        assert lan.get(f"/ui/api/status?token={settings.api_token}").status_code == 200


def test_desktop_url_uses_loopback_for_wildcard_host():
    from uzel.config import Settings
    from uzel.desktop import AppOptions, CoreServer

    server = CoreServer(Settings(), AppOptions(host="0.0.0.0", port=8770))
    assert server.url == "http://127.0.0.1:8770"
