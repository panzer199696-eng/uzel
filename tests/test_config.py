"""Настройки ядра."""

from __future__ import annotations

from pathlib import Path

from uzel.config import PROJECT_ROOT, Settings


def умолчания(**поля) -> Settings:
    """Настройки в отрыве от .env конкретной машины.

    Эти тесты проверяют умолчания кода, а установка по инструкции создаёт
    свой .env — и он их законно перебивает. 20.09.2026 строка
    UZEL_LOCAL_MODEL_DIR из .env.example уронила проверку умолчания
    в чистой установке, хотя ничего сломано не было.
    """
    return Settings(_env_file=None, **поля)


def test_defaults_match_the_spec(tmp_path):
    settings = умолчания(data_dir=tmp_path)

    assert settings.sample_rate == 16000
    assert settings.min_speech_ms == 400  # FR-011
    assert settings.utterance_gap_ms == 1500  # FR-012
    assert settings.conversation_gap_s == 300  # FR-013
    assert settings.stt_backends[0] == "local"  # FR-021
    assert settings.audio_ttl_days == 7  # FR-042


def test_cloud_is_opt_in_by_default(tmp_path):
    """Облако подключается только явной настройкой, молчаливого дефолта нет."""
    settings = умолчания(data_dir=tmp_path)

    assert settings.stt_backends == ("local",)
    assert settings.local_compute_type == "int8_float16"
    # Локальной моделью разбора стала Ollama; агент-шлюз — облако (ТЗ P5).
    assert settings.llm_backends == ("ollama",)
    assert settings.local_failure_streak == 3


def test_antihallucination_thresholds_defaults(tmp_path):
    settings = умолчания(data_dir=tmp_path)

    assert settings.local_beam_size == 5
    assert settings.local_no_speech_threshold == 0.6
    assert settings.local_logprob_threshold == -1.0
    assert settings.local_compression_threshold == 2.4


def test_local_model_dir_default_points_home(tmp_path):
    settings = умолчания(data_dir=tmp_path)

    assert settings.local_model_dir == Path.home() / "models-whisper"
    assert settings.local_model == "large-v3"


def test_listens_on_loopback_only(tmp_path):
    """NFR-005: наружу ядро не слушает."""
    assert умолчания(data_dir=tmp_path).host == "127.0.0.1"


def test_queue_dir_lives_under_data_dir(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")

    assert settings.queue_dir == tmp_path / "data" / "queue"


def test_environment_overrides_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("UZEL_LOCAL_MODEL", "medium")
    monkeypatch.setenv("UZEL_VAD_THRESHOLD", "0.7")

    settings = Settings(data_dir=tmp_path)

    assert settings.local_model == "medium"
    assert settings.vad_threshold == 0.7


def test_vad_model_path_points_inside_package(tmp_path):
    path = умолчания(data_dir=tmp_path).vad_model_path

    assert isinstance(path, Path)
    assert path.name == "silero_vad.onnx"


def test_default_data_dir_stays_outside_project():
    """Пункт 4.4 ТЗ: Desktop в бэкапе Google Drive — данные ему не доверяем."""
    assert not умолчания().data_dir.is_relative_to(PROJECT_ROOT)


def test_env_file_is_read_outside_project_and_wins(tmp_path):
    """P11.2, находка 14: .env с ключами не в зоне синка Drive, и он главный."""
    from uzel.config import LEGACY_ENV_FILE, USER_ENV_FILE

    assert not USER_ENV_FILE.is_relative_to(PROJECT_ROOT)
    files = Settings.model_config["env_file"]
    assert files[-1] == USER_ENV_FILE  # последний в кортеже побеждает
    assert LEGACY_ENV_FILE in files  # старый путь — для совместимости

    legacy = tmp_path / "old.env"
    legacy.write_text(
        "UZEL_LOCAL_MODEL=legacy\nUZEL_LOG_LEVEL=DEBUG\n", encoding="utf-8"
    )
    user = tmp_path / "user.env"
    user.write_text("UZEL_LOCAL_MODEL=user\n", encoding="utf-8")

    settings = Settings(_env_file=(legacy, user))

    assert settings.local_model == "user"
    assert settings.log_level == "DEBUG"  # что есть только в старом — читается


def test_vault_day_and_raw_dirs_composed_from_vault_dir(tmp_path):
    settings = умолчания(vault_dir=tmp_path / "vault")

    assert settings.vault_days_dir == (tmp_path / "vault" / "Проекты" / "Узел" / "Дни")
    assert settings.vault_raw_dir == (
        tmp_path / "vault" / "Проекты" / "Узел" / "_Сырое"
    )
    assert settings.export_transcript is False
    assert settings.raw_note_ttl_days == 30
