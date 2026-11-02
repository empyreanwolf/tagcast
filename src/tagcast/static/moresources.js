"use strict";

// More sources (fork v60): one search bar for SoundCloud, plus a box for YouTube (v61), SoundCloud and Audiomack links.
// Audiomack and mixtape sites have no search Tagcast can use, so their links are pasted instead.
const moreView = {query: "", lines: "", results: null, queued: new Set(), linkResults: null, busy: false, linkBusy: false, error: ""};

function moreHtml() {
  const v = moreView;
  const rows = (v.results || []).map((r, i) => {
    const done = v.queued.has(i);
    const have = r.status === "have";
    return `<li><span class="dl-badge ${have || done ? "have" : "partly"}">${done ? "Queued" : have ? "Have it" : r.site}</span>
      <div><b>${escapeHtml(r.title)}</b> <span class="muted">${escapeHtml(r.artist)}</span>
      ${have ? `<div class="dl-have">Already in your library</div>` : ""}</div>
      <button type="button" class="button" data-more="get" data-index="${i}" ${done || v.busy ? "disabled" : ""}>⬇ Download</button></li>`;
  }).join("");
  return `<p class="muted">Search SoundCloud, then download with one click. Files are lossy (MP3 or AAC from the site). Audiomack and mixtape sites cannot be searched from here: paste a link below. Files are saved as MP3 with cover art.</p>
    <form class="dl-actions" data-more="search-form"><input id="more-query" type="search" class="dl-search" value="${escapeHtml(v.query)}" placeholder="Artist or song" aria-label="Search SoundCloud" maxlength="200">
    <button type="submit" class="button primary" ${v.busy ? "disabled" : ""}>${v.busy ? "Searching…" : "Search"}</button></form>
    ${v.error ? `<p class="dl-have">${escapeHtml(v.error)}</p>` : ""}
    ${v.results ? (v.results.length ? `<div class="dl-actions"><button type="button" class="button" data-more="get-all" ${v.busy ? "disabled" : ""}>⬇ Download all new (${v.results.filter((r, i) => r.status !== "have" && !v.queued.has(i)).length})</button></div><ul class="dl-list">${rows}</ul>` : `<p class="muted">Nothing found.</p>`) : ""}
    ${moreLinksHtml()}
    <p class="muted">Downloading copyrighted music can break the sites' terms and the law where you live.</p>`;
}

// The shared link box (v61): YouTube, SoundCloud and Audiomack links and playlists. Shown on this tab and the YouTube tab.
function moreLinksHtml() {
  const v = moreView;
  return `<h3>Paste links</h3>
    <p class="muted">YouTube, SoundCloud or Audiomack links, one per line: a song, an album, a set or a playlist. A playlist downloads every song as one album (album = playlist name, tracks in order).</p>
    <textarea id="more-lines" class="dl-lines" rows="4" maxlength="20000" aria-label="YouTube, SoundCloud or Audiomack links" placeholder="https://www.youtube.com/playlist?list=…">${escapeHtml(v.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-more="links" ${v.linkBusy ? "disabled" : ""}>${v.linkBusy ? "Reading…" : "⬇ Download links"}</button></div>
    ${v.linkResults ? `<ul class="dl-list dl-nofold">${v.linkResults.map((r, i) => r.choose ? `<li><span class="dl-badge partly">Choose</span>
      <div><b>${escapeHtml(r.title || r.line)}</b><div class="muted">${escapeHtml(r.line)}</div></div>
      <span class="dl-row-actions">${r.has_song ? `<button type="button" class="button" data-more-pick="song" data-index="${i}">⬇ This song</button>` : ""}
      <button type="button" class="button primary" data-more-pick="list" data-index="${i}">⬇ ${moreListLabel(r)}</button></span></li>`
      : `<li><span class="dl-badge ${r.ok ? "have" : "partly"}">${r.ok ? "Queued" : "Failed"}</span>
      <div><b>${escapeHtml(r.line)}</b><div class="dl-have">${escapeHtml(r.note)}</div></div></li>`).join("")}</ul>` : ""}`;
}

// v63: a YouTube link with a list waits for a click: only that song, or the whole album, playlist or Mix.
function moreListLabel(r) {
  const what = {album: "Whole album", mix: "Whole Mix", playlist: "Whole playlist"}[r.kind] || "Whole playlist";
  return `${what} (${r.count} ${r.count === 1 ? "song" : "songs"})`;
}

async function moreLinks(lines, mode = "") {
  const v = moreView;
  v.linkBusy = true; renderDownloads();
  let results = [];
  try {
    results = (await api("/api/more/links", {lines, mode})).results;
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  v.linkBusy = false;
  return results;
}

async function moreQueue(indexes) {
  const v = moreView;
  const rows = indexes.map(i => v.results[i]);
  v.busy = true; v.error = ""; renderDownloads();
  try {
    await api("/api/more/add", {rows: rows.map(r => ({url: r.url, title: r.title, artist: r.artist, duration: r.duration, query: `SoundCloud search: ${v.query}`}))});
    indexes.forEach(i => v.queued.add(i));
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { v.error = error.message || String(error); }
  v.busy = false; renderDownloads();
}

document.addEventListener("submit", async event => {
  if (!event.target.closest?.('[data-more="search-form"]')) return;
  event.preventDefault();
  const v = moreView;
  v.query = $("#more-query")?.value || "";
  v.busy = true; v.error = ""; renderDownloads();
  try { v.results = (await api("/api/more/search", {query: v.query})).results; v.queued = new Set(); }
  catch (error) { v.error = error.message || String(error); v.results = null; }
  v.busy = false; renderDownloads();
});

document.addEventListener("click", async event => {
  const button = event.target.closest?.("[data-more]");
  if (!button) return;
  const v = moreView, action = button.dataset.more;
  if (action === "get") await moreQueue([Number(button.dataset.index)]);
  else if (action === "get-all") await moreQueue(v.results.map((r, i) => i).filter(i => v.results[i].status !== "have" && !v.queued.has(i)));
  else if (action === "links") {
    v.lines = $("#more-lines")?.value || "";
    v.linkResults = await moreLinks(v.lines);
    renderDownloads();
  }
});

document.addEventListener("click", async event => {
  const pick = event.target.closest?.("[data-more-pick]");
  if (!pick) return;
  const v = moreView, i = Number(pick.dataset.index), row = v.linkResults?.[i];
  if (!row || v.linkBusy) return;
  const [result] = await moreLinks(row.line, pick.dataset.morePick);
  if (result) v.linkResults[i] = result;
  renderDownloads();
});
