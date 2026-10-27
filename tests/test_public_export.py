"""Публичная выгрузка: что уходит наружу, что ловит сканер, как копируется.

Всё на временном git-репозитории, без сети. Образцы «секретов» и запретных
слов собираются из кусков во время теста: иначе сканер сработал бы на сам
этот файл при настоящей выгрузке.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "public_export.py"

spec = importlib.util.spec_from_file_location("uzel_public_export", TOOL_PATH)
pe = importlib.util.module_from_spec(spec)
sys.modules["uzel_public_export"] = pe
spec.loader.exec_module(pe)

FORBIDDEN = "Запрет" + "слово"  # вымышленное слово для своего списка


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Тест", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def make_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "src"
    repo.mkdir()
    git(repo, "init", "-q")
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "начало")
    return repo


def kinds(findings) -> list[str]:
    return [f.kind for f in findings]


# --- что исключается ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "ops/runner.py",
        "ops/public-denylist.txt",
        "specs/spec.md",
        "CLAUDE.md",
        "AGENTS.md",
        ".codex/config.toml",
        "uzel.egg-info/PKG-INFO",
        "core/uzel.egg-info/SOURCES.txt",
        "tools/public_export_denylist.txt",
    ],
)
def test_service_paths_are_excluded(path):
    assert pe.is_excluded(path)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "core/uzel/cli.py",
        "docs/CLAUDE-notes.md",  # совпадение имени только в корне
        "tools/public_export.py",
        "tests/ops_like.py",
        "hardware/specs.txt",
    ],
)
def test_regular_paths_are_kept(path):
    assert not pe.is_excluded(path)


# --- сканер ------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample, kind",
    [
        ("key = 'sk-" + "a1B2" * 8 + "'", "ключ sk-"),
        ("token: gh" + "p_" + "A" * 36, "токен GitHub"),
        ("github" + "_pat_" + "11ABC" * 8, "токен GitHub"),
        ("AI" + "za" + "B" * 35, "ключ Google"),
        ("xo" + "xb-" + "1234567890-abcdef", "токен Slack"),
        ("hf" + "_" + "c" * 34, "токен Hugging Face"),
        ("-----BEGIN RSA " + "PRIVATE KEY-----", "закрытый ключ"),
        ("store" + "Password = \"x\"", "пароль подписи"),
        ("key" + "Password=secret", "пароль подписи"),
        ("bot " + "123456789:" + "A" * 35, "токен Telegram"),
        ("пишите на " + "ivan" + "@" + "corp.ru", "e-mail"),
        ("путь " + "C:" + "\\" + "Users\\ivan\\data", "домашний путь"),
        ("путь " + "c:" + "/" + "users/ivan", "домашний путь"),
        ("адрес " + "12:34:56" + ":78:9A:BC", "MAC-адрес"),
    ],
)
def test_scanner_catches(sample, kind):
    findings = pe.scan_text("f.txt", "строка\n" + sample + "\n", [])
    assert kind in kinds(findings)
    assert findings[0].line == 2


@pytest.mark.parametrize(
    "sample",
    [
        "автор " + "123+ivan" + "@users.noreply.github.com",
        "пример " + "user" + "@example.com",
        "адрес " + "AA:BB:CC:DD:EE:FF",
        "адрес " + "00:00:00:00:00:00",
        "время 10:42, счёт 1:0",
        "storePassword — описание поля, без значения",
        "UZEL_DATA_DIR=D:\\uzel-data",
        "this@RecorderService.connected = connected",  # метка Kotlin
        "super@Outer.value",
    ],
)
def test_scanner_lets_harmless_through(sample):
    assert pe.scan_text("f.txt", sample, []) == []


def test_denylist_is_case_insensitive_and_reports_line():
    text = "чисто\nтут " + FORBIDDEN.upper() + " внутри\n"
    findings = pe.scan_text("doc.md", text, [FORBIDDEN])
    assert [(f.path, f.line) for f in findings] == [("doc.md", 2)]
    assert "запретное слово" in findings[0].kind


def test_report_never_prints_secret_itself():
    secret = "gh" + "p_" + "Z" * 36
    finding = pe.scan_text("a.py", "x = '" + secret + "'", [])[0]
    assert secret not in pe.format_finding(finding)
    assert pe.format_finding(finding).startswith("a.py:1: ")


def test_load_denylist_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text(
        "# комментарий\n\n  " + FORBIDDEN + "  \nВторое\n", encoding="utf-8-sig"
    )
    assert pe.load_denylist(path) == [FORBIDDEN, "Второе"]


# --- выгрузка целиком ---------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    return make_repo(
        tmp_path,
        {
            "README.md": "# проект\n",
            "core/app.py": "print('ok')\n",
            "ops/секрет.md": "служебное " + FORBIDDEN + "\n",
            "ops/public-denylist.txt": FORBIDDEN + "\n",
            "CLAUDE.md": "инструкции\n",
            "specs/spec.md": "ТЗ\n",
        },
    )


def test_export_copies_head_without_service_paths(repo, tmp_path):
    out = tmp_path / "out"
    (repo / "README.md").write_text("несохранённая правка\n", encoding="utf-8")

    code = pe.main(["--repo", str(repo), "--out", str(out)])

    assert code == 0
    copied = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert copied == ["README.md", "core/app.py"]
    # берётся HEAD, а не рабочее дерево
    assert (out / "README.md").read_text(encoding="utf-8") == "# проект\n"


def test_export_cleans_target_but_keeps_its_git(repo, tmp_path):
    out = tmp_path / "out"
    (out / ".git").mkdir(parents=True)
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (out / "старый.txt").write_text("удалить", encoding="utf-8")
    (out / "старая-папка").mkdir()
    (out / "старая-папка" / "f").write_text("x", encoding="utf-8")

    assert pe.main(["--repo", str(repo), "--out", str(out)]) == 0

    assert (out / ".git" / "HEAD").exists()
    assert not (out / "старый.txt").exists()
    assert not (out / "старая-папка").exists()
    assert (out / "README.md").exists()


def test_finding_blocks_export_with_code_1(repo, tmp_path, capsys):
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("ок\nтут " + FORBIDDEN + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "утечка")
    out = tmp_path / "out"

    code = pe.main(["--repo", str(repo), "--out", str(out)])

    assert code == 1
    assert "docs/a.md:2: " in capsys.readouterr().out
    assert not out.exists() or not any(out.iterdir())  # ничего не скопировано


def test_report_only_does_not_copy(repo, tmp_path, capsys):
    out = tmp_path / "out"

    code = pe.main(["--repo", str(repo), "--out", str(out), "--report-only"])

    assert code == 0
    assert not out.exists()
    assert "0 срабатываний" in capsys.readouterr().out


def test_missing_explicit_denylist_is_an_error(repo, tmp_path):
    code = pe.main(
        ["--repo", str(repo), "--report-only", "--denylist", str(tmp_path / "нет.txt")]
    )
    assert code == 2


def test_commit_in_target_repo(repo, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    git(out, "init", "-q")
    sha = git(repo, "rev-parse", "--short", "HEAD").strip()

    code = pe.main(
        ["--repo", str(repo), "--out", str(out), "--commit"],
        git_identity=("Тест", "test@example.com"),
    )

    assert code == 0
    assert git(out, "log", "-1", "--format=%s").strip() == f"Синхронизация с {sha}"
    # повтор без изменений — коммита нет и это не ошибка
    assert (
        pe.main(
            ["--repo", str(repo), "--out", str(out), "--commit"],
            git_identity=("Тест", "test@example.com"),
        )
        == 0
    )
    assert git(out, "rev-list", "--count", "HEAD").strip() == "1"
