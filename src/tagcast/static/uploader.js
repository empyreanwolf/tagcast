"use strict";

// Upload (fork v72): an Mp3tag-style grid for the files you are about to put into iBroadcast. Scan a folder, edit tags
// (one file, or many at once with <keep>), see songs you may already have, pick what to upload. Files are never moved
// or deleted. Edits stay "pending" (yellow) until Save; Undo puts the last save back. Duplicates are never decided
// for you: each flagged song waits for Skip or Upload anyway.
const uploaderView = {data: null, loading: false, status: "", folder: "", sel: new Set(), anchor: "", q: "", sortKey: "name", sortDir: 1,
  edits: new Map(), dupes: {}, dupeLibrary: true, decisions: new Map(), suggest: {}, showUploaded: false, dates: "all", pickDate: "",
  folderFilter: "", editing: null, open: new Set(), panel: "", timer: null, busy: false, bulk: {}, settingsOpen: false, dupesFor: ""};

const uploaderCols = [["name", "File name", false], ["title", "Title", true], ["artist", "Artist", true], ["album_artist", "Album artist", true],
  ["album", "Album", true], ["track_no", "#", true], ["disc", "Disc", true], ["year", "Year", true], ["genre", "Genre", true],
  ["has_cover", "Cover", false], ["format", "Format", false], ["size", "Size", false], ["created", "Created", false]];
const uploaderFields = ["title", "artist", "album_artist", "album", "track_no", "disc", "year", "genre"];
const uploaderDay = 86400;

function uploaderValue(row, key) {
  const edit = uploaderView.edits.get(row.id);
  return edit && key in edit ? edit[key] : row[key];
}

function uploaderSize(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }
function uploaderDate(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString("en-GB", {day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"}) : "";
}

function uploaderRows() {
  const v = uploaderView, d = v.data;
  if (!d) return [];
  const q = v.q.trim().toLowerCase(), now = Date.now() / 1000;
  const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0);
  const since = {today: startOfToday.getTime() / 1000, week: now - 7 * uploaderDay, since: d.last_upload || 0,
    pick: v.pickDate ? new Date(`${v.pickDate}T00:00:00`).getTime() / 1000 : 0}[v.dates];
  const rows = d.rows.filter(r => {
    if (r.uploaded && !v.showUploaded) return false;
    if (v.folderFilter && r.folder !== v.folderFilter) return false;
    if (v.dates !== "all" && since && r.created < since) return false;
    return !q || uploaderFields.concat(["name"]).some(k => String(uploaderValue(r, k) ?? "").toLowerCase().includes(q));
  });
  const key = v.sortKey, dir = v.sortDir;
  return rows.sort((a, b) => {
    const x = key === "name" || key === "format" || key === "size" || key === "created" || key === "has_cover" ? a[key] : uploaderValue(a, key);
    const y = key === "name" || key === "format" || key === "size" || key === "created" || key === "has_cover" ? b[key] : uploaderValue(b, key);
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), undefined, {numeric: true, sensitivity: "base"})) * dir;
  });
}

function uploaderDupeNeeds(row) { return Boolean(uploaderView.dupes[row.id]) && !uploaderView.decisions.has(row.id) && !row.uploaded; }

async function uploaderLoad(scan = false) {
  const v = uploaderView;
  v.loading = true; v.status = scan ? "Scanning the folder…" : ""; renderDownloads();
  try {
    v.data = scan ? await api("/api/uploader/scan", {folder: v.folder}) : await api("/api/uploader");
    if (!v.folder) v.folder = v.data.settings.folder || "";
    v.status = scan ? `${v.data.rows.length} files found.` : "";
    if (scan) { v.sel = new Set(); v.edits = new Map(); v.decisions = new Map(); v.open = new Set(); }
    uploaderPoll();
    if (v.data.rows.length) uploaderChecks();
  } catch (error) { v.status = error.message || String(error); if (!v.data) v.data = {rows: [], settings: {folder: "", genres: []}, genres: [], failed: [], progress: null}; }
  v.loading = false; renderDownloads();
}

async function uploaderChecks() {
  const v = uploaderView, ids = v.data.rows.filter(r => !r.uploaded && r.editable).map(r => r.id);
  if (!ids.length) return;
  try {
    const [dupes, artists] = await Promise.all([api("/api/uploader/duplicates", {ids}),
      api("/api/uploader/artists", {names: [...new Set(v.data.rows.map(r => r.artist).filter(Boolean))]})]);
    v.dupes = dupes.duplicates || {}; v.dupeLibrary = dupes.library !== false; v.suggest = artists.artists || {};
  } catch (error) { v.status = error.message || String(error); }
  renderDownloads();
}

function uploaderPoll() {
  const v = uploaderView;
  clearTimeout(v.timer);
  if (!v.data?.progress?.running) return;
  v.timer = setTimeout(async () => {
    try { const fresh = await api("/api/uploader"); v.data.progress = fresh.progress; v.data.failed = fresh.failed; v.data.last_upload = fresh.last_upload;
      if (!fresh.progress?.running) { v.data = {...fresh, rows: fresh.rows}; v.status = `Upload finished: ${fresh.progress?.ok || 0} sent, ${fresh.progress?.failed || 0} failed.`; v.busy = false; }
    } catch { /* try again on the next tick */ }
    renderDownloads(); uploaderPoll();
  }, 1200);
}

// -- the page ------------------------------------------------------------------------------------

function uploaderHtml() {
  const v = uploaderView, d = v.data;
  if (!d) { if (!v.loading) uploaderLoad(); return '<p class="muted">Loading…</p>'; }
  const rows = uploaderRows(), pending = v.edits.size, picked = rows.filter(r => v.sel.has(r.id));
  const toUpload = picked.filter(r => !r.uploaded && r.editable !== false && !(v.decisions.get(r.id) === "skip"));
  const blocked = toUpload.filter(uploaderDupeNeeds).length;
  const folders = [...new Set(d.rows.map(r => r.folder).filter(Boolean))].sort();
  const p = d.progress;
  return `<section class="dl-card upl-top">
      <h3>Upload</h3>
      <p class="muted">Check and tidy the tags of files before they go to iBroadcast. Your files are never moved or deleted: after an upload they stay where they are, marked ✓ Uploaded.${d.connected === false ? " <b>Connect your iBroadcast account to upload.</b>" : ""}</p>
      <div class="dl-actions"><input id="uploader-folder" class="dl-path" maxlength="500" value="${escapeHtml(v.folder)}" placeholder="Folder with the files to upload" aria-label="Folder to scan">
        <button type="button" class="button primary" data-uploader="scan" ${v.loading ? "disabled" : ""}>${d.rows.length ? "↻ Rescan" : "Scan this folder"}</button>
        <label class="checkline"><input type="checkbox" data-uploader-default ${v.folder && v.folder === d.settings.folder ? "checked" : ""}> Use as my default folder</label></div>
      <p class="muted" role="status">${escapeHtml(v.status || (d.scanned ? `Scanned ${uploaderDate(d.scanned)}` : "Not scanned yet."))}</p>
      ${uploaderSettingsHtml()}</section>
    ${p ? `<p class="dl-genre-banner" role="status">${p.running ? `Uploading ${p.done} of ${p.total}: ${escapeHtml(p.name || "")}…` : `Last upload: ${p.ok} sent${p.failed ? `, ${p.failed} failed (see below)` : ""}.`}</p>` : ""}
    ${uploaderFailedHtml()}
    ${d.rows.length ? `<div class="dl-actions upl-filters">
        <div class="search"><span aria-hidden="true">⌕</span><input id="uploader-search" maxlength="200" placeholder="Search the files" aria-label="Search the files" value="${escapeHtml(v.q)}"></div>
        <select data-uploader-dates aria-label="File created date">${[["all", "Any date"], ["today", "Created today"], ["week", "Last 7 days"], ["since", "Since last upload"], ["pick", "Pick a date…"]].map(([k, l]) => `<option value="${k}" ${v.dates === k ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${v.dates === "pick" ? `<input type="date" data-uploader-pick value="${escapeHtml(v.pickDate)}" aria-label="Created on or after">` : ""}
        ${folders.length ? `<select data-uploader-folder-filter aria-label="Folder"><option value="">All folders</option>${folders.map(f => `<option ${v.folderFilter === f ? "selected" : ""}>${escapeHtml(f)}</option>`).join("")}</select>` : ""}
        <label class="checkline"><input type="checkbox" data-uploader-uploaded ${v.showUploaded ? "checked" : ""}> Show uploaded (${d.rows.filter(r => r.uploaded).length})</label></div>
      <div class="dl-actions upl-bar">
        <span class="muted">${picked.length} of ${rows.length} selected</span>
        <button type="button" class="button" data-uploader="select-all">${rows.length && picked.length === rows.length ? "Select none" : "Select all"}</button>
        <button type="button" class="button primary" data-uploader="save" ${pending && !v.busy ? "" : "disabled"}>💾 Save (${pending})</button>
        <button type="button" class="button" data-uploader="discard" ${pending ? "" : "disabled"}>Discard changes</button>
        <button type="button" class="button" data-uploader="undo" ${d.can_undo ? "" : "disabled"}>↶ Undo last save</button>
        <button type="button" class="button" data-uploader-panel="edit" aria-expanded="${v.panel === "edit"}">Edit selected…</button>
        <button type="button" class="button" data-uploader-panel="tools" aria-expanded="${v.panel === "tools"}">Tools…</button>
        <button type="button" class="button primary" data-uploader="upload" ${toUpload.length && !blocked && !pending && !(p?.running) && d.connected !== false ? "" : "disabled"} title="${pending ? "Save your changes first" : blocked ? "Decide the flagged duplicates first" : ""}">⬆ Upload selected (${toUpload.length})</button></div>
      ${blocked ? `<p class="error-text" role="status">${blocked} selected ${blocked === 1 ? "song looks" : "songs look"} like ${blocked === 1 ? "one" : "ones"} you may already have. Choose Skip or Upload anyway on each ⚠ row first.</p>` : ""}
      ${Object.keys(v.dupes).length ? `<div class="dl-actions"><span class="muted">${Object.keys(v.dupes).length} possible duplicates.</span>
        <button type="button" class="button small" data-uploader="skip-dupes">Skip all duplicates</button></div>` : ""}
      ${v.dupeLibrary === false ? '<p class="muted">The iBroadcast library is not loaded, so only the files here were compared.</p>' : ""}
      ${v.panel === "edit" ? uploaderEditHtml() : ""}${v.panel === "tools" ? uploaderToolsHtml() : ""}
      <div class="upl-scroll"><table class="upl-grid"><thead><tr><th><input type="checkbox" data-uploader-all aria-label="Select all" ${rows.length && picked.length === rows.length ? "checked" : ""}></th><th></th>
        ${uploaderCols.map(([k, l]) => `<th><button type="button" class="text-action" data-uploader-sort="${k}">${l}${v.sortKey === k ? (v.sortDir > 0 ? " ▲" : " ▼") : ""}</button></th>`).join("")}</tr></thead>
        <tbody>${rows.map(uploaderRowHtml).join("") || `<tr><td colspan="${uploaderCols.length + 2}" class="muted">${d.rows.some(r => r.uploaded) && !v.showUploaded ? "Everything here is uploaded. Tick “Show uploaded” to see it." : "No files match."}</td></tr>`}</tbody></table></div>`
      : '<p class="muted">Scan a folder to see its files here.</p>'}
    <datalist id="uploader-genre-cells">${(d.genres || []).map(g => `<option value="${escapeHtml(g)}">`).join("")}</datalist>`;
}

function uploaderSettingsHtml() {
  const v = uploaderView, s = v.data.settings, genres = s.genres || [];
  return `<details class="dl-card" ${v.settingsOpen ? "open" : ""} data-uploader-details><summary>Upload settings · genre list (${genres.length})</summary>
    <p class="muted">Your genre list comes first in every genre drop-down (here and in Clean up), in this order. The other genres iBroadcast has follow, A to Z.</p>
    <ol class="upl-genres">${genres.map((g, i) => `<li>${escapeHtml(g)}
      <button type="button" class="text-action" data-uploader-genre="up" data-index="${i}" aria-label="Move ${escapeHtml(g)} up" ${i ? "" : "disabled"}>▲</button>
      <button type="button" class="text-action" data-uploader-genre="down" data-index="${i}" aria-label="Move ${escapeHtml(g)} down" ${i < genres.length - 1 ? "" : "disabled"}>▼</button>
      <button type="button" class="text-action" data-uploader-genre="remove" data-index="${i}" aria-label="Remove ${escapeHtml(g)}">✕</button></li>`).join("") || '<li class="muted">No genres in your list.</li>'}</ol>
    <div class="dl-actions"><input id="uploader-genre-add" class="dl-path" maxlength="100" list="uploader-genre-options" placeholder="Add a genre" aria-label="Add a genre">
      <datalist id="uploader-genre-options">${(v.data.genres || []).filter(g => !genres.includes(g)).map(g => `<option value="${escapeHtml(g)}">`).join("")}</datalist>
      <button type="button" class="button" data-uploader-genre="add">Add</button></div></details>`;
}

function uploaderFailedHtml() {
  const failed = uploaderView.data?.failed || [];
  if (!failed.length) return "";
  return `<section class="dl-card"><h3>Failed uploads (${failed.length})</h3>
    <div class="dl-actions"><button type="button" class="button small" data-uploader="retry-all">Retry all</button></div>
    <ul class="dl-list">${failed.map(f => `<li><div><b>${escapeHtml(f.name)}</b> <span class="muted">· ${uploaderDate(f.when)}</span>
      <div class="error-text">${escapeHtml(f.reason)}</div></div>
      <div><button type="button" class="text-action" data-uploader-failed="retry" data-path="${escapeHtml(f.path)}">Retry</button> ·
      <button type="button" class="text-action" data-uploader-failed="reveal" data-path="${escapeHtml(f.path)}">Show in folder</button> ·
      <button type="button" class="text-action" data-uploader-failed="remove" data-path="${escapeHtml(f.path)}">Remove</button></div></li>`).join("")}</ul></section>`;
}

function uploaderRowHtml(r) {
  const v = uploaderView, sel = v.sel.has(r.id), dupe = v.dupes[r.id], edit = v.edits.get(r.id) || {}, decision = v.decisions.get(r.id);
  const cell = key => {
    const val = uploaderValue(r, key), shown = key === "track_no" || key === "disc" ? (val || "") : (val ?? "");
    const editing = v.editing && v.editing.id === r.id && v.editing.key === key;
    const tip = key === "artist" || key === "album_artist" ? uploaderSuggestHtml(r, key, val) : "";
    return `<td class="${key in edit ? "upl-pending" : ""}" data-uploader-cell="${key}" data-id="${r.id}">${editing
      ? `<input data-uploader-input value="${escapeHtml(String(shown))}" maxlength="300" aria-label="${key}" ${key === "genre" ? 'list="uploader-genre-cells"' : ""}>`
      : `${escapeHtml(String(shown))}${tip}`}</td>`;
  };
  const row = `<tr class="${sel ? "upl-sel" : ""} ${r.uploaded ? "upl-done" : ""}" data-id="${r.id}">
    <td><input type="checkbox" data-uploader-pick="${r.id}" ${sel ? "checked" : ""} aria-label="Select ${escapeHtml(r.name)}"></td>
    <td>${r.uploaded ? `<span class="pill" title="Uploaded ${uploaderDate(r.uploaded)}">✓ ${uploaderDate(r.uploaded)}</span>` : ""}${dupe && !r.uploaded
      ? `<button type="button" class="pill missing" data-uploader-dupe="${r.id}" aria-expanded="${v.open.has(r.id)}">⚠ ${decision === "skip" ? "Skip" : decision === "upload" ? "Upload anyway" : "Duplicate?"}</button>` : ""}${r.error ? `<span class="pill missing" title="${escapeHtml(r.error)}">unreadable</span>` : ""}</td>
    <td>${escapeHtml(r.name)}</td>${["title", "artist", "album_artist", "album", "track_no", "disc", "year", "genre"].map(cell).join("")}
    <td>${r.has_cover ? "✓" : ""}</td><td>${escapeHtml((r.format || "").toUpperCase())}</td><td>${uploaderSize(r.size)}</td><td>${uploaderDate(r.created)}</td></tr>`;
  return row + (dupe && v.open.has(r.id) ? uploaderCompareHtml(r, dupe) : "");
}

function uploaderSuggestHtml(r, key, value) {
  const names = String(value || "").split(/\s*;\s*/).filter(Boolean), fixes = [];
  for (const name of names) { const s = uploaderView.suggest[name]; if (s && !s.known && s.suggest?.length) fixes.push([name, s.suggest[0].name]); }
  return fixes.map(([from, to]) => ` <button type="button" class="text-action" data-uploader-fix data-id="${r.id}" data-key="${key}" data-from="${escapeHtml(from)}" data-to="${escapeHtml(to)}" title="iBroadcast has “${escapeHtml(to)}”">→ ${escapeHtml(to)}</button>`).join("");
}

function uploaderCompareHtml(r, hits) {
  const decision = uploaderView.decisions.get(r.id);
  const where = {ibroadcast: "In iBroadcast", folder: "In this list", uploaded: "Uploaded before"};
  return `<tr class="upl-compare"><td></td><td colspan="${uploaderCols.length + 1}"><div class="upl-side">
    <div><b>This file</b><br>${escapeHtml(uploaderValue(r, "title"))}<br><span class="muted">${escapeHtml(uploaderValue(r, "artist"))} · ${escapeHtml(uploaderValue(r, "album"))}</span></div>
    ${hits.map(h => `<div><b>${where[h.where] || h.where}</b> <span class="pill">${h.how === "same" ? "same title" : "close title"}</span><br>${escapeHtml(h.title)}<br><span class="muted">${escapeHtml(h.artist)}${h.album ? ` · ${escapeHtml(h.album)}` : h.name ? ` · ${escapeHtml(h.name)}` : ""}</span></div>`).join("")}</div>
    <div class="dl-actions"><button type="button" class="button small ${decision === "skip" ? "primary" : ""}" data-uploader-decide="skip" data-id="${r.id}">Skip</button>
      <button type="button" class="button small ${decision === "upload" ? "primary" : ""}" data-uploader-decide="upload" data-id="${r.id}">Upload anyway</button></div></td></tr>`;
}

function uploaderEditHtml() {
  const v = uploaderView, genres = v.data.genres || [];
  const input = ([key, label]) => key === "genre"
    ? `<label>${label} <select data-uploader-bulk="genre"><option value="">&lt;keep&gt;</option>${genres.map(g => `<option ${v.bulk.genre === g ? "selected" : ""}>${escapeHtml(g)}</option>`).join("")}</select></label>`
    : `<label>${label} <input data-uploader-bulk="${key}" maxlength="300" placeholder="&lt;keep&gt;" value="${escapeHtml(v.bulk[key] || "")}"></label>`;
  return `<div class="dl-card upl-panel"><p class="muted">Fill only what should change on every selected file. Fields left as &lt;keep&gt; stay as they are.</p>
    <div class="upl-fields">${[["title", "Title"], ["artist", "Artist"], ["album_artist", "Album artist"], ["album", "Album"], ["track_no", "Track #"], ["disc", "Disc"], ["year", "Year"], ["genre", "Genre"]].map(input).join("")}</div>
    <div class="dl-actions"><button type="button" class="button primary" data-uploader="bulk-apply">Apply to the selected</button></div></div>`;
}

function uploaderToolsHtml() {
  const v = uploaderView, t = v.bulk;
  const field = name => `<select data-uploader-tool="${name}">${[["title", "Title"], ["artist", "Artist"], ["album_artist", "Album artist"], ["album", "Album"], ["genre", "Genre"]].map(([k, l]) => `<option value="${k}" ${(t[name] || "title") === k ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  return `<div class="dl-card upl-panel">
    <div class="dl-actions"><button type="button" class="button" data-uploader="number">Auto-number the selected (in the order shown)</button>
      <button type="button" class="button" data-uploader="split">Split artists (feat., &amp;, / …) · first = album artist</button>
      <button type="button" class="button" data-uploader="cover">Copy the cover of the first selected to the others</button></div>
    <div class="dl-actions"><b>Replace text</b> ${field("replaceField")} <input data-uploader-tool="find" maxlength="100" placeholder="Find" value="${escapeHtml(t.find || "")}">
      <input data-uploader-tool="with" maxlength="100" placeholder="Replace with" value="${escapeHtml(t.with || "")}"><button type="button" class="button" data-uploader="replace">Replace</button></div>
    <div class="dl-actions"><b>Letter case</b> ${field("caseField")}
      <button type="button" class="button" data-uploader-case="title">Title Case</button><button type="button" class="button" data-uploader-case="lower">lower case</button>
      <button type="button" class="button" data-uploader-case="upper">UPPER CASE</button><button type="button" class="button" data-uploader-case="sentence">Sentence case</button></div>
    <div class="dl-actions"><b>File name → tags</b> <input data-uploader-tool="fromName" maxlength="100" placeholder="%artist% - %title%" value="${escapeHtml(t.fromName ?? "%artist% - %title%")}" aria-label="Pattern"><button type="button" class="button" data-uploader="from-name">Fill tags</button></div>
    <div class="dl-actions"><b>Tags → file name</b> <input data-uploader-tool="toName" maxlength="100" placeholder="%track% %artist% - %title%" value="${escapeHtml(t.toName ?? "%track% %artist% - %title%")}" aria-label="File name pattern"><button type="button" class="button" data-uploader="to-name">Rename the files</button></div></div>`;
}

// -- editing helpers -----------------------------------------------------------------------------

function uploaderStage(id, fields) {
  const v = uploaderView, row = v.data.rows.find(r => r.id === id);
  if (!row) return;
  const edit = {...(v.edits.get(id) || {})};
  for (const [key, value] of Object.entries(fields)) {
    if (String(value ?? "") === String(row[key] ?? "") || (["track_no", "disc"].includes(key) && Number(value || 0) === Number(row[key] || 0))) delete edit[key]; else edit[key] = value;
  }
  if (Object.keys(edit).length) v.edits.set(id, edit); else v.edits.delete(id);
}

function uploaderSelected() { return uploaderRows().filter(r => uploaderView.sel.has(r.id)); }

function uploaderCase(value, mode) {
  const s = String(value || "");
  if (mode === "lower") return s.toLowerCase();
  if (mode === "upper") return s.toUpperCase();
  if (mode === "sentence") return s.toLowerCase().replace(/^(\s*\p{L})/u, m => m.toUpperCase());
  return s.toLowerCase().replace(/(^|[\s(\[\-–/"'])(\p{L})/gu, (m, a, b) => a + b.toUpperCase());
}

function uploaderFromName(name, pattern) {
  const stem = name.replace(/\.[^.]+$/, ""), keys = [];
  const source = pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/%(\w+)%/g, (m, k) => { keys.push(k); return "(.+?)"; });
  const found = stem.match(new RegExp(`^${source}$`));
  if (!found) return null;
  const out = {};
  keys.forEach((k, i) => { const key = k === "track" ? "track_no" : k; if (uploaderFields.includes(key)) out[key] = found[i + 1].trim(); });
  return out;
}

async function uploaderCall(path, body, ok) {
  const v = uploaderView;
  v.busy = true; renderDownloads();
  try { const r = await api(path, body); if (r.rows) v.data = {...v.data, ...r}; v.status = ok ? ok(r) : ""; return r; }
  catch (error) { v.status = error.message || String(error); }
  finally { v.busy = false; renderDownloads(); }
}

async function uploaderSaveGenres(genres) {
  const r = await uploaderCall("/api/uploader/settings", {genres});
  if (r) { uploaderView.data.settings = r.settings; uploaderView.data.genres = r.genres; renderDownloads(); }
}

// -- events ----------------------------------------------------------------------------------------

document.addEventListener("click", async event => {
  const t = event.target, v = uploaderView;
  if (!t.closest?.("[data-uploader],[data-uploader-panel],[data-uploader-sort],[data-uploader-case],[data-uploader-dupe],[data-uploader-decide],[data-uploader-fix],[data-uploader-genre],[data-uploader-failed]")) return;
  const act = t.closest("[data-uploader]")?.dataset.uploader;
  if (act === "scan") { v.folder = $("#uploader-folder")?.value.trim() || v.folder; await uploaderLoad(true); return; }
  if (act === "select-all") { const rows = uploaderRows(); v.sel = rows.length && rows.every(r => v.sel.has(r.id)) ? new Set() : new Set(rows.map(r => r.id)); renderDownloads(); return; }
  if (act === "discard") { v.edits = new Map(); renderDownloads(); return; }
  if (act === "save") {
    const edits = [...v.edits].map(([id, fields]) => ({id, fields}));
    const r = await uploaderCall("/api/uploader/save", {edits}, x => x.errors?.length ? `${x.saved} saved, ${x.errors.length} failed: ${x.errors[0].name}: ${x.errors[0].reason}` : `${x.saved} files saved.`);
    if (r) { v.edits = new Map(); uploaderChecks(); }
    return;
  }
  if (act === "undo") { const r = await uploaderCall("/api/uploader/undo", {}, () => "Last save undone."); if (r) uploaderChecks(); return; }
  if (act === "skip-dupes") { for (const id of Object.keys(v.dupes)) v.decisions.set(id, "skip"); renderDownloads(); return; }
  if (act === "upload") {
    const ids = uploaderSelected().filter(r => !r.uploaded && v.decisions.get(r.id) !== "skip").map(r => r.id);
    const r = await uploaderCall("/api/uploader/upload", {ids}, () => "Uploading…");
    if (r) { v.data.progress = r.progress; uploaderPoll(); }
    return;
  }
  if (act === "retry-all") { const r = await uploaderCall("/api/uploader/retry", {}, () => "Uploading again…"); if (r) { v.data.progress = r.progress; uploaderPoll(); } return; }
  if (act === "bulk-apply") {
    const fields = Object.fromEntries(Object.entries(v.bulk).filter(([k, val]) => uploaderFields.includes(k) && val !== "" && val != null));
    if (!Object.keys(fields).length) { v.status = "Type a value in at least one field."; renderDownloads(); return; }
    for (const r of uploaderSelected()) uploaderStage(r.id, fields);
    v.bulk = {}; v.status = "Changes are pending. Press Save to write them to the files."; renderDownloads(); return;
  }
  if (act === "number") {
    const rows = uploaderSelected(); rows.forEach((r, i) => uploaderStage(r.id, {track_no: i + 1}));
    v.status = `${rows.length} files numbered 1–${rows.length} (pending, press Save).`; renderDownloads(); return;
  }
  if (act === "split") {
    const r = await uploaderCall("/api/uploader/split", {ids: [...v.sel]}, x => `${Object.keys(x.edits).length} files change (pending, press Save).`);
    if (r) { for (const [id, f] of Object.entries(r.edits)) uploaderStage(id, f); renderDownloads(); }
    return;
  }
  if (act === "cover") {
    const rows = uploaderSelected(), first = rows.find(x => x.has_cover);
    if (!first) { v.status = "None of the selected files has a cover."; renderDownloads(); return; }
    await uploaderCall("/api/uploader/cover", {from: first.id, ids: rows.map(x => x.id)}, x => `Cover copied to ${x.saved} files.`); return;
  }
  if (act === "replace") {
    const field = v.bulk.replaceField || "title", find = v.bulk.find || "", rep = v.bulk.with || "";
    if (!find) { v.status = "Type the text to find."; renderDownloads(); return; }
    for (const r of uploaderSelected()) uploaderStage(r.id, {[field]: String(uploaderValue(r, field) ?? "").split(find).join(rep)});
    v.status = "Replaced (pending, press Save)."; renderDownloads(); return;
  }
  if (act === "from-name") {
    const pattern = v.bulk.fromName ?? "%artist% - %title%";
    let done = 0;
    for (const r of uploaderSelected()) { const f = uploaderFromName(r.name, pattern); if (f) { uploaderStage(r.id, f); done++; } }
    v.status = `${done} of ${uploaderSelected().length} file names fit the pattern (pending, press Save).`; renderDownloads(); return;
  }
  if (act === "to-name") {
    if (v.edits.size) { v.status = "Save your tag changes first, so the names use them."; renderDownloads(); return; }
    await uploaderCall("/api/uploader/rename", {ids: [...v.sel], pattern: v.bulk.toName ?? "%track% %artist% - %title%"}, x => `${x.renamed} files renamed${x.errors?.length ? `, ${x.errors.length} not: ${x.errors[0].name}: ${x.errors[0].reason}` : ""}.`);
    v.sel = new Set(); return;
  }
  const panel = t.closest("[data-uploader-panel]");
  if (panel) { v.panel = v.panel === panel.dataset.uploaderPanel ? "" : panel.dataset.uploaderPanel; renderDownloads(); return; }
  const sort = t.closest("[data-uploader-sort]");
  if (sort) { const key = sort.dataset.uploaderSort; v.sortDir = v.sortKey === key ? -v.sortDir : 1; v.sortKey = key; renderDownloads(); return; }
  const casing = t.closest("[data-uploader-case]");
  if (casing) { const field = v.bulk.caseField || "title"; for (const r of uploaderSelected()) uploaderStage(r.id, {[field]: uploaderCase(uploaderValue(r, field), casing.dataset.uploaderCase)}); v.status = "Letter case changed (pending, press Save)."; renderDownloads(); return; }
  const dupe = t.closest("[data-uploader-dupe]");
  if (dupe) { const id = dupe.dataset.uploaderDupe; v.open[v.open.has(id) ? "delete" : "add"](id); renderDownloads(); return; }
  const decide = t.closest("[data-uploader-decide]");
  if (decide) { v.decisions.set(decide.dataset.id, decide.dataset.uploaderDecide); renderDownloads(); return; }
  const fix = t.closest("[data-uploader-fix]");
  if (fix) {
    const row = v.data.rows.find(r => r.id === fix.dataset.id), key = fix.dataset.key;
    uploaderStage(row.id, {[key]: String(uploaderValue(row, key) ?? "").split(/\s*;\s*/).map(n => n === fix.dataset.from ? fix.dataset.to : n).join("; ")});
    renderDownloads(); return;
  }
  const genre = t.closest("[data-uploader-genre]");
  if (genre) {
    const list = [...(v.data.settings.genres || [])], i = Number(genre.dataset.index), what = genre.dataset.uploaderGenre;
    if (what === "add") { const value = $("#uploader-genre-add")?.value.trim(); if (!value) return; list.push(value); }
    else if (what === "remove") list.splice(i, 1);
    else { const j = what === "up" ? i - 1 : i + 1; if (j < 0 || j >= list.length) return; [list[i], list[j]] = [list[j], list[i]]; }
    v.settingsOpen = true; await uploaderSaveGenres(list); return;
  }
  const failed = t.closest("[data-uploader-failed]");
  if (failed) {
    const what = failed.dataset.uploaderFailed, path = failed.dataset.path;
    if (what === "retry") { const r = await uploaderCall("/api/uploader/retry", {paths: [path]}, () => "Uploading again…"); if (r) { v.data.progress = r.progress; uploaderPoll(); } }
    else if (what === "remove") await uploaderCall("/api/uploader/remove-failed", {paths: [path]}, () => "Removed from the list.");
    else await uploaderCall("/api/uploader/reveal", {path}, () => "");
  }
});

document.addEventListener("click", event => {  // select rows (Shift = a range, Ctrl = add one)
  const pick = event.target.closest?.("[data-uploader-pick]"), v = uploaderView;
  if (!pick) return;
  const id = pick.dataset.uploaderPick, rows = uploaderRows().map(r => r.id);
  if (event.shiftKey && v.anchor && rows.includes(v.anchor)) {
    const [a, b] = [rows.indexOf(v.anchor), rows.indexOf(id)].sort((x, y) => x - y);
    for (const rid of rows.slice(a, b + 1)) v.sel.add(rid);
  } else v.sel[pick.checked ? "add" : "delete"](id);
  v.anchor = id; renderDownloads();
});

document.addEventListener("click", event => {  // clicking a row's blank area selects it like Mp3tag
  const cell = event.target.closest?.("td[data-uploader-cell]");
  const v = uploaderView;
  if (!cell || event.target.closest("button, input, select")) return;
  const id = cell.dataset.id;
  if (event.ctrlKey || event.metaKey) v.sel[v.sel.has(id) ? "delete" : "add"](id);
  else if (event.shiftKey && v.anchor) { const rows = uploaderRows().map(r => r.id), [a, b] = [rows.indexOf(v.anchor), rows.indexOf(id)].sort((x, y) => x - y); rows.slice(a, b + 1).forEach(r => v.sel.add(r)); }
  else v.sel = new Set([id]);
  v.anchor = id; renderDownloads();
});

document.addEventListener("dblclick", event => {
  const cell = event.target.closest?.("td[data-uploader-cell]");
  if (!cell) return;
  uploaderView.editing = {id: cell.dataset.id, key: cell.dataset.uploaderCell}; renderDownloads();
  $("#downloads-content [data-uploader-input]")?.focus();
});

document.addEventListener("keydown", event => {
  const input = event.target.closest?.("[data-uploader-input]");
  if (!input) return;
  if (event.key === "Escape") { uploaderView.editing = null; renderDownloads(); }
  else if (event.key === "Enter") input.blur();
});

document.addEventListener("focusout", event => {
  const input = event.target.closest?.("[data-uploader-input]"), v = uploaderView;
  if (!input || !v.editing) return;
  const {id, key} = v.editing;
  v.editing = null;
  uploaderStage(id, {[key]: input.value});
  renderDownloads();
});

document.addEventListener("input", event => {
  const t = event.target, v = uploaderView;
  if (t.id === "uploader-search") { v.q = t.value; const at = t.selectionStart; renderDownloads(); const box = $("#uploader-search"); box?.focus(); box?.setSelectionRange?.(at, at); }
  else if (t.dataset?.uploaderBulk) v.bulk[t.dataset.uploaderBulk] = t.value;
  else if (t.dataset?.uploaderTool) v.bulk[t.dataset.uploaderTool] = t.value;
  else if (t.id === "uploader-folder") v.folder = t.value;
});

document.addEventListener("change", async event => {
  const t = event.target, v = uploaderView;
  if (t.dataset?.uploaderDates !== undefined) { v.dates = t.value; renderDownloads(); }
  else if (t.dataset?.uploaderPick !== undefined) { v.pickDate = t.value; renderDownloads(); }
  else if (t.dataset?.uploaderFolderFilter !== undefined) { v.folderFilter = t.value; renderDownloads(); }
  else if (t.dataset?.uploaderUploaded !== undefined) { v.showUploaded = t.checked; renderDownloads(); }
  else if (t.dataset?.uploaderAll !== undefined) { const rows = uploaderRows(); v.sel = t.checked ? new Set(rows.map(r => r.id)) : new Set(); renderDownloads(); }
  else if (t.dataset?.uploaderBulk === "genre") v.bulk.genre = t.value;
  else if (t.dataset?.uploaderTool === "replaceField" || t.dataset?.uploaderTool === "caseField") v.bulk[t.dataset.uploaderTool] = t.value;
  else if (t.dataset?.uploaderDefault !== undefined) { await uploaderCall("/api/uploader/settings", {folder: t.checked ? ($("#uploader-folder")?.value.trim() || v.folder) : ""}, () => t.checked ? "Saved as your default folder." : "Default folder cleared."); }
});

document.addEventListener("toggle", event => { if (event.target.matches?.("[data-uploader-details]")) uploaderView.settingsOpen = event.target.open; }, true);
