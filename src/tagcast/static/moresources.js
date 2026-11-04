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
    ${spotifyHtml()}
    <p class="muted">Downloading copyrighted music can break the sites' terms and the law where you live.</p>`;
}

// v66: spotDL for Spotify links: its own small Python environment, installed once with one click.
const spotifyView = {status: null, busy: false, timer: null};

async function spotifyLoad() {
  try { spotifyView.status = await api("/api/spotify/status"); } catch { spotifyView.status = {installed: false, installing: false, error: ""}; }
  clearTimeout(spotifyView.timer);
  if (spotifyView.status.installing) spotifyView.timer = setTimeout(spotifyLoad, 3000);
  if (state.screen === "downloads" && ["more", "youtube"].includes(downloadsView.tab)) renderDownloads();
}

function spotifyHtml() {
  const st = spotifyView.status;
  if (!st) { spotifyLoad(); return ""; }
  return `<details class="dl-fold" ${st.installed ? "" : "open"}><summary>Spotify (spotDL) <span class="muted">${st.installed ? "ready" : st.installing ? "installing…" : "not installed"}</span></summary>
    <p class="muted">Paste a Spotify song, album, playlist or artist link in the box above. spotDL (github.com/spotDL/spotify-downloader, MIT) reads Spotify's tags and finds each song on YouTube Music; Tagcast downloads it as MP3 with Spotify's tags and cover. Lossy, like YouTube.</p>
    ${st.installed ? "" : `<div class="dl-actions"><button type="button" class="button primary" data-spotify="install" ${st.installing ? "disabled" : ""}>${st.installing ? "Installing spotDL… (about a minute)" : "Install spotDL (one time)"}</button></div>`}
    ${st.error ? `<p class="dl-have">${escapeHtml(st.error)}</p>` : ""}
    <p class="muted">If Spotify limits spotDL's built-in key, add your own free app key from developer.spotify.com (Client ID and Secret).${st.own_key ? " Your own key is in use." : ""}</p>
    <div class="dl-actions"><input id="spotify-id" type="text" maxlength="100" placeholder="Client ID" aria-label="Spotify client ID">
      <input id="spotify-secret" type="password" maxlength="100" placeholder="Client secret" aria-label="Spotify client secret">
      <button type="button" class="button" data-spotify="key">Save key</button>${st.own_key ? `<button type="button" class="text-action" data-spotify="clear">Use built-in key</button>` : ""}</div></details>`;
}

document.addEventListener("click", async event => {
  const button = event.target.closest?.("[data-spotify]");
  if (!button) return;
  const action = button.dataset.spotify;
  try {
    if (action === "install") spotifyView.status = await api("/api/spotify/install", {});
    else spotifyView.status = await api("/api/spotify/settings", action === "clear" ? {client_id: "", client_secret: ""}
      : {client_id: $("#spotify-id")?.value || "", client_secret: $("#spotify-secret")?.value || ""});
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  if (spotifyView.status?.installing) spotifyLoad(); else renderDownloads();
});

// The shared link box (v61): YouTube, SoundCloud and Audiomack links and playlists. Shown on this tab and the YouTube tab.
function moreLinksHtml() {
  const v = moreView;
  return `<h3>Paste links</h3>
    <p class="muted">YouTube, Spotify, SoundCloud or Audiomack links, one per line: a song, an album, a set or a playlist. A playlist downloads every song as one album (album = playlist name, tracks in order). Spotify links get Spotify's own tags and cover (through spotDL).</p>
    <textarea id="more-lines" class="dl-lines" rows="4" maxlength="20000" aria-label="YouTube, Spotify, SoundCloud or Audiomack links" placeholder="https://www.youtube.com/playlist?list=…">${escapeHtml(v.lines)}</textarea>
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
    v.lines = dropQueuedLines(v.lines, v.linkResults);  // v72: queued links leave the box, failed ones stay
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
  if (result?.ok) v.lines = dropQueuedLines(v.lines, [result]);
  renderDownloads();
});
