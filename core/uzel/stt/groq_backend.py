"""Облачный бэкенд: Groq Whisper large-v3 (FR-020).

Точка входа OpenAI-совместимая, поэтому обходимся httpx без отдельного SDK.
Проверено вживую 19.08.2026: русский из WAV 16 кГц распознаётся.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from .base import (
    BackendUnavailable,
    STTBackend,
    TranscriptionError,
    TranscriptionResult,
    samples_to_wav_bytes,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqBackend(STTBackend):
    name = "groq"
    is_cloud = True

    def __init__(
        self,
        api_key: str | None,
        model: str = "whisper-large-v3",
        timeout_s: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self._api_key)

    def transcribe(
        self, samples: np.ndarray, sample_rate: int, language: str
    ) -> TranscriptionResult:
        import httpx

        if not self.available():
            raise BackendUnavailable("не задан UZEL_GROQ_API_KEY")

        audio_seconds = len(samples) / float(sample_rate)
        payload = samples_to_wav_bytes(samples, sample_rate)
        started = time.monotonic()

        try:
            response = httpx.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("audio.wav", payload, "audio/wav")},
                data={
                    "model": self._model,
                    "language": language,
                    "response_format": "verbose_json",
                    "temperature": "0",
                },
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"groq: сеть недоступна ({exc})") from exc

        if response.status_code != 200:
            raise TranscriptionError(
                f"groq: HTTP {response.status_code} — {response.text[:200]}"
            )

        body = response.json()
        return TranscriptionResult(
            text=(body.get("text") or "").strip(),
            language=body.get("language") or language,
            backend=self.name,
            audio_seconds=audio_seconds,
            latency_s=time.monotonic() - started,
            raw=body,
        )
