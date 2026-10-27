"""Векторный индекс: FR-041.

Проверки на настоящем Qdrant пропускаются, если нет кэша модели: качать 255 МБ
в обычном прогоне тестов нельзя.
"""

from __future__ import annotations

import pytest
from uzel.config import Settings
from uzel.vector import COLLECTION, NullIndex, QdrantIndex, build_index


def test_null_index_answers_without_failing():
    index = NullIndex()

    assert index.upsert(1, "текст", {}) is False
    assert index.search("что угодно") == []
    assert index.delete(1) is False
    assert index.count() == 0


def test_build_index_returns_stub_when_disabled(settings):
    index = build_index(settings.model_copy(update={"vector_enabled": False}))

    assert isinstance(index, NullIndex)


def test_build_index_returns_qdrant_when_enabled(settings):
    index = build_index(settings)

    assert isinstance(index, QdrantIndex)
    assert index.collection == COLLECTION
    assert index.path == settings.data_dir / "qdrant"


def test_index_failure_does_not_raise_into_the_core(tmp_path):
    """Недоступный индекс не имеет права ронять конвейер."""
    index = QdrantIndex(tmp_path / "q", model_name="нет-такой-модели")

    assert index.upsert(1, "текст", {}) is False
    assert index.search("запрос") == []
    assert index.count() == 0


def test_empty_text_is_not_indexed(tmp_path):
    index = QdrantIndex(tmp_path / "q")

    assert index.upsert(1, "   ", {}) is False


# --- проверки на настоящем Qdrant ---


def _skip_without_model_cache() -> None:
    cache = Settings().fastembed_cache
    if cache is None or not cache.exists():
        pytest.skip("нет кэша модели эмбеддингов — качать 255 МБ в тестах нельзя")


@pytest.fixture
def live_index(tmp_path):
    _skip_without_model_cache()
    index = QdrantIndex(tmp_path / "qdrant", cache_dir=Settings().fastembed_cache)
    try:
        yield index
    finally:
        index.close()


def _seed(index) -> None:
    index.upsert(1, "Обсудили заявку Петра на облицовочный кирпич", {"uid": "d"})
    index.upsert(2, "Составлен акт освидетельствования скрытых работ", {"uid": "d"})
    index.upsert(3, "Завтра поеду кататься на велосипеде", {"uid": "d"})


def test_semantic_search_beats_substring_matching(live_index):
    """Находит по смыслу там, где LIKE бессилен: слова и формы не совпадают."""
    _seed(live_index)

    assert live_index.search("покупка кирпича", limit=3)[0]["id"] == 1
    assert live_index.search("приёмка работ", limit=3)[0]["id"] == 2
    assert live_index.search("что там с Петром", limit=3)[0]["id"] == 1


@pytest.mark.xfail(
    reason="предел модели, замерено 24.08.2026: синоним материала находил акт вместо заявки, "
    "«поездка на велике» ставит акт выше велосипеда. Синонимы и разговорную речь "
    "paraphrase-multilingual-MiniLM не тянет — кандидат на замену модели.",
    strict=False,
)
def test_semantic_search_handles_synonyms_and_slang(live_index):
    _seed(live_index)

    assert live_index.search("стройматериал", limit=3)[0]["id"] == 1
    assert live_index.search("поездка на велике", limit=3)[0]["id"] == 3


def test_payload_comes_back_with_the_hit(live_index):
    live_index.upsert(7, "разговор про мосты", {"uid": "dev-1", "started_at": 100.0})

    hit = live_index.search("мосты", limit=1)[0]

    assert hit["id"] == 7
    assert hit["uid"] == "dev-1"
    assert hit["text"] == "разговор про мосты"


def test_upsert_replaces_previous_version_of_the_same_utterance(live_index):
    live_index.upsert(1, "первая версия текста", {})
    live_index.upsert(1, "исправленная версия текста", {})

    assert live_index.count() == 1
    assert (
        live_index.search("версия", limit=1)[0]["text"] == "исправленная версия текста"
    )


def test_delete_removes_point_from_the_index(live_index):
    live_index.upsert(1, "текст на удаление", {})
    assert live_index.count() == 1

    live_index.delete(1)

    assert live_index.count() == 0
