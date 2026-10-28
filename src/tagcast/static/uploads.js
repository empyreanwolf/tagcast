"use strict";

// Recently uploaded (fork v38): the Clean up page's second tab. It mirrors iBroadcast's
// Date Uploaded page: one row per day (cover mosaic, date, albums, tracks, duration), newest
// first. Open a day for its albums and tracks with genre, artist, album artist, album and
// year; anything missing or suspicious is flagged. "Most recent" is the flat newest-first
// list, like the Most Recent Uploads playlist. Fixing a mistake opens the normal editor
// (and Review dialog), and a day can be ticked off once you have checked it.
const uploadsView = {sub: "scan", mode: "days", days: null, checked: new Set(), day: "", dayData: null, recent: null,
  problemsOnly: false, loading: false, error: "", request: 0, timer: null};

const uploadProblemLabels = {no_genre: "No genre", odd_genre: "Odd genre", combined_genre: "Genres joined with ;",
  no_artist: "No artist", no_album_artist: "No album artist", no_album: "No album name", no_year: "No year", no_cover: "No cover"};

function uploadsDate(day) {
  const date = new Date(`${day}T00:00:00`);
  return Number.isNaN(date.getTime()) ? day : date.toLocaleDateString("en", {weekday: "short", day: "numeric", month: "short", year: "numeric"});
}

function uploadsLength(seconds) {
  const total = Math.round(seconds || 0), h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60);
  return h ? `${h} h ${m} min` : `${m} min ${String(total % 60).padStart(2, "0")} s`;
}

function uploadsTabsHtml() {
  const v = uploadsView;
  return `<div class="dl-actions" role="tablist" aria-label="Clean up">${[["scan", "Scan before upload"], ["uploads", "Recently uploaded"]].map(([key, label]) =>
    `<button type="button" role="tab" class="button small ${v.sub === key ? "primary" : ""}" data-uploads-sub="${key}" aria-selected="${v.sub === key}">${label}</button>`).join("")}</div>`;
}

function uploadProblemPills(problems) {
  return (problems || []).map(p => `<span class="pill missing">${escapeHtml(uploadProblemLabels[p] || p)}</span>`).join(" ");
}

function uploadsHtml() {
  const v = uploadsView;
  if (v.error) return `<p class="error-text">${escapeHtml(v.error)}</p><button type="button" class="button" data-uploads="refresh">Try again</button>`;
  const modes = `<div class="dl-actions"><div class="galaxy-modes" role="radiogroup" aria-label="View">${[["days", "By day"], ["recent", "Most recent"]].map(([key, label]) =>
    `<button type="button" role="radio" aria-checked="${v.mode === key && !v.day}" data-uploads-mode="${key}">${label}</button>`).join("")}</div>
    <label class="checkline"><input type="checkbox" data-uploads-problems ${v.problemsOnly ? "checked" : ""}> Problems only</label>
    <button type="button" class="button small" data-uploads="refresh" ${v.loading ? "disabled" : ""}>${v.loading ? "Loading…" : "Refresh"}</button></div>`;
  if (v.day) return `${modes}${uploadsDayHtml()}`;
  if (v.mode === "recent") return `${modes}${uploadsRecentHtml()}`;
  return `${modes}${uploadsDaysHtml()}`;
}

function uploadsDaysHtml() {
  const v = uploadsView;
  if (!v.days) { uploadsLoad(); return '<p class="muted">Loading…</p>'; }
  const days = v.problemsOnly ? v.days.filter(d => d.problems) : v.days;
  if (!days.length) return `<p class="muted">${v.days.length ? "No upload day has problems." : "No uploads with a stored upload date yet."}</p>`;
  return `<ul class="dl-list uploads-days">${days.map(d => `<li><button type="button" class="uploads-day" data-uploads-day="${escapeHtml(d.date)}">
      <span class="uploads-mosaic" aria-hidden="true">${[0, 1, 2, 3].map(i => d.covers[i] ? `<img src="${escapeHtml(d.covers[i])}" alt="" loading="lazy">` : "<i></i>").join("")}</span>
      <span><b>${escapeHtml(uploadsDate(d.date))}</b><br><span class="muted">${d.albums.toLocaleString("en")} ${d.albums === 1 ? "album" : "albums"} · ${d.tracks.toLocaleString("en")} ${d.tracks === 1 ? "track" : "tracks"} · ${uploadsLength(d.seconds)}</span></span>
      <span>${d.problems ? `<span class="pill missing">${d.problems} to check</span>` : ""} ${v.checked.has(d.date) ? '<span class="pill">✓ Checked</span>' : ""}</span></button></li>`).join("")}</ul>`;
}

function uploadsDayHtml() {
  const v = uploadsView, d = v.dayData;
  const head = `<div class="dl-actions"><button type="button" class="button small" data-uploads="back">← All days</button><strong>${escapeHtml(uploadsDate(v.day))}</strong>
    <label class="checkline"><input type="checkbox" data-uploads-checked ${v.checked.has(v.day) ? "checked" : ""}> I checked this day</label></div>`;
  if (!d) return `${head}<p class="muted">Loading…</p>`;
  const albums = d.albums.map(a => ({...a, shown: v.problemsOnly ? a.tracks.filter(t => t.problems.length) : a.tracks})).filter(a => a.shown.length);
  return `${head}<p class="muted">${d.albums.length} albums · ${d.tracks} tracks · ${uploadsLength(d.seconds)}${d.problems ? ` · ${d.problems} with problems` : " · nothing looks wrong"}</p>
    ${albums.map(a => `<section class="dl-card"><div class="dl-actions">${a.cover ? `<img class="uploads-cover" src="${escapeHtml(a.cover)}" alt="" loading="lazy">` : ""}
      <div><b>${escapeHtml(a.album)}</b><br><span class="muted">${escapeHtml(a.album_artist)}${a.year ? ` · ${a.year}` : ""} · ${a.tracks.length} ${a.tracks.length === 1 ? "track" : "tracks"}</span></div>
      <button type="button" class="button small" data-uploads-edit="${escapeHtml(a.id)}">Edit album</button></div>
      ${uploadsTableHtml(a.shown, false)}</section>`).join("") || '<p class="muted">Nothing to show for this day.</p>'}`;
}

function uploadsRecentHtml() {
  const v = uploadsView;
  if (!v.recent) { uploadsLoad(); return '<p class="muted">Loading…</p>'; }
  const tracks = v.problemsOnly ? v.recent.tracks.filter(t => t.problems.length) : v.recent.tracks;
  return `<p class="muted">The ${v.recent.tracks.length} newest of ${v.recent.total.toLocaleString("en")} uploaded tracks.</p>${tracks.length ? uploadsTableHtml(tracks, true) : '<p class="muted">No problems in these.</p>'}`;
}

function uploadsTableHtml(tracks, withAlbum) {
  const cell = (value, bad) => `<td${bad ? ' class="uploads-bad"' : ""}>${value}</td>`;
  return `<div class="galaxy-table-wrap"><table class="dl-table galaxy-table"><thead><tr>${withAlbum ? "<th>Uploaded</th>" : "<th>#</th>"}<th>Title</th><th>Artist</th><th>Album artist</th>${withAlbum ? "<th>Album</th>" : ""}<th>Year</th><th>Genre</th><th>Check</th>${withAlbum ? "<th></th>" : ""}</tr></thead><tbody>
    ${tracks.map(t => `<tr>${withAlbum ? `<td>${escapeHtml(t.uploaded_at.replace("T", " ").slice(0, 16))}</td>` : `<td>${t.track || ""}</td>`}
      <td>${escapeHtml(t.title)}</td>${cell(escapeHtml(t.artist), t.problems.includes("no_artist"))}${cell(escapeHtml(t.album_artist), t.problems.includes("no_album_artist"))}
      ${withAlbum ? cell(escapeHtml(t.album), t.problems.includes("no_album")) : ""}${cell(t.year || "—", t.problems.includes("no_year"))}
      ${cell(escapeHtml(t.genres.join(", ") || "—"), t.problems.some(p => p.endsWith("genre")))}<td>${uploadProblemPills(t.problems)}</td>
      ${withAlbum ? `<td><button type="button" class="button small" data-uploads-edit="${escapeHtml(t.album_id)}">Edit album</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

async function uploadsLoad(force = false) {
  const v = uploadsView, request = ++v.request;
  if (v.loading && !force) return;
  v.loading = true; v.error = "";
  try {
    const wanted = v.day ? "day" : v.mode;
    const data = await api(`/api/uploads?view=${wanted}${v.day ? `&day=${encodeURIComponent(v.day)}` : ""}`);
    if (request !== v.request) return;
    if (wanted === "days") { v.days = data.days; v.checked = new Set(data.checked); }
    else if (wanted === "day") { v.dayData = data; if (data.checked) v.checked.add(v.day); else v.checked.delete(v.day); }
    else v.recent = data;
  } catch (error) { v.error = error.message; }
  v.loading = false;
  uploadsRefresh();
}

function uploadsRefresh() {
  if (state.screen === "downloads" && downloadsView.tab === "tagclean" && uploadsView.sub === "uploads" && $("#downloads-content")) $("#downloads-content").innerHTML = tagcleanHtml();
}

// The editor, review and results dialogs change tags: look again shortly after one closes.
document.addEventListener("close", event => {
  if (!["editor", "review", "results"].includes(event.target?.id) || uploadsView.sub !== "uploads") return;
  clearTimeout(uploadsView.timer);
  uploadsView.timer = setTimeout(() => uploadsLoad(true), 2500);
}, true);

document.addEventListener("click", event => {
  const target = event.target, v = uploadsView;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "tagclean") return;
  const sub = target.closest("[data-uploads-sub]");
  if (sub) { v.sub = sub.dataset.uploadsSub; v.error = ""; uploadsRefresh(); if (v.sub === "scan") tagcleanRefresh(); return; }
  const mode = target.closest("[data-uploads-mode]");
  if (mode) { v.mode = mode.dataset.uploadsMode; v.day = ""; v.dayData = null; uploadsLoad(true); uploadsRefresh(); return; }
  const day = target.closest("[data-uploads-day]");
  if (day) { v.day = day.dataset.uploadsDay; v.dayData = null; uploadsLoad(true); uploadsRefresh(); return; }
  const action = target.closest("[data-uploads]")?.dataset.uploads;
  if (action === "back") { v.day = ""; v.dayData = null; uploadsLoad(true); uploadsRefresh(); return; }
  if (action === "refresh") { uploadsLoad(true); uploadsRefresh(); return; }
  const edit = target.closest("[data-uploads-edit]");
  if (edit) openEditor([edit.dataset.uploadsEdit]);
});

document.addEventListener("change", event => {
  const target = event.target, v = uploadsView;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "tagclean") return;
  if (target.matches("[data-uploads-problems]")) { v.problemsOnly = target.checked; uploadsRefresh(); return; }
  if (target.matches("[data-uploads-checked]")) {
    const day = v.day, checked = target.checked;
    if (checked) v.checked.add(day); else v.checked.delete(day);
    api("/api/uploads/checked", {day, checked}).catch(error => { toast(`Could not save the mark: ${error.message}`); if (checked) v.checked.delete(day); else v.checked.add(day); uploadsRefresh(); });
  }
});
