"""Замер качества распознавания (критерий A1).

WER = (замены + пропуски + вставки) / число слов эталона.

Два тонких места, от которых зависит, честная получится цифра или нет:

1. **Нормализация.** «412» и «четыреста двенадцать» — одно и то же слово,
   произнесённое человеком и записанное моделью по-разному. Без приведения
   числительных к цифрам WER завышается на ровном месте.
2. **Отдельный WER по доменным словам.** Общая цифра размазывает главное:
   универсальные модели сыплются именно на «АОСР», «КС-2», «М-29». Ради этого
   и делается словарь, поэтому его эффект надо мерить отдельно.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Термины, ради которых всё затевалось. По ним считается отдельный WER.
# ⚠️ Записаны ОСНОВАМИ без окончаний: проверка идёт по началу слова, а полная
# форма «освидетельствование» не покрывает «освидетельствования» — то есть
# промахивается ровно на том слове, ради которого словарь и нужен.
DOMAIN_STEMS = (
    "аоср", "кс-2", "кс-3", "м-29", "гост", "снип", "ппр",
    "оквэд", "кпп", "инн", "смр", "пто",
    "исполнительн", "освидетельствовани", "скрыт", "нивелир", "теодолит",
    "щебен", "щебн", "бетон", "арматур", "опалубк", "ростверк", "сва",
    "пролёт", "пролет", "опор", "насып", "уплотнени", "монолит", "геодез",
)

# Короткие термины сравниваются целиком: «сп» иначе поймает «спасибо».
DOMAIN_EXACT = frozenset({"сп", "тк", "кс", "ту", "пос"})
_UNITS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_SCALES = {
    "тысяча": 1000,
    "тысячи": 1000,
    "тысяч": 1000,
    "тысячу": 1000,
    "миллион": 1_000_000,
    "миллиона": 1_000_000,
    "миллионов": 1_000_000,
}
_NUMBER_WORDS = {**_UNITS, **_TENS, **_HUNDREDS}

_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _strip_punctuation(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = text.replace("–", "-").replace("—", "-")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _rank(token: str) -> int:
    """Разряд числительного: сотни=3, десятки=2, единицы и «надцать»=1."""
    if token in _HUNDREDS:
        return 3
    if token in _TENS:
        return 2
    return 1


def words_to_number(tokens: list[str]) -> int | None:
    """Собирает число из подряд идущих числительных. ``None``, если это не число.

    Разряды обязаны идти строго по убыванию: «сто двадцать три» — число, а
    «один два три» — перечисление, и складывать его в 6 нельзя.
    """
    if not tokens:
        return None

    total = 0
    current = 0
    previous_rank = 4  # больше любого разряда
    seen = False

    for token in tokens:
        if token in _NUMBER_WORDS:
            rank = _rank(token)
            if rank >= previous_rank:
                return None  # разряд не убывает — это перечисление, а не число
            current += _NUMBER_WORDS[token]
            previous_rank = rank
            seen = True
        elif token in _SCALES:
            current = (current or 1) * _SCALES[token]
            total += current
            current = 0
            previous_rank = 4  # после «тысячи» разряды начинаются заново
            seen = True
        else:
            return None

    return total + current if seen else None


def collapse_numerals(tokens: list[str]) -> list[str]:
    """Числительные словами → цифры: «четыреста двенадцать» → «412»."""
    result: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] not in _NUMBER_WORDS and tokens[index] not in _SCALES:
            result.append(tokens[index])
            index += 1
            continue

        end = index
        while end < len(tokens) and (
            tokens[end] in _NUMBER_WORDS or tokens[end] in _SCALES
        ):
            end += 1

        value = words_to_number(tokens[index:end])
        if value is None:
            result.extend(tokens[index:end])
        else:
            result.append(str(value))
        index = end
    return result


def normalise(text: str) -> list[str]:
    """Текст → слова, приведённые к сравнимому виду."""
    tokens = _strip_punctuation(text.lower()).split()
    return collapse_numerals(tokens)


@dataclass
class WerReport:
    reference_words: int
    substitutions: int
    deletions: int
    insertions: int
    domain_total: int = 0
    domain_errors: int = 0
    examples: list[tuple[str, str]] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        return self.errors / self.reference_words if self.reference_words else 0.0

    @property
    def domain_wer(self) -> float:
        return self.domain_errors / self.domain_total if self.domain_total else 0.0

    @property
    def accuracy(self) -> float:
        return max(0.0, 1.0 - self.wer)

    def passed(self, threshold: float = 0.15) -> bool:
        return self.wer <= threshold


def _is_domain(word: str) -> bool:
    if word in DOMAIN_EXACT:
        return True
    return any(word.startswith(stem) for stem in DOMAIN_STEMS)


def compute_wer(
    reference: str, hypothesis: str, *, max_examples: int = 20
) -> WerReport:
    """Считает WER выравниванием по Левенштейну на уровне слов."""
    ref = normalise(reference)
    hyp = normalise(hypothesis)

    # Классическая матрица расстояний; хранится целиком ради обратного прохода.
    rows, cols = len(ref) + 1, len(hyp) + 1
    distance = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distance[i][0] = i
    for j in range(cols):
        distance[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                distance[i][j] = distance[i - 1][j - 1]
            else:
                distance[i][j] = 1 + min(
                    distance[i - 1][j - 1],  # замена
                    distance[i - 1][j],  # пропуск
                    distance[i][j - 1],  # вставка
                )

    substitutions = deletions = insertions = 0
    domain_total = domain_errors = 0
    examples: list[tuple[str, str]] = []

    i, j = len(ref), len(hyp)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            if _is_domain(ref[i - 1]):
                domain_total += 1
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
            substitutions += 1
            if _is_domain(ref[i - 1]):
                domain_total += 1
                domain_errors += 1
            if len(examples) < max_examples:
                examples.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
            deletions += 1
            if _is_domain(ref[i - 1]):
                domain_total += 1
                domain_errors += 1
            if len(examples) < max_examples:
                examples.append((ref[i - 1], "—"))
            i -= 1
        else:
            insertions += 1
            if len(examples) < max_examples:
                examples.append(("—", hyp[j - 1]))
            j -= 1

    examples.reverse()
    return WerReport(
        reference_words=len(ref),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        domain_total=domain_total,
        domain_errors=domain_errors,
        examples=examples,
    )


def render_report(
    report: WerReport,
    *,
    title: str,
    backend: str,
    audio_seconds: float,
    processing_seconds: float,
    threshold: float = 0.15,
    settings_note: str = "",
) -> str:
    """Отчёт в markdown — ложится в tests/wer/замеры/."""
    verdict = "✅ порог пройден" if report.passed(threshold) else "❌ порог НЕ пройден"
    speed = audio_seconds / processing_seconds if processing_seconds else 0.0

    lines = [
        f"# Замер WER — {title}",
        "",
        f"**{verdict}** (порог {threshold:.0%})",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| WER | **{report.wer:.1%}** |",
        f"| Точность | {report.accuracy:.1%} |",
        f"| Слов в эталоне | {report.reference_words} |",
        f"| Замен | {report.substitutions} |",
        f"| Пропусков | {report.deletions} |",
        f"| Вставок | {report.insertions} |",
        f"| WER по доменным словам | {report.domain_wer:.1%} ({report.domain_errors} из {report.domain_total}) |",
        f"| Бэкенд | {backend} |",
        f"| Длительность аудио | {audio_seconds / 60:.1f} мин |",
        f"| Время расчёта | {processing_seconds / 60:.1f} мин (×{speed:.1f} к реальному времени) |",
        "",
    ]

    if settings_note:
        lines += [f"Настройки: {settings_note}", ""]

    if report.examples:
        lines += ["## Ошибки", "", "| Эталон | Распознано |", "|---|---|"]
        lines += [f"| {ref} | {hyp} |" for ref, hyp in report.examples]
        lines.append("")

    if report.domain_total == 0:
        lines += [
            "> ⚠️ В эталоне не встретилось ни одного доменного термина — "
            "смысл словаря на этой записи не проверяется. Для честного замера "
            "нужна запись с рабочей лексикой (АОСР, КС-2, ГОСТ, шифры).",
            "",
        ]

    return "\n".join(lines)
