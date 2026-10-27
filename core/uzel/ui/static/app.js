// УЗЕЛ — локальная логика панели, без внешних зависимостей.
"use strict";

const STATUS_INTERVAL_MS = 3000;
const state = {
  day: todayIso(),
  expanded: {},
  transcripts: {},
  lastStatus: null,
  dayRequest: 0,
  qualityRequest: 0,
  searchRequest: 0,
  statusRequest: 0,
  privacyRevision: 0,
  busy: false,
};
const els = {
  version: document.getElementById("version"),
  recording: document.getElementById("recording"),
  permission: document.getElementById("permission"),
  queue: document.getElementById("queue"),
  device: document.getElementById("device"),
  db: document.getElementById("db"),
  windows: document.getElementById("windows"),
  worker: document.getElementById("worker"),
  btnMute: document.getElementById("btn-mute"),
  btnPause: document.getElementById("btn-pause"),
  btnResume: document.getElementById("btn-resume"),
  actionMessage: document.getElementById("action-message"),
  dayDate: document.getElementById("day-date"),
  dayPrev: document.getElementById("day-prev"),
  dayNext: document.getElementById("day-next"),
  dayToday: document.getElementById("day-today"),
  daySummary: document.getElementById("day-summary"),
  dayFeed: document.getElementById("day-feed"),
  dayTasks: document.getElementById("day-tasks"),
  tasksCount: document.getElementById("tasks-count"),
  searchForm: document.getElementById("search-form"),
  searchQ: document.getElementById("search-q"),
  searchNote: document.getElementById("search-note"),
  searchResults: document.getElementById("search-results"),
  qualityBody: document.getElementById("quality-body"),
};

// ---------- утилиты ----------

function todayIso() {
  return dateIso(new Date());
}

function dateIso(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
}

function shiftIso(iso, days) {
  const [year, month, day] = iso.split("-").map(Number);
  return dateIso(new Date(year, month - 1, day + days));
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

function countLabel(n, one, few, many) {
  return `${n} ${plural(n, one, few, many)}`;
}

function pluralWith(n, forms) {
  return countLabel(n, ...forms);
}

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function errorText(value) {
  if (typeof value === "string") return value;
  if (value && typeof value.message === "string") return value.message;
  return "";
}

async function fetchJson(url, options) {
  try {
    const response = await fetch(url, options);
    let data;
    try {
      data = await response.json();
    } catch (error) {
      return { ok: false, status: response.status, detail: "Программа вернула непонятный ответ.", data: null };
    }
    if (!response.ok || !data || typeof data !== "object" || Array.isArray(data) || data.error) {
      return { ok: false, status: response.status, detail: errorText(data?.detail || data?.error) || "Не удалось получить данные.", data: null };
    }
    return { ok: true, status: response.status, detail: "", data };
  } catch (error) {
    return { ok: false, status: 0, detail: "Нет ответа от программы. Попробуйте ещё раз.", data: null };
  }
}

function untilLabel(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return dateIso(date) === todayIso() ? time : `${date.toLocaleDateString("ru-RU")} в ${time}`;
}

function create(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className || "";
  if (text !== undefined) element.textContent = text;
  return element;
}

function emptyState(target, title, note = "", error = false) {
  target.textContent = "";
  const block = create("div", "empty-state" + (error ? " is-error" : ""));
  block.appendChild(create("strong", "", title));
  if (note) block.appendChild(create("span", "", note));
  target.appendChild(block);
}

function setHtml(target, html) {
  if (target.innerHTML !== html) target.innerHTML = html;
}

// ---------- связь и разрешение записи ----------

function updatePrivacyButtons() {
  const code = state.lastStatus?.privacy?.код;
  const unavailable = !state.lastStatus?.privacy || state.busy;
  els.btnMute.disabled = unavailable || code === "paused";
  els.btnPause.disabled = unavailable || code === "paused";
  els.btnResume.disabled = unavailable || code === "allowed";
}

function renderStatus(status) {
  state.lastStatus = status && status.privacy ? status : null;
  if (!state.lastStatus) {
    els.recording.className = "plate plate-offline";
    setHtml(els.recording, "Программа не отвечает<small>Не удалось проверить связь и разрешение записи. Управление станет доступно, когда программа ответит.</small>");
    els.permission.textContent = "Состояние неизвестно";
    els.queue.textContent = "Очередь недоступна";
    els.device.textContent = "Связь неизвестна · заряд неизвестен";
    els.version.textContent = "Версия недоступна";
    els.db.textContent = "Нет данных";
    els.windows.textContent = "Нет данных";
    els.worker.textContent = "Нет данных";
    updatePrivacyButtons();
    return;
  }

  const privacy = status.privacy;
  const code = privacy.код;
  const device = status.device || {};
  const fresh = device.known === true && device.connected === true && device.stale === false;
  const hasAudio = fresh && number(device.packets) > 0;
  const until = untilLabel(privacy.до);
  let title, note, tone;
  if (code === "allowed") {
    if (hasAudio) {
      title = "Кулон передаёт звук";
      note = "Связь активна. Аудиофрагменты поступают в программу.";
      tone = "recording";
    } else if (fresh) {
      title = "Кулон подключён";
      note = "Связь есть, ожидаем звук. Аудиофрагменты пока не поступают.";
      tone = "waiting";
    } else {
      title = "Нет связи с кулоном";
      note = "Новое аудио не поступает. Проверьте питание кулона, Bluetooth на ПК и подключение телефона.";
      tone = "unknown";
    }
    els.permission.textContent = "Запись разрешена";
  } else if (code === "manual" || code === "muted") {
    title = "Запись на паузе";
    note = until ? `Пауза до ${until}. Разрешение записи восстановится автоматически.` : "Временная пауза. Время окончания неизвестно.";
    tone = "muted";
    els.permission.textContent = until ? `Пауза до ${until}` : "Временная пауза";
  } else if (code === "paused") {
    title = "Запись выключена";
    note = "Запись можно включить кнопкой «Возобновить запись».";
    if (until) note += ` Указанное время: ${until}.`;
    tone = "muted";
    els.permission.textContent = "Выключена вручную";
  } else if (code === "schedule") {
    title = "Запись выключена";
    note = until ? `Действует окно без записи до ${until}.` : "Действует окно без записи по расписанию.";
    tone = "muted";
    els.permission.textContent = "Выключена по расписанию";
  } else {
    title = "Запись недоступна";
    note = privacy.причина || "Не удалось определить разрешение записи. Проверьте состояние программы в диагностике.";
    tone = "unknown";
    els.permission.textContent = "Разрешение не подтверждено";
  }
  els.recording.className = `plate plate-${tone}`;
  setHtml(els.recording, `${escapeHtml(title)}<small>${escapeHtml(note)}</small>`);

  let battery = "<small>Заряд неизвестен</small>";
  const percent = device.battery_percent;
  if (fresh && typeof percent === "number" && Number.isFinite(percent) && percent >= 0 && percent <= 100) {
    battery = `<div class="battery"><progress max="100" value="${percent}" aria-label="Заряд кулона"></progress><span>Заряд ${percent}%</span></div>`;
  }
  setHtml(els.device, `<strong>${fresh ? "На связи" : "Нет свежей связи"}</strong>${battery}`);
  const queue = status.queue || {};
  const pending = number(queue.pending);
  const processing = number(queue.processing);
  setHtml(els.queue, `<strong>${pending === 0 && processing === 0 ? "Очередь пуста" : pluralWith(pending, ["фрагмент ждёт", "фрагмента ждут", "фрагментов ждут"])}</strong><small>${processing ? `В обработке: ${processing}` : "Нет фрагментов в обработке"}</small>`);

  els.version.textContent = status.version ? `Версия ${status.version}` : "Версия неизвестна";
  const db = status.db || {};
  els.db.textContent = db.error ? "База недоступна" : `${pluralWith(number(db.conversations), ["разговор", "разговора", "разговоров"])}, ${pluralWith(number(db.facts), ["факт", "факта", "фактов"])}, ${pluralWith(number(db.tasks), ["задача", "задачи", "задач"])}`;
  const windows = list(status.quiet_windows);
  els.windows.textContent = windows.length ? windows.map(item => typeof item === "string" ? item : item.title || item.name || "Окно по расписанию").join("; ") : "Окна не заданы";
  const worker = status.worker || {};
  els.worker.textContent = worker.stop_file === true ? "Обработка приостановлена" : worker.stop_file === false ? "Автоматическая обработка разрешена" : "Состояние обработки неизвестно";
  updatePrivacyButtons();
}

async function refreshStatus() {
  if (state.busy) return;
  const request = ++state.statusRequest;
  const revision = state.privacyRevision;
  const result = await fetchJson("/ui/api/status");
  if (request !== state.statusRequest || revision !== state.privacyRevision || state.busy) return;
  renderStatus(result.ok ? result.data : null);
}

async function privacyAction(path) {
  if (state.busy || !state.lastStatus?.privacy) return;
  state.busy = true;
  state.privacyRevision++;
  updatePrivacyButtons();
  els.actionMessage.className = "action-message";
  els.actionMessage.textContent = "Меняем режим записи…";
  const result = await fetchJson(path, { method: "POST" });
  state.busy = false;
  if (result.ok && typeof result.data.код === "string") {
    renderStatus({ ...state.lastStatus, privacy: result.data });
    const code = result.data.код;
    els.actionMessage.textContent = code === "allowed" ? "Запись разрешена. Состояние связи с кулоном показано выше." : code === "paused" ? "Запись выключена. Для включения нажмите «Возобновить запись»." : code === "manual" || code === "muted" ? "Пауза включена. Разрешение восстановится автоматически." : "Разрешение обновлено. Текущее состояние показано выше.";
  } else {
    els.actionMessage.className = "action-message is-error";
    els.actionMessage.textContent = `Не удалось изменить режим записи. ${result.detail || "Программа вернула непонятный ответ."}`;
    updatePrivacyButtons();
  }
}

// ---------- выбранный день ----------

function setDay(date) {
  state.day = date || todayIso();
  state.expanded = {};
  state.transcripts = {};
  els.dayDate.value = state.day;
  loadDay();
  loadQuality();
}

async function loadDay() {
  const date = state.day;
  const request = ++state.dayRequest;
  els.daySummary.textContent = "Загружаем сводку…";
  emptyState(els.dayFeed, "Загружаем разговоры…");
  emptyState(els.dayTasks, "Загружаем задачи…");
  els.tasksCount.textContent = "—";
  els.dayFeed.setAttribute("aria-busy", "true");
  els.dayTasks.setAttribute("aria-busy", "true");
  const result = await fetchJson(`/ui/api/day?date=${encodeURIComponent(date)}`);
  if (request !== state.dayRequest || date !== state.day) return;
  els.dayFeed.setAttribute("aria-busy", "false");
  els.dayTasks.setAttribute("aria-busy", "false");
  if (!result.ok) {
    els.daySummary.textContent = "Сводка недоступна";
    emptyState(els.dayFeed, "Не удалось загрузить разговоры", result.detail, true);
    emptyState(els.dayTasks, "Не удалось загрузить задачи", "Попробуйте выбрать дату ещё раз.", true);
    return;
  }
  const data = result.data;
  const conversations = list(data.conversations);
  const tasks = list(data.tasks);
  const totals = data.totals || {};
  const summary = [
    [number(totals.conversations ?? conversations.length), ["разговор", "разговора", "разговоров"]],
    [number(totals.facts ?? list(data.facts).length), ["факт", "факта", "фактов"]],
    [number(totals.tasks ?? tasks.length), ["задача", "задачи", "задач"]],
  ];
  els.daySummary.innerHTML = summary.map(([count, forms]) => `<span class="stat-pill"><strong>${count}</strong> ${plural(count, ...forms)}</span>`).join("");
  if (totals.speech) els.daySummary.appendChild(create("span", "stat-pill", `Речи: ${totals.speech}`));
  els.dayFeed.textContent = "";
  if (conversations.length) {
    for (const conversation of conversations) els.dayFeed.appendChild(renderConversation(conversation));
  } else {
    emptyState(els.dayFeed, "Разговоров пока нет", date === todayIso() ? "Здесь появятся разговоры, сохранённые сегодня." : "За выбранный день разговоры не сохранены.");
  }
  els.dayTasks.textContent = "";
  els.tasksCount.textContent = String(tasks.length);
  if (tasks.length) {
    for (const task of tasks) els.dayTasks.appendChild(renderTask(task));
  } else {
    emptyState(els.dayTasks, "Задач пока нет", "Здесь появятся задачи, выделенные из разговоров за этот день.");
  }
}

function conversationStatus(code) {
  return ({ closed: "Завершён", open: "Идёт", processing: "Обрабатывается", pending: "Ожидает обработки", queued: "В очереди", transcribing: "Расшифровывается", completed: "Завершён", done: "Завершён", error: "Ошибка обработки", failed: "Ошибка обработки", archived: "В архиве" })[code] || "Состояние неизвестно";
}

function taskStatus(code) {
  return ({ done: "Выполнена", completed: "Выполнена", open: "К выполнению", todo: "К выполнению", pending: "К выполнению", new: "К выполнению", in_progress: "В работе", processing: "В работе", cancelled: "Отменена", canceled: "Отменена" })[code] || "Состояние не указано";
}

function renderTask(task) {
  const row = create("div", "task-row");
  const done = task.status === "done" || task.status === "completed";
  const head = create("div", "task-head");
  const mark = create("span", "task-mark", done ? "✓" : "•");
  mark.setAttribute("aria-hidden", "true");
  head.appendChild(mark);
  head.appendChild(create("span", "task-text", task.text || "Задача без описания"));
  row.appendChild(head);
  const meta = [];
  if (task.assignee) meta.push(`Исполнитель: ${task.assignee}`);
  if (task.due) meta.push(`Срок: ${task.due}`);
  if (task.exported) meta.push("Выгружена");
  if (meta.length) row.appendChild(create("div", "task-meta", meta.join(" · ")));
  row.appendChild(create("span", "task-status", taskStatus(task.status)));
  return row;
}

function renderConversation(conversation) {
  const block = create("article", "conversation");
  block.dataset.id = String(conversation.id);
  const head = create("div", "conversation-head");
  head.innerHTML = `<span class="conversation-time">${escapeHtml(conversation.started || "Время неизвестно")}${conversation.ended ? `–${escapeHtml(conversation.ended)}` : ""}</span><span class="conversation-status">${escapeHtml(conversationStatus(conversation.status))}</span>${conversation.duration ? `<span class="conversation-duration">${escapeHtml(conversation.duration)}</span>` : ""}`;
  block.appendChild(head);
  if (conversation.summary) block.appendChild(create("p", "conversation-summary", conversation.summary));
  else block.appendChild(create("p", "conversation-summary muted", "Краткое содержание ещё не готово."));
  const chips = create("div", "chips");
  for (const tag of [...list(conversation.tags), ...list(conversation.participants)]) chips.appendChild(create("span", "chip", tag));
  if (chips.children.length) block.appendChild(chips);
  const counts = [number(conversation.utterances), Array.isArray(conversation.facts) ? conversation.facts.length : number(conversation.facts), Array.isArray(conversation.tasks) ? conversation.tasks.length : number(conversation.tasks)];
  block.appendChild(create("div", "conversation-counters", `${pluralWith(counts[0], ["реплика", "реплики", "реплик"])} · ${pluralWith(counts[1], ["факт", "факта", "фактов"])} · ${pluralWith(counts[2], ["задача", "задачи", "задач"])}`));
  const button = create("button", "conversation-toggle", "Открыть разговор");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  const details = create("div", "conversation-details");
  details.id = `conversation-details-${conversation.id}`;
  details.hidden = true;
  button.setAttribute("aria-controls", details.id);
  button.addEventListener("click", () => toggleConversation(block, conversation.id));
  block.appendChild(button);
  block.appendChild(details);
  return block;
}

async function toggleConversation(block, id) {
  const details = block.querySelector(".conversation-details");
  const button = block.querySelector(".conversation-toggle");
  const willOpen = details.hidden;
  details.hidden = !willOpen;
  button.setAttribute("aria-expanded", String(willOpen));
  button.textContent = willOpen ? "Свернуть" : "Открыть разговор";
  if (!willOpen || details.dataset.loaded || details.dataset.loading) return;
  if (state.expanded[id]) {
    fillConversationDetails(details, state.expanded[id], id);
    details.dataset.loaded = "true";
    return;
  }
  const date = state.day;
  const generation = state.dayRequest;
  details.dataset.loading = "true";
  details.setAttribute("aria-busy", "true");
  details.textContent = "Загружаем факты и задачи…";
  const result = await fetchJson(`/ui/api/conversation/${encodeURIComponent(id)}`);
  delete details.dataset.loading;
  details.setAttribute("aria-busy", "false");
  if (date !== state.day || generation !== state.dayRequest) return;
  details.textContent = "";
  if (!result.ok) {
    emptyState(details, "Не удалось открыть разговор", `${result.detail} Сверните и откройте разговор, чтобы повторить.`, true);
    return;
  }
  state.expanded[id] = result.data;
  details.dataset.loaded = "true";
  fillConversationDetails(details, result.data, id);
}

function fillConversationDetails(details, data, id = data.id) {
  details.textContent = "";
  const content = create("div", "detail-content");
  content.setAttribute("tabindex", "0");
  content.setAttribute("aria-label", "Факты и задачи разговора");
  const facts = list(data.facts);
  const tasks = list(data.tasks);
  content.appendChild(create("h3", "details-title", `Факты · ${facts.length}`));
  if (!facts.length) content.appendChild(create("p", "muted", "Факты пока не выделены."));
  for (const fact of facts) {
    const row = create("div", "fact-row");
    row.innerHTML = `${fact.said ? `<span class="fact-time">${escapeHtml(fact.said)}</span>` : ""}${escapeHtml(fact.text)}`;
    content.appendChild(row);
  }
  content.appendChild(create("h3", "details-title", `Задачи · ${tasks.length}`));
  if (!tasks.length) content.appendChild(create("p", "muted", "Задачи пока не выделены."));
  for (const task of tasks) content.appendChild(renderTask(task));
  details.appendChild(content);
  const button = create("button", "show-transcript", "Показать расшифровку");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", `transcript-${id}`);
  button.addEventListener("click", () => loadTranscript(details, id, button));
  details.appendChild(button);
  const message = create("div", "detail-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  details.appendChild(message);
}

async function loadTranscript(details, id, button) {
  if (button.disabled) return;
  if (Object.prototype.hasOwnProperty.call(state.transcripts, id)) {
    renderTranscript(details, id, button);
    return;
  }
  const date = state.day;
  const generation = state.dayRequest;
  const message = details.querySelector(".detail-message");
  button.disabled = true;
  message.textContent = "Загружаем расшифровку…";
  const result = await fetchJson(`/ui/api/conversation/${encodeURIComponent(id)}?with_utterances=true`);
  button.disabled = false;
  if (date !== state.day || generation !== state.dayRequest) return;
  if (!result.ok) {
    message.textContent = `Не удалось загрузить расшифровку. ${result.detail} Нажмите кнопку, чтобы повторить.`;
    return;
  }
  message.textContent = "";
  state.transcripts[id] = list(result.data.transcript);
  renderTranscript(details, id, button);
}

function privateBadge(value) {
  return value === true ? '<span class="private-badge">Приватная запись</span>' : "";
}

function renderTranscript(details, id, button) {
  const existing = details.querySelector(".transcript");
  if (existing) {
    existing.remove();
    button.textContent = "Показать расшифровку";
    button.setAttribute("aria-expanded", "false");
    return;
  }
  const rows = state.transcripts[id] || [];
  const box = create("div", "transcript");
  box.id = `transcript-${id}`;
  box.setAttribute("tabindex", "0");
  box.setAttribute("aria-label", "Расшифровка разговора");
  if (!rows.length) box.textContent = "Расшифровка пока недоступна.";
  for (const row of rows) {
    const line = create("div", "transcript-row");
    line.innerHTML = `<span class="transcript-time">${escapeHtml(row.started)}</span>${row.speaker ? `<span class="transcript-speaker">${escapeHtml(row.speaker)}:</span>` : ""}${privateBadge(row.is_private)}<div>${escapeHtml(row.text)}</div>`;
    box.appendChild(line);
  }
  details.appendChild(box);
  button.textContent = "Скрыть расшифровку";
  button.setAttribute("aria-expanded", "true");
}

// ---------- поиск ----------

async function runSearch() {
  const query = els.searchQ.value.trim();
  const request = ++state.searchRequest;
  els.searchResults.textContent = "";
  els.searchResults.setAttribute("aria-busy", query ? "true" : "false");
  if (!query) {
    els.searchNote.textContent = "Введите слово, тему или фразу.";
    return;
  }
  els.searchNote.textContent = "Ищем по разговорам…";
  const result = await fetchJson(`/ui/api/search?q=${encodeURIComponent(query)}&limit=20`);
  if (request !== state.searchRequest) return;
  els.searchResults.setAttribute("aria-busy", "false");
  if (!result.ok) {
    els.searchNote.textContent = `Поиск не удался. ${result.detail}`;
    return;
  }
  const rows = list(result.data.results);
  els.searchNote.textContent = rows.length ? `${pluralWith(rows.length, ["результат", "результата", "результатов"])}${rows.length === 20 ? " · показаны первые 20" : ""}` : "Ничего не найдено. Попробуйте другое слово или фразу.";
  for (const row of rows) {
    const item = create("div", "search-result");
    const meta = [row.started, row.speaker || "Говорящий не указан", row.conversation_id ? `Разговор №${row.conversation_id}` : ""].filter(Boolean);
    item.innerHTML = `<div class="search-meta">${escapeHtml(meta.join(" · "))}${privateBadge(row.is_private)}</div><div class="search-text">${escapeHtml(row.text)}</div>`;
    els.searchResults.appendChild(item);
  }
}

// ---------- диагностика ----------

async function loadQuality() {
  const date = state.day;
  const request = ++state.qualityRequest;
  els.qualityBody.textContent = "Загружаем статистику…";
  const result = await fetchJson(`/ui/api/quality?date=${encodeURIComponent(date)}`);
  if (request !== state.qualityRequest || date !== state.day) return;
  els.qualityBody.textContent = "";
  if (!result.ok) {
    emptyState(els.qualityBody, "Статистика недоступна", result.detail, true);
    return;
  }
  const data = result.data;
  const totals = create("div", "quality-totals");
  for (const [num, label] of [[data.total, "Реплик всего"], [data.accepted, "Принято"], [data.rejected, "Отклонено"], [data.flagged, "Помечено"]]) {
    const cell = create("div", "quality-total");
    cell.innerHTML = `<div class="num">${number(num)}</div><div class="label">${label}</div>`;
    totals.appendChild(cell);
  }
  els.qualityBody.appendChild(totals);
  for (const reason of list(data.by_reason)) {
    const row = create("div", "quality-reason");
    row.innerHTML = `<span>${escapeHtml(reason.title)}</span><span>${number(reason.count)}</span>`;
    els.qualityBody.appendChild(row);
  }
  if (!number(data.total)) els.qualityBody.appendChild(create("p", "muted", "За этот день обработанных реплик пока нет."));
  else if (!list(data.by_reason).length) els.qualityBody.appendChild(create("p", "muted", "Причины отклонения и пометки не зафиксированы."));
  const filter = data.enforce === true ? "Фильтр применяется: сомнительные реплики могут быть отклонены." : data.enforce === false ? "Фильтр в режиме наблюдения: пометки не блокируют реплики." : "Режим фильтра неизвестен.";
  els.qualityBody.appendChild(create("p", "quality-note", filter));
  if (data.note) els.qualityBody.appendChild(create("p", "quality-note", data.note));
}

// ---------- запуск ----------

function init() {
  els.dayDate.value = state.day;
  els.btnMute.addEventListener("click", () => privacyAction("/privacy/mute?minutes=30"));
  els.btnPause.addEventListener("click", () => privacyAction("/privacy/pause"));
  els.btnResume.addEventListener("click", () => privacyAction("/privacy/unmute"));
  els.dayDate.addEventListener("change", () => setDay(els.dayDate.value));
  els.dayPrev.addEventListener("click", () => setDay(shiftIso(state.day, -1)));
  els.dayNext.addEventListener("click", () => setDay(shiftIso(state.day, 1)));
  els.dayToday.addEventListener("click", () => setDay(todayIso()));
  els.searchForm.addEventListener("submit", event => {
    event.preventDefault();
    runSearch();
  });
  refreshStatus();
  setInterval(refreshStatus, STATUS_INTERVAL_MS);
  loadDay();
  loadQuality();
}

document.addEventListener("DOMContentLoaded", init);
