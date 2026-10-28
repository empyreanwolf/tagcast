"use strict";

// Activity log (fork v35): everything Tagcast did, newest first, with what worked and what
// didn't per area over the last day. The server records it (activity.py); this page reads
// it, refreshes itself while open, and reports the page's own script errors there too.

const activityView = {entries: [], summary: [], categories: {}, latest: 0, file: "", cat: "", status: "", query: "",
  live: true, loading: false, error: "", timer: null, request: 0};
const ACTIVITY_LIMIT = 500;

function showActivity(cat = activityView.cat, status = activityView.status) {
  Object.assign(activityView, {cat, status});
  showScreen("activity");
  renderActivity();
  loadActivity(true);
}

function activityParams(after = 0) {
  return new URLSearchParams({cat: activityView.cat, status: activityView.status, q: activityView.query,
    after: String(after), limit: String(ACTIVITY_LIMIT)});
}

// fresh: replace the list (a filter changed); otherwise only ask for newer entries.
async function loadActivity(fresh = false) {
  clearTimeout(activityView.timer);
  if (state.screen !== "activity") return;
  const request = ++activityView.request;
  activityView.loading = fresh && !activityView.entries.length;
  try {
    const data = await api(`/api/activity?${activityParams(fresh ? 0 : activityView.latest)}`);
    if (request !== activityView.request) return;
    activityView.entries = fresh ? data.entries : [...data.entries, ...activityView.entries].slice(0, ACTIVITY_LIMIT);
    Object.assign(activityView, {summary: data.summary || [], categories: data.categories || {}, file: data.file || "",
      latest: Math.max(activityView.latest, data.latest || 0), error: ""});
    if (fresh) activityView.latest = data.latest || 0;
  } catch (error) {
    if (request === activityView.request) activityView.error = error.message;
  } finally {
    if (request === activityView.request) {
      activityView.loading = false;
      renderActivity();
      if (activityView.live && state.screen === "activity") activityView.timer = setTimeout(() => loadActivity(), 3000);
    }
  }
}

const activityStatusLabel = {ok: "OK", error: "Error", warn: "Warning", info: "Info"};
const activityBadge = {ok: "have", error: "act-error", warn: "partly", info: "new"};

function activityTime(t) {
  const when = new Date(t * 1000), today = new Date().toDateString() === when.toDateString();
  const time = when.toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  return today ? time : `${when.toLocaleDateString("en-GB", {day: "numeric", month: "short"})} ${time}`;
}

function activityEntryHtml(e) {
  const label = activityView.categories[e.cat] || e.cat;
  const took = e.ms != null ? `<span class="muted"> · ${e.ms >= 1000 ? `${(e.ms / 1000).toFixed(1)} s` : `${e.ms} ms`}</span>` : "";
  const head = `<span class="dl-badge ${activityBadge[e.status] || "new"}">${activityStatusLabel[e.status] || e.status}</span>
    <div class="act-main"><div class="act-title">${escapeHtml(e.title)}</div>
    <div class="muted act-meta">${activityTime(e.t)} · ${escapeHtml(label)}${e.source ? ` · ${escapeHtml(e.source)}` : ""}${took}</div>`;
  return `<li data-id="${e.id}" class="act-entry act-${escapeHtml(e.status)}">${e.detail
    ? `<details><summary>${head}</div></summary><pre class="act-detail">${escapeHtml(e.detail)}</pre></details>`
    : `<div class="act-row">${head}</div></div>`}</li>`;
}

function activitySummaryHtml() {
  if (!activityView.summary.length) return '<p class="muted">Nothing recorded in the last 24 hours yet.</p>';
  return `<div class="act-summary">${activityView.summary.map(s => `<button type="button" class="act-card ${s.error ? "has-errors" : s.warn ? "has-warnings" : ""} ${activityView.cat === s.cat ? "active" : ""}" data-activity-cat="${escapeHtml(s.cat)}">
    <strong>${escapeHtml(s.label)}</strong>
    <span>${fmt(s.ok)} OK${s.error ? ` · <b class="act-error-text">${fmt(s.error)} ${s.error === 1 ? "error" : "errors"}</b>` : ""}${s.warn ? ` · ${fmt(s.warn)} ${s.warn === 1 ? "warning" : "warnings"}` : ""}${s.info ? ` · ${fmt(s.info)} info` : ""}</span>
    ${s.last_problem ? `<small class="muted">Last problem ${escapeHtml(activityTime(s.last_problem.t))}: ${escapeHtml(s.last_problem.title.slice(0, 120))}</small>` : ""}</button>`).join("")}</div>`;
}

function renderActivity() {
  const box = $("#activity-content");
  if (!box || state.screen !== "activity") return;
  const focused = document.activeElement?.id === "activity-search";
  const cats = Object.entries(activityView.categories);
  const opened = new Set([...(box.querySelectorAll?.("details[open]") || [])].map(d => d.closest("li")?.dataset.id));
  box.innerHTML = `<h3>Last 24 hours</h3>${activitySummaryHtml()}
    <div class="toolbar act-toolbar"><div class="search"><span aria-hidden="true">⌕</span><input id="activity-search" maxlength="200" placeholder="Search the log (a title, an artist, an error…)" aria-label="Search the activity log" value="${escapeHtml(activityView.query)}"></div>
      <select id="activity-cat" aria-label="Area"><option value="">Every area</option>${cats.map(([value, label]) => `<option value="${escapeHtml(value)}" ${activityView.cat === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>
      <select id="activity-status" aria-label="Outcome">${[["", "Every outcome"], ["problems", "Problems only"], ["error", "Errors"], ["warn", "Warnings"], ["ok", "OK"], ["info", "Info"]]
        .map(([value, label]) => `<option value="${value}" ${activityView.status === value ? "selected" : ""}>${label}</option>`).join("")}</select>
      <label class="act-live"><input type="checkbox" id="activity-live" ${activityView.live ? "checked" : ""}> Live</label>
      <a class="button" href="/api/activity/export" download>Download log file</a></div>
    ${activityView.error ? `<p class="error-box" role="alert">${escapeHtml(activityView.error)}</p>` : ""}
    <p class="muted" role="status">${activityView.loading ? "Loading…" : `${fmt(activityView.entries.length)} ${activityView.entries.length === 1 ? "entry" : "entries"}${activityView.entries.length >= ACTIVITY_LIMIT ? ` (the newest ${ACTIVITY_LIMIT}; search or filter to go further back)` : ""}`}${activityView.file ? ` · saved in <code>${escapeHtml(activityView.file)}</code>` : ""}</p>
    ${activityView.entries.length ? `<ul class="dl-list act-list">${activityView.entries.map(activityEntryHtml).join("")}</ul>`
      : activityView.loading ? "" : '<div class="empty">Nothing matches. Try another area or outcome.</div>'}`;
  for (const id of opened) box.querySelector?.(`li[data-id="${id}"] details`)?.setAttribute("open", "");
  if (focused) { const input = $("#activity-search"); input.focus?.(); input.setSelectionRange?.(input.value.length, input.value.length); }
}

let activitySearchTimer = null;
document.addEventListener("input", event => {
  if (event.target.id !== "activity-search") return;
  activityView.query = event.target.value;
  clearTimeout(activitySearchTimer);
  activitySearchTimer = setTimeout(() => loadActivity(true), 250);
});

document.addEventListener("change", event => {
  if (event.target.id === "activity-cat") { activityView.cat = event.target.value; loadActivity(true); }
  else if (event.target.id === "activity-status") { activityView.status = event.target.value; loadActivity(true); }
  else if (event.target.id === "activity-live") {
    activityView.live = event.target.checked;
    if (activityView.live) loadActivity(); else clearTimeout(activityView.timer);
  }
});

document.addEventListener("click", event => {
  const open = event.target.closest("[data-activity-open]");
  if (open) { showActivity(open.dataset.activityCat ?? "", open.dataset.activityStatus ?? ""); return; }
  if (event.target.closest("[data-activity-back]")) { showScreen("overview"); return; }
  const card = event.target.closest("[data-activity-cat]");
  if (card && card.closest("#activity-content")) {
    activityView.cat = activityView.cat === card.dataset.activityCat ? "" : card.dataset.activityCat;
    loadActivity(true);
  }
});

// -- the page's own problems ---------------------------------------------------------------
// Script errors and failed promises are sent to the same log. Reports made while Tagcast
// can't be reached wait and go with the next one; at most 20 a minute, to never flood it.

const pageReports = {waiting: [], sent: [], flushing: false};

function reportPageProblem(kind, message, where = "", stack = "") {
  const now = Date.now();
  pageReports.sent = pageReports.sent.filter(t => now - t < 60000);
  if (pageReports.sent.length >= 20 || !message) return;
  pageReports.sent.push(now);
  pageReports.waiting.push({kind, message: String(message).slice(0, 500), where: String(where).slice(0, 300),
    stack: String(stack || "").slice(0, 2000), screen: state?.screen || ""});
  flushPageReports();
}

// One sender at a time: overlapping sends would post the same waiting report twice.
async function flushPageReports() {
  if (pageReports.flushing) return;
  pageReports.flushing = true;
  try {
    while (pageReports.waiting.length) {
      const report = pageReports.waiting[0];
      try {
        const response = await fetch("/api/activity/client", {method: "POST",
          headers: {"Content-Type": "application/json", "X-Tagcast": "1"}, body: JSON.stringify(report)});
        if (!response.ok) return;
        pageReports.waiting.shift();
      } catch { return; /* Tagcast isn't reachable; try again with the next report */ }
    }
  } finally {
    pageReports.flushing = false;
  }
}

window.addEventListener?.("error", event => {
  if (!event?.message) return;
  reportPageProblem("script", event.message, `${(event.filename || "").split("/").pop()}:${event.lineno || "?"}`, event.error?.stack);
});
window.addEventListener?.("unhandledrejection", event => {
  const reason = event?.reason;
  if (reason?.status) return;  // a server answer the page already showed; the server logged it
  reportPageProblem("promise", reason?.message || String(reason), "", reason?.stack);
});
