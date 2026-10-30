"""Срок задачи по словам цитаты считает код, а не модель (30.10.2026).

Боевая база: «позвонить в понедельник», сказанное в пятницу 23.10.2026, получило
срок 25.10 (воскресенье); «провести изыскание в феврале» — 01.02.
"""

from __future__ import annotations

from datetime import date

import pytest
from uzel.llm import LlmRouter
from uzel.nlu import NluProcessor, resolve_due, strict_items

from test_nlu_strict import CHUNK, DAY, Scripted, v2

FRIDAY = date(2026, 10, 23)


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("я хотел в понедельник здесь позвонить", date(2026, 10, 26)),
        ("давай завтра созвонимся", date(2026, 10, 24)),
        ("послезавтра привезут трубы", date(2026, 10, 25)),
        ("сегодня до вечера скину", FRIDAY),
        ("к пятнице подготовь акты", date(2026, 10, 30)),  # тот же день недели — через неделю
        ("до среды надо отправить", date(2026, 10, 28)),
        ("провести изыскание в феврале", date(2027, 2, 28)),
        ("сдать в октябре", date(2026, 10, 31)),
        ("через неделю вернёмся к вопросу", date(2026, 10, 30)),
    ],
)
def test_resolve_due_ru(quote, expected):
    assert resolve_due(quote, FRIDAY) == expected


@pytest.mark.parametrize(
    "quote",
    [
        "до 15 марта сдать отчёт",  # с числом — дату переписывает модель
        "завтра или в понедельник",  # слова спорят
        "в среде разработки всё работает",  # «среде» — не день недели
        "мы попробуем его на авито поискать",
        "",
    ],
)
def test_resolve_due_none(quote):
    assert resolve_due(quote, FRIDAY) is None


def test_resolve_due_without_day():
    assert resolve_due("завтра", None) is None


def test_resolve_due_english():
    assert resolve_due("call them on Monday", FRIDAY) == date(2026, 10, 26)
    assert resolve_due("you may want to check", FRIDAY) is None


def test_wrong_model_date_is_replaced():
    _, tasks, _ = strict_items(
        v2(
            tasks=[
                {
                    "text": "забрать принтер и заказать шлейф",
                    "owner": "я",
                    "quote": "в пятницу заберём принтер и закажем шлейф",
                    "due": "2026-10-15",  # модель ошиблась на день
                }
            ]
        ),
        CHUNK,
        DAY,
    )
    assert tasks[0].due == "2026-10-16"


def test_vocab_goes_into_prompt_only_when_set():
    answers = [v2(), v2()]
    backend = Scripted(answers)
    NluProcessor(LlmRouter([backend]), user_vocab="уголовное дело, экспертиза").analyse(CHUNK, day=DAY)
    NluProcessor(LlmRouter([backend])).analyse(CHUNK, day=DAY)
    assert "уголовное дело, экспертиза" in backend.prompts[0]
    assert "Частые слова" not in backend.prompts[1]
