"""Векторный поиск по репликам (FR-041).

Qdrant в локальном режиме — тот же движок и та же модель эмбеддингов, что у
существующего RAG по Obsidian, поэтому кэш моделей переиспользуется и ничего
не качается заново.

Индекс намеренно отделён от SQLite: если Qdrant или модель недоступны, ядро
обязано работать дальше, просто без смыслового поиска.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_SIZE = 384
COLLECTION = "uzel_utterances"


class VectorIndex(Protocol):
    """Контракт индекса. Реализации не должны бросать наружу."""

    def upsert(self, utterance_id: int, text: str, payload: dict) -> bool: ...

    def search(self, query: str, limit: int = 10) -> list[dict]: ...

    def delete(self, utterance_id: int) -> bool: ...

    def count(self) -> int: ...


class NullIndex:
    """Заглушка: смысловой поиск выключен, остальное ядро работает."""

    def upsert(self, utterance_id: int, text: str, payload: dict) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> list[dict]:
        return []

    def delete(self, utterance_id: int) -> bool:
        return False

    def count(self) -> int:
        return 0


class QdrantIndex:
    """Локальный Qdrant + fastembed. Модель грузится при первом обращении."""

    def __init__(
        self,
        path: Path | str,
        *,
        cache_dir: Path | str | None = None,
        collection: str = COLLECTION,
        model_name: str = EMBED_MODEL,
    ) -> None:
        self.path = Path(path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.collection = collection
        self.model_name = model_name
        self._client = None
        self._embedder = None

    # ---------- ленивая инициализация ----------

    def _ensure(self):
        if self._client is not None:
            return self._client

        from fastembed import TextEmbedding
        from qdrant_client import QdrantClient, models

        self.path.mkdir(parents=True, exist_ok=True)
        self._embedder = TextEmbedding(
            self.model_name, cache_dir=str(self.cache_dir) if self.cache_dir else None
        )
        self._client = QdrantClient(path=str(self.path))

        existing = {c.name for c in self._client.get_collections().collections}
        if self.collection not in existing:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=VECTOR_SIZE, distance=models.Distance.COSINE
                ),
            )
            log.info("создана коллекция %s", self.collection)
        return self._client

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure()
        return [vector.tolist() for vector in self._embedder.embed(list(texts))]

    # ---------- операции ----------

    def upsert(self, utterance_id: int, text: str, payload: dict) -> bool:
        if not text.strip():
            return False
        try:
            from qdrant_client import models

            client = self._ensure()
            vector = self._embed([text])[0]
            client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(
                        id=utterance_id,
                        vector=vector,
                        payload={"text": text, **payload},
                    )
                ],
            )
            return True
        except Exception as exc:
            log.warning(
                "векторный индекс недоступен, реплика %d не проиндексирована: %s",
                utterance_id,
                exc,
            )
            return False

    def search(self, query: str, limit: int = 10) -> list[dict]:
        try:
            client = self._ensure()
            vector = self._embed([query])[0]
            hits = client.query_points(
                collection_name=self.collection, query=vector, limit=limit
            ).points
            return [
                {"id": hit.id, "score": hit.score, **(hit.payload or {})}
                for hit in hits
            ]
        except Exception as exc:
            log.warning("смысловой поиск недоступен: %s", exc)
            return []

    def delete(self, utterance_id: int) -> bool:
        try:
            from qdrant_client import models

            client = self._ensure()
            client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=[utterance_id]),
            )
            return True
        except Exception as exc:
            log.warning("не удалось удалить точку %d: %s", utterance_id, exc)
            return False

    def count(self) -> int:
        try:
            client = self._ensure()
            return int(client.count(collection_name=self.collection).count)
        except Exception as exc:
            log.warning("не удалось получить размер индекса: %s", exc)
            return 0

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def build_index(settings) -> VectorIndex:
    """Собирает индекс по настройкам; при выключенном Qdrant — заглушка."""
    if not settings.vector_enabled:
        log.info("смысловой поиск выключен настройкой")
        return NullIndex()
    return QdrantIndex(
        settings.qdrant_path,
        cache_dir=settings.fastembed_cache,
        collection=settings.qdrant_collection,
    )
