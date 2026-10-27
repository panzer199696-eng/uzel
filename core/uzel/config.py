"""Конфигурация ядра (FR/NFR — см. specs/spec.md)."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent

log = logging.getLogger(__name__)

# P11.2, находка 14: .env с ключами — вне проекта. Проект лежит на Рабочем
# столе, а тот заведён в бэкап Google Drive: .gitignore прячет файл от git,
# но не от Drive. Путь задаётся переменной UZEL_ENV_FILE, по умолчанию —
# рядом с данными. Файл в корне проекта читается для совместимости, но
# слабее: строка из ~/uzel-data/.env перебивает его.
LEGACY_ENV_FILE = PROJECT_ROOT / ".env"
USER_ENV_FILE = Path(
    os.environ.get("UZEL_ENV_FILE") or Path.home() / "uzel-data" / ".env"
)


class Settings(BaseSettings):
    """Настройки: переменные окружения ``UZEL_*`` сильнее ``~/uzel-data/.env``,
    тот сильнее устаревшего ``.env`` в корне проекта."""

    model_config = SettingsConfigDict(
        env_prefix="UZEL_",
        # В кортеже побеждает последний файл.
        env_file=(LEGACY_ENV_FILE, USER_ENV_FILE),
        env_file_encoding="utf-8-sig",  # .env из Блокнота начинается с BOM — без -sig ломает разбор
        extra="ignore",
    )

    # --- общее ---
    # НЕ PROJECT_ROOT/"data": проект лежит на Рабочем столе, а он заведён в
    # бэкап Google Drive — туда уехали бы сырое аудио, база всех реплик и .env
    # с ключами (пункт 4.4 ТЗ). По умолчанию — вне зоны синка, дома.
    data_dir: Path = Path.home() / "uzel-data"
    sample_rate: int = 16000
    log_level: str = "INFO"

    # NFR-005: наружу не слушаем
    host: str = "127.0.0.1"
    port: int = 8770

    # P11, находка 1: токен на ВСЁ приложение (кроме /health). Пуст при
    # петлевом host — локально работать должно без танцев. Не петлевой
    # host без токена — ядро отказывается стартовать (см. security.py).
    api_token: str | None = None

    # --- VAD и сегментация (FR-010…FR-013) ---
    vad_model_path: Path = PACKAGE_ROOT / "models" / "silero_vad.onnx"
    vad_threshold: float = 0.5
    min_speech_ms: int = 400  # FR-011
    min_silence_ms: int = 400
    speech_pad_ms: int = 300
    utterance_gap_ms: int = 1500  # FR-012
    # Склейка реплик через стыки фрагментов перед расшифровкой: предел длины
    # склеенного куска — окно whisper. 0 — склейка выключена.
    stitch_max_s: float = 30.0
    conversation_gap_s: int = 300  # FR-013

    # --- STT (FR-020…FR-023) ---
    # Порядок = приоритет. FR-021: локальный бэкенд первым. Groq подключается
    # только явной настройкой: облако — не молчаливый запасной путь.
    stt_backends: tuple[str, ...] = ("local",)
    stt_language: str = "ru"

    groq_api_key: str | None = None
    groq_model: str = "whisper-large-v3"
    groq_timeout_s: float = 120.0

    local_model: str = "large-v3"
    # Папка с выгруженной моделью: существование local_model_dir/local_model
    # исключает выход в сеть при загрузке.
    local_model_dir: Path | None = Path.home() / "models-whisper"
    local_device: str = "auto"
    # int8_float16: 1953 МиБ против 3084 у float16 — на карте 6 ГБ.
    local_compute_type: str = "int8_float16"
    # Параметры против галлюцинаций faster-whisper (FR-023).
    local_beam_size: int = 5
    local_no_speech_threshold: float = 0.6
    local_logprob_threshold: float = -1.0
    local_compression_threshold: float = 2.4
    # Предохранитель роутера: N подряд отказов локального останавливают пачку.
    local_failure_streak: int = 3

    # --- большие модели (FR-030…FR-032) ---
    # Ollama первой и по умолчанию единственной: она единственная считает
    # на этом ПК. Шлюз и Groq — облако (см. llm.py), подключаются явно.
    llm_backends: tuple[str, ...] = ("ollama",)
    llm_timeout_s: float = 180.0
    # Предохранитель роутера разбора (P11, находка 7): N подряд отказов
    # локальных моделей останавливают разбор — симметрия с local_failure_streak.
    llm_failure_streak: int = 3

    ollama_url: str = "http://127.0.0.1:11434"
    # Стоит на этом ПК с 19.09.2026 (4,7 ГБ). Русский и format:"json" держит,
    # в 6 ГБ карты с num_ctx 8192 влезает не целиком: 21 % слоёв уходит на CPU.
    # qwen3:8b с 23.10.2026 (решение автора): на 7 боевых разговорах задачи предметные,
    # пересказ конкретный; ~71 с на кусок против ~30 у qwen2.5, 6,6 ГБ (37 % на CPU).
    # Замер — ops/замер-разбора-v2.py. Откат: UZEL_OLLAMA_MODEL=qwen2.5:7b-instruct-q4_K_M.
    ollama_model: str = "qwen3:8b"
    ollama_timeout_s: float = 600.0  # 7B Q4 на GTX 1660 Ti считает долго
    ollama_keep_alive: str = "5m"
    ollama_num_ctx: int = 8192

    # Предел расшифровки на один запрос модели. Настройка, а не константа
    # модуля: зависит от num_ctx локальной модели.
    # 12000 оказалось много: ~5000 токенов куска + ответ не влезал в потолок
    # ответа, JSON обрывался (P10, 24.09.2026). 6000 символов ≈ 2700 токенов.
    nlu_max_chars: int = 6000
    # Кто носит микрофон — подставляется в промпт разбора («Владелец микрофона: …»).
    # Именительный падеж: «инженер ПТО», «врач», «менеджер по продажам».
    user_role: str = "пользователь"

    # OpenAI-совместимый агент-шлюз на локальном порту (см. llm.GatewayBackend).
    # Считается облаком: локален только порт.
    gateway_url: str = "http://127.0.0.1:8642"
    gateway_api_key: str | None = None
    gateway_model: str = "agent"

    groq_llm_url: str = "https://api.groq.com/openai/v1"
    groq_llm_model: str = "openai/gpt-oss-120b"

    # --- приватность (FR-070, FR-071) ---
    # Формат: "ЧЧ:ММ-ЧЧ:ММ" или "ЧЧ:ММ-ЧЧ:ММ/пн,вт,ср"
    quiet_windows: tuple[str, ...] = ()

    # --- выгрузка (FR-050…FR-052) ---
    vault_dir: Path = Path.home() / "Documents" / "Obsidian"
    # Папка человеческих отчётов за день (по файлу на дату). None — не писать.
    report_dir: Path | None = None
    # Новая структура выгрузки (ТЗ 2026-08-24, пункт 4.2): заметки дней и,
    # отдельно под TTL, дословные расшифровки — только по явному флагу.
    vault_days_subdir: str = r"Проекты\Узел\Дни"
    vault_raw_subdir: str = r"Проекты\Узел\_Сырое"
    export_transcript: bool = False  # дословная расшифровка в vault — только по флагу
    raw_note_ttl_days: int = 30
    # НЕ корневой «Задачи — незавершённые.md»: конвейер не имеет права писать
    # в файл задач владельца (пункт 4.3 ТЗ), у УЗЕЛ свой входящий список.
    tasks_note: str = r"Проекты\Узел\Входящее — задачи.md"
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    # --- хранение (FR-040…FR-043) ---
    audio_ttl_days: int = 7
    db_name: str = "uzel.sqlite"

    # --- фильтр качества (ТЗ 2026-08-24, пакет 2) ---
    # Первые две недели фильтр ПОМЕЧАЕТ, но пропускает: пороги ниже — гипотезы,
    # откалибровать их можно только на реальном материале (uzel quality-report).
    quality_enforce: bool = False
    quality_min_chars: int = 12
    quality_min_words: int = 3
    quality_lang_min_chars: int = 100
    quality_sparse_min_speech_s: float = 2.0
    quality_min_chars_per_second: float = 3.0
    quality_max_chars_per_second: float = 30.0
    quality_loop_ngram_repeats: int = 4
    quality_loop_min_words: int = 10
    quality_unique_word_ratio: float = 0.35
    quality_duplicate_gap_s: float = 30.0
    quality_conversation_min_utterances: int = 2
    quality_conversation_min_chars: int = 400
    quality_conversation_min_speech_s: float = 60.0
    quality_conversation_noise_ratio: float = 0.7
    quality_conversation_noise_max_accepted: int = 10

    # Смысловой поиск. Кэш модели эмбеддингов (255 МБ) — постоянная папка: по
    # умолчанию fastembed кладёт его во временную, и чистка диска качает заново.
    vector_enabled: bool = True
    qdrant_collection: str = "uzel_utterances"
    fastembed_cache: Path | None = Path.home() / "models-fastembed"

    # --- воркер конвейера (ТЗ 2026-08-24, пакет 3) ---
    worker_batch_size: int = 8  # пачка реплик на один такт ночного батча
    worker_time_limit_s: float = 3600.0  # предел одного прогона батча
    worker_poll_interval_s: float = 5.0  # пауза дешёвого прохода между опросами очереди
    worker_claim_limit: int = 32  # сколько фрагментов берём за один опрос

    # VRAM 6 ГБ не держит whisper и LLM одновременно: ночной батч работает
    # в два такта — вся расшифровка, выгрузка whisper, весь разбор.
    worker_unload_between_phases: bool = True

    # --- совместимость с приложением Omi (P8) ---
    omi_enabled: bool = True  # False → раздел /omi не монтируется вовсе
    omi_token: str | None = (
        None  # задан → без него 401 (нужен, если слушаем не только localhost)
    )
    omi_session_ttl_s: float = 6 * 3600.0  # сколько помнить привязку session_id → время

    @property
    def queue_dir(self) -> Path:
        return self.data_dir / "queue"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def vault_days_dir(self) -> Path:
        return self.vault_dir / self.vault_days_subdir

    @property
    def vault_raw_dir(self) -> Path:
        return self.vault_dir / self.vault_raw_subdir

    @property
    def tasks_note_path(self) -> Path:
        return self.vault_dir / self.tasks_note

    @property
    def worker_stop_file(self) -> Path:
        """Стоп-файл ночного батча: появился — доработали такт и вышли."""
        return self.data_dir / "worker.stop"


@lru_cache
def get_settings() -> Settings:
    if LEGACY_ENV_FILE.exists():
        log.warning(
            "%s лежит в зоне синка Google Drive — перенеси его в %s",
            LEGACY_ENV_FILE,
            USER_ENV_FILE,
        )
    return Settings()
