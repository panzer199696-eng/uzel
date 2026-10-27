"""Расшифровка речи: бэкенды и роутер."""

from .base import (
    AllBackendsFailed,
    BackendUnavailable,
    CloudNotAllowed,
    STTBackend,
    TranscriptionError,
    TranscriptionResult,
)
from .router import STTRouter, build_router

__all__ = [
    "AllBackendsFailed",
    "BackendUnavailable",
    "CloudNotAllowed",
    "STTBackend",
    "STTRouter",
    "TranscriptionError",
    "TranscriptionResult",
    "build_router",
]
