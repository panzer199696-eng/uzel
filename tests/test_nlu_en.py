"""Разбор на английском интерфейсе (27.10.2026): промпты, вид записи, данные языка.

Сводки, факты и задачи пишутся на языке интерфейса, даже если разговор на другом:
промпт велит писать на нём, а цитата остаётся дословной на языке разговора.
Модель — заглушка, сети нет.
"""

from __future__ import annotations

from datetime import date

from test_nlu_strict import CHUNK, DAY, Scripted, v2
from uzel import i18n
from uzel.llm import LlmRouter
from uzel.nlu import NluProcessor, strict_items

EN_CHUNK = (
    "[09:10] hi, it's about the concrete order for the north block\n"
    "[09:11] the supplier confirmed delivery of forty cubic metres\n"
    "[09:12] I'll send the invoice to accounting by Friday\n"
    "[09:13] Mike, call the crane operator and book him for Monday\n"
)


def en_payload(**over):
    payload = {
        "kind": "work",
        "summary": "Call about the concrete order.",
        "tasks": [],
        "decisions": [],
        "facts": [],
        "topics": ["concrete"],
        "people": [],
    }
    payload.update(over)
    return payload


def test_english_system_prompt_with_role_date_and_weekday():
    backend = Scripted([en_payload()])
    NluProcessor(LlmRouter([backend]), lang="en").analyse(EN_CHUNK, day=DAY)
    prompt = backend.prompts[0]
    assert "Microphone owner: user." in prompt
    assert "Recording date: 2026-10-13 (Tuesday)." in prompt
    assert "Write in English, even if the conversation is in another language." in prompt
    assert '"kind": "work" | "home" | "leisure"' in prompt  # схема с одинарными скобками


def test_role_in_any_language_goes_into_english_prompt():
    backend = Scripted([en_payload()])
    NluProcessor(LlmRouter([backend]), lang="en", user_role="инженер ПТО").analyse(EN_CHUNK)
    assert "Microphone owner: инженер ПТО." in backend.prompts[0]
    assert "Recording date: unknown (weekday unknown)." in backend.prompts[0]


def test_processor_follows_interface_language_by_default():
    i18n.set_language("en")
    backend = Scripted([en_payload()])
    result = NluProcessor(LlmRouter([backend])).analyse(EN_CHUNK, day=DAY)
    assert "Microphone owner" in backend.prompts[0]
    assert result.kind == "work"


def test_english_strict_items_ground_quotes_due_and_owner():
    facts, tasks, kind = strict_items(
        en_payload(
            tasks=[
                {"text": "send the invoice to accounting", "owner": "me", "quote": "I'll send the invoice to accounting by Friday", "due": "2026-10-16"},
                {"text": "book the crane operator for Monday", "owner": "Mike", "quote": "call the crane operator and book him for Monday", "due": "2026-10-19"},
                {"text": "call him", "owner": "unknown", "quote": "call the crane operator"},
                {"text": "order the steel beams", "owner": "someone", "quote": "nobody said anything about steel"},
            ],
            decisions=[{"text": "forty cubic metres are delivered", "quote": "the supplier confirmed delivery of forty cubic metres"}],
        ),
        EN_CHUNK,
        DAY,
        lang="en",
    )
    assert kind == "work"
    assert [(t.text, t.assignee, t.due) for t in tasks] == [
        ("send the invoice to accounting", "me", "2026-10-16"),
        ("book the crane operator for Monday", "Mike", "2026-10-19"),
    ]
    assert [f.text for f in facts] == ["Agreed: forty cubic metres are delivered"]


def test_english_leisure_gives_nothing():
    facts, tasks, kind = strict_items(
        en_payload(
            kind="leisure",
            tasks=[{"text": "find the clues in the library", "owner": "me", "quote": "the supplier confirmed delivery of forty"}],
        ),
        EN_CHUNK,
        DAY,
        lang="en",
    )
    assert (facts, tasks, kind) == ([], [], "leisure")


def test_kind_from_other_language_is_mapped_to_interface_language():
    """Русский интерфейс, а модель ответила видом по-английски — и наоборот."""
    assert strict_items(v2(kind="work"), CHUNK, DAY, lang="ru")[2] == "дело"
    assert strict_items(v2(kind="leisure"), CHUNK, DAY, lang="ru")[2] == "досуг"
    assert strict_items(en_payload(kind="быт"), EN_CHUNK, DAY, lang="en")[2] == "home"


def test_russian_conversation_on_english_interface_keeps_russian_quote():
    """Разговор по-русски, интерфейс английский: задача по-английски, цитата дословно."""
    backend = Scripted(
        [
            en_payload(
                tasks=[{"text": "find the cable on Avito", "owner": "me", "quote": "мы попробуем его на авито поискать"}],
            ),
            {"class": "A"},
        ]
    )
    result = NluProcessor(LlmRouter([backend]), lang="en").analyse(CHUNK, day=DAY)
    assert [(t.text, t.quote) for t in result.tasks] == [
        ("find the cable on Avito", "мы попробуем его на авито поискать")
    ]
    assert "Task: find the cable on Avito" in backend.users[1]
    assert 'Answer STRICTLY with JSON: {"class"' in backend.prompts[1]


def test_english_merge_prompt_for_long_conversations():
    backend = Scripted([en_payload(summary="One."), en_payload(summary="Two.")] + [{"x": 1}])
    backend.answers[-1] = "merged"  # ответ сведения — простой текст
    long = EN_CHUNK + EN_CHUNK
    NluProcessor(LlmRouter([backend]), lang="en", max_chars=len(EN_CHUNK)).analyse(long, day=date(2026, 10, 13))
    assert "Merge these summaries" in backend.prompts[-1]


def test_person_tags_are_recognised_in_any_language():
    from uzel.worker import person_from_tag

    assert person_from_tag("человек:Иван") == "Иван"
    assert person_from_tag("person:Mike") == "Mike"
    assert person_from_tag("вид:дело") is None
    assert person_from_tag("закупки") is None
