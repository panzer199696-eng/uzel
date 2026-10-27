"""Разбор разговора моделью: FR-030, FR-031, FR-032."""

from __future__ import annotations

import json

import pytest
from uzel.llm import (
    AllLlmBackendsFailed,
    LlmBackend,
    LlmCloudNotAllowed,
    LlmError,
    LlmResult,
    LlmRouter,
    build_llm_router,
)
from uzel.nlu import (
    NluError,
    NluFact,
    NluProcessor,
    NluResult,
    extract_json,
    store_result,
    validate,
)

GOOD = {
    "summary": "Обсудили заявку ООО «Ромашка» и сроки поставки.",
    "facts": ["Заявка подаётся 30 августа", "Нужен кирпич марки М150"],
    "tasks": [{"text": "подготовить КП", "assignee": "Иван", "due": "2026-08-30"}],
    "topics": ["закупки"],
    "people": ["Иван"],
}


class FakeLlm(LlmBackend):
    def __init__(self, name, replies, *, is_cloud=False, ready=True, error=None):
        self.name = name
        self.is_cloud = is_cloud
        self._replies = list(replies)
        self._ready = ready
        self._error = error
        self.calls = 0
        self.requests: list[dict] = []

    def available(self) -> bool:
        return self._ready

    def complete(
        self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False
    ) -> LlmResult:
        self.calls += 1
        self.requests.append(
            {
                "user": messages[-1]["content"],
                "json_mode": json_mode,
                "max_tokens": max_tokens,
                "messages": [dict(m) for m in messages],
            }
        )
        if self._error is not None:
            raise self._error
        text = self._replies.pop(0) if self._replies else self._replies_exhausted()
        return LlmResult(text=text, backend=self.name, model="fake", latency_s=0.0)

    @staticmethod
    def _replies_exhausted():
        raise AssertionError("у поддельной модели кончились ответы")


# --- извлечение JSON ---


def test_extract_plain_json():
    assert extract_json('{"summary": "текст"}') == {"summary": "текст"}


def test_extract_json_from_markdown_fence():
    raw = 'Вот результат:\n```json\n{"summary": "текст"}\n```\nГотово.'

    assert extract_json(raw) == {"summary": "текст"}


def test_extract_json_surrounded_by_chatter():
    raw = 'Конечно! {"summary": "текст", "facts": []} — надеюсь, помогло.'

    assert extract_json(raw)["summary"] == "текст"


def test_extract_rejects_answer_without_json():
    with pytest.raises(NluError):
        extract_json("Извините, не могу помочь.")


def test_extract_rejects_empty_answer():
    with pytest.raises(NluError):
        extract_json("   ")


def test_extract_rejects_json_array():
    with pytest.raises(NluError):
        extract_json("[1, 2, 3]")


# --- проверка схемы ---


def test_validate_accepts_full_payload():
    summary, facts, tasks, topics, people = validate(GOOD)

    assert summary.startswith("Обсудили")
    assert len(facts) == 2
    assert tasks[0].text == "подготовить КП"
    assert tasks[0].assignee == "Иван"
    assert tasks[0].due == "2026-08-30"
    assert topics == ["закупки"]
    assert people == ["Иван"]


def test_validate_requires_summary():
    with pytest.raises(NluError):
        validate({"summary": "  ", "facts": []})


def test_validate_fills_missing_lists():
    summary, facts, tasks, topics, people = validate({"summary": "есть"})

    assert (facts, tasks, topics, people) == ([], [], [], [])


def test_validate_accepts_tasks_written_as_plain_strings():
    """Модель нередко отдаёт задачи строками вместо объектов."""
    _, _, tasks, _, _ = validate({"summary": "есть", "tasks": ["позвонить"]})

    assert tasks[0].text == "позвонить"
    assert tasks[0].assignee is None


def test_validate_drops_tasks_without_text():
    _, _, tasks, _, _ = validate(
        {"summary": "есть", "tasks": [{"assignee": "Иван"}, {"text": "  "}, 42]}
    )

    assert tasks == []


# --- факты с таймкодом (ТЗ 2026-08-24, пакет 4) ---


def test_validate_accepts_fact_object_with_timecode():
    payload = {
        "summary": "есть",
        "facts": [{"text": "заявка 30 августа", "at": "10:42"}],
    }

    _, facts, _, _, _ = validate(payload)

    assert facts == [NluFact(text="заявка 30 августа", at="10:42")]


def test_validate_normalizes_timecode_with_leading_zero():
    _, facts, _, _, _ = validate(
        {"summary": "есть", "facts": [{"text": "факт", "at": "9:05"}]}
    )

    assert facts[0].at == "09:05"


def test_validate_accepts_fact_as_plain_string():
    """Старый формат от моделей без времени — факт без таймкода."""
    _, facts, _, _, _ = validate({"summary": "есть", "facts": ["просто факт"]})

    assert facts == [NluFact(text="просто факт", at=None)]


@pytest.mark.parametrize("bad_at", ["вечером", "25:99", "10:42:00", 1042, ""])
def test_validate_drops_garbage_timecode_but_keeps_fact(bad_at):
    _, facts, _, _, _ = validate(
        {"summary": "есть", "facts": [{"text": "факт", "at": bad_at}]}
    )

    assert facts == [NluFact(text="факт", at=None)]


def test_validate_reads_timecode_from_alternative_keys():
    """Модели путаются в имени поля — берём первую непустую из известных."""
    _, facts, _, _, _ = validate(
        {"summary": "есть", "facts": [{"text": "факт", "time": "11:20"}]}
    )

    assert facts[0].at == "11:20"
    _, facts, _, _, _ = validate(
        {"summary": "есть", "facts": [{"text": "факт", "таймкод": "11:21"}]}
    )

    assert facts[0].at == "11:21"


def test_validate_skips_fact_objects_without_text():
    _, facts, _, _, _ = validate(
        {"summary": "есть", "facts": [{"at": "10:00"}, {"text": "  "}, 42, None]}
    )

    assert facts == []


def test_validate_treats_empty_assignee_as_absent():
    _, _, tasks, _, _ = validate(
        {"summary": "есть", "tasks": [{"text": "дело", "assignee": "", "due": None}]}
    )

    assert tasks[0].assignee is None and tasks[0].due is None


def test_validate_skips_blank_items_in_lists():
    _, facts, _, _, _ = validate({"summary": "есть", "facts": ["ок", "  ", ""]})

    assert facts == [NluFact(text="ок")]


# --- разбор ---


def test_analyse_returns_structure():
    processor = NluProcessor(LlmRouter([FakeLlm("m", [json.dumps(GOOD)])]))

    result = processor.analyse("расшифровка разговора")

    assert result.summary.startswith("Обсудили")
    assert result.tasks[0].assignee == "Иван"
    assert result.backend == "m"


def test_analyse_asks_room_for_long_answer():
    """P10: на 2048 токенах ответ по длинному куску обрывался посреди JSON."""
    backend = FakeLlm("m", [json.dumps(GOOD)])
    processor = NluProcessor(LlmRouter([backend]))

    processor.analyse("расшифровка разговора")

    assert backend.requests[0]["max_tokens"] >= 3072


def test_default_chunk_fits_answer_budget():
    """P10: кусок 12000 символов + ответ не влезали; предел — 6000."""
    from uzel.config import Settings

    assert Settings.model_fields["nlu_max_chars"].default == 6000


def test_analyse_retries_once_on_broken_json():
    """FR-031: невалидный ответ — один повтор."""
    backend = FakeLlm("m", ["не json вовсе", json.dumps(GOOD)])
    processor = NluProcessor(LlmRouter([backend]))

    result = processor.analyse("расшифровка")

    assert backend.calls == 2
    assert result.summary.startswith("Обсудили")


def test_analyse_gives_up_after_second_failure():
    backend = FakeLlm("m", ["мусор", "снова мусор"])
    processor = NluProcessor(LlmRouter([backend]))

    with pytest.raises(NluError):
        processor.analyse("расшифровка")

    assert backend.calls == 2


def test_analyse_does_not_fall_back_to_cloud_after_local_failure():
    """Отказ локальной модели НЕ переливается в облако даже при allow_cloud.

    Договор изменён 23.09.2026 (P11, находка 7). Раньше упавшая Ollama означала,
    что транскрипты суток молча уезжают в облако — ровно тот сценарий, от
    которого STTRouter защищён с P1. Теперь защита симметричная.
    """
    dead_local = FakeLlm("ollama", [], error=LlmError("модель не поднялась"))
    cloud = FakeLlm("groq", [json.dumps(GOOD)], is_cloud=True)
    processor = NluProcessor(LlmRouter([dead_local, cloud]))

    with pytest.raises(NluError):
        processor.analyse("расшифровка", allow_cloud=True)

    assert cloud.calls == 0, "облако вызвано после отказа локальной модели"


def test_analyse_falls_back_between_cloud_models():
    """Фолбэк сохраняется там, где он ничего не нарушает: облако → облако."""
    dead_cloud = FakeLlm(
        "gateway", [], error=LlmError("upstream отказал"), is_cloud=True
    )
    alive_cloud = FakeLlm("groq", [json.dumps(GOOD)], is_cloud=True)
    processor = NluProcessor(LlmRouter([dead_cloud, alive_cloud]))

    assert processor.analyse("расшифровка", allow_cloud=True).backend == "groq"


def test_analyse_reports_when_no_model_answered():
    dead = FakeLlm("m", [], error=LlmError("нет связи"))
    processor = NluProcessor(LlmRouter([dead]))

    with pytest.raises(NluError, match="модель недоступна"):
        processor.analyse("расшифровка")


def test_analyse_rejects_empty_transcript():
    processor = NluProcessor(LlmRouter([FakeLlm("m", [])]))

    with pytest.raises(NluError):
        processor.analyse("   ")


def test_long_transcript_is_trimmed_not_rejected():
    backend = FakeLlm("m", [json.dumps(GOOD)])
    processor = NluProcessor(LlmRouter([backend]), max_chars=100)

    processor.analyse("а" * 5000)

    assert backend.calls == 1


def test_private_conversation_never_reaches_cloud_model():
    """Саммари приватного разговора в облаке — та же утечка, что и звук."""
    cloud = FakeLlm("groq", [json.dumps(GOOD)], is_cloud=True)
    processor = NluProcessor(LlmRouter([cloud]))

    with pytest.raises(NluError):
        processor.analyse("секретный разговор", allow_cloud=False)

    assert cloud.calls == 0


# --- длинные разговоры: разбор по частям (ТЗ P5) ---


def _payload(summary, fact, task):
    return {
        "summary": summary,
        "facts": [fact],
        "tasks": [{"text": task}] if task else [],
        "topics": [],
        "people": [],
    }


def test_long_transcript_is_split_not_silently_trimmed():
    """3 куска → 3 разбора + 1 сведение пересказов; хвост не теряется."""
    lines = [f"[12:{i:02d}] реплика номер {i} про заявку и кирпич" for i in range(6)]
    transcript = "\n".join(lines)
    assert len(transcript) > 200

    replies = [
        json.dumps(_payload("Пересказ один.", "факт раз", "задача один")),
        json.dumps(_payload("Пересказ два.", "факт два", "задача два")),
        json.dumps(_payload("Пересказ три.", "факт три", None)),
        "Сводный пересказ разговора.",
    ]
    backend = FakeLlm("m", replies)
    processor = NluProcessor(LlmRouter([backend]), max_chars=120)

    result = processor.analyse(transcript)

    assert backend.calls == 4  # три куска и сведение пересказов
    assert result.summary == "Сводный пересказ разговора."
    assert [f.text for f in result.facts] == ["факт раз", "факт два", "факт три"]
    assert [t.text for t in result.tasks] == ["задача один", "задача два"]
    assert result.backend == "m"
    # каждый кусок уехал в модель с требованием JSON (сведению он не нужен)
    assert [r["json_mode"] for r in backend.requests] == [True, True, True, False]


def test_duplicates_across_chunks_are_merged():
    payload = json.dumps(_payload("Пересказ.", "факт повторяется", "задача"))
    backend = FakeLlm("m", [payload, payload, payload, "Сводка."])
    lines = [f"[10:{i:02d}] " + "текст реплики " * 4 for i in range(6)]
    processor = NluProcessor(LlmRouter([backend]), max_chars=140)

    result = processor.analyse("\n".join(lines))

    assert [f.text for f in result.facts] == ["факт повторяется"]
    assert [t.text for t in result.tasks] == ["задача"]


def test_one_failed_chunk_does_not_lose_the_rest():
    backend = FakeLlm(
        "m",
        [
            "не json вовсе",  # первый кусок: невалидный ответ
            "снова не json",  # …и повтор тоже мусор — кусок списан
            json.dumps(_payload("Пересказ два.", "факт два", "задача два")),
            json.dumps(_payload("Пересказ три.", "факт три", None)),
            "Сводка двух кусков.",
        ],
    )
    lines = [f"[11:{i:02d}] " + "текст реплики " * 4 for i in range(6)]
    processor = NluProcessor(LlmRouter([backend]), max_chars=140)

    result = processor.analyse("\n".join(lines))

    assert [f.text for f in result.facts] == ["факт два", "факт три"]
    assert result.summary == "Сводка двух кусков."


def test_all_chunks_failed_raises_nlu_error():
    backend = FakeLlm("m", ["мусор"] * 6)  # три куска × две попытки
    lines = [f"[11:{i:02d}] " + "текст реплики " * 4 for i in range(6)]
    processor = NluProcessor(LlmRouter([backend]), max_chars=140)

    with pytest.raises(NluError, match="ни один кусок"):
        processor.analyse("\n".join(lines))


def test_failed_summary_merge_falls_back_to_concatenation():
    class DyingAfterThree(FakeLlm):
        """Три куска отвечает, на сведении пересказов отказывает."""

        def complete(self, messages, **kwargs):
            if not self._replies:
                raise LlmError("сведение недоступно")
            return super().complete(messages, **kwargs)

    backend = DyingAfterThree(
        "m",
        [
            json.dumps(_payload("Пересказ один.", None, None)),
            json.dumps(_payload("Пересказ два.", None, None)),
            json.dumps(_payload("Пересказ три.", None, None)),
        ],
    )
    lines = [f"[12:{i:02d}] " + "текст реплики " * 4 for i in range(6)]
    processor = NluProcessor(LlmRouter([backend]), max_chars=140)

    result = processor.analyse("\n".join(lines))

    # сведение упало — пересказы склеены через пробел, без исключения
    assert result.summary == "Пересказ один. Пересказ два. Пересказ три."


def test_oversized_line_becomes_whole_chunk():
    """Реплика длиннее куска не режется пополам: в начале — таймкод."""
    long_line = "[13:07] " + "очень длинная реплика " * 20
    transcript = "\n".join(["[13:05] короткая", long_line, "[13:09] ещё короткая"])
    backend = FakeLlm(
        "m",
        [
            json.dumps(_payload("Один.", None, None)),
            json.dumps(_payload("Два.", None, None)),
            json.dumps(_payload("Три.", None, None)),
            "Сводка.",
        ],
    )
    processor = NluProcessor(LlmRouter([backend]), max_chars=80)

    result = processor.analyse(transcript)

    assert backend.calls == 4
    user_messages = [r["user"] for r in backend.requests]
    assert long_line in user_messages  # реплика уехала целиком
    assert all(len(m) <= 80 or m == long_line for m in user_messages)


# --- запись результата ---


def test_store_result_writes_everything_and_closes_conversation(tmp_path):
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        conversation = storage.open_conversation(uid="dev-1", started_at=100.0)
        processor = NluProcessor(LlmRouter([FakeLlm("m", [json.dumps(GOOD)])]))

        store_result(storage, conversation, processor.analyse("расшифровка"))

        assert len(storage.facts_of(conversation)) == 2
        assert storage.tasks_of(conversation)[0]["assignee"] == "Иван"
        row = storage.conversations_between(0, 1000)[0]
        assert row["status"] == "closed"
        assert row["summary"].startswith("Обсудили")


def test_store_result_maps_fact_timecode_to_absolute_timestamp(tmp_path):
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=[NluFact(text="факт", at="10:42")])

        store_result(storage, cid, result, time_map={"10:42": 1758000120.0})

        rows = storage.fact_rows_of(cid)
        assert rows[0]["said_at"] == 1758000120.0


def test_store_result_keeps_fact_with_unknown_timecode_and_warns(tmp_path, caplog):
    """Таймкода нет в разговоре — признак галлюцинации: факт цел, журнал предупредил."""
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=[NluFact(text="факт", at="23:59")])

        with caplog.at_level("WARNING", logger="uzel.nlu"):
            store_result(storage, cid, result, time_map={"10:42": 1.0})

        rows = storage.fact_rows_of(cid)
        assert len(rows) == 1  # факт не потерялся
        assert rows[0]["said_at"] is None
        assert rows[0]["text"] == "факт"
        assert any("не нашёлся в разговоре" in message for message in caplog.messages)


def test_journal_never_carries_conversation_text(tmp_path, caplog):
    """P11.2, находка 12: одного `> лог.txt 2>&1` хватило бы, чтобы разговоры
    легли на диск открытым текстом. В журнал — id и таймкод, не слова."""
    from uzel.nlu import extract_json
    from uzel.storage import Storage

    маркер = "Сорокин-секретное-слово"
    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=[NluFact(text=маркер, at="23:59")])
        with caplog.at_level("DEBUG"):
            store_result(storage, cid, result, time_map={})
        fact_id = storage.fact_rows_of(cid)[0]["id"]

    with pytest.raises(NluError) as caught:
        extract_json(f"Вот пересказ: {маркер}, без JSON")

    assert маркер not in caught.value.args[0]
    assert all(маркер not in message for message in caplog.messages)
    assert any(f"факт {fact_id}" in message for message in caplog.messages)


def test_store_result_dedup_by_text_survives_time_map(tmp_path):
    """Повторный разбор не создаёт вторую запись — дедуп по тексту, не по времени."""
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=[NluFact(text="факт", at="10:42")])

        store_result(storage, cid, result, time_map={"10:42": 1.0})
        store_result(storage, cid, result, time_map={"10:42": 2.0})

        assert len(storage.fact_rows_of(cid)) == 1


def test_store_result_accepts_plain_string_facts_without_time_map(tmp_path):
    """Совместимость: факты строками и без карты времени записываются как раньше."""
    from uzel.storage import Storage

    with Storage(tmp_path / "db.sqlite") as storage:
        cid = storage.open_conversation(uid="dev-1", started_at=100.0)
        result = NluResult(summary="с", facts=["строка-факт"])

        store_result(storage, cid, result)

        rows = storage.fact_rows_of(cid)
        assert [row["text"] for row in rows] == ["строка-факт"]
        assert rows[0]["said_at"] is None


# --- роутер моделей ---


def test_router_skips_unconfigured_backend():
    """Ненастроенный бэкенд не вызывается; облачный следом — тоже.

    Ненастроенная локальная модель — такой же отказ локального звена, как и
    упавшая: после него облако вычёркивается из перебора (P11, находка 7).
    """
    unconfigured_local = FakeLlm("ollama", [], ready=False)
    cloud = FakeLlm("groq", ["{}"], is_cloud=True)

    with pytest.raises(AllLlmBackendsFailed):
        LlmRouter([unconfigured_local, cloud]).complete(
            [{"role": "user", "content": "x"}], allow_cloud=True
        )

    assert unconfigured_local.calls == 0
    assert cloud.calls == 0


def test_router_raises_when_every_model_fails():
    with pytest.raises(AllLlmBackendsFailed):
        LlmRouter([FakeLlm("m", [], error=LlmError("нет"))]).complete(
            [{"role": "user", "content": "x"}]
        )


def test_router_refuses_cloud_only_setup_for_private_content():
    with pytest.raises(LlmCloudNotAllowed):
        LlmRouter([FakeLlm("groq", ["{}"], is_cloud=True)]).complete(
            [{"role": "user", "content": "x"}], allow_cloud=False
        )


def test_build_llm_router_default_is_local_ollama(settings):
    assert [b.name for b in build_llm_router(settings).backends] == ["ollama"]
    assert build_llm_router(settings).backends[0].is_cloud is False

    router = build_llm_router(
        settings.model_copy(update={"llm_backends": ("ollama", "gateway", "groq")})
    )

    assert [b.name for b in router.backends] == ["ollama", "gateway", "groq-llm"]
    assert router.backends[0].is_cloud is False
    assert router.backends[1].is_cloud is True  # шлюз считает в облаке, не локально


def test_build_llm_router_ignores_unknown_names(settings):
    router = build_llm_router(
        settings.model_copy(update={"llm_backends": ("groq", "xxx")})
    )

    assert [b.name for b in router.backends] == ["groq-llm"]


# --- роль владельца микрофона в промпте ---


def _system_prompt(processor_kwargs: dict) -> str:
    backend = FakeLlm("m", [json.dumps(GOOD)])
    NluProcessor(LlmRouter([backend]), **processor_kwargs).analyse("[10:00] текст")
    return backend.requests[0]["messages"][0]["content"]


def test_prompt_role_is_neutral_by_default():
    system = _system_prompt({})
    assert "Владелец микрофона: пользователь." in system
    assert "ПТО" not in system


def test_prompt_takes_user_role_from_argument():
    system = _system_prompt({"user_role": "прораб на стройке"})
    assert "Владелец микрофона: прораб на стройке." in system


def test_user_role_setting_reads_environment(monkeypatch, tmp_path):
    from uzel.config import Settings

    assert Settings(data_dir=tmp_path).user_role == "пользователь"
    monkeypatch.setenv("UZEL_USER_ROLE", "инженер-сметчик")
    assert Settings(data_dir=tmp_path).user_role == "инженер-сметчик"
