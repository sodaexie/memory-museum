"use strict";

const byId = (id) => document.getElementById(id);
const graphEnabled = typeof initGraph === "function";
const settingsEnabled = typeof initModelSettings === "function";
const views = ["today", "journey", "archive", "memory", "entry"];
if (graphEnabled) views.push("graph");
if (settingsEnabled) views.push("settings");
const state = {
  home: null, model: null, memoryId: null, reflectionId: null,
  searchRequest: 0, navigation: 0, running: new Set(), journey: null,
};
const journeyFields = {
  view: "当时的想法", feeling: "当时的感受", decision: "想做或已做", reflection: "后来想到",
};
const maxJourneySelection = 8;

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function action(label, callback, className = "text-button") {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", callback);
  return button;
}

function link(label, hash, className = "quiet-link") {
  const node = element("a", className, label);
  node.href = hash;
  return node;
}

async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "请求失败，请再试一次。");
  return data;
}

function feedback(id, message = "", isError = false) {
  const node = byId(id);
  node.textContent = message;
  node.hidden = !message;
  node.classList.toggle("error", isError);
}

function dateLabel(value, options) {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("zh-CN", options).format(new Date(year, month - 1, day, 12));
}

function shortDate(date) {
  return dateLabel(date, { month: "long", day: "numeric" });
}

function unique(values) {
  return [...new Set(values)];
}

function chipGroup(entry, interactive = false) {
  const container = element("div", "chips");
  const choices = [
    ...entry.people.map((value) => [value, "person"]),
    ...unique([...entry.topics, ...entry.tags]).map((value) => [value, "topic"]),
  ];
  for (const [value, kind] of choices.slice(0, 10)) {
    const className = kind === "person" ? "chip chip-person" : "chip";
    const chip = interactive
      ? action(value, () => setSearch(value, kind), className)
      : element("span", className, value);
    container.append(chip);
  }
  return container;
}

function noteDate(date, currentYear) {
  const label = date.startsWith(currentYear + "-")
    ? shortDate(date)
    : dateLabel(date, { year: "numeric", month: "numeric", day: "numeric" });
  const time = element("time", "note-date", label);
  time.dateTime = date;
  return time;
}

function rowArrow() {
  const arrow = element("span", "row-arrow", "›");
  arrow.setAttribute("aria-hidden", "true");
  return arrow;
}

function noteRow(entry, currentYear) {
  const row = link("", `#entry/${entry.id}`, "note-row");
  if (currentYear) row.append(noteDate(entry.date, currentYear));
  const content = element("div", "note-content");
  content.append(element("p", "note-summary", entry.summary));
  const chips = chipGroup(entry);
  if (chips.childElementCount) content.append(chips);
  row.append(content, rowArrow());
  return row;
}

function renderNoteDays(id, entries, emptyMessage) {
  const list = byId(id);
  if (!entries.length) {
    list.replaceChildren(blank(emptyMessage));
    return;
  }
  const openDays = new Set([...list.querySelectorAll(".note-day[open]")].map((day) => day.dataset.date));
  const days = new Map();
  for (const entry of entries) {
    if (!days.has(entry.date)) days.set(entry.date, []);
    days.get(entry.date).push(entry);
  }
  const currentYear = (state.home?.today ?? entries[0].date).slice(0, 4);
  const groups = document.createDocumentFragment();
  for (const [date, notes] of days) {
    if (notes.length === 1) {
      groups.append(noteRow(notes[0], currentYear));
      continue;
    }
    const day = element("details", "note-day");
    day.dataset.date = date;
    day.open = openDays.has(date);
    const heading = element("summary", "note-row note-day-summary");
    const count = element("span", "note-day-count");
    count.append(`${notes.length} 条`, rowArrow());
    heading.append(noteDate(date, currentYear), element("span", "note-summary", notes[0].summary), count);
    const items = element("div", "note-day-entries");
    for (const note of notes) items.append(noteRow(note));
    day.append(heading, items);
    groups.append(day);
  }
  list.replaceChildren(groups);
}

function blank(label) {
  return element("div", "empty-state", label);
}

async function loadHome() {
  const data = await request("/api/home");
  state.home = data;
  byId("header-date").textContent = dateLabel(data.today, {
    year: "numeric", month: "long", day: "numeric", weekday: "long",
  });
  if (!byId("entry-date").value) byId("entry-date").value = data.today;
  byId("demo-banner").hidden = !data.demo;
  byId("fill-sample").hidden = !data.demo;
  renderNoteDays("recent-list", data.recent, "日记会按日期出现在这里。");
}

async function loadStatus() {
  const status = byId("model-status");
  try {
    state.model = await request("/api/status");
    const labels = {
      demo: "示例模式",
      ready: "本机模型已就绪",
      missing: `等待下载 ${state.model.model}`,
      offline: "等待连接 Ollama",
      api_configured: state.model.provider === "openai" ? "OpenAI 已配置" : "自定义 API 已配置",
      api_missing: "等待填写 API Key",
    };
    status.textContent = labels[state.model.mode];
    status.dataset.mode = state.model.mode;
  } catch {
    status.textContent = "模型状态暂不可用";
    status.dataset.mode = "offline";
  }
}

function setSearch(value, kind) {
  byId("search-query").value = value;
  byId("search-kind").value = kind;
  if (location.hash === "#archive") {
    loadArchive().catch(displayError);
  } else {
    location.hash = "archive";
  }
}

async function loadArchive() {
  const searchRequest = ++state.searchRequest;
  const params = new URLSearchParams({
    q: byId("search-query").value,
    kind: byId("search-kind").value,
  });
  const result = await request(`/api/entries?${params}`);
  if (searchRequest !== state.searchRequest) return;
  const filters = byId("search-filters");
  const options = [
    ...result.people.slice(0, 8).map(([name]) => [name, "person"]),
    ...result.topics.map((topic) => [topic, "topic"]),
  ];
  filters.replaceChildren(...options.map(([value, kind]) => action(
    value, () => setSearch(value, kind), kind === "person" ? "chip chip-person" : "chip",
  )));
  byId("search-result-count").textContent = `${result.entries.length} 条记录`;
  renderNoteDays("archive-list", result.entries, "没有找到匹配的日记。");
}

function mainEntryLink(entryId, back) {
  const params = back ? "?" + new URLSearchParams({ back }) : "";
  return `#entry/${entryId}${params}`;
}

function isEntryOpen(entryId) {
  return location.hash.split("?")[0] === `#entry/${entryId}`;
}

function citationLinks(sources, back) {
  const container = element("div", "citations");
  for (const source of sources) {
    container.append(link(`${shortDate(source.date)}原文 ↗`, mainEntryLink(source.id, back)));
  }
  return container;
}

function journeyHash(query, person) {
  return "#journey?" + new URLSearchParams({ q: query, person });
}

function journeyForEntry(entry) {
  const person = entry.people.length === 1 ? entry.people[0] : "";
  return journeyHash(entry.journey_topic || "", person);
}

function renderJourneySelection(journey) {
  const size = journey.selected.size;
  const button = byId("journey-review-button");
  button.disabled = journey.loading || size < 2;
  button.textContent = journey.loading ? "正在提炼…" : `提炼选中记录（${size} 条）`;
  for (const checkbox of byId("journey-timeline").querySelectorAll("input[type=checkbox]")) {
    checkbox.disabled = journey.loading || (!checkbox.checked && size >= maxJourneySelection);
  }
  feedback("journey-feedback", size < 2 ? "选两条或更多记录，可以比较想法的变化。" : "");
}

function timelineStep(record, journey) {
  const step = element("li", "journey-step");
  const stamp = element("time", "journey-time");
  stamp.dateTime = record.date;
  stamp.setAttribute("aria-label", dateLabel(record.date, { dateStyle: "long" }));
  stamp.append(element("strong", "journey-day", shortDate(record.date)), element("span", "journey-year", record.date.slice(0, 4)));
  const card = element("article", "card journey-entry");
  const top = element("div", "journey-entry-top");
  const checkbox = element("input");
  checkbox.type = "checkbox";
  checkbox.dataset.entryId = record.id;
  checkbox.checked = journey.selected.has(record.id);
  const label = element("label", "journey-check");
  label.append(checkbox, document.createTextNode("纳入提炼"));
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) journey.selected.add(record.id);
    else journey.selected.delete(record.id);
    const hadReview = journey.review !== null;
    journey.review = null;
    if (hadReview) {
      renderJourney(journey);
      byId("journey-timeline").querySelector(`[data-entry-id="${record.id}"]`)?.focus();
      feedback("journey-feedback", "已更新选中的记录，可重新提炼。");
    } else {
      renderJourneySelection(journey);
    }
  });
  top.append(label, link("原文 ↗", mainEntryLink(record.id, journey.hash)));
  card.append(top);
  const extracted = journey.review?.items.find((item) => item.id === record.id)?.fields ?? {};
  const fields = { ...record.fields, ...extracted };
  if (Object.keys(fields).length) {
    const list = element("dl", "journey-fields");
    for (const [key, fieldLabel] of Object.entries(journeyFields)) {
      if (!fields[key]) continue;
      const row = element("div", key === "decision" ? "journey-decision" : "");
      row.append(element("dt", "", fieldLabel), element("dd", "", fields[key]));
      list.append(row);
    }
    const details = element("details", "journey-details");
    details.append(element("summary", "", "查看日记摘录"), element("p", "journey-excerpt", record.text));
    card.append(list, details);
  } else {
    card.append(element("p", "journey-excerpt", record.text));
  }
  step.append(stamp, card);
  return step;
}

function renderJourney(journey) {
  const records = journey.records;
  byId("journey-count").textContent = records.length ? `${records.length} 条 · 从早到晚` : "";
  const empty = byId("journey-empty");
  empty.hidden = Boolean(records.length);
  empty.textContent = journey.query || journey.person
    ? "还没有找到匹配的日记。可以缩短关键词，或只按人物回看。"
    : "输入一件事或一个人，看看当时的记录。";
  byId("journey-timeline").replaceChildren(...records.map((record) => timelineStep(record, journey)));
  byId("journey-review-panel").hidden = !records.length;
  renderJourneySelection(journey);
  const insight = byId("journey-insight");
  insight.replaceChildren();
  if (journey.review?.change) {
    const box = element("div", "journey-change");
    box.append(
      element("p", "", journey.review.change.text),
      citationLinks(journey.review.change.sources, journey.hash),
    );
    insight.append(box);
  }
}

async function loadJourney(query, person) {
  byId("journey-query").value = query;
  byId("journey-person").value = person;
  const hash = journeyHash(query, person);
  if (state.journey?.hash === hash && state.journey.ready) {
    renderJourney(state.journey);
    return;
  }
  const journey = {
    query, person, hash, records: [], selected: new Set(), review: null, loading: false, ready: false,
  };
  state.journey = journey;
  renderJourney(journey);
  if (query || person) {
    byId("journey-empty").textContent = "正在寻找相关日记…";
    const data = await request("/api/journey?" + new URLSearchParams({ q: query, person }));
    if (state.journey !== journey) return;
    journey.records = data.records;
    const selected = data.records.length > maxJourneySelection
      ? [data.records[0], ...data.records.slice(1 - maxJourneySelection)]
      : data.records;
    journey.selected = new Set(selected.map((record) => record.id));
  }
  journey.ready = true;
  renderJourney(journey);
}

async function reviewJourney() {
  const journey = state.journey;
  if (!journey || journey.loading || journey.selected.size < 2) return;
  journey.loading = true;
  renderJourneySelection(journey);
  feedback("journey-feedback", "正在查阅选中的日记…");
  let status = "";
  let isError = false;
  try {
    journey.review = await request("/api/journey", {
      query: journey.query, person: journey.person, source_ids: [...journey.selected],
    });
    if (!journey.review.change) status = "这几条记录还不足以概括变化，可以继续点开原文回看。";
  } catch (error) {
    status = error.message;
    isError = true;
  } finally {
    journey.loading = false;
    if (state.journey === journey) {
      renderJourney(journey);
      if (status) feedback("journey-feedback", status, isError);
    }
  }
}

function oldNote(source) {
  const card = link("", `#entry/${source.id}`, "old-note");
  const header = element("div", "old-note-header");
  header.append(element("time", "", shortDate(source.date)), element("span", "", source.reasons.join(" · ")));
  card.append(header, element("p", "", source.quote));
  return card;
}

function adviceCard(advice) {
  const card = element("div", "advice");
  card.append(element("h3", "", "可以想一想"), element("p", "", advice.text), citationLinks(advice.sources));
  return card;
}

function renderReflection(entry, message = "") {
  const area = byId("reflection");
  area.replaceChildren();
  if (entry.summary) area.append(element("p", "reflection-summary", entry.summary));
  const chips = chipGroup(entry, true);
  if (chips.childElementCount) area.append(chips);
  if (message) area.append(element("p", "muted reflection-copy", message));
  if (entry.related.length) {
    area.append(element("h3", "reflection-subtitle", "相关旧事"));
    area.append(...entry.related.map(oldNote));
  } else if (!message) {
    area.append(element("p", "muted reflection-copy", "暂时没有找到相关旧事。"));
  }
  if (entry.advice) area.append(adviceCard(entry.advice));
  const actions = element("div", "reflection-actions");
  if (entry.text.trim()) {
    actions.append(link("回看心路历程 →", journeyForEntry(entry), "quiet-link reflection-link"));
  }
  actions.append(link("打开这条日记 ↗", `#entry/${entry.id}`, "quiet-link reflection-link"));
  area.append(actions);
}

async function analyzeEntry(entry, fromComposer = false) {
  if (state.running.has(entry.id)) return;
  state.running.add(entry.id);
  if (fromComposer) {
    state.reflectionId = entry.id;
    renderReflection(entry, "正在整理这条记录…");
  }
  else renderDetail(entry);
  try {
    const result = await request(`/api/analyze/${entry.id}`, {
      refresh: entry.analysis === "ready", automatic: fromComposer,
    });
    if (state.reflectionId === entry.id) renderReflection(result.entry, result.notice || "");
    if (state.journey) state.journey.ready = false;
    if (graphEnabled) graphView.stale = true;
    if (isEntryOpen(entry.id)) renderDetail(result.entry, result.notice || "");
  } catch (error) {
    const recovered = await request(`/api/entry/${entry.id}`).catch(() => entry);
    if (state.reflectionId === entry.id) renderReflection(recovered, error.message);
    if (isEntryOpen(entry.id)) renderDetail(recovered, error.message);
  } finally {
    state.running.delete(entry.id);
    const activeRetry = byId("retry-analysis");
    if (activeRetry && isEntryOpen(entry.id)) {
      activeRetry.disabled = false;
      activeRetry.textContent = "重新整理";
    }
    loadHome().catch(displayError);
    loadStatus();
  }
}

function editLabelForm(entry) {
  const form = element("form", "label-form");
  for (const [field, label] of [["people", "人物"], ["tags", "主题标签"]]) {
    const row = element("label", "label-field", label);
    const input = element("input");
    input.name = field;
    input.value = (field === "tags" ? unique([...entry.topics, ...entry.tags]) : entry.people).join("、");
    input.placeholder = "用顿号或逗号分开";
    input.maxLength = 500;
    row.append(input);
    form.append(row);
  }
  const save = element("button", "button secondary", "保存标签");
  save.type = "submit";
  const status = element("p", "form-feedback");
  status.hidden = true;
  status.setAttribute("role", "status");
  form.append(save, status);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    const values = new FormData(form);
    try {
      const updated = await request(`/api/labels/${entry.id}`, {
        people: values.get("people").split(/[、，,\n]/),
        tags: values.get("tags").split(/[、，,\n]/),
      });
      if (state.journey) state.journey.ready = false;
      if (graphEnabled) graphView.stale = true;
      if (isEntryOpen(entry.id)) renderDetail(updated, "标签已保存。");
      loadHome().catch(displayError);
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
      save.disabled = false;
    }
  });
  return form;
}

function renderDetail(entry, message = "") {
  byId("detail-date").textContent = dateLabel(entry.date, { dateStyle: "full" });
  byId("detail-text").textContent = entry.text;
  renderEntryAttachments(entry);
  const panel = element("section", "card detail-meta");
  panel.append(element("h2", "", "日记整理"));
  if (entry.summary) panel.append(element("p", "reflection-summary", entry.summary));
  const chips = chipGroup(entry, true);
  if (chips.childElementCount) panel.append(chips);
  const processing = state.running.has(entry.id);
  const retry = action(
    processing ? "正在整理…" : entry.analysis === "pending" ? "用 AI 整理" : "重新整理",
    () => analyzeEntry(entry), "text-button",
  );
  retry.id = "retry-analysis";
  retry.disabled = processing;
  if (message) panel.append(element("p", "muted reflection-copy", message));
  if (entry.text.trim()) panel.append(retry);
  panel.append(editLabelForm(entry));
  panel.append(action("从这条日记留下一条记忆 ↗", () => openMemoryForEntry(entry.id)));
  panel.append(link("回看相关心路历程 →", journeyForEntry(entry), "quiet-link reflection-link"));
  if (entry.advice) panel.append(adviceCard(entry.advice));
  byId("detail-metadata").replaceChildren(panel);
  byId("detail-events-section").hidden = !entry.events.length;
  byId("detail-events").replaceChildren(...entry.events.map((event) => {
    const block = element("article", "card event-card");
    block.append(element("strong", "", event.description), element("blockquote", "", event.quote));
    if (event.feeling) block.append(element("p", "muted", `写下的感受 · ${event.feeling}`));
    return block;
  }));
  byId("detail-related-section").hidden = !entry.related.length;
  byId("detail-related").replaceChildren(...entry.related.map(oldNote));
}

function clearMemoryEditor() {
  state.memoryId = null;
  byId("memory-form").reset();
  byId("memory-form-heading").textContent = "记住一件事";
  byId("memory-cancel").hidden = true;
}

function editMemory(memory) {
  state.memoryId = memory.id;
  byId("memory-text").value = memory.text;
  byId("memory-form-heading").textContent = "编辑这条记忆";
  byId("memory-cancel").hidden = false;
  for (const option of byId("memory-source").options) option.selected = memory.source_ids.includes(option.value);
  byId("memory-text").focus();
}

function entryDate(id) {
  return id.slice(0, 10);
}

async function loadMemories() {
  const [{ memories }, { entries }] = await Promise.all([request("/api/memories"), request("/api/entries")]);
  const source = byId("memory-source");
  const selected = new Set([...source.selectedOptions].map((option) => option.value));
  if (state.pendingMemory) selected.add(state.pendingMemory);
  source.replaceChildren(...entries.map((entry) => {
    const option = element("option", "", `${shortDate(entry.date)} · ${entry.summary.replace(/\s+/g, " ").slice(0, 39)}`);
    option.value = entry.id;
    option.selected = selected.has(entry.id);
    return option;
  }));
  if (state.pendingMemory) {
    state.pendingMemory = null;
    byId("memory-text").focus();
  }
  const cards = memories.map((memory) => {
    const card = element("article", "card memory-card");
    const text = element("p", "", memory.text);
    const buttons = element("div", "memory-item-actions");
    buttons.append(
      action("编辑", () => editMemory(memory)),
      action("删除", async () => {
        if (!window.confirm("删除这条记忆？")) return;
        try {
          await request(`/api/memories/${memory.id}`, { delete: true });
          if (state.memoryId === memory.id) clearMemoryEditor();
          await loadMemories();
          feedback("memory-status", "记忆已删除。");
        } catch (error) {
          feedback("memory-status", error.message, true);
        }
      }),
    );
    card.append(text);
    if (memory.source_ids.length) {
      card.append(citationLinks(memory.source_ids.map((id) => ({ id, date: entryDate(id) }))));
    }
    card.append(buttons);
    return card;
  });
  byId("memory-list").replaceChildren(...(cards.length ? cards : [blank("你确认过的经验会出现在这里。")]));
}

function openMemoryForEntry(entryId) {
  clearMemoryEditor();
  state.pendingMemory = entryId;
  location.hash = "memory";
}

function displayError(error) {
  feedback("app-error", error.message, true);
}

async function navigate() {
  const navigation = ++state.navigation;
  const [route, query = ""] = location.hash.slice(1).split("?");
  const params = new URLSearchParams(query);
  const [requested, id] = route.split("/");
  const page = views.includes(requested) && (requested !== "entry" || id) ? requested : "today";
  feedback("app-error");
  for (const name of views) byId(`view-${name}`).hidden = name !== page;
  for (const item of document.querySelectorAll("[data-page]")) {
    if (item.dataset.page === page || (page === "entry" && item.dataset.page === "archive")) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  }
  window.scrollTo(0, 0);
  if (page === "archive") await loadArchive();
  if (page === "graph") await loadGraph(params.get("q") ?? "");
  if (page === "memory") await loadMemories();
  if (page === "settings") await loadModelSettings();
  else if (settingsEnabled) byId("model-api-key").value = "";
  if (page === "journey") {
    if (!state.home) await loadHome();
    if (navigation !== state.navigation) return;
    const overview = !params.get("q") && !params.get("person");
    byId("journey-progress").hidden = !overview;
    if (overview) loadJourneyProgress();
    const demoDefault = state.home.demo && !params.has("q") && !params.has("person");
    await loadJourney(params.get("q") ?? (demoDefault ? "改约" : ""), params.get("person") ?? (demoDefault ? "小 A" : ""));
  }
  if (page === "entry") {
    const back = params.get("back");
    const fromJourney = back === "#journey" || back?.startsWith("#journey?");
    const fromGraph = back === "#graph" || back?.startsWith("#graph?");
    byId("detail-back").href = fromJourney || fromGraph ? back : "#archive";
    byId("detail-back").textContent = fromJourney ? "← 返回心路历程" : fromGraph ? "← 返回关联图" : "← 返回往事";
    byId("detail-text").textContent = "正在读取…";
    renderEntryAttachments(null);
    byId("detail-date").textContent = "";
    byId("detail-metadata").replaceChildren();
    byId("detail-events-section").hidden = true;
    byId("detail-related-section").hidden = true;
    const entry = await request(`/api/entry/${encodeURIComponent(id)}`);
    if (navigation === state.navigation) renderDetail(entry);
  }
}

function countText() {
  byId("entry-length").textContent = `${Array.from(byId("entry-text").value).length} 字`;
}

byId("entry-text").addEventListener("input", countText);
byId("fill-sample").addEventListener("click", () => {
  byId("entry-text").value = state.home.sample;
  byId("entry-text").focus();
  countText();
});
byId("model-status").addEventListener("click", loadStatus);
byId("entry-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("entry-text");
  const button = byId("save-entry");
  const date = byId("entry-date");
  const attachments = byId("entry-attachments");
  if (button.disabled) return;
  if (!byId("link-editor").hidden && byId("entry-link").value.trim() && !addDraftLink(false)) return;
  if (!input.value.trim() && !draftImages.length && !draftLinks.length) {
    feedback("save-status", "写点什么，或添加一张图片、一个链接。", true);
    input.focus();
    return;
  }
  button.disabled = true;
  attachments.disabled = true;
  input.readOnly = true;
  date.readOnly = true;
  byId("fill-sample").disabled = true;
  feedback("save-status", "正在保存…");
  try {
    const entry = await request("/api/entries", {
      text: input.value, date: date.value,
      images: await Promise.all(draftImages.map(encodeDraftPicture)),
      links: draftLinks.map((url) => url.href),
    });
    if (state.journey) state.journey.ready = false;
    if (graphEnabled) graphView.stale = true;
    input.value = "";
    clearDraftAttachments();
    countText();
    feedback("save-status", "原文已保存。");
    loadHome().catch(displayError);
    if (entry.text.trim()) analyzeEntry(entry, true);
    else {
      state.reflectionId = entry.id;
      renderReflection(entry, "附件已保存。打开这条日记就能回看。");
    }
  } catch (error) {
    feedback("save-status", error.message, true);
  } finally {
    button.disabled = false;
    attachments.disabled = false;
    input.readOnly = false;
    date.readOnly = false;
    byId("fill-sample").disabled = false;
  }
});
byId("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadArchive().catch(displayError);
});
byId("search-kind").addEventListener("change", () => loadArchive().catch(displayError));
byId("journey-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const query = byId("journey-query").value.trim();
  const person = byId("journey-person").value.trim();
  const hash = journeyHash(query, person);
  state.journey = null;
  if (location.hash === hash) loadJourney(query, person).catch(displayError);
  else location.hash = hash;
});
byId("journey-review-button").addEventListener("click", reviewJourney);
byId("progress-refresh").addEventListener("click", () => loadJourneyProgress(true));
byId("memory-cancel").addEventListener("click", clearMemoryEditor);
byId("memory-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  const sourceIds = [...byId("memory-source").selectedOptions].map((option) => option.value);
  const path = state.memoryId ? `/api/memories/${state.memoryId}` : "/api/memories";
  try {
    await request(path, { text: byId("memory-text").value, source_ids: sourceIds });
    clearMemoryEditor();
    feedback("memory-status", "记忆已保存。");
    await loadMemories();
  } catch (error) {
    feedback("memory-status", error.message, true);
  } finally {
    button.disabled = false;
  }
});
window.addEventListener("hashchange", () => navigate().catch(displayError));
initAttachments();
if (graphEnabled) initGraph();
else document.querySelector('[data-page="graph"]').hidden = true;
if (settingsEnabled) initModelSettings();
else document.querySelector('[data-page="settings"]').hidden = true;
loadHome().catch(displayError);
loadStatus();
navigate().catch(displayError);
