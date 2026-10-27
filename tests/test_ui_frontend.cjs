"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this.hidden = false;
    this.disabled = false; this.value = ""; this.className = ""; this._text = ""; this._html = "";
  }
  set textContent(value) { this._text = String(value); this._html = ""; this.children = []; }
  get textContent() { return this._text + this._html + this.children.map(child => child.textContent).join(" "); }
  set innerHTML(value) { this._html = String(value); this._text = ""; this.children = []; }
  get innerHTML() { return this._html + this._text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;") + this.children.map(child => child.innerHTML).join(" "); }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    const match = child => selector.startsWith(".") ? child.className.split(" ").includes(selector.slice(1)) : child.tagName.toLowerCase() === selector;
    for (const child of this.children) { if (match(child)) return child; const found = child.querySelector(selector); if (found) return found; }
    return null;
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
}

const staticDirectory = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, "static");
const localesDirectory = path.join(staticDirectory, "..", "..", "locales");
// Словарь страницы, как его кладёт сервер в window.UZEL_I18N: ключи ui.* языка.
function uiCatalog(lang) {
  const all = JSON.parse(fs.readFileSync(path.join(localesDirectory, `${lang}.json`), "utf8"));
  return { lang, strings: Object.fromEntries(Object.entries(all).filter(([key]) => key.startsWith("ui."))) };
}
function makeContext(lang, store) {
  const ctx = vm.createContext({
    document: { getElementById(id) { if (!store.has(id)) store.set(id, new Element()); return store.get(id); }, createElement: tag => new Element(tag), addEventListener() {} },
    Date, Math, Number, String, Object, Array, Set, Map, encodeURIComponent, setInterval() {},
    fetch: async () => { throw new Error("No live requests in tests"); },
    UZEL_I18N: uiCatalog(lang),
  });
  vm.runInContext(fs.readFileSync(path.join(staticDirectory, "app.js"), "utf8"), ctx);
  return ctx;
}
const elements = new Map();
const context = makeContext("ru", elements);
const run = source => vm.runInContext(source, context);
const el = id => elements.get(id);
const base = { version: "test", privacy: { код: "allowed" }, queue: { pending: 2, processing: 1 }, device: { known: true, connected: true, stale: false, packets: 3, battery_percent: 76 }, db: {}, quiet_windows: [] };
const checks = [];
function test(name, callback) { checks.push([name, callback]); }
function render(overrides = {}) { context.statusFixture = { ...base, ...overrides }; run("renderStatus(statusFixture)"); }
const response = data => ({ ok: true, status: 200, json: async () => data });

test("allowed + stale не обещает запись и скрывает старый заряд", () => {
  render({ device: { ...base.device, stale: true } });
  assert.match(el("recording").textContent, /Нет связи с кулоном/);
  assert.doesNotMatch(el("recording").textContent, /Запись идёт|передаёт звук/);
  assert.doesNotMatch(el("device").innerHTML, /76/);
  assert.doesNotMatch(el("recording").className, /plate-recording/);
});
test("disconnected и unknown не обещают запись", () => {
  for (const device of [{ ...base.device, connected: false }, { ...base.device, known: false }, {}]) {
    render({ device }); assert.match(el("recording").textContent, /Нет связи с кулоном/);
  }
});
test("связь без пакетов и свежие пакеты различаются", () => {
  render({ device: { ...base.device, packets: 0 } });
  assert.match(el("recording").textContent, /Кулон подключён/);
  assert.doesNotMatch(el("recording").className, /plate-recording/);
  render(); assert.match(el("recording").textContent, /Кулон передаёт звук/);
  assert.match(el("recording").className, /plate-recording/);
  assert.match(el("permission").textContent, /Запись разрешена/);
});
test("privacy и offline сохраняют правильные disabled", () => {
  for (const [code, title, muteDisabled, pauseDisabled, resumeDisabled] of [
    ["allowed", "Кулон передаёт звук", false, false, true],
    ["muted", "Запись на паузе", false, false, false],
    ["manual", "Запись на паузе", false, false, false],
    ["paused", "Запись выключена", true, true, false],
  ]) {
    render({ privacy: { код: code, до: "2026-10-13T10:30:00" } });
    assert.match(el("recording").textContent, new RegExp(title));
    assert.equal(el("btn-mute").disabled, muteDisabled); assert.equal(el("btn-pause").disabled, pauseDisabled); assert.equal(el("btn-resume").disabled, resumeDisabled);
  }
  run("renderStatus(null)"); assert.match(el("recording").textContent, /Программа не отвечает/);
  for (const id of ["btn-mute", "btn-pause", "btn-resume"]) assert.equal(el(id).disabled, true);
});
test("escapeHtml экранирует все опасные символы", () => {
  context.escapeFixture = '<a x="v">&\'';
  assert.equal(run("escapeHtml(escapeFixture)"), "&lt;a x=&quot;v&quot;&gt;&amp;&#39;");
  assert.equal(run("escapeHtml(null)"), "");
});
test("fetchJson обрабатывает HTTP, сеть, JSON и API errors", async () => {
  context.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: "<ошибка>" }) });
  assert.equal((await run("fetchJson('/test')")).ok, false);
  context.fetch = async () => { throw new Error("network"); };
  assert.equal((await run("fetchJson('/test')")).ok, false);
  context.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error("bad JSON"); } });
  assert.equal((await run("fetchJson('/test')")).ok, false);
  context.fetch = async () => response({ error: "база занята" });
  assert.equal((await run("fetchJson('/test')")).ok, false);
});
test("приватные поисковые результаты экранируются без score", async () => {
  el("search-q").value = "тема";
  context.fetch = async () => response({ results: [{ started: "<время>", speaker: "<имя>", text: "<script>x</script>", is_private: true, score: 0.123 }] });
  await run("runSearch()");
  assert.match(el("search-results").innerHTML, /Приватная запись/);
  assert.match(el("search-results").innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(el("search-results").innerHTML, /<script>|score/);
  assert.match(el("search-note").textContent, /1 результат/);
});
test("приватные реплики экранируются и скрываются явной кнопкой", () => {
  context.detailsFixture = new Element(); context.buttonFixture = new Element("button");
  run(`state.transcripts[1] = [{ started: '<time>', speaker: '<name>', text: '<script>', is_private: true }]; renderTranscript(detailsFixture, 1, buttonFixture)`);
  assert.match(context.detailsFixture.innerHTML, /Приватная запись/); assert.match(context.detailsFixture.innerHTML, /&lt;script&gt;/);
  assert.equal(context.buttonFixture.textContent, "Скрыть расшифровку");
  run("renderTranscript(detailsFixture, 1, buttonFixture)"); assert.equal(context.detailsFixture.querySelector(".transcript"), null);
  assert.equal(context.buttonFixture.textContent, "Показать расшифровку");
});
test("разговор открывается кнопкой без автозагрузки расшифровки", async () => {
  context.conversationFixture = { id: 4, started: "09:00", ended: "09:05", status: "closed", duration: "5 мин", tags: ["<tag>"], facts: 1, tasks: 0, utterances: 3 };
  const block = run("renderConversation(conversationFixture)");
  const button = block.querySelector(".conversation-toggle"); assert.equal(button.tagName, "BUTTON"); assert.equal(button.getAttribute("aria-expanded"), "false");
  assert.match(block.textContent, /Завершён/);
  const requests = []; context.fetch = async url => { requests.push(url); return response({ id: 4, facts: [], tasks: [] }); };
  context.blockFixture = block; await run("toggleConversation(blockFixture, 4)");
  assert.equal(button.getAttribute("aria-expanded"), "true"); assert.equal(requests.length, 1); assert.doesNotMatch(requests[0], /with_utterances/);
});
test("старые ответы дня не заменяют новую дату; кеш деталей сбрасывается", async () => {
  const pending = [];
  context.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
  run("state.day = '2026-10-12'"); const old = run("loadDay()");
  run("state.day = '2026-10-13'; state.expanded = {}; state.transcripts = {}"); const current = run("loadDay()");
  pending[1].resolve(response({ conversations: [], tasks: [{ text: "Новая задача", status: "open" }], totals: {} })); await current;
  pending[0].resolve(response({ conversations: [], tasks: [{ text: "Старая задача" }], totals: {} })); await old;
  assert.match(el("day-tasks").textContent, /Новая задача/); assert.doesNotMatch(el("day-tasks").textContent, /Старая задача/);
});
test("privacy POST применяет только privacy и показывает ошибку", async () => {
  render(); context.fetch = async () => response({ код: "paused" }); await run("privacyAction('/privacy/pause')");
  assert.match(el("recording").textContent, /Запись выключена/); assert.match(el("device").innerHTML, /76/);
  context.fetch = async () => { throw new Error("offline"); }; await run("privacyAction('/privacy/unmute')");
  assert.match(el("action-message").textContent, /Не удалось/); assert.equal(el("btn-resume").disabled, false);
});
test("schedule и ошибки разрешения не делают статус зелёным", () => {
  render({ privacy: { код: "schedule", до: "2026-10-14T10:30:00" } });
  assert.match(el("recording").textContent, /по расписанию|окно без записи/);
  for (const code of ["bad_state", "bad_schedule", "unknown"]) {
    render({ privacy: { код: code, причина: "<ошибка>" } });
    assert.match(el("recording").textContent, /Запись недоступна/);
    assert.doesNotMatch(el("recording").className, /plate-recording/);
    assert.match(el("recording").innerHTML, /&lt;ошибка&gt;/);
    assert.equal(el("btn-resume").disabled, false);
  }
});
test("busy блокирует кнопки и повторный POST, после ответа восстанавливает", async () => {
  render(); let resolve; let calls = 0;
  context.fetch = () => { calls++; return new Promise(done => { resolve = done; }); };
  const action = run("privacyAction('/privacy/mute?minutes=30')");
  for (const id of ["btn-mute", "btn-pause", "btn-resume"]) assert.equal(el(id).disabled, true);
  await run("privacyAction('/privacy/pause')"); assert.equal(calls, 1);
  resolve(response({ код: "manual", до: "2026-10-14T12:30:00" })); await action;
  assert.equal(el("btn-mute").disabled, false); assert.equal(el("btn-pause").disabled, false); assert.equal(el("btn-resume").disabled, false);
});
test("запоздавший poll не перезаписывает ответ privacy POST", async () => {
  render(); let finishPoll;
  context.fetch = url => url === "/ui/api/status" ? new Promise(done => { finishPoll = done; }) : Promise.resolve(response({ код: "paused" }));
  const poll = run("refreshStatus()"); await run("privacyAction('/privacy/pause')");
  finishPoll(response(base)); await poll;
  assert.match(el("recording").textContent, /Запись выключена/); assert.equal(el("btn-resume").disabled, false);
});
test("ошибочный день не выдаётся за пустой день, пустой день различается", async () => {
  context.fetch = async () => response({ error: "база недоступна" }); await run("loadDay()");
  assert.match(el("day-feed").textContent, /Не удалось загрузить/); assert.match(el("day-tasks").textContent, /Не удалось загрузить/);
  context.fetch = async () => response({ conversations: [], tasks: [], totals: {} }); await run("loadDay()");
  assert.match(el("day-feed").textContent, /Разговоров пока нет/); assert.match(el("day-tasks").textContent, /Задач пока нет/);
  assert.equal(el("day-feed").getAttribute("aria-busy"), "false");
});
test("смена даты через setDay очищает детали и расшифровки", async () => {
  run("state.expanded[42] = {}; state.transcripts[42] = []");
  context.fetch = async url => response(url.includes("/quality") ? { total: 0 } : { conversations: [], tasks: [] });
  run("setDay('2026-10-15')"); await Promise.resolve(); await Promise.resolve();
  assert.equal(run("Object.keys(state.expanded).length"), 0); assert.equal(run("Object.keys(state.transcripts).length"), 0);
  assert.equal(el("day-date").value, "2026-10-15");
});
test("поиск различает ошибку, пустые результаты и устаревшие ответы", async () => {
  el("search-q").value = "старое"; let finishOld;
  context.fetch = () => new Promise(done => { finishOld = done; }); const old = run("runSearch()");
  el("search-q").value = "новое"; context.fetch = async () => response({ results: [{ text: "Новый результат" }] }); await run("runSearch()");
  finishOld(response({ results: [{ text: "Старый результат" }] })); await old;
  assert.match(el("search-results").textContent, /Новый результат/); assert.doesNotMatch(el("search-results").textContent, /Старый результат/);
  context.fetch = async () => response({ results: [] }); await run("runSearch()"); assert.match(el("search-note").textContent, /Ничего не найдено/);
  context.fetch = async () => response({ error: "база недоступна" }); await run("runSearch()"); assert.match(el("search-note").textContent, /Поиск не удался/);
});
test("запоздавшие детали разговора не попадают в кеш другого дня", async () => {
  context.detailConversation = { id: 88, status: "closed" }; context.detailBlock = run("renderConversation(detailConversation)");
  let finish; context.fetch = () => new Promise(done => { finish = done; }); const pending = run("toggleConversation(detailBlock, 88)");
  run("state.day = '2026-10-16'; state.expanded = {}; state.transcripts = {}; state.dayRequest++");
  finish(response({ id: 88, facts: [{ text: "Старый факт" }], tasks: [] })); await pending;
  assert.equal(run("state.expanded[88]"), undefined);
});
test("качество экранирует API и не подтверждает работающую расшифровку", async () => {
  render(); assert.equal(el("worker").textContent, "Состояние обработки неизвестно");
  render({ worker: { stop_file: false, batch_hint: "raw tech hint" } });
  assert.equal(el("worker").textContent, "Автоматическая обработка разрешена");
  render({ worker: { stop_file: true } }); assert.equal(el("worker").textContent, "Обработка приостановлена");
  context.fetch = async () => response({ total: "<script>", accepted: 2, by_reason: [{ title: "<img>", count: "<svg>" }], enforce: false, note: "<script>" });
  await run("loadQuality()"); assert.doesNotMatch(el("quality-body").innerHTML, /<script>|<img>|<svg>/);
  assert.match(el("quality-body").textContent, /режиме наблюдения/);
});
test("init реально запускает только локальные GET и связывает все сценарии", async () => {
  const urls = []; context.fetch = async (url, options) => {
    urls.push(url); assert.equal(options?.method, undefined);
    return response(url === "/ui/api/status" ? base : url.includes("/quality") ? { total: 0 } : { conversations: [], tasks: [] });
  };
  run("init()"); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(urls.length, 3); assert.ok(urls.every(url => url.startsWith("/ui/api/")));
  for (const id of ["btn-mute", "btn-pause", "btn-resume", "day-prev", "day-next", "day-today"]) assert.equal(typeof el(id).listeners.click, "function");
  assert.equal(typeof el("search-form").listeners.submit, "function");
});
test("HTML имеет все DOM точки, закрытую диагностику и версии локальных ресурсов", () => {
  const html = fs.readFileSync(path.join(staticDirectory, "index.html"), "utf8");
  const source = fs.readFileSync(path.join(staticDirectory, "app.js"), "utf8");
  const css = fs.readFileSync(path.join(staticDirectory, "style.css"), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const match of source.matchAll(/getElementById\("([^"]+)"\)/g)) assert.ok(ids.includes(match[1]), `Missing DOM id ${match[1]}`);
  assert.match(html, /<details class="diagnostics panel">/); assert.equal((html.match(/ui=20261027-1/g) || []).length, 2);
  assert.doesNotMatch(html + css + source, /https?:\/\//);
  assert.equal((css.match(/{/g) || []).length, (css.match(/}/g) || []).length);
  assert.match(css, /prefers-reduced-motion/); assert.match(css, /focus-visible/);
});

test("строки страницы и app.js — только из каталога, ключи есть в en и ru", () => {
  const html = fs.readFileSync(path.join(staticDirectory, "index.html"), "utf8");
  const source = fs.readFileSync(path.join(staticDirectory, "app.js"), "utf8");
  const cyr = /[а-яё]/i;
  // Служебные метки подстановки ({ВЕРСИЯ} и др.) — не текст для человека.
  assert.doesNotMatch(html.replace(/\{[А-ЯЁ]+\}/g, ""), cyr);
  const code = source.split("\n").filter(line => !line.trim().startsWith("//")).join("\n").replace(/\.(код|до|причина)(?![а-яё])/gi, "");
  assert.doesNotMatch(code, cyr);
  const keys = [...html.matchAll(/\{\{t:([\w.]+)\}\}/g)].map(m => m[1]).concat([...source.matchAll(/\bt\("(ui\.[\w.]+)"/g)].map(m => m[1]), [...source.matchAll(/\btn\("(ui\.[\w.]+)"/g)].map(m => m[1]));
  assert.ok(keys.length > 50);
  for (const lang of ["en", "ru"]) {
    const strings = uiCatalog(lang).strings;
    for (const key of keys) assert.ok(key in strings, `${lang}: нет ключа ${key}`);
  }
});
test("английский интерфейс: статус, формы числа, задачи", async () => {
  const store = new Map();
  const en = makeContext("en", store);
  en.statusFixture = base; vm.runInContext("renderStatus(statusFixture)", en);
  assert.match(store.get("recording").textContent, /The pendant is sending audio/);
  assert.match(store.get("queue").innerHTML, /2 chunks waiting/);
  assert.match(store.get("permission").textContent, /Recording allowed/);
  vm.runInContext("renderStatus(null)", en);
  assert.match(store.get("recording").textContent, /The app is not responding/);
  en.fetch = async () => response({ conversations: [], tasks: [], totals: {} }); await vm.runInContext("loadDay()", en);
  assert.match(store.get("day-feed").textContent, /No conversations yet/);
  assert.match(store.get("day-summary").innerHTML, /<strong>0<\/strong> conversations/);
  assert.equal(vm.runInContext("tn('ui.n.facts', 1)", en), "1 fact");
});
test("русские формы числа: 1, 2, 5, 21", () => {
  assert.equal(run("tn('ui.n.facts', 1)"), "1 факт");
  assert.equal(run("tn('ui.n.facts', 2)"), "2 факта");
  assert.equal(run("tn('ui.n.facts', 5)"), "5 фактов");
  assert.equal(run("tn('ui.n.facts', 21)"), "21 факт");
  render(); assert.match(el("queue").innerHTML, /2 фрагмента ждут/);
});
test("нет словаря — видны ключи, а не падение", () => {
  const store = new Map();
  const bare = vm.createContext({
    document: { getElementById(id) { if (!store.has(id)) store.set(id, new Element()); return store.get(id); }, createElement: tag => new Element(tag), addEventListener() {} },
    Date, Math, Number, String, Object, Array, Set, Map, encodeURIComponent, setInterval() {},
  });
  vm.runInContext(fs.readFileSync(path.join(staticDirectory, "app.js"), "utf8"), bare);
  assert.equal(vm.runInContext("t('ui.no_data')", bare), "ui.no_data");
});

(async () => {
  let failed = 0;
  for (const [name, callback] of checks) {
    try { await callback(); console.log(`PASS ${name}`); }
    catch (error) { failed++; console.error(`FAIL ${name}\n${error.message}`); }
  }
  console.log(`${checks.length - failed}/${checks.length} checks passed`);
  process.exitCode = failed ? 1 : 0;
})();
