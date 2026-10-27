"""Приёмка возвращения (ТЗ P13): окружение, эталон, вердикт по A1.

Сеть и видеокарта запрещены: HTTP, дисковая статистика и find_spec подменяются
параметрами, STT не запускается (готовая гипотеза), VAD и роутер — monkeypatch.
"""

from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from uzel import cli
from uzel.audio import write_wav
from uzel.homecoming import (
    Check,
    NoSpeechFound,
    check_environment,
    find_reference,
    render_verdict,
    transcribe_for_wer,
)
from uzel.wer import compute_wer

_GB = 1024**3
_DiskUsage = namedtuple("_DiskUsage", "total used free")

# Бытовой эталон без доменных терминов, 35 слов.
REF_PLAIN = (
    "Смотри вчера мы долго ходили по площадке и обсуждали много всего разного "
    "включая погоду обеды и прочие бытовые вопросы потом позвонили Петру "
    "насчёт заявки на август он обещал всё проверить и перезвонить ближе "
    "к вечеру"
)
# Совсем другой текст — WER около единицы.
REF_ALIEN = (
    "море летом тёплое чайки кричат громко волны бьют в песок и солёный ветер "
    "тянет к себе одеяло никакой работы сегодня нет только отдых и сон"
)
# Эталон с одним доменным термином: потерянный термин при низком общем WER.
REF_DOMAIN = (
    "составлен АОСР по устройству основания и дальше идёт длинный обычный "
    "текст про погоду обеды дорогу машину и прочие бытовые дела чтобы общий "
    "процент ошибок остался низким"
)
HYP_DOMAIN_LOST_TERM = REF_DOMAIN.replace("АОСР", "ОСАГО")


def run(args, settings) -> int:
    parsed = cli.build_parser().parse_args(args)
    return parsed.func(parsed, settings)


def _disk(free_gb: float):
    return _DiskUsage(total=1000 * _GB, used=0, free=int(free_gb * _GB))


def _fake_http(tags=None, health=None):
    """http_get-заглушка: отвечает только на /api/tags и /health."""

    def get(url, timeout):
        if url.endswith("/api/tags"):
            return tags
        if url.endswith("/health"):
            return health
        return None

    return get


def _env_settings(settings, **update) -> object:
    """Настройки, не зависящие от машины: VAD и папка весов — во временной."""
    base = {
        "vad_model_path": None,  # заполняется ниже
        "local_model_dir": None,
    }
    base.update(update)
    if base["vad_model_path"] is None:
        base["vad_model_path"] = Path(update.get("_vad", "нет.onnx"))
    base.pop("_vad", None)
    return settings.model_copy(update=base)


def _by_name(checks) -> dict:
    return {check.name: check for check in checks}


# ---------- check_environment ----------


def test_env_vad_present(tmp_path, settings):
    (tmp_path / "vad.onnx").write_bytes(b"")
    s = _env_settings(settings, vad_model_path=tmp_path / "vad.onnx")
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    assert checks["Модель VAD"].ok is True
    assert checks["Модель VAD"].blocking is False


def test_env_vad_missing_blocks(settings):
    s = _env_settings(settings, vad_model_path=Path("нет-точно.onnx"))
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    vad = checks["Модель VAD"]
    assert vad.ok is False
    assert vad.blocking is True


def test_env_faster_whisper_missing_blocks(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(
            s,
            http_get=_fake_http(),
            disk_usage=lambda p: _disk(100),
            find_spec=lambda name: None,
        )
    )
    whisper = checks["faster-whisper"]
    assert whisper.ok is False
    assert whisper.blocking is True
    assert "pip install -r requirements-cuda.txt" in whisper.detail


def test_env_faster_whisper_present_ok(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(
            s,
            http_get=_fake_http(),
            disk_usage=lambda p: _disk(100),
            find_spec=lambda name: object(),
        )
    )
    assert checks["faster-whisper"].ok is True


def test_env_faster_whisper_skipped_without_local_backend(settings):
    s = _env_settings(settings, stt_backends=("groq",))
    checks = check_environment(
        s,
        http_get=_fake_http(),
        disk_usage=lambda p: _disk(100),
        find_spec=lambda name: None,  # даже если модуля нет — проверять нельзя
    )
    assert "faster-whisper" not in _by_name(checks)


def test_env_whisper_weights_missing_is_not_blocking(tmp_path, settings):
    s = _env_settings(settings, local_model_dir=tmp_path / "weights")
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    weights = checks["Whisper large-v3"]
    assert weights.ok is False
    assert weights.blocking is False
    assert "скачаются" in weights.detail


def test_env_whisper_weights_present(tmp_path, settings):
    weights_dir = tmp_path / "weights" / "large-v3"
    weights_dir.mkdir(parents=True)
    (weights_dir / "model.bin").write_bytes(b"")
    s = _env_settings(settings, local_model_dir=tmp_path / "weights")
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    assert checks["Whisper large-v3"].ok is True


@pytest.mark.parametrize(
    "free_gb,ok,blocking",
    [
        (100, True, False),
        (20, True, False),
        (10, False, False),
        (2, False, False),
        (1.5, False, True),
    ],
)
def test_env_disk_thresholds(settings, free_gb, ok, blocking):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(free_gb))
    )
    disk = checks["Место на диске"]
    assert disk.ok is ok
    assert disk.blocking is blocking


def test_env_disk_checks_nearest_existing_parent(tmp_path, settings):
    seen = []

    def disk_usage(path):
        seen.append(Path(path))
        return _disk(100)

    s = _env_settings(settings, data_dir=tmp_path / "a" / "b")  # не существует
    checks = check_environment(s, http_get=_fake_http(), disk_usage=disk_usage)
    assert checks  # не упало
    assert seen and all(p.exists() for p in seen)
    assert seen[0] == tmp_path


def test_env_disk_free_gb_in_detail(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(10))
    )
    assert "10.0 ГБ" in checks["Место на диске"].detail


def test_env_ollama_with_model(settings):
    s = _env_settings(settings)
    tags = {"models": [{"name": s.ollama_model}]}
    checks = _by_name(
        check_environment(
            s, http_get=_fake_http(tags=tags), disk_usage=lambda p: _disk(100)
        )
    )
    ollama = checks["Ollama"]
    assert ollama.ok is True
    assert ollama.blocking is False
    assert s.ollama_model in ollama.detail


def test_env_ollama_without_model(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(
            s, http_get=_fake_http(tags={"models": []}), disk_usage=lambda p: _disk(100)
        )
    )
    ollama = checks["Ollama"]
    assert ollama.ok is False
    assert ollama.blocking is False  # для A1 не нужна
    assert "ollama pull" in ollama.detail


def test_env_ollama_silent(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    ollama = checks["Ollama"]
    assert ollama.ok is False
    assert ollama.blocking is False
    assert "ollama serve" in ollama.detail


def test_env_core_ok(settings):
    s = _env_settings(settings)
    health = {"status": "ok", "version": "1.2.3"}
    checks = _by_name(
        check_environment(
            s, http_get=_fake_http(health=health), disk_usage=lambda p: _disk(100)
        )
    )
    core = checks["Ядро"]
    assert core.ok is True
    assert "1.2.3" in core.detail


def test_env_core_silent_is_not_blocking(settings):
    s = _env_settings(settings)
    checks = _by_name(
        check_environment(s, http_get=_fake_http(), disk_usage=lambda p: _disk(100))
    )
    core = checks["Ядро"]
    assert core.ok is False
    assert core.blocking is False
    assert "uzel.cmd app" in core.detail


def test_env_core_0000_maps_to_loopback(settings):
    urls = []

    def http_get(url, timeout):
        urls.append(url)

    s = _env_settings(settings, host="0.0.0.0")
    check_environment(s, http_get=http_get, disk_usage=lambda p: _disk(100))
    assert "http://127.0.0.1:8770/health" in urls
    assert not any("0.0.0.0" in url for url in urls)


# ---------- find_reference ----------


def test_ref_nothing_found(tmp_path):
    state = find_reference(tmp_path, "эталон-01")
    assert state.audio_ok is False
    assert state.text_ok is False
    assert state.audio == tmp_path / "эталон-01.wav"
    assert state.text == tmp_path / "эталон-01.txt"


def test_ref_m4a_gives_ffmpeg_hint(tmp_path):
    (tmp_path / "эталон-01.m4a").write_bytes(b"")
    state = find_reference(tmp_path, "эталон-01")
    assert state.audio_ok is False
    assert len(state.problems) == 1
    problem = state.problems[0]
    assert "ffmpeg" in problem
    assert "эталон-01.m4a" in problem
    assert "-ac 1 -ar 16000" in problem


def test_ref_wav_wrong_format_is_problem(tmp_path):
    sf.write(
        str(tmp_path / "эталон-01.wav"),
        np.zeros((1600, 2), dtype="float32"),
        44100,
    )
    state = find_reference(tmp_path, "эталон-01")
    assert state.audio_ok is False
    assert any("ffmpeg" in p and "16000" in p for p in state.problems)


def test_ref_broken_wav_header(tmp_path):
    (tmp_path / "эталон-01.wav").write_text("это не wav", encoding="utf-8")
    state = find_reference(tmp_path, "эталон-01")
    assert state.audio_ok is False
    assert any("битый" in p for p in state.problems)


def test_ref_short_text_is_problem(tmp_path):
    write_wav(tmp_path / "эталон-01.wav", np.zeros(16000, dtype=np.float32), 16000)
    (tmp_path / "эталон-01.txt").write_text("пара слов", encoding="utf-8")
    state = find_reference(tmp_path, "эталон-01")
    assert state.text_ok is False
    assert any("подозрительно короткий" in p for p in state.problems)


def test_ref_good(tmp_path):
    write_wav(tmp_path / "эталон-01.wav", np.zeros(16000, dtype=np.float32), 16000)
    (tmp_path / "эталон-01.txt").write_text(REF_PLAIN, encoding="utf-8")
    state = find_reference(tmp_path, "эталон-01")
    assert state.audio_ok is True
    assert state.text_ok is True
    assert state.problems == []


# ---------- render_verdict ----------


def test_verdict_passed_points_to_first_battle_day():
    text = render_verdict(compute_wer(REF_PLAIN, REF_PLAIN), threshold=0.15)
    assert "A1: ПРОЙДЕН" in text
    assert "первый боевой день" in text


def test_verdict_failed_points_to_error_table():
    text = render_verdict(compute_wer(REF_PLAIN, REF_ALIEN), threshold=0.15)
    assert "A1: НЕ ПРОЙДЕН" in text
    assert "GigaAM" in text


def test_verdict_rtf_mentions_nfr003():
    text = render_verdict(compute_wer(REF_PLAIN, REF_PLAIN), threshold=0.15, rtf=0.4)
    assert "NFR-003" in text
    assert "0.40" in text


def test_verdict_domain_lost_warning():
    report = compute_wer(REF_DOMAIN, HYP_DOMAIN_LOST_TERM)
    assert report.passed(0.15)  # общий порог пройден
    text = render_verdict(report, threshold=0.15)
    assert "термины теряются" in text


def test_verdict_domain_wer_printed_as_percent():
    # Регресс 25.09: доля печаталась форматом «.0f %» — любой замер давал «0 %».
    report = compute_wer(REF_DOMAIN, HYP_DOMAIN_LOST_TERM)
    assert report.domain_errors > 0
    text = render_verdict(report, threshold=0.15)
    assert f"{report.domain_wer:.1%}" in text
    assert f"({report.domain_errors} из {report.domain_total} терминов)" in text


def test_verdict_no_domain_terms_is_incomplete():
    text = render_verdict(compute_wer(REF_PLAIN, REF_PLAIN), threshold=0.15)
    assert "вердикт неполный" in text
    assert "текст-для-начитки.md" in text


# ---------- transcribe_for_wer (всё подменено) ----------


class _FakeRouter:
    def __init__(self, flags: list) -> None:
        self._flags = flags

    def transcribe(self, samples, sample_rate, *, language, allow_cloud):
        self._flags.append(allow_cloud)
        return SimpleNamespace(text="привет как дела", backend="local")


@pytest.mark.parametrize(
    "backend,expected",
    [(None, False), ("local", False), ("groq", True)],
)
def test_transcribe_cloud_allowed_only_for_groq(
    tmp_path, settings, monkeypatch, backend, expected
):
    wav = tmp_path / "аудио.wav"
    write_wav(wav, np.zeros(16000, dtype=np.float32), 16000)
    flags: list = []
    monkeypatch.setattr("uzel.homecoming.build_router", lambda s: _FakeRouter(flags))

    result = transcribe_for_wer(wav, settings, backend=backend, no_vad=True)

    assert flags == [expected]
    assert result.hypothesis == "привет как дела"
    assert result.backend == "local"
    assert result.audio_seconds == pytest.approx(1.0)
    assert result.speech_seconds == pytest.approx(1.0)


def test_transcribe_no_speech_raises(tmp_path, settings, monkeypatch):
    wav = tmp_path / "аудио.wav"
    write_wav(wav, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setattr("uzel.homecoming.SileroVAD", lambda path, sr: object())
    monkeypatch.setattr("uzel.homecoming.detect_speech", lambda *a, **kw: [])

    with pytest.raises(NoSpeechFound):
        transcribe_for_wer(wav, settings)


# ---------- команда CLI ----------


def _make_reference(wer_dir: Path, text: str = REF_PLAIN) -> None:
    write_wav(wer_dir / "эталон-01.wav", np.zeros(16000, dtype=np.float32), 16000)
    (wer_dir / "эталон-01.txt").write_text(text, encoding="utf-8")


def test_cli_no_reference_returns_two(tmp_path, settings, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()

    code = run(["homecoming", "--skip-env", "--wer-dir", str(wer_dir)], settings)

    out = capsys.readouterr().out
    assert code == 2
    assert str(wer_dir / "эталон-01.wav") in out
    assert str(wer_dir / "эталон-01.txt") in out
    assert "текст-для-начитки" in out
    assert "запусти эту же команду ещё раз" in out


def test_cli_hypothesis_pass_writes_report(tmp_path, settings, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    hypothesis = tmp_path / "гипотеза.txt"
    hypothesis.write_text(REF_PLAIN, encoding="utf-8")
    out_dir = tmp_path / "отчёты"

    code = run(
        [
            "homecoming",
            "--skip-env",
            "--wer-dir",
            str(wer_dir),
            "--hypothesis",
            str(hypothesis),
            "--out-dir",
            str(out_dir),
        ],
        settings,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "A1: ПРОЙДЕН" in out
    reports = list(out_dir.glob("*-приёмка-готовый-текст.md"))
    assert len(reports) == 1
    assert "A1: ПРОЙДЕН" in reports[0].read_text(encoding="utf-8")
    # гипотеза готовая — файла распознанного текста не будет
    assert list(out_dir.glob("*распознано*")) == []


def test_cli_bad_hypothesis_fails_gate(tmp_path, settings, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    hypothesis = tmp_path / "гипотеза.txt"
    hypothesis.write_text(REF_ALIEN, encoding="utf-8")

    code = run(
        [
            "homecoming",
            "--skip-env",
            "--wer-dir",
            str(wer_dir),
            "--hypothesis",
            str(hypothesis),
            "--out-dir",
            str(tmp_path / "отчёты"),
        ],
        settings,
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "A1: НЕ ПРОЙДЕН" in out


def test_cli_lost_domain_terms_warn(tmp_path, settings, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir, REF_DOMAIN)
    hypothesis = tmp_path / "гипотеза.txt"
    hypothesis.write_text(HYP_DOMAIN_LOST_TERM, encoding="utf-8")

    code = run(
        [
            "homecoming",
            "--skip-env",
            "--wer-dir",
            str(wer_dir),
            "--hypothesis",
            str(hypothesis),
        ],
        settings,
    )

    out = capsys.readouterr().out
    assert code == 0  # общий порог пройден
    assert "термины теряются" in out


def test_cli_without_domain_terms_verdict_incomplete(tmp_path, settings, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir, REF_PLAIN)
    hypothesis = tmp_path / "гипотеза.txt"
    hypothesis.write_text(REF_PLAIN, encoding="utf-8")

    run(
        [
            "homecoming",
            "--skip-env",
            "--wer-dir",
            str(wer_dir),
            "--hypothesis",
            str(hypothesis),
        ],
        settings,
    )

    assert "вердикт неполный" in capsys.readouterr().out


def test_cli_blocking_env_returns_three(tmp_path, settings, monkeypatch, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    from uzel import homecoming

    monkeypatch.setattr(
        homecoming,
        "check_environment",
        lambda s, **kw: [Check("Модель VAD", False, "нет файла", True)],
    )

    code = run(["homecoming", "--wer-dir", str(wer_dir)], settings)

    out = capsys.readouterr().out
    assert code == 3
    assert "Модель VAD" in out


def test_cli_blocking_env_ignored_with_hypothesis(
    tmp_path, settings, monkeypatch, capsys
):
    """Готовый текст не нуждается в окружении — блокировки не мешают."""
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    hypothesis = tmp_path / "гипотеза.txt"
    hypothesis.write_text(REF_PLAIN, encoding="utf-8")
    from uzel import homecoming

    monkeypatch.setattr(
        homecoming,
        "check_environment",
        lambda s, **kw: [Check("Модель VAD", False, "нет файла", True)],
    )

    code = run(
        ["homecoming", "--wer-dir", str(wer_dir), "--hypothesis", str(hypothesis)],
        settings,
    )

    assert code == 0
    assert "A1: ПРОЙДЕН" in capsys.readouterr().out


def test_cli_env_marks_are_printed(tmp_path, settings, monkeypatch, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    from uzel import homecoming

    monkeypatch.setattr(
        homecoming,
        "check_environment",
        lambda s, **kw: [
            Check("Модель VAD", True, "на месте", False),
            Check("Ollama", False, "молчит", False),
            Check("Место на диске", False, "нет места", True),
        ],
    )

    code = run(["homecoming", "--wer-dir", str(wer_dir)], settings)

    out = capsys.readouterr().out
    assert code == 3
    assert "✅ Модель VAD — на месте" in out
    assert "⚠️ Ollama — молчит" in out
    assert "❌ Место на диске — нет места" in out


def test_cli_no_speech_returns_one(tmp_path, settings, monkeypatch, capsys):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    from uzel import homecoming

    def boom(*a, **kw):
        raise NoSpeechFound("VAD не нашёл речи")

    monkeypatch.setattr(homecoming, "transcribe_for_wer", boom)

    code = run(["homecoming", "--skip-env", "--wer-dir", str(wer_dir)], settings)

    assert code == 1
    assert "VAD не нашёл речи" in capsys.readouterr().err


def test_cli_stt_failure_returns_three_without_traceback(
    tmp_path, settings, monkeypatch, capsys
):
    wer_dir = tmp_path / "wer"
    wer_dir.mkdir()
    _make_reference(wer_dir)
    from uzel import homecoming

    def boom(*a, **kw):
        raise RuntimeError("CUDA недоступна")

    monkeypatch.setattr(homecoming, "transcribe_for_wer", boom)

    code = run(["homecoming", "--skip-env", "--wer-dir", str(wer_dir)], settings)

    captured = capsys.readouterr()
    assert code == 3
    assert "CUDA недоступна" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_parser_accepts_russian_name_and_alias():
    for argv in (["приёмка-возвращения"], ["homecoming"]):
        args = cli.build_parser().parse_args(argv)
        assert args.func is cli.cmd_homecoming


def test_parser_defaults_point_to_reference_dir():
    args = cli.build_parser().parse_args(["homecoming"])
    assert args.wer_dir == cli.PROJECT_ROOT / "tests" / "wer"
    assert args.name == "эталон-01"
    assert args.threshold == 0.15
