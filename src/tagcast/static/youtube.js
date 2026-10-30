"use strict";

// YouTube (fork v59): songs found with yt-dlp, first in the source order. Lossy (about 128 kbps AAC).
const youtubeView = {status: null, lines: "", results: null, busy: false,
  // v61: an artist's albums and singles, from YouTube Music or plain YouTube.
  artist: "", where: "music", releases: null, relBusy: false, relError: "", queued: new Set()};

async function youtubeLoad() {
  try { youtubeView.status = await api("/api/youtube/status"); } catch { youtubeView.status = {enabled: false, installed: false, version: ""}; }
  if (state.screen === "downloads" && ["youtube", "settings"].includes(downloadsView.tab)) renderDownloads();
}

function youtubeReleaseHtml(r, key) {
  const done = youtubeView.queued.has(key), have = r.status === "have";
  const badge = done ? "Queued" : have ? "Have it" : r.status === "partly" ? "Partly" : `${r.tracks.length} ${r.tracks.length === 1 ? "song" : "songs"}`;
  return `<li><details class="dl-fold"><summary><span class="dl-badge ${have || done ? "have" : "partly"}">${badge}</span> <b>${escapeHtml(r.title)}</b></summary>
    <ol class="muted">${r.tracks.map(t => `<li>${escapeHtml(t.title)}</li>`).join("")}</ol></details>
    <button type="button" class="button" data-youtube="release" data-key="${escapeHtml(key)}" ${done || youtubeView.busy ? "disabled" : ""}>⬇ Download</button></li>`;
}

function youtubeArtistHtml() {
  const v = youtubeView, data = v.releases;
  const group = (kind, label) => {
    const list = data[kind] || [];
    const fresh = list.filter((r, i) => r.status !== "have" && !v.queued.has(`${kind}:${i}`)).length;
    return `<details class="dl-fold" open><summary>${label} <span class="muted">${list.length}</span></summary>
      ${list.length ? `<div class="dl-actions"><button type="button" class="button" data-youtube="release-all" data-kind="${kind}" ${fresh && !v.busy ? "" : "disabled"}>⬇ Download all ${kind} (${fresh} new)</button></div>
      <ul class="dl-list dl-nofold">${list.map((r, i) => youtubeReleaseHtml(r, `${kind}:${i}`)).join("")}</ul>` : `<p class="muted">None found.</p>`}</details>`;
  };
  return `<p class="muted">${escapeHtml(data.artist)} · from ${data.where === "music" ? "YouTube Music" : "YouTube"}${data.fell_back ? " (YouTube Music found nothing)" : ""}. Albums you already have are skipped by Download all.</p>
    ${group("albums", "Albums")}${group("singles", "Singles")}`;
}

function youtubeHtml() {
  const view = youtubeView;
  if (!view.status) youtubeLoad();
  const st = view.status;
  return `<details class="dl-fold" open><summary>Artist albums &amp; singles</summary>
    <form class="dl-actions" data-youtube="artist-form"><input id="youtube-artist" type="search" class="dl-search" value="${escapeHtml(view.artist)}" placeholder="Artist name" aria-label="Artist to search on YouTube" maxlength="200">
    <span class="dl-seg">${[["music", "YouTube Music"], ["youtube", "YouTube"]].map(([k, l]) => `<button type="button" class="button ${view.where === k ? "primary" : ""}" data-youtube-where="${k}" aria-pressed="${view.where === k}">${l}</button>`).join("")}</span>
    <button type="submit" class="button primary" ${view.relBusy ? "disabled" : ""}>${view.relBusy ? "Searching…" : "Search"}</button></form>
    ${view.relError ? `<p class="dl-have">${escapeHtml(view.relError)}</p>` : ""}
    ${view.releases ? youtubeArtistHtml() : ""}</details>
    <details class="dl-fold" open><summary>Links &amp; playlists</summary>${moreLinksHtml()}</details>
    <details class="dl-fold" open><summary>Songs by name</summary>
    <p class="muted">${st && !st.installed ? "yt-dlp is not installed yet: restart Tagcast once (it installs it), or run <code>pip install yt-dlp</code>. " : ""}
    One song per line: <code>Artist - Title</code>. Tagcast looks for the official audio on YouTube and queues it. Files are lossy.</p>
    <textarea id="youtube-lines" class="dl-lines" rows="5" maxlength="20000" aria-label="Songs to download from YouTube" placeholder="Coldplay - Yellow">${escapeHtml(view.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-youtube="add" ${view.busy ? "disabled" : ""}>${view.busy ? "Searching…" : "⬇ Download from YouTube"}</button></div>
    ${view.results ? `<ul class="dl-list">${view.results.map(r => `<li><span class="dl-badge ${r.ok ? "have" : "partly"}">${r.ok ? "Queued" : "Not found"}</span>
      <div><b>${escapeHtml(r.line)}</b><div class="dl-have">${escapeHtml(r.note)}</div></div></li>`).join("")}</ul>` : ""}</details>
    <p class="muted">Saved as MP3 with square cover art and the page's tags. yt-dlp: github.com/yt-dlp/yt-dlp. Downloading copyrighted music from YouTube can break YouTube's terms and the law where you live.</p>`;
}

function youtubeSettingsHtml() {
  const st = youtubeView.status;
  if (!st) { youtubeLoad(); return '<p class="muted">Loading…</p>'; }
  return `<label class="checkline"><input type="checkbox" data-youtube-enabled ${st.enabled ? "checked" : ""}> Try YouTube first for every download (then Galaxy, Bandcamp, Soulseek)</label>
    <p class="muted">${st.installed ? `yt-dlp ${escapeHtml(st.version)} is installed. If YouTube stops working, update it: <code>pip install -U yt-dlp</code>.` : "yt-dlp is not installed yet. Restart Tagcast once, or run <code>pip install yt-dlp</code>."}</p>`;
}

document.addEventListener("click", async event => {
  const add = event.target.closest?.('[data-youtube="add"]');
  if (!add) return;
  const view = youtubeView;
  view.lines = $("#youtube-lines")?.value || "";
  view.busy = true; renderDownloads();
  try {
    view.results = (await api("/api/youtube/add", {lines: view.lines})).results;
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  view.busy = false; renderDownloads();
});

document.addEventListener("change", async event => {
  const box = event.target.closest?.("[data-youtube-enabled]");
  if (!box) return;
  try { youtubeView.status = await api("/api/youtube/settings", {enabled: box.checked}); }
  catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  renderDownloads();
});

// v61: artist search, the YouTube Music / YouTube switch and one-click release downloads.
async function youtubeQueueReleases(keys) {
  const v = youtubeView;
  const picked = keys.map(k => { const [kind, i] = k.split(":"); return v.releases[kind][Number(i)]; });
  v.busy = true; v.relError = ""; renderDownloads();
  try {
    await api("/api/youtube/releases", {artist: v.releases.artist, releases: picked.map(r => ({title: r.title, tracks: r.tracks}))});
    keys.forEach(k => v.queued.add(k));
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { v.relError = error.message || String(error); }
  v.busy = false; renderDownloads();
}

document.addEventListener("submit", async event => {
  if (!event.target.closest?.('[data-youtube="artist-form"]')) return;
  event.preventDefault();
  const v = youtubeView;
  v.artist = $("#youtube-artist")?.value || "";
  v.relBusy = true; v.relError = ""; renderDownloads();
  try { v.releases = await api("/api/youtube/artist", {artist: v.artist, where: v.where}); v.queued = new Set(); }
  catch (error) { v.relError = error.message || String(error); v.releases = null; }
  v.relBusy = false; renderDownloads();
});

document.addEventListener("click", async event => {
  const where = event.target.closest?.("[data-youtube-where]");
  if (where) {
    youtubeView.where = where.dataset.youtubeWhere;
    youtubeView.artist = $("#youtube-artist")?.value || youtubeView.artist;
    renderDownloads();
    return;
  }
  const button = event.target.closest?.('[data-youtube="release"], [data-youtube="release-all"]');
  if (!button) return;
  const v = youtubeView;
  if (button.dataset.youtube === "release") await youtubeQueueReleases([button.dataset.key]);
  else {
    const kind = button.dataset.kind;
    await youtubeQueueReleases((v.releases[kind] || []).map((r, i) => `${kind}:${i}`)
      .filter((k, i) => v.releases[kind][i].status !== "have" && !v.queued.has(k)));
  }
});
