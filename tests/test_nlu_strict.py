"""Строгий разбор (схема v2, 23.10.2026): только то, что подтверждено расшифровкой.

Основание — разметка 124 задач из боевой базы: «позвать Катю (null)», срок «2023-12-29»
у разговора 13.10.2026, 16 «задач» из детективной игры («найти улики», «Скотланд-Ярд»).
"""

from __future__ import annotations

import json
from datetime import date

from uzel.llm import LlmBackend, LlmResult, LlmRouter
from uzel.nlu import NluProcessor, context_around, grounding, strict_items, validate

CHUNK = (
    "[14:42] да здравствуйте вы только что звонили с этого номера по принтеру\n"
    "[14:43] мы попробуем его на авито поискать или на разборках\n"
    "[14:44] если не найдём, в пятницу заберём принтер и закажем шлейф\n"
    "[14:45] Саня, скинь мне номер, кто звонил\n"
)
DAY = date(2026, 10, 13)  # вторник


def v2(**over):
    payload = {
        "kind": "дело",
        "summary": "Звонок по ремонту принтера.",
        "tasks": [],
        "decisions": [],
        "facts": [],
        "topics": ["принтер"],
        "people": [],
    }
    payload.update(over)
    return payload


def test_null_like_assignee_and_due_are_empty():
    _, _, tasks, _, _ = validate(
        {
            "summary": "есть",
            "tasks": [
                {"text": "позвать Катю", "assignee": "null", "due": "null"},
                {"text": "дело", "assignee": "пусто", "due": "не указано"},
                {"text": "дело2", "assignee": "Саня", "due": "в пятницу"},
            ],
        }
    )
    assert [(t.assignee, t.due) for t in tasks] == [(None, None), (None, None), ("Саня", None)]


def test_grounding_tolerates_asr_noise_but_not_invention():
    assert grounding("мы попробуем его на авито поискать", CHUNK) == 1.0
    assert grounding("попробуем его на авито поискать или на разборке", CHUNK) >= 0.8
    assert grounding("найдите улики в Скотланд-Ярде", CHUNK) < 0.5
    assert grounding("", CHUNK) == 0.0


def test_task_without_grounded_quote_is_dropped():
    _, tasks, _ = strict_items(
        v2(
            tasks=[
                {"text": "найти шлейф на авито", "owner": "я", "quote": "мы попробуем его на авито поискать"},
                {"text": "рассмотреть снижение должности", "owner": "", "quote": "давайте понизим его в должности"},
                {"text": "без цитаты", "owner": "я"},
            ]
        ),
        CHUNK,
        DAY,
    )
    assert [t.text for t in tasks] == ["найти шлейф на авито"]


def test_owner_normalised():
    _, tasks, _ = strict_items(
        v2(
            tasks=[
                {"text": "найти шлейф на авито", "owner": "я", "quote": "мы попробуем его на авито поискать"},
                {"text": "скинуть номер звонившего", "owner": "Саня", "quote": "Саня, скинь мне номер, кто звонил"},
                {"text": "перезвонить по принтеру", "owner": "название", "quote": "вы только что звонили с этого номера"},
            ]
        ),
        CHUNK,
        DAY,
    )
    assert [t.assignee for t in tasks] == ["я", "Саня", None]


def test_due_only_when_quote_names_time():
    _, tasks, _ = strict_items(
        v2(
            tasks=[
                {"text": "забрать принтер", "owner": "я", "quote": "в пятницу заберём принтер и закажем шлейф", "due": "2026-10-16"},
                {"text": "найти шлейф", "owner": "я", "quote": "мы попробуем его на авито поискать", "due": "2026-10-14"},
                {"text": "старый срок", "owner": "я", "quote": "в пятницу заберём принтер", "due": "2023-12-29"},
            ]
        ),
        CHUNK,
        DAY,
    )
    assert [t.due for t in tasks] == ["2026-10-16", None, None]


def test_leisure_gives_no_tasks_or_facts():
    facts, tasks, kind = strict_items(
        v2(
            kind="досуг",
            tasks=[{"text": "найти улики", "owner": "я", "quote": "мы попробуем его на авито поискать"}],
            facts=[{"text": "в кабинете ничего не нашли", "quote": "мы попробуем его на авито поискать"}],
        ),
        CHUNK,
        DAY,
    )
    assert (facts, tasks, kind) == ([], [], "досуг")


def test_decisions_become_facts_and_junk_facts_go():
    facts, _, _ = strict_items(
        v2(
            decisions=[{"text": "в пятницу забрать принтер", "quote": "в пятницу заберём принтер и закажем шлейф"}],
            facts=[
                {"text": "22800", "at": "14:42", "quote": "звонили с этого номера"},
                {"text": "шлейф ищут на Авито и разборках", "at": "14:43", "quote": "на авито поискать или на разборках"},
                {"text": "шлейф ищут на Авито и разборках", "at": "14:43", "quote": "на авито поискать или на разборках"},
            ],
        ),
        CHUNK,
        DAY,
    )
    assert [f.text for f in facts] == ["Договорились: в пятницу забрать принтер", "шлейф ищут на Авито и разборках"]
    assert facts[1].at == "14:43"


class Scripted(LlmBackend):
    name = "ollama"
    is_cloud = False

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.users = []

    def available(self) -> bool:
        return True

    def complete(self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False):
        self.prompts.append(messages[0]["content"])
        self.users.append(messages[-1]["content"])
        text = json.dumps(self.answers.pop(0), ensure_ascii=False)
        return LlmResult(text=text, backend="ollama", model="m", latency_s=0.0)


def test_bare_verb_tasks_dropped():
    _, tasks, _ = strict_items(
        v2(
            tasks=[
                {"text": "позвони", "owner": "я", "quote": "Саня, скинь мне номер, кто звонил"},
                {"text": "надо тебе", "owner": "я", "quote": "Саня, скинь мне номер, кто звонил"},
                {"text": "скинуть номер звонившего", "owner": "Саня", "quote": "Саня, скинь мне номер, кто звонил"},
            ]
        ),
        CHUNK,
        DAY,
    )
    assert [t.text for t in tasks] == ["скинуть номер звонившего"]
    assert tasks[0].quote == "Саня, скинь мне номер, кто звонил"


def test_context_around_quote():
    ctx = context_around("в пятницу заберём принтер", CHUNK, radius=1)
    assert "в пятницу заберём принтер" in ctx and "авито" in ctx and "Саня" in ctx and "здравствуйте" not in ctx


def test_processor_verifies_each_task():
    """Детективная игра в куске, который модель сочла «делом»: проверка задач узким
    вопросом отсекает игровые (B) и не-задачи (C), оставляет реальные (A)."""
    backend = Scripted(
        [
            v2(
                tasks=[
                    {"text": "найти шлейф на авито", "owner": "я", "quote": "мы попробуем его на авито поискать"},
                    {"text": "допросить звонившего про принтер", "owner": "я", "quote": "вы только что звонили с этого номера"},
                    {"text": "заказать шлейф для принтера", "owner": "я", "quote": "заберём принтер и закажем шлейф"},
                ]
            ),
            {"class": "A"},
            {"class": "B"},
            {"class": "C"},
        ]
    )
    result = NluProcessor(LlmRouter([backend])).analyse(CHUNK, day=DAY)
    assert [t.text for t in result.tasks] == ["найти шлейф на авито"]
    assert "Задача: допросить звонившего про принтер" in backend.users[2]


def test_processor_v2_passes_date_and_filters():
    backend = Scripted(
        [
            v2(
                tasks=[
                    {"text": "найти шлейф на авито", "owner": "я", "quote": "мы попробуем его на авито поискать", "due": "2026-10-14"},
                    {"text": "выдумка полная", "owner": "null", "quote": "этого никто не говорил вообще"},
                ]
            ),
            {"class": "A"},
        ]
    )
    result = NluProcessor(LlmRouter([backend])).analyse(CHUNK, day=DAY)
    assert "2026-10-13" in backend.prompts[0] and "вторник" in backend.prompts[0]
    assert [(t.text, t.assignee, t.due) for t in result.tasks] == [("найти шлейф на авито", "я", None)]
    assert result.kind == "дело"


def test_processor_kind_work_wins_over_leisure_across_chunks():
    backend = Scripted([v2(kind="досуг"), v2(kind="дело")] + [v2(kind="дело")] * 2)
    long = CHUNK * 3
    result = NluProcessor(LlmRouter([backend]), max_chars=len(CHUNK) + 5).analyse(long, day=DAY)
    assert result.kind == "дело"
