"use strict";

// Downloads (fork v9): check songs/albums against iBroadcast and the download history,
// decide about possible duplicates, see the queue and the history, import the old
// Galaxy/Telegram histories and choose the download folder. Duplicates are never
// queued automatically, and nothing is uploaded to iBroadcast.
const downloadsView = {
  tab: "check", lines: "", info: null, queue: null, held: null, results: null, checkKind: "song",
  history: {query: "", source: "", offset: 0, data: null}, picked: new Set(), busy: false, timer: null,
};
const downloadsTabs = [["check", "Check"], ["decide", "Needs your decision"], ["queue", "Queue & history"], ["settings", "Folder & imports"]];
const downloadsStatusLabels = {have: "In iBroadcast", partly: "Partly (other edition)", downloaded: "Downloaded before", new: "New"};
const downloadsSourceLabels = {galaxy: "Galaxy", telegram: "Telegram", bandcamp: "Bandcamp", soulseek: "Soulseek", manual: "Added here"};

function downloadsDate(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"});
}

// Every failure shows its message with the fork version and plays the alert sound.
function downloadsError(error) {
  setActionStatus("#downloads-status", `${error.message || error} (${artistCleanupVersion})`);
  artistCleanupAlarm();
}

function downloadsNote(message) {
  setActionStatus("#downloads-status", "");
  if (message) toast(message);
}

function showDownloads(tab) {
  if (tab) downloadsView.tab = tab;
  showScreen("downloads");
  $("#downloads-version").textContent = artistCleanupVersion;
  renderDownloads();
  loadDownloads();
}

async function loadDownloads() {
  try {
    const [info, queue] = await Promise.all([api("/api/downloads"), api("/api/downloads/queue")]);
    downloadsView.info = info; downloadsView.queue = queue.queue; downloadsView.held = queue.held;
    for (const id of [...downloadsView.picked]) if (!queue.held.some(h => h.id === id)) downloadsView.picked.delete(id);
    if (downloadsView.tab === "queue") await loadDownloadsHistory();
    renderDownloads();
  } catch (error) { downloadsError(error); }
}

async function loadDownloadsHistory() {
  const h = downloadsView.history;
  const params = new URLSearchParams({q: h.query, source: h.source, offset: String(h.offset)});
  h.data = await api(`/api/downloads/history?${params}`);
}

function renderDownloads() {
  const view = downloadsView, held = view.held?.length || 0;
  $("#downloads-tabs").innerHTML = downloadsTabs.map(([key, label]) => `<button type="button" role="tab" data-downloads-tab="${key}"
    aria-selected="${view.tab === key}" tabindex="${view.tab === key ? 0 : -1}">${label}${key === "decide" && held ? ` <b class="dl-count">${held}</b>` : ""}</button>`).join("");
  const body = {check: downloadsCheckHtml, decide: downloadsDecideHtml, queue: downloadsQueueHtml, settings: downloadsSettingsHtml}[view.tab]();
  $("#downloads-content").innerHTML = body;
}

function downloadsYouHave(rows) {
  return (rows || []).map(r => r.where === "ibroadcast"
    ? `<span>iBroadcast: ${escapeHtml(r.title)} · ${escapeHtml(r.artist)}${r.album ? ` · <i>${escapeHtml(r.album)}</i>` : ""}${r.tracks ? ` · ${r.tracks} tracks` : ""}</span>`
    : `<span>Downloaded ${escapeHtml(downloadsDate(r.when) || "before")} via ${escapeHtml(downloadsSourceLabels[r.source] || r.source || "?")}: ${escapeHtml(r.title)}${r.artist ? ` · ${escapeHtml(r.artist)}` : ""}${r.songs ? ` (${r.songs} songs)` : ""}</span>`).join("");
}

function downloadsItemLabel(item) {
  const tracks = item.kind === "album" && item.tracks ? ` · ${item.tracks} tracks` : "";
  return `${item.kind === "album" ? "Album: " : ""}<b>${escapeHtml(item.title)}</b>${item.artist ? ` · ${escapeHtml(item.artist)}` : ""}${tracks}`;
}

function downloadsCheckHtml() {
  const view = downloadsView, results = view.results;
  const counts = results ? Object.keys(downloadsStatusLabels).map(s => [s, results.filter(r => r.status === s).length]).filter(([, n]) => n) : [];
  return `<p class="muted">One per line: <code>Artist - Title</code>. Albums may end with <code>| 12</code> for the number of tracks; another track count counts as a partly owned edition.</p>
    <div class="dl-kind" role="radiogroup" aria-label="What you check">
      <label><input type="radio" name="dl-kind" value="song" ${view.checkKind === "song" ? "checked" : ""}> Songs</label>
      <label><input type="radio" name="dl-kind" value="album" ${view.checkKind === "album" ? "checked" : ""}> Albums</label></div>
    <textarea id="downloads-lines" class="dl-lines" rows="6" maxlength="100000" placeholder="${view.checkKind === "album" ? "Company Flow - Funcrusher Plus | 15" : "Coldplay - Yellow"}" aria-label="Songs or albums to check">${escapeHtml(view.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-downloads-check ${view.busy ? "disabled" : ""}>Check</button>
      ${results?.length ? `<button type="button" class="button" data-downloads-add>Add to queue…</button><span class="muted">New items are queued; everything you may have goes to Needs your decision.</span>` : ""}</div>
    ${results ? `<p class="dl-summary" role="status">${counts.map(([s, n]) => `<span class="dl-badge ${s}">${n} ${downloadsStatusLabels[s]}</span>`).join(" ") || "Nothing to check."}</p>
    <ul class="dl-list">${results.map(r => `<li><span class="dl-badge ${r.status}">${downloadsStatusLabels[r.status]}</span>
      <div><div>${downloadsItemLabel(r)}</div><div class="dl-have">${downloadsYouHave(r.you_have)}</div></div></li>`).join("")}</ul>` : ""}
    <p class="muted">Bandcamp, Soulseek, Galaxy, Telegram and artist discographies arrive in later versions (v10–v14).</p>`;
}

function downloadsDecideHtml() {
  const held = downloadsView.held;
  if (!held) return '<p class="muted">Loading…</p>';
  if (!held.length) return '<p class="muted">Nothing is waiting. Possible duplicates from any source appear here, and are never downloaded until you choose Download anyway.</p>';
  const all = held.every(h => downloadsView.picked.has(h.id)), picked = downloadsView.picked.size;
  return `<p class="muted">You may already have these. Choose for each one, or tick several and decide in bulk.</p>
    <div class="dl-actions"><label><input type="checkbox" data-downloads-pick-all ${all ? "checked" : ""}> Select all (${held.length})</label>
      <button type="button" class="button" data-downloads-decide="skip" ${picked ? "" : "disabled"}>Skip ${picked || ""}</button>
      <button type="button" class="button primary" data-downloads-decide="download" ${picked ? "" : "disabled"}>Download anyway ${picked || ""}</button></div>
    <ul class="dl-list dl-decide">${held.map(h => `<li>
      <input type="checkbox" data-downloads-pick="${escapeHtml(h.id)}" aria-label="Select ${escapeHtml(h.title)}" ${downloadsView.picked.has(h.id) ? "checked" : ""}>
      <div class="dl-compare"><div><span class="eyebrow">THIS ONE · ${escapeHtml(downloadsSourceLabels[h.source] || h.source)}</span>${downloadsItemLabel(h)}</div>
        <div><span class="eyebrow">YOU HAVE · ${escapeHtml(downloadsStatusLabels[h.status] || h.status)}</span><div class="dl-have">${downloadsYouHave(h.you_have)}</div></div></div>
      <div class="dl-row-actions"><button type="button" class="button small" data-downloads-one="skip" data-id="${escapeHtml(h.id)}">Skip</button>
        <button type="button" class="button small" data-downloads-one="download" data-id="${escapeHtml(h.id)}">Download anyway</button></div></li>`).join("")}</ul>`;
}

function downloadsQueueHtml() {
  const queue = downloadsView.queue || [], h = downloadsView.history, data = h.data;
  const stateLabel = item => item.state === "skipped" ? "Skipped" : item.state === "queued"
    ? (item.allow_duplicate ? "Queued (you chose Download anyway)" : "Queued · waits for a source (v10+)") : item.state;
  const sources = Object.entries(downloadsView.info?.history_sources || {});
  const pages = data ? Math.ceil(data.total / 50) : 0, page = Math.floor(h.offset / 50);
  return `<h3>Queue</h3>
    ${queue.length ? `<ul class="dl-list">${queue.map(i => `<li><span class="dl-badge ${i.state === "skipped" ? "partly" : "new"}">${escapeHtml(stateLabel(i))}</span>
      <div>${downloadsItemLabel(i)}<div class="muted">${escapeHtml(downloadsSourceLabels[i.source] || i.source)} · added ${escapeHtml(downloadsDate(i.added))}</div></div>
      <button type="button" class="button small" data-downloads-remove="${escapeHtml(i.id)}" aria-label="Remove ${escapeHtml(i.title)} from the list">Remove</button></li>`).join("")}</ul>`
      : '<p class="muted">The queue is empty.</p>'}
    <h3>History <span class="muted">${downloadsView.info ? `${downloadsView.info.history.toLocaleString("en")} songs` : ""}</span></h3>
    <div class="toolbar dl-history-tools"><div class="search"><span aria-hidden="true">⌕</span><input id="downloads-history-search" maxlength="200" placeholder="Search the history" aria-label="Search the download history" value="${escapeHtml(h.query)}"></div>
      <select id="downloads-history-source" aria-label="Source"><option value="">All sources</option>${sources.map(([s, n]) => `<option value="${escapeHtml(s)}" ${h.source === s ? "selected" : ""}>${escapeHtml(downloadsSourceLabels[s] || s)} (${n})</option>`).join("")}</select></div>
    ${!data ? '<p class="muted">Loading…</p>' : !data.total ? '<p class="muted">No songs yet. Import your Galaxy and Telegram histories under Folder &amp; imports.</p>'
      : `<table class="dl-table"><thead><tr><th>Song</th><th>Artist</th><th>Album</th><th>Source</th><th>Date</th></tr></thead><tbody>${data.entries.map(e => `<tr>
        <td>${escapeHtml(e.title)}</td><td>${escapeHtml((e.artists || []).join(", ") || "—")}</td><td>${escapeHtml(e.album || "—")}</td>
        <td>${escapeHtml(downloadsSourceLabels[e.source] || e.source || "")}</td><td>${escapeHtml(downloadsDate(e.when))}</td></tr>`).join("")}</tbody></table>
      <div class="pagination">${pages > 1 ? `<button type="button" class="button small" data-downloads-page="${page - 1}" ${page ? "" : "disabled"}>← Previous</button>
        <span>Page ${page + 1} of ${pages} · ${data.total.toLocaleString("en")} songs</span>
        <button type="button" class="button small" data-downloads-page="${page + 1}" ${page + 1 < pages ? "" : "disabled"}>Next →</button>` : ""}</div>`}`;
}

function downloadsSettingsHtml() {
  const info = downloadsView.info, imports = info?.imports || {};
  const done = kind => imports[kind] ? `<p class="muted">Last import ${escapeHtml(downloadsDate(imports[kind].when))}: ${imports[kind].added} new of ${imports[kind].found} (${escapeHtml(imports[kind].path)}).</p>` : "";
  return `<section class="dl-card"><h3>Download folder</h3>
      <p class="muted">Every source saves here (as <code>Album Artist\\Album\\## - Title</code>). Tagcast creates the folder if needed.</p>
      <div class="dl-actions"><input id="downloads-folder" class="dl-path" maxlength="400" value="${escapeHtml(info?.folder || "")}" aria-label="Download folder">
      <button type="button" class="button primary" data-downloads-folder>Save folder</button></div>
      ${info && !info.folder_set ? '<p class="muted">Not saved yet: this is the default.</p>' : ""}</section>
    <section class="dl-card"><h3>Import the Galaxy history</h3>
      <p class="muted">The Galaxy app keeps <code>history.json</code> in its own folder, next to <code>app.py</code> and <code>run.bat</code>.</p>
      <div class="dl-actions"><input id="downloads-galaxy-path" class="dl-path" maxlength="400" placeholder="C:\\Users\\you\\Documents\\gd-music-downloader\\history.json" aria-label="Galaxy history.json">
      <button type="button" class="button" data-downloads-find>Find it</button><button type="button" class="button primary" data-downloads-import="galaxy">Import</button></div>
      <div id="downloads-galaxy-found"></div>${done("galaxy")}</section>
    <section class="dl-card"><h3>Import the Telegram history</h3>
      <p class="muted">Reads <code>state.json</code> and <code>download_history.csv</code>. Importing again adds only what is new.</p>
      <div class="dl-actions"><input id="downloads-telegram-path" class="dl-path" maxlength="400" value="${escapeHtml(info?.telegram_folder || "")}" aria-label="Telegram downloader folder">
      <button type="button" class="button primary" data-downloads-import="telegram">Import</button></div>${done("telegram")}</section>`;
}

async function downloadsRun(work) {
  if (downloadsView.busy) return;
  downloadsView.busy = true;
  try { await work(); } catch (error) { downloadsError(error); } finally { downloadsView.busy = false; }
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("#show-downloads")) { showDownloads(); return; }
  if (!target.closest("#downloads-page")) return;
  const tab = target.closest("[data-downloads-tab]");
  if (tab) {
    downloadsView.tab = tab.dataset.downloadsTab; setActionStatus("#downloads-status", "");
    renderDownloads(); loadDownloads();
    $(`[data-downloads-tab="${downloadsView.tab}"]`)?.focus();
    return;
  }
  if (target.closest("[data-downloads-check]")) {
    downloadsRun(async () => {
      const lines = downloadsView.lines = $("#downloads-lines").value;
      if (!lines.trim()) { toast("Type at least one line to check."); return; }
      const data = await api("/api/downloads/check", {lines, kind: downloadsView.checkKind});
      downloadsView.results = data.results; renderDownloads(); downloadsNote("");
    });
    return;
  }
  if (target.closest("[data-downloads-add]")) {
    downloadsRun(async () => {
      const items = downloadsView.results.map(({kind, title, artist, tracks}) => ({kind, title, artist, tracks}));
      const data = await api("/api/downloads/submit", {items});
      downloadsView.results = null;
      downloadsNote(`${data.queued} queued, ${data.held} waiting for your decision${data.skipped ? `, ${data.skipped} already listed` : ""}.`);
      downloadsView.tab = data.held ? "decide" : "queue";
      await loadDownloads();
    });
    return;
  }
  const decide = target.closest("[data-downloads-decide], [data-downloads-one]");
  if (decide) {
    const one = decide.dataset.downloadsOne, action = one || decide.dataset.downloadsDecide;
    const ids = one ? [decide.dataset.id] : [...downloadsView.picked];
    downloadsRun(async () => {
      const data = await api("/api/downloads/decide", {ids, action});
      for (const id of ids) downloadsView.picked.delete(id);
      downloadsNote(`${data.decided} ${action === "skip" ? "skipped" : "added to the queue"}.`);
      await loadDownloads();
    });
    return;
  }
  const remove = target.closest("[data-downloads-remove]");
  if (remove) { downloadsRun(async () => { await api("/api/downloads/remove", {ids: [remove.dataset.downloadsRemove]}); await loadDownloads(); }); return; }
  const page = target.closest("[data-downloads-page]");
  if (page) {
    downloadsView.history.offset = Math.max(0, Number(page.dataset.downloadsPage)) * 50;
    downloadsRun(async () => { await loadDownloadsHistory(); renderDownloads(); });
    return;
  }
  if (target.closest("[data-downloads-folder]")) {
    downloadsRun(async () => {
      downloadsView.info = await api("/api/downloads/folder", {folder: $("#downloads-folder").value});
      renderDownloads(); downloadsNote(`Downloads go to ${downloadsView.info.folder}.`);
    });
    return;
  }
  if (target.closest("[data-downloads-find]")) {
    downloadsRun(async () => {
      const {paths} = await api("/api/downloads/find-galaxy");
      if (paths.length) $("#downloads-galaxy-path").value = paths[0];
      $("#downloads-galaxy-found").innerHTML = paths.length > 1
        ? `<p class="muted">Also found: ${paths.slice(1).map(p => `<button type="button" class="text-action" data-downloads-use="${escapeHtml(p)}">${escapeHtml(p)}</button>`).join(", ")}</p>`
        : paths.length ? "" : '<p class="muted">Not found in Desktop, Documents, Downloads, Music or OneDrive. Paste the path of the Galaxy app folder\'s history.json.</p>';
    });
    return;
  }
  const use = target.closest("[data-downloads-use]");
  if (use) { $("#downloads-galaxy-path").value = use.dataset.downloadsUse; return; }
  const importButton = target.closest("[data-downloads-import]");
  if (importButton) {
    const kind = importButton.dataset.downloadsImport;
    downloadsRun(async () => {
      const data = await api("/api/downloads/import", {kind, path: $(`#downloads-${kind}-path`).value});
      downloadsNote(`${kind === "galaxy" ? "Galaxy" : "Telegram"}: ${data.added} songs added, ${data.already} already in the history (${data.history} in total).`);
      await loadDownloads();
    });
  }
});

document.addEventListener("change", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page")) return;
  if (target.name === "dl-kind") {
    downloadsView.checkKind = target.value; downloadsView.results = null; renderDownloads();
  } else if (target.matches("[data-downloads-pick]")) {
    downloadsView.picked[target.checked ? "add" : "delete"](target.dataset.downloadsPick); renderDownloads();
  } else if (target.matches("[data-downloads-pick-all]")) {
    downloadsView.picked = new Set(target.checked ? downloadsView.held.map(h => h.id) : []); renderDownloads();
  } else if (target.id === "downloads-history-source") {
    Object.assign(downloadsView.history, {source: target.value, offset: 0});
    downloadsRun(async () => { await loadDownloadsHistory(); renderDownloads(); });
  }
});

document.addEventListener("input", event => {
  if (event.target.id === "downloads-lines") { downloadsView.lines = event.target.value; return; }
  if (event.target.id !== "downloads-history-search") return;
  clearTimeout(downloadsView.timer);
  downloadsView.timer = setTimeout(() => {
    Object.assign(downloadsView.history, {query: event.target.value, offset: 0});
    downloadsRun(async () => {
      await loadDownloadsHistory(); renderDownloads();
      const input = $("#downloads-history-search");
      input.focus(); input.setSelectionRange(input.value.length, input.value.length);
    });
  }, 300);
});

document.addEventListener("keydown", event => {
  const tab = event.target.closest?.("[data-downloads-tab]");
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const keys = downloadsTabs.map(([key]) => key), index = keys.indexOf(tab.dataset.downloadsTab);
  const next = event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + keys.length) % keys.length;
  $(`[data-downloads-tab="${keys[next]}"]`).click();
});
