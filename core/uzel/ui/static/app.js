// УЗЕЛ — локальная логика панели, без внешних зависимостей.
"use strict";

// Строки интерфейса подставляет сервер в window.UZEL_I18N (каталог ui.* языка
// интерфейса). Нет словаря — видны ключи: так недоперевод заметен сразу.
const I18N = globalThis.UZEL_I18N || { lang: "en", strings: {} };

function fill(template, params) {
  return String(template).replace(/\{\{|\}\}|\{(\w+)\}/g, (match, name) => {
    if (match === "{{") return "{";
    if (match === "}}") return "}";
    return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match;
  });
}

function t(key, params = {}) {
  const value = I18N.strings[key];
  return typeof value === "string" ? fill(value, params) : key;
}

function pluralCategory(n) {
  try {
    return new Intl.PluralRules(I18N.lang).select(n);
  } catch (error) {
    return n === 1 ? "one" : "other";
  }
}

// Формы множественного числа: { one, few, many, other } — по правилам языка.
function tn(key, n, params = {}) {
  const value = I18N.strings[key];
  if (typeof value === "string") return fill(value, { n, ...params });
  if (!value || typeof value !== "object") return key;
  const form = value[pluralCategory(n)] ?? value.other ?? Object.values(value)[0];
  return fill(form, { n, ...params });
}

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
  langSelect: document.getElementById("lang-select"),
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
      return { ok: false, status: response.status, detail: t("ui.err.bad_response"), data: null };
    }
    if (!response.ok || !data || typeof data !== "object" || Array.isArray(data) || data.error) {
      return { ok: false, status: response.status, detail: errorText(data?.detail || data?.error) || t("ui.err.no_data"), data: null };
    }
    return { ok: true, status: response.status, detail: "", data };
  } catch (error) {
    return { ok: false, status: 0, detail: t("ui.err.no_answer"), data: null };
  }
}

function untilLabel(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString(I18N.lang, { hour: "2-digit", minute: "2-digit" });
  return dateIso(date) === todayIso() ? time : t("ui.until.other_day", { date: date.toLocaleDateString(I18N.lang), time });
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
    setHtml(els.recording, `${escapeHtml(t("ui.status.offline.title"))}<small>${escapeHtml(t("ui.status.offline.note"))}</small>`);
    els.permission.textContent = t("ui.perm.unknown");
    els.queue.textContent = t("ui.queue.unavailable");
    els.device.textContent = t("ui.device.unknown");
    els.version.textContent = t("ui.version.unavailable");
    els.db.textContent = t("ui.no_data");
    els.windows.textContent = t("ui.no_data");
    els.worker.textContent = t("ui.no_data");
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
      title = t("ui.rec.streaming.title");
      note = t("ui.rec.streaming.note");
      tone = "recording";
    } else if (fresh) {
      title = t("ui.rec.connected.title");
      note = t("ui.rec.connected.note");
      tone = "waiting";
    } else {
      title = t("ui.rec.offline.title");
      note = t("ui.rec.offline.note");
      tone = "unknown";
    }
    els.permission.textContent = t("ui.perm.allowed");
  } else if (code === "manual" || code === "muted") {
    title = t("ui.rec.muted.title");
    note = until ? t("ui.rec.muted.note_until", { until }) : t("ui.rec.muted.note");
    tone = "muted";
    els.permission.textContent = until ? t("ui.perm.muted_until", { until }) : t("ui.perm.muted");
  } else if (code === "paused") {
    title = t("ui.rec.paused.title");
    note = t("ui.rec.paused.note");
    if (until) note += t("ui.rec.paused.note_time", { until });
    tone = "muted";
    els.permission.textContent = t("ui.perm.paused");
  } else if (code === "schedule") {
    title = t("ui.rec.paused.title");
    note = until ? t("ui.rec.schedule.note_until", { until }) : t("ui.rec.schedule.note");
    tone = "muted";
    els.permission.textContent = t("ui.perm.schedule");
  } else {
    title = t("ui.rec.unavailable.title");
    note = privacy.причина || t("ui.rec.unavailable.note");
    tone = "unknown";
    els.permission.textContent = t("ui.perm.unconfirmed");
  }
  els.recording.className = `plate plate-${tone}`;
  setHtml(els.recording, `${escapeHtml(title)}<small>${escapeHtml(note)}</small>`);

  let battery = `<small>${escapeHtml(t("ui.battery.unknown"))}</small>`;
  const percent = device.battery_percent;
  if (fresh && typeof percent === "number" && Number.isFinite(percent) && percent >= 0 && percent <= 100) {
    battery = `<div class="battery"><progress max="100" value="${percent}" aria-label="${escapeHtml(t("ui.battery.label"))}"></progress><span>${escapeHtml(t("ui.battery.value", { percent }))}</span></div>`;
  }
  setHtml(els.device, `<strong>${escapeHtml(fresh ? t("ui.device.online") : t("ui.device.stale"))}</strong>${battery}`);
  const queue = status.queue || {};
  const pending = number(queue.pending);
  const processing = number(queue.processing);
  setHtml(els.queue, `<strong>${escapeHtml(pending === 0 && processing === 0 ? t("ui.queue.empty") : tn("ui.queue.waiting", pending))}</strong><small>${escapeHtml(processing ? t("ui.queue.processing", { n: processing }) : t("ui.queue.none_processing"))}</small>`);

  els.version.textContent = status.version ? t("ui.version.value", { version: status.version }) : t("ui.version.unknown");
  const db = status.db || {};
  els.db.textContent = db.error ? t("ui.db.unavailable") : `${tn("ui.n.conversations", number(db.conversations))}, ${tn("ui.n.facts", number(db.facts))}, ${tn("ui.n.tasks", number(db.tasks))}`;
  const windows = list(status.quiet_windows);
  els.windows.textContent = windows.length ? windows.map(item => typeof item === "string" ? item : item.title || item.name || t("ui.windows.default")).join("; ") : t("ui.windows.none");
  const worker = status.worker || {};
  els.worker.textContent = worker.stop_file === true ? t("ui.worker.paused") : worker.stop_file === false ? t("ui.worker.allowed") : t("ui.worker.unknown");
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
  els.actionMessage.textContent = t("ui.action.changing");
  const result = await fetchJson(path, { method: "POST" });
  state.busy = false;
  if (result.ok && typeof result.data.код === "string") {
    renderStatus({ ...state.lastStatus, privacy: result.data });
    const code = result.data.код;
    els.actionMessage.textContent = code === "allowed" ? t("ui.action.allowed") : code === "paused" ? t("ui.action.paused") : code === "manual" || code === "muted" ? t("ui.action.muted") : t("ui.action.other");
  } else {
    els.actionMessage.className = "action-message is-error";
    els.actionMessage.textContent = t("ui.action.failed", { detail: result.detail || t("ui.err.bad_response") });
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
  els.daySummary.textContent = t("ui.day.loading_summary");
  emptyState(els.dayFeed, t("ui.day.loading_conversations"));
  emptyState(els.dayTasks, t("ui.day.loading_tasks"));
  els.tasksCount.textContent = "—";
  els.dayFeed.setAttribute("aria-busy", "true");
  els.dayTasks.setAttribute("aria-busy", "true");
  const result = await fetchJson(`/ui/api/day?date=${encodeURIComponent(date)}`);
  if (request !== state.dayRequest || date !== state.day) return;
  els.dayFeed.setAttribute("aria-busy", "false");
  els.dayTasks.setAttribute("aria-busy", "false");
  if (!result.ok) {
    els.daySummary.textContent = t("ui.day.summary_unavailable");
    emptyState(els.dayFeed, t("ui.day.conversations_failed"), result.detail, true);
    emptyState(els.dayTasks, t("ui.day.tasks_failed"), t("ui.day.retry_date"), true);
    return;
  }
  const data = result.data;
  const conversations = list(data.conversations);
  const tasks = list(data.tasks);
  const totals = data.totals || {};
  const summary = [
    [number(totals.conversations ?? conversations.length), "ui.word.conversations"],
    [number(totals.facts ?? list(data.facts).length), "ui.word.facts"],
    [number(totals.tasks ?? tasks.length), "ui.word.tasks"],
  ];
  els.daySummary.innerHTML = summary.map(([count, key]) => `<span class="stat-pill"><strong>${count}</strong> ${escapeHtml(tn(key, count))}</span>`).join("");
  if (totals.speech) els.daySummary.appendChild(create("span", "stat-pill", t("ui.day.speech", { speech: totals.speech })));
  els.dayFeed.textContent = "";
  if (conversations.length) {
    for (const conversation of conversations) els.dayFeed.appendChild(renderConversation(conversation));
  } else {
    emptyState(els.dayFeed, t("ui.day.no_conversations"), date === todayIso() ? t("ui.day.no_conversations_today") : t("ui.day.no_conversations_day"));
  }
  els.dayTasks.textContent = "";
  els.tasksCount.textContent = String(tasks.length);
  if (tasks.length) {
    for (const task of tasks) els.dayTasks.appendChild(renderTask(task));
  } else {
    emptyState(els.dayTasks, t("ui.day.no_tasks"), t("ui.day.no_tasks_note"));
  }
}

function conversationStatus(code) {
  const key = ({ closed: "closed", open: "open", processing: "processing", pending: "pending", queued: "queued", transcribing: "transcribing", completed: "closed", done: "closed", error: "error", failed: "error", archived: "archived" })[code] || "unknown";
  return t(`ui.conv.status.${key}`);
}

function taskStatus(code) {
  const key = ({ done: "done", completed: "done", open: "open", todo: "open", pending: "open", new: "open", in_progress: "in_progress", processing: "in_progress", cancelled: "cancelled", canceled: "cancelled" })[code] || "unknown";
  return t(`ui.task.status.${key}`);
}

function renderTask(task) {
  const row = create("div", "task-row");
  const done = task.status === "done" || task.status === "completed";
  const head = create("div", "task-head");
  const mark = create("span", "task-mark", done ? "✓" : "•");
  mark.setAttribute("aria-hidden", "true");
  head.appendChild(mark);
  head.appendChild(create("span", "task-text", task.text || t("ui.task.no_text")));
  row.appendChild(head);
  const meta = [];
  if (task.assignee) meta.push(t("ui.task.assignee", { name: task.assignee }));
  if (task.due) meta.push(t("ui.task.due", { due: task.due }));
  if (task.exported) meta.push(t("ui.task.exported"));
  if (meta.length) row.appendChild(create("div", "task-meta", meta.join(" · ")));
  row.appendChild(create("span", "task-status", taskStatus(task.status)));
  return row;
}

function renderConversation(conversation) {
  const block = create("article", "conversation");
  block.dataset.id = String(conversation.id);
  const head = create("div", "conversation-head");
  head.innerHTML = `<span class="conversation-time">${escapeHtml(conversation.started || t("ui.conv.time_unknown"))}${conversation.ended ? `–${escapeHtml(conversation.ended)}` : ""}</span><span class="conversation-status">${escapeHtml(conversationStatus(conversation.status))}</span>${conversation.duration ? `<span class="conversation-duration">${escapeHtml(conversation.duration)}</span>` : ""}`;
  block.appendChild(head);
  if (conversation.summary) block.appendChild(create("p", "conversation-summary", conversation.summary));
  else block.appendChild(create("p", "conversation-summary muted", t("ui.conv.no_summary")));
  const chips = create("div", "chips");
  for (const tag of [...list(conversation.tags), ...list(conversation.participants)]) chips.appendChild(create("span", "chip", tag));
  if (chips.children.length) block.appendChild(chips);
  const counts = [number(conversation.utterances), Array.isArray(conversation.facts) ? conversation.facts.length : number(conversation.facts), Array.isArray(conversation.tasks) ? conversation.tasks.length : number(conversation.tasks)];
  block.appendChild(create("div", "conversation-counters", `${tn("ui.n.utterances", counts[0])} · ${tn("ui.n.facts", counts[1])} · ${tn("ui.n.tasks", counts[2])}`));
  const button = create("button", "conversation-toggle", t("ui.conv.open"));
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
  button.textContent = willOpen ? t("ui.conv.collapse") : t("ui.conv.open");
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
  details.textContent = t("ui.conv.loading");
  const result = await fetchJson(`/ui/api/conversation/${encodeURIComponent(id)}`);
  delete details.dataset.loading;
  details.setAttribute("aria-busy", "false");
  if (date !== state.day || generation !== state.dayRequest) return;
  details.textContent = "";
  if (!result.ok) {
    emptyState(details, t("ui.conv.failed"), t("ui.conv.failed_note", { detail: result.detail }), true);
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
  content.setAttribute("aria-label", t("ui.conv.details_label"));
  const facts = list(data.facts);
  const tasks = list(data.tasks);
  content.appendChild(create("h3", "details-title", t("ui.conv.facts", { n: facts.length })));
  if (!facts.length) content.appendChild(create("p", "muted", t("ui.conv.no_facts")));
  for (const fact of facts) {
    const row = create("div", "fact-row");
    row.innerHTML = `${fact.said ? `<span class="fact-time">${escapeHtml(fact.said)}</span>` : ""}${escapeHtml(fact.text)}`;
    content.appendChild(row);
  }
  content.appendChild(create("h3", "details-title", t("ui.conv.tasks", { n: tasks.length })));
  if (!tasks.length) content.appendChild(create("p", "muted", t("ui.conv.no_tasks")));
  for (const task of tasks) content.appendChild(renderTask(task));
  details.appendChild(content);
  const button = create("button", "show-transcript", t("ui.transcript.show"));
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
  message.textContent = t("ui.transcript.loading");
  const result = await fetchJson(`/ui/api/conversation/${encodeURIComponent(id)}?with_utterances=true`);
  button.disabled = false;
  if (date !== state.day || generation !== state.dayRequest) return;
  if (!result.ok) {
    message.textContent = t("ui.transcript.failed", { detail: result.detail });
    return;
  }
  message.textContent = "";
  state.transcripts[id] = list(result.data.transcript);
  renderTranscript(details, id, button);
}

function privateBadge(value) {
  return value === true ? `<span class="private-badge">${escapeHtml(t("ui.private"))}</span>` : "";
}

function renderTranscript(details, id, button) {
  const existing = details.querySelector(".transcript");
  if (existing) {
    existing.remove();
    button.textContent = t("ui.transcript.show");
    button.setAttribute("aria-expanded", "false");
    return;
  }
  const rows = state.transcripts[id] || [];
  const box = create("div", "transcript");
  box.id = `transcript-${id}`;
  box.setAttribute("tabindex", "0");
  box.setAttribute("aria-label", t("ui.transcript.label"));
  if (!rows.length) box.textContent = t("ui.transcript.empty");
  for (const row of rows) {
    const line = create("div", "transcript-row");
    line.innerHTML = `<span class="transcript-time">${escapeHtml(row.started)}</span>${row.speaker ? `<span class="transcript-speaker">${escapeHtml(row.speaker)}:</span>` : ""}${privateBadge(row.is_private)}<div>${escapeHtml(row.text)}</div>`;
    box.appendChild(line);
  }
  details.appendChild(box);
  button.textContent = t("ui.transcript.hide");
  button.setAttribute("aria-expanded", "true");
}

// ---------- поиск ----------

async function runSearch() {
  const query = els.searchQ.value.trim();
  const request = ++state.searchRequest;
  els.searchResults.textContent = "";
  els.searchResults.setAttribute("aria-busy", query ? "true" : "false");
  if (!query) {
    els.searchNote.textContent = t("ui.search.enter");
    return;
  }
  els.searchNote.textContent = t("ui.search.searching");
  const result = await fetchJson(`/ui/api/search?q=${encodeURIComponent(query)}&limit=20`);
  if (request !== state.searchRequest) return;
  els.searchResults.setAttribute("aria-busy", "false");
  if (!result.ok) {
    els.searchNote.textContent = t("ui.search.failed", { detail: result.detail });
    return;
  }
  const rows = list(result.data.results);
  els.searchNote.textContent = rows.length ? `${tn("ui.n.results", rows.length)}${rows.length === 20 ? t("ui.search.first20") : ""}` : t("ui.search.nothing");
  for (const row of rows) {
    const item = create("div", "search-result");
    const meta = [row.started, row.speaker || t("ui.search.no_speaker"), row.conversation_id ? t("ui.search.conversation", { id: row.conversation_id }) : ""].filter(Boolean);
    item.innerHTML = `<div class="search-meta">${escapeHtml(meta.join(" · "))}${privateBadge(row.is_private)}</div><div class="search-text">${escapeHtml(row.text)}</div>`;
    els.searchResults.appendChild(item);
  }
}

// ---------- диагностика ----------

async function loadQuality() {
  const date = state.day;
  const request = ++state.qualityRequest;
  els.qualityBody.textContent = t("ui.quality.loading");
  const result = await fetchJson(`/ui/api/quality?date=${encodeURIComponent(date)}`);
  if (request !== state.qualityRequest || date !== state.day) return;
  els.qualityBody.textContent = "";
  if (!result.ok) {
    emptyState(els.qualityBody, t("ui.quality.unavailable"), result.detail, true);
    return;
  }
  const data = result.data;
  const totals = create("div", "quality-totals");
  for (const [num, label] of [[data.total, t("ui.quality.total")], [data.accepted, t("ui.quality.accepted")], [data.rejected, t("ui.quality.rejected")], [data.flagged, t("ui.quality.flagged")]]) {
    const cell = create("div", "quality-total");
    cell.innerHTML = `<div class="num">${number(num)}</div><div class="label">${escapeHtml(label)}</div>`;
    totals.appendChild(cell);
  }
  els.qualityBody.appendChild(totals);
  for (const reason of list(data.by_reason)) {
    const row = create("div", "quality-reason");
    row.innerHTML = `<span>${escapeHtml(reason.title)}</span><span>${number(reason.count)}</span>`;
    els.qualityBody.appendChild(row);
  }
  if (!number(data.total)) els.qualityBody.appendChild(create("p", "muted", t("ui.quality.none")));
  else if (!list(data.by_reason).length) els.qualityBody.appendChild(create("p", "muted", t("ui.quality.no_reasons")));
  const filter = data.enforce === true ? t("ui.quality.enforce_on") : data.enforce === false ? t("ui.quality.enforce_off") : t("ui.quality.enforce_unknown");
  els.qualityBody.appendChild(create("p", "quality-note", filter));
  if (data.note) els.qualityBody.appendChild(create("p", "quality-note", data.note));
}

// ---------- язык интерфейса ----------

// Выбор сохраняет сервер (файл в каталоге данных): его видят трей, CLI и ночной
// батч, а не только этот браузер. После смены — перезагрузка со словарём.
async function changeLanguage(code) {
  els.langSelect.disabled = true;
  const result = await fetchJson(`/ui/api/language?lang=${encodeURIComponent(code)}`, { method: "POST" });
  if (result.ok) {
    globalThis.location?.reload();
    return;
  }
  els.langSelect.disabled = false;
  els.actionMessage.className = "action-message is-error";
  els.actionMessage.textContent = t("ui.lang.failed", { detail: result.detail });
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
  els.langSelect.addEventListener("change", () => changeLanguage(els.langSelect.value));
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
