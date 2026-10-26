"use strict";

// Bandcamp (fork v13): paste an artist or album link; every release is listed with Have /
// Partly / Downloaded before / Missing, loaded one album at a time. Download the whole
// discography, an album, or single songs (Bandcamp's free 128 kbps streams). Songs you may
// already have wait in Needs your decision.
const bandcampView = {url: "", base: "", releases: [], albums: new Map(), open: new Set(), filter: "all", loading: false, request: 0};
const bandcampStatus = {have: "Have", partly: "Partly", downloaded: "Downloaded before", new: "Missing"};

function bandcampThumb(url, cls = "") {
  return `<span class="galaxy-thumb ${cls}" aria-hidden="true">♪${url ? `<img src="${escapeHtml(url)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</span>`;
}

function bandcampHtml() {
  const view = bandcampView;
  return `<div class="galaxy-search">
      <input id="bandcamp-url" class="dl-path" maxlength="400" placeholder="https://artist.bandcamp.com, or an album or track link" aria-label="Bandcamp link" value="${escapeHtml(view.url)}">
      <button type="button" class="button primary" data-bandcamp-open ${view.loading ? "disabled" : ""}>Show releases</button></div>
    <p class="muted">Free 128 kbps MP3 streams only; nothing is bought. Files are tagged with cover and saved as Album Artist\\Album\\## - Title.mp3.</p>
    <div id="bandcamp-list">${bandcampListHtml()}</div>`;
}

function bandcampListHtml() {
  const view = bandcampView;
  if (view.loading) return '<p class="muted">Reading the Bandcamp page…</p>';
  if (!view.releases.length) return "";
  const loaded = view.releases.map(u => view.albums.get(u)).filter(a => a && a.tracks);
  const counts = Object.keys(bandcampStatus).map(s => [s, loaded.filter(a => a.status === s).length]).filter(([, n]) => n);
  const waiting = view.releases.length - view.releases.filter(u => view.albums.has(u) && view.albums.get(u) !== "loading").length;
  const shown = view.releases.filter(u => view.filter === "all" || view.albums.get(u)?.status === view.filter || (view.filter === "new" && !view.albums.get(u)?.tracks));
  const missing = loaded.filter(a => a.status !== "have");
  return `<div class="dl-actions"><strong>${view.releases.length} releases on ${escapeHtml(view.base.replace("https://", ""))}</strong>
      ${counts.map(([s, n]) => `<span class="dl-badge ${s === "new" ? "new" : s}">${n} ${bandcampStatus[s]}</span>`).join(" ")}
      ${waiting ? `<span class="muted">checking ${waiting} more…</span>` : ""}</div>
    <div class="dl-actions">
      <button type="button" class="button primary" data-bandcamp-all ${missing.length ? "" : "disabled"}>⬇ Download discography (${missing.length} not complete)</button>
      <label>Show <select id="bandcamp-filter">${[["all", "All"], ["new", "Missing"], ["partly", "Partly"], ["downloaded", "Downloaded before"], ["have", "Have"]].map(([v, l]) =>
        `<option value="${v}" ${view.filter === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <span class="muted">Songs you have wait in Needs your decision.</span></div>
    <ul class="bandcamp-releases">${shown.map(url => bandcampReleaseHtml(url)).join("")}</ul>`;
}

function bandcampReleaseHtml(url) {
  const a = bandcampView.albums.get(url), open = bandcampView.open.has(url);
  if (!a || a === "loading") return `<li class="bandcamp-release">${bandcampThumb("")}<div><b>${escapeHtml(decodeURIComponent(url.split("/").pop()).replace(/-/g, " "))}</b><div class="muted">${a === "loading" ? "Checking…" : "Waiting…"}</div></div></li>`;
  if (a.error) return `<li class="bandcamp-release">${bandcampThumb("")}<div><b>${escapeHtml(url.split("/").pop())}</b><div class="error-text">${escapeHtml(a.error)}</div></div></li>`;
  return `<li class="bandcamp-release ${a.status}">
    ${bandcampThumb(a.thumb, "galaxy-album-art")}
    <div class="bandcamp-release-body"><div><b>${escapeHtml(a.title)}</b> <span class="muted">· ${escapeHtml(a.artist)}${a.year ? ` · ${a.year}` : ""} · ${a.tracks.length} ${a.tracks.length === 1 ? "song" : "songs"}${a.unavailable ? ` · ${a.unavailable} purchase-only` : ""}</span></div>
      <div class="dl-actions"><span class="dl-badge ${a.status === "new" ? "new" : a.status}">${bandcampStatus[a.status]}${a.have && a.status === "partly" ? ` (${a.have} of ${a.tracks.length})` : ""}</span>
        <button type="button" class="button small" data-bandcamp-add="${escapeHtml(url)}" ${a.tracks.length ? "" : "disabled"}>⬇ Download album</button>
        <button type="button" class="text-action" data-bandcamp-toggle="${escapeHtml(url)}" aria-expanded="${open}">${open ? "Hide songs" : "Show songs"}</button>
        <a class="text-action" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open on Bandcamp ↗</a></div>
      ${open ? `<table class="dl-table galaxy-table"><tbody>${a.tracks.map(t => `<tr><td>${t.num}</td><td>${escapeHtml(t.title)}${t.artist !== a.artist ? ` <span class="muted">· ${escapeHtml(t.artist)}</span>` : ""}</td>
        <td>${galaxyBadge(t)}</td><td><button type="button" class="button small" data-bandcamp-song="${escapeHtml(url)}" data-num="${t.num}" ${t.queued ? "disabled" : ""}>⬇ Download</button></td></tr>`).join("")}</tbody></table>` : ""}
    </div></li>`;
}

function bandcampRefreshList() {
  if (downloadsView.tab === "bandcamp" && $("#bandcamp-list")) $("#bandcamp-list").innerHTML = bandcampListHtml();
}

async function bandcampOpen() {
  const view = bandcampView;
  view.url = $("#bandcamp-url").value.trim();
  if (!view.url) { toast("Paste a Bandcamp link."); return; }
  const request = ++view.request;
  Object.assign(view, {loading: true, releases: [], base: "", filter: "all"});
  view.albums.clear(); view.open.clear(); bandcampRefreshList();
  try {
    const data = await api("/api/bandcamp/discography", {url: view.url});
    if (request !== view.request) return;
    view.base = data.base;
    view.releases = data.selected ? [data.selected, ...data.releases.filter(u => u !== data.selected)] : data.releases;
    if (data.selected) view.open.add(data.selected);
    setActionStatus("#downloads-status", "");
  } catch (error) {
    if (request === view.request) downloadsError(error);
  } finally {
    if (request === view.request) { view.loading = false; bandcampRefreshList(); }
  }
  // Check the releases one at a time (Bandcamp is read at most about once per second).
  for (const url of view.releases) {
    if (request !== view.request) return;
    await bandcampLoad(url, request);
  }
}

async function bandcampLoad(url, request = bandcampView.request) {
  bandcampView.albums.set(url, "loading"); bandcampRefreshList();
  try {
    const album = await api("/api/bandcamp/album", {url});
    if (request === bandcampView.request) bandcampView.albums.set(url, album);
  } catch (error) {
    if (request === bandcampView.request) bandcampView.albums.set(url, {error: error.message});
  }
  if (request === bandcampView.request) bandcampRefreshList();
}

async function bandcampAdd(urls, nums) {
  let queued = 0, held = 0;
  for (const url of urls) {
    try {
      const r = await api("/api/bandcamp/add", nums ? {url, nums} : {url});
      queued += r.queued; held += r.held;
    } catch (error) { downloadsError(error); }
  }
  downloadsNote(`${queued} queued${held ? `, ${held} waiting in Needs your decision` : ""}.`);
  await loadDownloads();
  for (const url of urls) await bandcampLoad(url);
}

document.addEventListener("click", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "bandcamp") return;
  if (target.closest("[data-bandcamp-open]")) { bandcampOpen(); return; }
  const toggle = target.closest("[data-bandcamp-toggle]");
  if (toggle) { const u = toggle.dataset.bandcampToggle; bandcampView.open.has(u) ? bandcampView.open.delete(u) : bandcampView.open.add(u); bandcampRefreshList(); return; }
  const add = target.closest("[data-bandcamp-add]");
  if (add) { downloadsRun(() => bandcampAdd([add.dataset.bandcampAdd])); return; }
  const song = target.closest("[data-bandcamp-song]");
  if (song) { downloadsRun(() => bandcampAdd([song.dataset.bandcampSong], [Number(song.dataset.num)])); return; }
  if (target.closest("[data-bandcamp-all]")) {
    const urls = bandcampView.releases.filter(u => bandcampView.albums.get(u)?.tracks && bandcampView.albums.get(u).status !== "have");
    if (confirm(`Queue ${urls.length} releases? Songs you may already have wait in Needs your decision.`)) downloadsRun(() => bandcampAdd(urls));
  }
});

document.addEventListener("change", event => {
  if (event.target.id === "bandcamp-filter") { bandcampView.filter = event.target.value; bandcampRefreshList(); }
});
document.addEventListener("input", event => { if (event.target.id === "bandcamp-url") bandcampView.url = event.target.value; });
document.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.id === "bandcamp-url") { event.preventDefault(); bandcampOpen(); }
});
