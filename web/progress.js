"use strict";

function growthMoment(moment) {
  const item = element("li", "growth-moment");
  const heading = element("div", "growth-moment-title");
  heading.append(element("h4", "", moment.label));
  if (moment.kind === "highlight") {
    item.classList.add("growth-highlight");
    heading.append(element("span", "growth-tag", "高光"));
  } else if (moment.kind === "turning_point") {
    heading.append(element("span", "growth-tag", "转折"));
  }
  const source = link("读原文 ↗", mainEntryLink(moment.id, "#journey"), "quiet-link");
  source.setAttribute("aria-label", `读「${moment.label}」的日记原文`);
  item.append(heading);
  if (moment.quote !== moment.label) item.append(element("p", "muted", moment.quote));
  item.append(source);
  return item;
}

function renderGrowthStory(thread, today) {
  const story = element("article", "growth-story");
  const timeline = element("ol", "growth-timeline");
  const days = new Map();
  for (const moment of thread.moments) {
    if (!days.has(moment.date)) days.set(moment.date, []);
    days.get(moment.date).push(moment);
  }
  for (const [date, moments] of days) {
    const day = element("li", "growth-day");
    const time = element("time", "growth-date", shortDate(date));
    time.dateTime = date;
    if (date === today) time.prepend(element("strong", "", "今天"));
    if (moments.some((moment) => moment.kind === "highlight")) day.classList.add("has-highlight");
    const items = element("ul", "growth-moments");
    items.append(...moments.map(growthMoment));
    day.append(time, items);
    timeline.append(day);
  }
  story.append(element("h3", "", thread.title), timeline);
  return story;
}

function renderJourneyProgress(view) {
  const button = byId("progress-refresh");
  button.hidden = !view.has_entries;
  button.textContent = view.external ? "用 API 整理" : view.threads === null ? "整理足迹" : "重新整理";
  const stories = (view.threads ?? []).map((thread) => renderGrowthStory(thread, view.today));
  byId("progress-list").replaceChildren(...stories);
  let message = "";
  if (!view.has_entries) message = "写下最近的日记后，高光和变化会在这里慢慢连起来。";
  else if (view.threads?.length === 0) message = "这几篇日记里暂时没有找到适合连起来的时刻。";
  else if (view.threads === null && !view.external && !view.automatic) message = "连上本机模型，就可以回看近期日记里的高光和变化。";
  if (view.external && view.has_entries) {
    message = [message, "点击按钮会将近期日记摘录发送给所选 API。"].filter(Boolean).join(" ");
  }
  feedback("progress-feedback", message);
}

async function loadJourneyProgress(manual = false) {
  const button = byId("progress-refresh");
  if (button.disabled) return;
  button.disabled = true;
  try {
    if (manual) {
      feedback("progress-feedback", "正在回看近期的日记…");
      renderJourneyProgress(await request("/api/journey/progress", { automatic: false }));
      return;
    }
    const view = await request("/api/journey/progress");
    renderJourneyProgress(view);
    if (view.automatic) {
      feedback("progress-feedback", "正在回看近期的日记…");
      renderJourneyProgress(await request("/api/journey/progress", { automatic: true }));
    }
  } catch (error) {
    feedback("progress-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
}
