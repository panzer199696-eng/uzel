"""Публичная выгрузка репозитория: HEAD без служебных папок, после проверки.

Берёт файлы, отслеживаемые git на HEAD, отбрасывает служебное (ops/, specs/,
инструкции агентов), прогоняет сканер секретов и личных данных и только при
нуле срабатываний копирует в целевую папку. Её ``.git`` не трогается, так что
целевая папка может быть клоном публичного репозитория.

    python tools/public_export.py --out ..\\uzel-public --report-only
    python tools/public_export.py --out ..\\uzel-public --commit

Коды выхода: 0 — чисто (и скопировано), 1 — есть срабатывания, 2 — ошибка
запуска. Список запретных слов лежит вне выгрузки (``ops/public-denylist.txt``)
и сам наружу не уходит.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_REPO = Path(__file__).resolve().parents[1]
DEFAULT_DENYLIST = "ops/public-denylist.txt"

# Служебное: остаётся только в рабочем репозитории.
EXCLUDED_DIRS = ("ops", "specs", ".codex")
EXCLUDED_ROOT_FILES = ("CLAUDE.md", "AGENTS.md")
_DENYLIST_FILE = re.compile(r"^tools/public_export_denylist[^/]*$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str


def is_excluded(path: str) -> bool:
    """True — файл не попадает в публичную выгрузку. Путь — через ``/``."""
    parts = PurePosixPath(path).parts
    if not parts:
        return True
    if parts[0] in EXCLUDED_DIRS:
        return True
    if len(parts) == 1 and parts[0] in EXCLUDED_ROOT_FILES:
        return True
    if any(part.endswith(".egg-info") for part in parts[:-1]):
        return True
    return bool(_DENYLIST_FILE.match(path))


# --- сканер -------------------------------------------------------------------

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ключ sk-", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("токен GitHub", re.compile(r"\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("ключ Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("токен Slack", re.compile(r"\bxox[bp]-[A-Za-z0-9-]{10,}")),
    ("токен Hugging Face", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("закрытый ключ", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    # значение пароля подписи APK в build.gradle (поля store/key Password)
    ("пароль подписи", re.compile(r"\b(?:store|key)Password\s*[=:(]\s*\S")),
    ("токен Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
# Заведомо примерные адреса (RFC 2606) и обезличенные адреса GitHub.
_EMAIL_ALLOWED_DOMAINS = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net|ru)|users\.noreply\.github\.com)$"
    r"|\.(?:example|invalid|test|localhost)$",
    re.IGNORECASE,
)
# Адреса авторов вендорного кода в его же заголовках — публичны изначально.
EMAIL_ALLOWED_ADDRESSES: frozenset[str] = frozenset()
# Метки Kotlin выглядят как адрес: ``this@RecorderService.connected``.
_KOTLIN_LABELS = frozenset({"this", "super", "return", "break", "continue"})

_HOME_PATH = re.compile(r"(?:\b[A-Za-z]:|/mnt/[a-z])[\\/]+users[\\/]", re.IGNORECASE)
_MAC = re.compile(r"(?<![0-9A-Fa-f:])[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f:])")
_MAC_ALLOWED = {"AA:BB:CC:DD:EE:FF", "00:00:00:00:00:00"}


def _email_findings(line: str) -> bool:
    for match in _EMAIL.finditer(line):
        address = match.group(0).lower()
        if address.split("@", 1)[0] in _KOTLIN_LABELS:  # this@Service.field
            continue
        if _EMAIL_ALLOWED_DOMAINS.search(match.group(1)):
            continue
        if address in EMAIL_ALLOWED_ADDRESSES:
            continue
        return True
    return False


def scan_text(path: str, text: str, denylist: list[str]) -> list[Finding]:
    """Срабатывания в тексте файла: по одному на правило и строку."""
    findings: list[Finding] = []
    lowered_deny = [(word, word.lower()) for word in denylist]
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _SECRET_RULES:
            if pattern.search(line):
                findings.append(Finding(path, number, kind))
        if _email_findings(line):
            findings.append(Finding(path, number, "e-mail"))
        if _HOME_PATH.search(line):
            findings.append(Finding(path, number, "домашний путь"))
        if any(m.group(0).upper() not in _MAC_ALLOWED for m in _MAC.finditer(line)):
            findings.append(Finding(path, number, "MAC-адрес"))
        low = line.lower()
        for word, word_low in lowered_deny:
            if word_low in low:
                findings.append(Finding(path, number, f"запретное слово «{word}»"))
    return findings


def format_finding(finding: Finding) -> str:
    """``файл:строка: тип`` — без самого найденного значения."""
    return f"{finding.path}:{finding.line}: {finding.kind}"


def load_denylist(path: Path) -> list[str]:
    """Слова по одному на строку; ``#`` — комментарий, пустые строки пропускаются."""
    words = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        word = raw.strip()
        if word and not word.startswith("#"):
            words.append(word)
    return words


# --- git ----------------------------------------------------------------------


@dataclass(frozen=True)
class TrackedFile:
    path: str
    mode: str
    sha: str


def _git(repo: Path, *args: str, identity: tuple[str, str] | None = None) -> bytes:
    prefix = ["git", "-c", "core.quotepath=off"]
    if identity:
        prefix += ["-c", f"user.name={identity[0]}", "-c", f"user.email={identity[1]}"]
    return subprocess.run(
        [*prefix, *args], cwd=repo, check=True, capture_output=True
    ).stdout


def list_head_files(repo: Path) -> list[TrackedFile]:
    """Файлы HEAD (не рабочего дерева): несохранённые правки наружу не уходят."""
    out = _git(repo, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    files = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        if kind != "blob":  # подмодули не выгружаем
            continue
        files.append(TrackedFile(name.decode("utf-8"), mode, sha))
    return files


def read_blobs(repo: Path, files: list[TrackedFile]) -> dict[str, bytes]:
    """Содержимое файлов одним ``git cat-file --batch``."""
    if not files:
        return {}
    request = "".join(f"{f.sha}\n" for f in files).encode()
    raw = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=repo, input=request, check=True, capture_output=True
    ).stdout
    blobs: dict[str, bytes] = {}
    pos = 0
    for f in files:
        header_end = raw.index(b"\n", pos)
        size = int(raw[pos:header_end].split()[2])
        start = header_end + 1
        blobs[f.path] = raw[start : start + size]
        pos = start + size + 1  # после содержимого — перевод строки
    return blobs


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


# --- выгрузка -----------------------------------------------------------------


def clean_target(out: Path) -> None:
    """Очищает целевую папку, кроме её ``.git``."""
    out.mkdir(parents=True, exist_ok=True)
    for entry in out.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def write_files(out: Path, files: list[TrackedFile], blobs: dict[str, bytes]) -> None:
    for f in files:
        target = out / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs[f.path])


def commit_target(
    out: Path, files: list[TrackedFile], source_sha: str, identity: tuple[str, str] | None
) -> bool:
    """Коммит в целевой папке. False — менять нечего."""
    if not (out / ".git").exists():
        _git(out, "init", "-q")
    _git(out, "add", "-A")
    # Windows не хранит бит исполнения — переносим его из исходника явно.
    executable = [f.path for f in files if f.mode == "100755"]
    if executable:
        _git(out, "update-index", "--chmod=+x", "--", *executable)
    if not _git(out, "status", "--porcelain").strip():
        return False
    _git(out, "commit", "-q", "-m", f"Синхронизация с {source_sha}", identity=identity)
    return True


def main(argv: list[str] | None = None, *, git_identity: tuple[str, str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Публичная выгрузка репозитория УЗЕЛ")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="исходный репозиторий")
    parser.add_argument("--out", type=Path, help="целевая папка (её .git сохраняется)")
    parser.add_argument("--denylist", type=Path, help=f"по умолчанию {DEFAULT_DENYLIST}")
    parser.add_argument("--report-only", action="store_true", help="только проверить, не копировать")
    parser.add_argument("--commit", action="store_true", help="закоммитить в целевой папке (без push)")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not args.report_only and args.out is None:
        print("нужен --out (или --report-only)")
        return 2
    if args.out is not None and args.out.resolve() == repo:
        print("--out совпадает с исходным репозиторием")
        return 2

    if args.denylist is not None:
        if not args.denylist.is_file():
            print(f"нет файла запретных слов: {args.denylist}")
            return 2
        denylist = load_denylist(args.denylist)
    elif (repo / DEFAULT_DENYLIST).is_file():
        denylist = load_denylist(repo / DEFAULT_DENYLIST)
    else:
        print(f"предупреждение: нет {DEFAULT_DENYLIST} — проверка без списка запретных слов")
        denylist = []

    try:
        files = [f for f in list_head_files(repo) if not is_excluded(f.path)]
        blobs = read_blobs(repo, files)
        source_sha = _git(repo, "rev-parse", "--short", "HEAD").decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"git не отработал: {exc}")
        return 2

    findings: list[Finding] = []
    for f in files:
        findings.extend(scan_text(f.path, _decode(blobs[f.path]), denylist))
    for finding in findings:
        print(format_finding(finding))
    total = sum(len(b) for b in blobs.values())
    print(
        f"{len(files)} файлов, {total / 1024 / 1024:.1f} МиБ, HEAD {source_sha}: "
        f"{len(findings)} срабатываний"
    )
    if findings:
        return 1
    if args.report_only:
        return 0

    out = args.out.resolve()
    clean_target(out)
    write_files(out, files, blobs)
    print(f"выгружено в {out}")
    if args.commit:
        if commit_target(out, files, source_sha, git_identity):
            print(f"коммит: Синхронизация с {source_sha}")
        else:
            print("изменений нет — коммит не нужен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
