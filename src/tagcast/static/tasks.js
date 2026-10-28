"use strict";

// Background tasks (fork v36). Anything that writes to iBroadcast waits its turn in a queue
// on the server instead of holding the page: api() hands those requests to /api/queue, which
// answers at once, and this file follows each task until it finishes. Two ways to use it:
//   await api("/api/save", body)         -- unchanged for callers: resolves with the answer, or
//                                           throws the same error the direct request would have
//   runQueued(path, body, label)         -- the same, with a label for the indicator; a caller
//                                           that has already closed its dialog just doesn't await it
// The Tasks indicator (bottom right) lists what is running, what is waiting and what failed.
const bgTasks = {watched: new Map(), view: null, timer: null, failures: 0, open: false, dismissed: new Set(), congested: false};

function taskLabel(path, body) {
  const n = list => Array.isArray(list) ? list.length : 0;
  const noun = body?.target === "album" ? "cover" : "artist image";
  const labels = {
    "/api/save": () => `Save ${n(body?.changes)} ${n(body?.changes) === 1 ? "record" : "records"}`,
    "/api/artwork": () => `Change ${noun}: ${body?.label || ""}`,
    "/api/artwork/undo": () => `Restore the previous ${noun}: ${body?.label || ""}`,
    "/api/trash": () => `Move ${n(body?.tracks)} ${n(body?.tracks) === 1 ? "track" : "tracks"} to the trash`,
    "/api/trash/albums": () => `Move ${n(body?.albums)} ${n(body?.albums) === 1 ? "album" : "albums"} to the trash`,
    "/api/tags/save": () => "Save tags",
    "/api/favourite": () => "Save rating",
    "/api/album-rating": () => "Save rating",
    "/api/artist-rating": () => "Save rating",
    "/api/album-favourite": () => "Save favourite",
    "/api/artist-favourite": () => "Save favourite",
    "/api/playlist": () => "Save playlist",
    "/api/playlist/artwork": () => "Change playlist cover",
    "/api/lyrics/save": () => "Save lyrics"};
  return (labels[path] || (() => path.replace("/api/", "")))().trim();
}

function taskError(status, data, fallback) {
  const error = Error(data?.error || fallback || `Tagcast returned HTTP ${status}.`);
  error.status = status; error.data = data || {};
  return error;
}

// Queue the request and resolve with its answer once the server has run it.
function runQueued(path, body, label = taskLabel(path, body)) {
  return new Promise((resolve, reject) => {
    (async () => {
      const {task} = await rawApi("/api/queue", {path, body, label});
      bgTasks.watched.set(task.id, {resolve, reject, label: task.label});
      await settleTask(task.id);   // quick tasks (and tests) finish before the first poll
      if (bgTasks.watched.has(task.id)) scheduleTaskPoll(0);
    })().catch(reject);
  });
}

// Ask about one task; if it has finished, hand its answer to whoever is waiting for it.
async function settleTask(id) {
  const watcher = bgTasks.watched.get(id);
  if (!watcher) return;
  let state;
  try { state = await rawApi(`/api/tasks/${encodeURIComponent(id)}`); }
  catch (error) { bgTasks.watched.delete(id); watcher.reject(error); return; }
  if (state.state === "done") { bgTasks.watched.delete(id); watcher.resolve(state.result); }
  else if (state.state === "error") { bgTasks.watched.delete(id); watcher.reject(taskError(state.status, state.data)); }
}

function scheduleTaskPoll(delay) {
  if (bgTasks.timer) return;
  bgTasks.timer = setTimeout(pollTasks, delay ?? (bgTasks.watched.size ? 800 : 2500));
}

async function pollTasks() {
  bgTasks.timer = null;
  try {
    const view = await rawApi("/api/tasks");
    bgTasks.failures = 0; bgTasks.view = view;
    const known = new Map(view.tasks.map(t => [t.id, t]));
    for (const id of [...bgTasks.watched.keys()]) {
      const task = known.get(id);
      if (!task || task.state === "done" || task.state === "error") await settleTask(id);
    }
    if (view.congested && !bgTasks.congested) toast(view.message);
    bgTasks.congested = view.congested;
  } catch (error) {
    bgTasks.failures += 1;
    if (bgTasks.failures >= 5) {  // Tagcast itself has stopped answering: tell everyone who is waiting
      for (const [id, watcher] of [...bgTasks.watched]) { bgTasks.watched.delete(id); watcher.reject(error); }
      bgTasks.view = null;
    }
  }
  renderTasks();
  if (bgTasks.watched.size || bgTasks.view?.active > 0) scheduleTaskPoll();
}

function tasksActive() {
  return bgTasks.watched.size > 0 || (bgTasks.view?.active || 0) > 0;
}

function renderTasks() {
  const root = $("#tasks");
  if (!root) return;
  const tasks = bgTasks.view?.tasks || [];
  const going = tasks.filter(t => t.state === "queued" || t.state === "running");
  const failed = tasks.filter(t => t.state === "error" && !bgTasks.dismissed.has(t.id));
  const waiting = going.filter(t => t.state === "queued").length;
  root.hidden = !going.length && !failed.length;
  if (root.hidden) bgTasks.open = false;
  const pill = $("#tasks-pill");
  pill.dataset.state = going.length ? (bgTasks.view?.congested ? "busy" : "working") : "error";
  const parts = [];
  if (going.length) parts.push(`Saving ${going.length} ${going.length === 1 ? "change" : "changes"}${waiting ? ` · ${waiting} waiting` : ""}`);
  if (failed.length) parts.push(`${failed.length} not saved`);
  pill.textContent = parts.join(" · ");
  pill.setAttribute("aria-expanded", String(bgTasks.open));
  const panel = $("#tasks-panel");
  panel.hidden = !bgTasks.open || root.hidden;
  if (panel.hidden) return;
  const done = tasks.filter(t => t.state === "done").slice(0, 3);
  const row = (task, text, extra = "") => `<li data-state="${escapeHtml(task.state)}"><div><strong>${escapeHtml(task.label)}</strong><br><span class="muted">${escapeHtml(text)}</span></div>${extra}</li>`;
  panel.innerHTML = `${bgTasks.view?.message ? `<p class="tasks-note">${escapeHtml(bgTasks.view.message)}</p>` : ""}`
    + `<ul class="tasks-list">${going.map(t => row(t, t.state === "running" ? (t.note || `Saving to iBroadcast… ${Math.round(t.running_for || 0)} s`) : `Waiting · ${Math.round(t.waited || 0)} s`)).join("")}`
    + failed.map(t => row(t, t.error || "This change was not saved.", `<button type="button" class="button small" data-task-dismiss="${escapeHtml(t.id)}">Dismiss</button>`)).join("")
    + done.map(t => row(t, `Saved${t.took != null ? ` · ${t.took} s` : ""}`)).join("") + "</ul>";
}

document.addEventListener("click", event => {
  if (event.target.closest("#tasks-pill")) { bgTasks.open = !bgTasks.open; renderTasks(); return; }
  const dismiss = event.target.closest("[data-task-dismiss]");
  if (dismiss) { bgTasks.dismissed.add(dismiss.dataset.taskDismiss); renderTasks(); }
});

// Closing Tagcast drops whatever is still waiting in the queue, so ask first.
window.addEventListener("beforeunload", event => {
  if (!tasksActive()) return;
  event.preventDefault();
  event.returnValue = "";
});
