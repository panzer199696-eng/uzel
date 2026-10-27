"""Разбор разговора моделью: саммари, факты, задачи (FR-030…FR-032).

Ответ модели валидируется по схеме. Невалидный — один повтор с более жёсткой
инструкцией, затем разговор помечается ``nlu_failed``. Транскрипт при этом
остаётся: сбой модели никогда не имеет права стоить нам расшифровки.

Промпты и языковые данные (дни недели, слова «пустоты», приметы срока) — в
каталогах ``locales/<код>.json``, ключи ``nlu.*``. Сводка, факты и задачи
пишутся на языке интерфейса, даже если разговор на другом; цитата-основание
остаётся дословной. Списки слов берутся из ВСЕХ каталогов сразу: разговор
бывает не на языке интерфейса.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import i18n
from .i18n import t
from .llm import LlmError, LlmLocalFused, LlmRouter

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 24000
# Потолок ответа модели на один кусок. 2048 не хватало: на куске 12000 символов
# qwen2.5 7B упиралась в него (done_reason=length), JSON обрывался, обе попытки
# шли впустую (P10, 24.09.2026). 3072 + ~2700 токенов куска 6000 символов
# укладываются в num_ctx 8192.
NLU_MAX_TOKENS = 3072

# Старая схема (до 23.10.2026): рабочий путь идёт по SYSTEM_PROMPT_V2 ниже, эта
# остаётся для ops/проверка-nlu-куска-p10.py и разбора старых ответов.
SYSTEM_PROMPT = """Ты разбираешь расшифровку живого разговора.

Верни СТРОГО один объект JSON, без пояснений и без markdown-ограждений:
{
  "summary": "2–4 предложения по существу разговора",
  "facts": [{"text": "факт", "at": "ЧЧ:ММ — время реплики, в которой это прозвучало"}],
  "tasks": [{"text": "что сделать", "assignee": "кто или null", "due": "YYYY-MM-DD или null"}],
  "topics": ["тема", "..."],
  "people": ["имя", "..."]
}

Правила:
- Пиши по-русски.
- В facts — только то, что прямо прозвучало. Ничего не додумывай.
- В каждом факте "at" — ровно тот таймкод [ЧЧ:ММ], который стоит в начале реплики,
  откуда взят факт. Не придумывай время: если не уверен — ставь null.
- В tasks — только обязательства и поручения, а не пересказ разговора.
- Если чего-то в разговоре нет, ставь пустой список.
- Расшифровка машинная: в ней бывают ошибки распознавания. Очевидные исправляй
  по смыслу молча, сомнительные оставляй как есть."""

# Схема v2 (23.10.2026). Разметка 124 задач из боевой базы показала: «(null)» вместо
# исполнителя, выдуманные сроки (2023-12-29 у разговора 13.10.2026), 16 «задач» из
# детективной игры. Поэтому: дата записи в промпте, вид записи (досуг — без задач),
# дословная цитата-основание у каждого пункта — её сверяет strict_items().
# Роль владельца — настройка UZEL_USER_ROLE: «инженер на стройке» и «врач» дают модели
# разный словарь задач. В именительном падеже, чтобы подстановка не ломала грамматику.
# Текст промпта — в каталоге языка (nlu.prompt.system); русский — дословно прежний.
# Русские константы оставлены под старыми именами для замеров в ops/.
SYSTEM_PROMPT_V2 = i18n.data("nlu.prompt.system", lang="ru")
WEEKDAYS = tuple(i18n.data("nlu.weekdays", lang="ru"))

# Вид записи: код → слово языка (nlu.kind.<код>). По убыванию веса: в разговоре
# с делом — это дело.
KIND_CODES = ("work", "home", "leisure")

# Модели пишут «пустоту» словами: "null", "пусто", "некто", "название" (буквально
# из схемы). Общие знаки — здесь, слова языков — в каталогах (nlu.nullish).
_NULLISH = {"", "null", "none", "nil", "-", "—"} | {
    word for words in i18n.data_all("nlu.nullish") for word in words
}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# В цитате должно звучать время, иначе срок модель додумала («завтра» на всё подряд).
# Приметы срока — из каталогов всех языков (nlu.due_cues) плюс числовая дата.
_DUE_CUE = re.compile(
    "|".join(
        [cue for cues in i18n.data_all("nlu.due_cues") for cue in cues]
        + [r"\d{1,2}[./]\d{1,2}"]
    ),
    re.IGNORECASE,
)
GROUND_MIN = 0.8  # доля совпадения цитаты с расшифровкой (шум распознавания допустим)
# «позвони», «надо тебе» — не задачи: нужно хотя бы два значимых слова
_FILLER = {word for words in i18n.data_all("nlu.filler") for word in words}
MIN_TASK_WORDS = 2

# Пересборка 23.10.2026: детективная игра (#37) в длинном разговоре — часть кусков модель
# сочла «делом», и игровые «задачи» прошли. Узкий вопрос по каждой задаче малая модель
# решает надёжнее, чем вид всего куска внутри большого промпта (nlu.prompt.verify).
VERIFY_PROMPT = t("nlu.prompt.verify", lang="ru")


def kind_code(raw: str | None) -> str | None:
    """Вид записи на любом языке («дело», "work") → код; незнакомое — ``None``."""
    text = (raw or "").strip().lower()
    if text in KIND_CODES:
        return text
    for lang in i18n.available_languages():
        for code in KIND_CODES:
            word = i18n.catalog(lang).get(f"nlu.kind.{code}")
            if isinstance(word, str) and word.lower() == text:
                return code
    return None


def kind_word(code: str, lang: str | None = None) -> str:
    """Код вида → слово языка интерфейса: ``leisure`` → «досуг»."""
    return t(f"nlu.kind.{code}", lang=lang)


def _content_words(text: str) -> list[str]:
    return [w for w in _words(text) if len(w) >= 3 and w not in _FILLER]


def context_around(quote: str, source: str, radius: int = 6) -> str:
    """Строка расшифровки с цитатой и по ``radius`` реплик вокруг — контекст для проверки."""
    lines = [ln for ln in (source or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    best = max(range(len(lines)), key=lambda i: grounding(quote, lines[i]))
    return "\n".join(lines[max(0, best - radius) : best + radius + 1])


def _clean(value: Any) -> str | None:
    """Строка или None; «null», «пусто» и прочая словесная пустота — None."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower().strip(" .") in _NULLISH else text


def _words(text: str) -> list[str]:
    # Буквы любого алфавита и цифры: цитата бывает не на языке интерфейса.
    return re.findall(r"[^\W_]+", text.lower().replace("ё", "е"))


def grounding(quote: str, source: str) -> float:
    """Насколько цитата есть в расшифровке: лучшее совпадение окна той же длины, 0…1."""
    q, words = _words(quote or ""), _words(source or "")
    if len(q) < 2 or not words:
        return 0.0
    n, best = len(q), 0.0
    for i in range(max(1, len(words) - n + 1)):
        ratio = difflib.SequenceMatcher(None, q, words[i : i + n]).ratio()
        if ratio > best:
            best = ratio
            if best == 1.0:
                break
    return best


def _due(raw: Any, quote: str, day: date | None) -> str | None:
    due = _clean(raw)
    if not due or not _ISO_DATE.match(due) or not _DUE_CUE.search(quote or ""):
        return None
    try:
        when = date.fromisoformat(due)
    except ValueError:
        return None
    if day and not (day <= when <= day + timedelta(days=366)):
        return None
    return due


def strict_items(
    payload: dict[str, Any], source: str, day: date | None, *, lang: str | None = None
) -> tuple[list["NluFact"], list["NluTask"], str]:
    """Схема v2 → факты и задачи, подтверждённые расшифровкой ``source``.

    Пункт без цитаты или с цитатой, которой нет в расшифровке, отбрасывается.
    Досуг (игра, фильм) не даёт ни задач, ни фактов. Договорённости идут в факты
    с пометкой «Договорились:» — отдельной таблицы под них нет. Вид записи
    возвращается словом языка ``lang`` (по умолчанию — интерфейса), на каком
    бы языке его ни назвала модель.
    """
    raw_kind = _clean(payload.get("kind"))
    raw_kind = raw_kind.lower() if raw_kind else ""
    code = kind_code(raw_kind)
    kind = kind_word(code, lang) if code else raw_kind
    if code == "leisure":
        return [], [], kind

    def ok(item: Any) -> bool:
        return isinstance(item, dict) and grounding(str(item.get("quote") or ""), source) >= GROUND_MIN

    tasks: list[NluTask] = []
    for item in payload.get("tasks") or []:
        text = _clean(item.get("text")) if isinstance(item, dict) else None
        if not text or len(set(_content_words(text))) < MIN_TASK_WORDS or not ok(item):
            continue
        quote = str(item.get("quote"))
        tasks.append(
            NluTask(
                text=text,
                assignee=_clean(item.get("owner") or item.get("assignee")),
                due=_due(item.get("due"), quote, day),
                quote=quote,
            )
        )

    facts: list[NluFact] = []
    seen: set[str] = set()
    for item in payload.get("decisions") or []:
        text = _clean(item.get("text")) if isinstance(item, dict) else None
        if text and ok(item):
            facts.append(NluFact(text=t("nlu.agreed", lang=lang, text=text)))
            seen.add(text.lower())
    for item in payload.get("facts") or []:
        text = _clean(item.get("text")) if isinstance(item, dict) else None
        # «22800», «декабрь» — не утверждения: нужно хотя бы три слова
        if not text or len(_words(text)) < 3 or text.lower() in seen or not ok(item):
            continue
        seen.add(text.lower())
        raw_at = next((item[key] for key in _AT_KEYS if item.get(key) is not None), None)
        facts.append(NluFact(text=text, at=_parse_timecode(raw_at)))
    return facts, tasks, kind


# Повтор и сведение пересказов — тоже из каталога (nlu.prompt.retry / merge).
RETRY_PROMPT = t("nlu.prompt.retry", lang="ru")
MERGE_SYSTEM_PROMPT = t("nlu.prompt.merge", lang="ru")


class NluError(RuntimeError):
    """Разбор не удался. Транскрипт при этом сохраняется."""


@dataclass(frozen=True)
class NluTask:
    text: str
    assignee: str | None = None
    due: str | None = None
    # цитата-основание (схема v2): в базу не пишется, нужна проверке задачи
    quote: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class NluFact:
    text: str
    at: str | None = None  # "ЧЧ:ММ" из расшифровки, либо None


@dataclass(frozen=True)
class NluResult:
    summary: str
    facts: list[NluFact] = field(default_factory=list)
    tasks: list[NluTask] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    kind: str = ""  # схема v2: слово вида на языке разбора (дело / work…); пусто — старая схема


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# "9:05", "10:42" — но не "вечером" и не "25:99" (часы/минуты проверяются отдельно).
_TIMECODE = re.compile(r"^\d{1,2}:\d{2}$")

_AT_KEYS = ("at", "time", "таймкод")  # модели путаются в имени поля


def _parse_timecode(value: Any) -> str | None:
    """Приводит значение к ``ЧЧ:ММ`` с ведущим нулём, либо ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not _TIMECODE.match(text):
        return None
    hours, minutes = (int(part) for part in text.split(":"))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _as_facts(value: Any) -> list[NluFact]:
    """Факты в обеих формах: строка (старый формат) или объект с ``at``."""
    if not isinstance(value, list):
        return []
    facts: list[NluFact] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                facts.append(NluFact(text=text))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        raw_at = next(
            (item[key] for key in _AT_KEYS if item.get(key) is not None), None
        )
        facts.append(NluFact(text=text, at=_parse_timecode(raw_at)))
    return facts


def extract_json(text: str) -> dict[str, Any]:
    """Достаёт объект JSON из ответа модели.

    Модели любят обернуть ответ в ``` или предварить фразой «Вот результат:»,
    поэтому голого ``json.loads`` мало.
    """
    if not text or not text.strip():
        raise NluError("пустой ответ модели")

    candidates = [match.group(1) for match in _FENCE.finditer(text)]
    candidates.append(text.strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    # Без самого ответа: он пересказывает разговор, а ошибка уходит в журнал
    # (P11.2, находка 12).
    raise NluError(f"в ответе модели нет объекта JSON ({len(text)} символов)")


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate(
    payload: dict[str, Any],
) -> tuple[str, list[str], list[NluTask], list[str], list[str]]:
    """Приводит ответ модели к схеме. Лишнее отбрасывает, недостающее — пустое."""
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise NluError("в ответе модели нет summary")

    tasks: list[NluTask] = []
    for item in payload.get("tasks") or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                tasks.append(NluTask(text=text))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        due = _clean(item.get("due"))
        tasks.append(
            NluTask(
                text=text,
                assignee=_clean(item.get("assignee")),
                due=due if due and _ISO_DATE.match(due) else None,
            )
        )

    return (
        summary,
        _as_facts(payload.get("facts")),
        tasks,
        _as_str_list(payload.get("topics")),
        _as_str_list(payload.get("people")),
    )


class NluProcessor:
    """Расшифровка разговора → структура. Один повтор при невалидном ответе.

    Длинные разговоры режутся по строкам-репликам и разбираются по частям
    (ТЗ P5): контекст локальной модели — 8k токенов, и прежнее молчаливое
    обрезание хвоста теряло вторую половину дня целиком. Слияние кусков —
    без дублей; пересказы сводятся отдельным запросом, при его отказе —
    склейкой через пробел.
    """

    def __init__(
        self,
        router: LlmRouter,
        *,
        max_chars: int = MAX_TRANSCRIPT_CHARS,
        user_role: str | None = None,
        lang: str | None = None,
    ) -> None:
        self.router = router
        self.max_chars = max_chars
        # Язык промптов и вывода; по умолчанию — язык интерфейса.
        self.lang = i18n.resolve(lang) if lang else i18n.get_language()
        self.user_role = (user_role or "").strip() or t("nlu.default_role", lang=self.lang)

    def analyse(
        self, transcript: str, *, allow_cloud: bool = False, day: date | None = None
    ) -> NluResult:
        """Разбирает расшифровку разговора в структуру.

        ``allow_cloud`` по умолчанию ``False``: на входе целиком рабочий
        разговор, и молчаливая отправка его в облачную модель была бы той же
        утечкой, что и звук в облако. Разрешение передаётся явно.
        """
        transcript = (transcript or "").strip()
        if not transcript:
            raise NluError("пустая расшифровка")

        chunks = self._split_chunks(transcript)
        if len(chunks) > 1:
            log.info(
                "расшифровка %d символов режется на %d кусков (предел %d)",
                len(transcript),
                len(chunks),
                self.max_chars,
            )

        summaries: list[str] = []
        facts: list[NluFact] = []
        tasks: list[NluTask] = []
        topics: list[str] = []
        people: list[str] = []
        seen_facts: set[tuple[str, str | None]] = set()
        seen_tasks: set[tuple[str, str | None, str | None]] = set()
        backend = model = ""
        kinds: set[str] = set()
        last_error: Exception | None = None

        for chunk in chunks:
            try:
                parsed = self._analyse_chunk(chunk, allow_cloud=allow_cloud, day=day)
            except NluError as exc:
                last_error = exc
                log.warning("кусок расшифровки не разобрался: %s", exc)
                continue

            if not backend:  # итог — от первого удачного куска
                backend, model = parsed.backend, parsed.model
            if parsed.kind:
                kinds.add(parsed.kind)
            summaries.append(parsed.summary)
            for fact in parsed.facts:
                key = (fact.text, fact.at)
                if key not in seen_facts:
                    seen_facts.add(key)
                    facts.append(fact)
            for task in parsed.tasks:
                key = (task.text, task.assignee, task.due)
                if key not in seen_tasks:
                    seen_tasks.add(key)
                    tasks.append(task)
            for topic in parsed.topics:
                if topic not in topics:
                    topics.append(topic)
            for name in parsed.people:
                if name not in people:
                    people.append(name)

        if not summaries:
            raise NluError(f"ни один кусок расшифровки не разобрался: {last_error}")

        summary = (
            summaries[0]
            if len(summaries) == 1
            else self._merge_summaries(summaries, allow_cloud=allow_cloud)
        )
        return NluResult(
            summary=summary,
            facts=facts,
            tasks=tasks,
            topics=topics,
            people=people,
            backend=backend,
            model=model,
            kind=next(
                (
                    word
                    for word in (kind_word(code, self.lang) for code in KIND_CODES)
                    if word in kinds
                ),
                next(iter(kinds), ""),
            ),
        )

    def _split_chunks(self, transcript: str) -> list[str]:
        """Режет по строкам-репликам; реплика длиннее предела — отдельный кусок.

        Резать реплику пополам нельзя: в её начале таймкод ``[ЧЧ:ММ]``, по
        которому факты получают абсолютное время.
        """
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in transcript.splitlines():
            extra = len(line) if not current else 1 + len(line)
            if current and current_len + extra > self.max_chars:
                chunks.append("\n".join(current))
                current, current_len = [], 0
                extra = len(line)
            current.append(line)
            current_len += extra
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _analyse_chunk(
        self, chunk: str, *, allow_cloud: bool, day: date | None = None
    ) -> NluResult:
        """Разбор одного куска: та же логика с одним повтором, что и раньше."""
        weekdays = i18n.data("nlu.weekdays", lang=self.lang)
        system = t(
            "nlu.prompt.system",
            lang=self.lang,
            role=self.user_role,
            date=day.isoformat() if day else t("nlu.date_unknown", lang=self.lang),
            weekday=weekdays[day.weekday()]
            if day
            else t("nlu.weekday_unknown", lang=self.lang),
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": chunk},
        ]

        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                # json_mode: локальная 7B без него регулярно оборачивает JSON
                # в пояснения. Облачные бэкенды параметр игнорируют.
                result = self.router.complete(
                    messages,
                    allow_cloud=allow_cloud,
                    json_mode=True,
                    max_tokens=NLU_MAX_TOKENS,
                )
            except LlmLocalFused:
                # Предохранитель — стоп-сигнал, а не «разбор не удался»:
                # поднимаем наверх, воркер остановит фазу, разговор останется
                # открытым и дождётся починки локальной модели.
                raise
            except LlmError as exc:
                raise NluError(f"модель недоступна: {exc}") from exc

            kind = ""
            try:
                payload = extract_json(result.text)
                summary, facts, tasks, topics, people = validate(payload)
                if "kind" in payload:  # схема v2 — только подтверждённое расшифровкой
                    facts, tasks, kind = strict_items(payload, chunk, day, lang=self.lang)
                    tasks = self._verify_tasks(tasks, chunk, allow_cloud=allow_cloud)
            except NluError as exc:
                last_error = exc
                log.warning("попытка %d: ответ не прошёл проверку — %s", attempt, exc)
                if attempt == 1:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": result.text[:2000]},
                        {"role": "user", "content": t("nlu.prompt.retry", lang=self.lang)},
                    ]
                continue

            return NluResult(
                summary=summary,
                facts=facts,
                tasks=tasks,
                topics=topics,
                people=people,
                backend=result.backend,
                model=result.model,
                kind=kind,
            )

        raise NluError(f"ответ модели не прошёл проверку дважды: {last_error}")

    def _verify_tasks(
        self, tasks: list[NluTask], chunk: str, *, allow_cloud: bool
    ) -> list[NluTask]:
        """Узкий вопрос по каждой задаче: реальное (A), игра/фильм (B), не задача (C).

        Остаются только A. Сбой проверки задачу не отбрасывает — без неё
        поведение то же, что до 23.10.2026."""
        kept: list[NluTask] = []
        for task in tasks:
            question = t(
                "nlu.prompt.verify_question",
                lang=self.lang,
                task=task.text,
                quote=task.quote or "",
                context=context_around(task.quote or task.text, chunk),
            )
            try:
                answer = self.router.complete(
                    [
                        {"role": "system", "content": t("nlu.prompt.verify", lang=self.lang)},
                        {"role": "user", "content": question},
                    ],
                    allow_cloud=allow_cloud,
                    json_mode=True,
                    max_tokens=32,
                )
                verdict = str(extract_json(answer.text).get("class", "A")).strip().upper()[:1]
            except LlmLocalFused:
                raise
            except (LlmError, NluError) as exc:
                log.warning("проверка задачи не удалась, задача оставлена: %s", exc)
                verdict = "A"
            if verdict == "A":
                kept.append(task)
        return kept

    def _merge_summaries(self, summaries: list[str], *, allow_cloud: bool) -> str:
        """Сводит кусочные пересказы в один. Отказ модели — склейка через пробел."""
        listing = "\n".join(
            f"{index}. {text}" for index, text in enumerate(summaries, start=1)
        )
        messages = [
            {"role": "system", "content": t("nlu.prompt.merge", lang=self.lang)},
            {"role": "user", "content": listing},
        ]
        try:
            result = self.router.complete(messages, allow_cloud=allow_cloud)
        except LlmLocalFused:
            raise  # стоп-сигнал важнее склейки: разбор остановлен целиком
        except LlmError as exc:
            log.warning("сведение пересказов не удалось, склеиваю как есть: %s", exc)
            return " ".join(summaries)

        merged = result.text.strip()
        if not merged:
            log.warning("сведение пересказов дало пустой ответ, склеиваю как есть")
            return " ".join(summaries)
        return merged


def store_result(
    storage,
    conversation_id: int,
    result: NluResult,
    *,
    time_map: dict[str, float] | None = None,
) -> None:
    """Раскладывает разбор по таблицам и закрывает разговор.

    Идемпотентна: воркер может упасть между записью фактов и закрытием
    разговора, и повторный прогон не имеет права удвоить их. Удалять записанное
    нельзя — значит, не вставляем повтор.

    ``time_map`` ставит каждому факту абсолютное время: ``"ЧЧ:ММ" → timestamp``
    реплики этого разговора. Факт с таймкодом, которого в разговоре нет, —
    единственный автоматический признак связной галлюцинации, поэтому он
    сохраняется (с ``said_at=None``) и оставляет предупреждение в журнале.
    """
    moments = time_map or {}
    known_facts = set(storage.facts_of(conversation_id))
    for fact in result.facts:
        # Воркер и старые тесты приносят факты строками — перевариваем обе формы.
        if isinstance(fact, NluFact):
            text, at = fact.text, fact.at
        else:
            text, at = str(fact).strip(), None
        if not text or text in known_facts:  # сравнение по тексту как есть
            continue
        said_at = moments.get(at) if at else None
        fact_id = storage.add_fact(conversation_id, text, said_at=said_at)
        if at is not None and said_at is None:
            # P11.2, находка 12: в журнал — id и таймкод, не текст разговора.
            log.warning(
                "факт %d со временем %s не нашёлся в разговоре %d",
                fact_id,
                at,
                conversation_id,
            )
        known_facts.add(text)

    known_tasks = {
        (row["text"], row["assignee"], row["due"])
        for row in storage.tasks_of(conversation_id)
    }
    for task in result.tasks:
        key = (task.text, task.assignee, task.due)
        if key in known_tasks:
            continue
        storage.add_task(
            conversation_id, task.text, assignee=task.assignee, due=task.due
        )
        known_tasks.add(key)

    storage.close_conversation(conversation_id, summary=result.summary)
