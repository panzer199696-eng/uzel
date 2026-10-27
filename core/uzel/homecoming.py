"""Приёмка возвращения из отпуска: одна команда на весь вечер (гейт A1).

Владелец возвращается и должен закрыть A1 (WER ≤ 15 %) за один вечер. Команда
``uzel приёмка-возвращения`` проверяет окружение, подсказывает, чего не хватает,
ждёт эталон в ``tests/wer/`` и, когда всё на месте, сама считает WER — общий и
по доменным терминам — и печатает вердикт.

Здесь живёт и расшифровка эталона `transcribe_for_wer`: она общая для
``uzel wer`` и приёмки, чтобы правило облака («только явный --backend groq»)
существовало в одном месте.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .audio import read_wav
from .config import Settings
from .stt.router import build_router
from .vad import SileroVAD, detect_speech

_GB = 1024**3

# Расширения, которые человек обычно приносит с диктофона вместо WAV.
_AUDIO_EXTENSIONS = (".m4a", ".mp3", ".ogg", ".opus", ".flac", ".webm", ".aac")


class NoSpeechFound(RuntimeError):
    """VAD не нашёл речи в поданном аудио — замерять нечего."""


@dataclass
class TranscribeResult:
    """Итог расшифровки эталона: текст и цифры для отчёта."""

    hypothesis: str
    backend: str
    audio_seconds: float
    speech_seconds: float
    processing_seconds: float


def transcribe_for_wer(
    audio_path: Path | str,
    settings: Settings,
    *,
    backend: str | None = None,
    no_vad: bool = False,
    echo=print,
) -> TranscribeResult:
    """Расшифровывает эталон для замера WER.

    Правило облака — буквально как в ``cmd_wer``: наружу уходит только то,
    что человек сам выбрал флагом ``--backend groq``. Никакого «а пусть
    Groq попробует» после отказа локального бэкенда.
    """
    import time

    audio_path = Path(audio_path)
    samples, sample_rate = read_wav(audio_path, expected_sr=settings.sample_rate)
    audio_seconds = len(samples) / sample_rate
    echo(f"Эталон: {audio_path.name}, {audio_seconds / 60:.1f} мин")

    backends = [backend] if backend else list(settings.stt_backends)
    router = build_router(settings.model_copy(update={"stt_backends": tuple(backends)}))
    # Прогрев до таймера: загрузка large-v3 занимает ~8 с и на коротком
    # эталоне раздувала RTF вчетверо (приёмка 25.09: 2,2 на 4 с звука).
    for candidate in getattr(router, "backends", []):
        if not candidate.is_cloud:
            candidate.available()

    started = time.monotonic()

    if no_vad:
        pieces = [samples]
        speech_seconds = audio_seconds
    else:
        vad = SileroVAD(settings.vad_model_path, sample_rate)
        segments = detect_speech(
            samples,
            sample_rate,
            vad=vad,
            threshold=settings.vad_threshold,
            min_speech_ms=settings.min_speech_ms,
            min_silence_ms=settings.min_silence_ms,
            speech_pad_ms=settings.speech_pad_ms,
        )
        if not segments:
            raise NoSpeechFound("VAD не нашёл речи — замерять нечего")
        pieces = [segment.slice(samples, sample_rate) for segment in segments]
        speech_seconds = sum(segment.duration for segment in segments)
        echo(
            f"VAD: отрезков речи {len(segments)}, {speech_seconds / 60:.1f} мин "
            f"({speech_seconds / audio_seconds:.0%} потока)"
        )

    texts: list[str] = []
    backend_name = backends[0] if backends else "—"
    for index, piece in enumerate(pieces, start=1):
        # Облако — только если человек сам выбрал его флагом --backend groq.
        allow_cloud = backend == "groq"
        result = router.transcribe(
            piece,
            sample_rate,
            language=settings.stt_language,
            allow_cloud=allow_cloud,
        )
        texts.append(result.text)
        backend_name = result.backend
        echo(
            f"  [{index}/{len(pieces)}] {result.backend}: {len(result.text)} символов"
        )

    return TranscribeResult(
        hypothesis=" ".join(texts),
        backend=backend_name,
        audio_seconds=audio_seconds,
        speech_seconds=speech_seconds,
        processing_seconds=time.monotonic() - started,
    )


@dataclass
class Check:
    """Одна проверка окружения: имя, итог, подсказка, блокирует ли замер."""

    name: str  # «Модель VAD», «Whisper large-v3», «Ollama», «Место на диске», «Ядро»
    ok: bool
    detail: str  # что найдено / что делать, одной-двумя фразами
    blocking: bool  # True — без этого WER не посчитать


def _http_get(url: str, timeout: float) -> dict | None:
    """GET → JSON-ответ либо ``None`` при любой ошибке. Исключений наружу нет."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _nearest_existing_dir(path: Path) -> Path:
    """Каталог либо ближайший существующий родитель (для disk_usage)."""
    target = Path(path)
    while not target.exists():
        target = target.parent
    return target


def check_environment(
    settings: Settings,
    *,
    http_get=_http_get,
    disk_usage=shutil.disk_usage,
    find_spec=importlib.util.find_spec,
) -> list[Check]:
    """Проверяет всё, что нужно для вечера замера, и говорит, что чинить.

    Сеть и диск подменяемыми параметрами — ради тестов без сети и ради того,
    чтобы проверки не дёргали ничего лишнего сами по себе.
    """
    checks: list[Check] = []

    # 1. Модель VAD — без неё не отрезать тишину.
    vad_path = Path(settings.vad_model_path)
    if vad_path.exists():
        checks.append(Check("Модель VAD", True, f"на месте: {vad_path}", False))
    else:
        checks.append(
            Check("Модель VAD", False, f"нет файла {vad_path}", True)
        )

    # 2. faster-whisper установлен — но НЕ импортируем: импорт грузит CUDA.
    if "local" in settings.stt_backends:
        if find_spec("faster_whisper") is not None:
            checks.append(Check("faster-whisper", True, "установлен", False))
        else:
            checks.append(
                Check(
                    "faster-whisper",
                    False,
                    "не установлен: pip install -r requirements-cuda.txt",
                    True,
                )
            )

    # 3. Веса whisper: без них не блокируем — просто предупредить о скачивании.
    if settings.local_model_dir is not None:
        weights = Path(settings.local_model_dir) / settings.local_model
        if (weights / "model.bin").exists():
            checks.append(
                Check(f"Whisper {settings.local_model}", True, f"веса на месте: {weights}", False)
            )
        else:
            checks.append(
                Check(
                    f"Whisper {settings.local_model}",
                    False,
                    f"весов в {weights} нет — скачаются при первом запуске, "
                    "~3 ГБ, нужен интернет",
                    False,
                )
            )

    # 4. Ollama: для A1 не нужна, поэтому никогда не блокирует.
    tags = http_get(f"{settings.ollama_url.rstrip('/')}/api/tags", 3)
    if tags is None:
        checks.append(
            Check(
                "Ollama",
                False,
                f"не отвечает (для A1 не нужна) — поднять: ollama serve, "
                f"затем ollama pull {settings.ollama_model}; "
                "нужна для разбора разговоров в первый боевой день",
                False,
            )
        )
    else:
        names = [model.get("name") for model in tags.get("models", [])]
        if settings.ollama_model in names:
            checks.append(
                Check(
                    "Ollama",
                    True,
                    f"отвечает, модель {settings.ollama_model} готова "
                    "(для разбора разговоров в первый боевой день)",
                    False,
                )
            )
        else:
            checks.append(
                Check(
                    "Ollama",
                    False,
                    f"отвечает, но модели {settings.ollama_model} нет — "
                    f"догрузить: ollama pull {settings.ollama_model}",
                    False,
                )
            )

    # 5. Место на диске: сырое аудио идёт ~600 МБ/сутки.
    free = disk_usage(_nearest_existing_dir(Path(settings.data_dir))).free
    free_gb = free / _GB
    if free_gb >= 20:
        checks.append(
            Check("Место на диске", True, f"свободно {free_gb:.1f} ГБ", False)
        )
    elif free_gb >= 2:
        checks.append(
            Check(
                "Место на диске",
                False,
                f"свободно {free_gb:.1f} ГБ — мало: сырое аудио ~600 МБ/сутки",
                False,
            )
        )
    else:
        checks.append(
            Check(
                "Место на диске",
                False,
                f"свободно {free_gb:.1f} ГБ — критически мало, освободи место",
                True,
            )
        )

    # 6. Ядро: для замера не нужно, для боевого дня — обязательно.
    host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host
    health = http_get(f"http://{host}:{settings.port}/health", 2)
    if health is not None and health.get("status") == "ok":
        version = health.get("version", "?")
        checks.append(Check("Ядро", True, f"работает, версия {version}", False))
    else:
        checks.append(
            Check(
                "Ядро",
                False,
                "ядро не запущено — для замера не нужно, "
                "для боевого дня: uzel.cmd app",
                False,
            )
        )

    return checks


@dataclass
class ReferenceState:
    """Состояние эталона: что нашлось и что с ним не так."""

    audio: Path
    text: Path
    audio_ok: bool
    text_ok: bool
    problems: list[str] = field(default_factory=list)


def _inspect_wav(path: Path) -> str | None:
    """Заголовок WAV глазами модуля wave: нужен моно 16 кГц. None — всё хорошо."""
    import wave

    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        return f"{path.name}: битый WAV-заголовок ({exc})"
    if channels != 1 or rate != 16000:
        return (
            f"{path.name}: {channels} кан., {rate} Гц — нужен моно 16 кГц: "
            f"ffmpeg -i {path} -ac 1 -ar 16000 {path.with_suffix('.wav')}"
        )
    return None


def find_reference(wer_dir: Path | str, name: str) -> ReferenceState:
    """Ищет эталон ``<name>.wav`` + ``<name>.txt`` и проверяет его пригодность."""
    wer_dir = Path(wer_dir)
    audio = wer_dir / f"{name}.wav"
    text = wer_dir / f"{name}.txt"
    problems: list[str] = []

    audio_ok = False
    if audio.exists():
        problem = _inspect_wav(audio)
        if problem is None:
            audio_ok = True
        else:
            problems.append(problem)
    else:
        # Диктофон обычно отдаёт не WAV — подскажем готовую команду конвертации.
        for extension in _AUDIO_EXTENSIONS:
            candidate = wer_dir / f"{name}{extension}"
            if candidate.exists():
                problems.append(
                    f"найден {candidate.name} вместо WAV — сконвертируй: "
                    f"ffmpeg -i {candidate} -ac 1 -ar 16000 {audio}"
                )
                break

    text_ok = False
    if text.exists():
        words = len(text.read_text(encoding="utf-8").split())
        if words == 0:
            problems.append(f"{text.name}: файл пустой")
        elif words < 20:
            problems.append(f"эталон подозрительно короткий: {words} слов")
        else:
            text_ok = True

    return ReferenceState(audio, text, audio_ok, text_ok, problems)


def render_verdict(report, *, threshold: float, rtf: float | None = None) -> str:
    """Блок вердикта по гейту A1 — цифры, приговор и следующий шаг."""
    passed = report.passed(threshold)
    lines = [
        "## Вердикт по A1",
        "",
        f"- Общий WER: {report.wer:.1%} при пороге {threshold:.0%}",
        f"- WER по доменным терминам: {report.domain_wer:.1%} "
        f"({report.domain_errors} из {report.domain_total} терминов)",
    ]
    if rtf is not None:
        margin = "запас есть" if rtf <= 0.5 else "запаса нет"
        lines.append(
            f"- RTF: {rtf:.2f} (NFR-003: RTF ≤ 0,5 — {margin} по скорости)"
        )
    lines += ["", f"**{'A1: ПРОЙДЕН' if passed else 'A1: НЕ ПРОЙДЕН'}**", ""]

    if report.domain_total == 0:
        lines += [
            "> ⚠️ Доменных терминов в эталоне нет, вердикт неполный: "
            "начитай часть 1–2 из текст-для-начитки.md",
            "",
        ]
    elif passed and report.domain_wer > 0.30:
        # Главный риск проекта: общая цифра прячет потерю «АОСР» и «КС-2»
        # (см. docstring wer.py) — сказать об этом вслух.
        lines += [
            f"> ⚠️ Общий порог пройден, но доменные термины теряются "
            f"({report.domain_errors} из {report.domain_total}) — это и есть "
            "главный риск, см. описание в wer.py",
            "",
        ]

    if passed:
        lines.append(
            "Следующий шаг: модель large-v3 остаётся; дальше — первый боевой "
            "день, чек-лист docs/08-первый-боевой-день.md"
        )
    else:
        lines.append(
            "Следующий шаг: смотри таблицу ошибок в отчёте; только теперь "
            "есть смысл пробовать другую модель (GigaAM)"
        )
    return "\n".join(lines)
