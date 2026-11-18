"use strict";

// More sources (fork v60): one search bar for SoundCloud, plus a box for YouTube (v61), SoundCloud and Audiomack links.
// Audiomack and mixtape sites have no search Tagcast can use, so their links are pasted instead.
const moreView = {query: "", lines: "", results: null, queued: new Set(), linkResults: null, busy: false, error: "", linkJobs: {}};

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
const spotifyView = {status: null, busy: false, timer: null, searchQuery: "", searchBusy: false, searchNote: ""};

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
    <p class="muted">Paste a Spotify song, album, playlist or artist link in the box above, or search by name below. spotDL (github.com/spotDL/spotify-downloader, MIT) reads Spotify's tags and finds each song on YouTube Music; Tagcast downloads it as MP3 with Spotify's tags and cover. Lossy, like YouTube.</p>
    ${st.installed ? "" : `<div class="dl-actions"><button type="button" class="button primary" data-spotify="install" ${st.installing ? "disabled" : ""}>${st.installing ? "Installing spotDL… (about a minute)" : "Install spotDL (one time)"}</button></div>`}
    ${st.error ? `<p class="dl-have">${escapeHtml(st.error)}</p>` : ""}
    <form class="dl-actions" data-spotify="search-form"><input id="spotify-search" type="search" maxlength="200" placeholder="Artist - song, or an album name" aria-label="Search Spotify" value="${escapeHtml(spotifyView.searchQuery)}">
      <button type="submit" class="button primary" ${spotifyView.searchBusy ? "disabled" : ""}>${spotifyView.searchBusy ? "Searching…" : "Search & queue"}</button></form>
    ${spotifyView.searchNote ? `<p class="dl-have">${escapeHtml(spotifyView.searchNote)}</p>` : ""}
    <p class="muted">If Spotify limits spotDL's built-in key, add your own free app key from developer.spotify.com (Client ID and Secret).${st.own_key ? " Your own key is in use." : ""}</p>
    <div class="dl-actions"><input id="spotify-id" type="text" maxlength="100" placeholder="Client ID" aria-label="Spotify client ID">
      <input id="spotify-secret" type="password" maxlength="100" placeholder="Client secret" aria-label="Spotify client secret">
      <button type="button" class="button" data-spotify="key">Save key</button>${st.own_key ? `<button type="button" class="text-action" data-spotify="clear">Use built-in key</button>` : ""}</div></details>`;
}

document.addEventListener("submit", async event => {
  if (!event.target.closest?.('[data-spotify="search-form"]')) return;
  event.preventDefault();
  const v = spotifyView;
  const query = $("#spotify-search")?.value || "";
  v.searchQuery = query; v.searchBusy = true; v.searchNote = ""; renderDownloads();
  try {
    const result = await api("/api/spotify/search", {query});
    v.searchNote = result.note; v.searchQuery = "";
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { v.searchNote = error.message || String(error); }
  v.searchBusy = false; renderDownloads();
});

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
  // v107: reading happens in the background now - the button never blocks the page waiting for it.
  // The results list lives in its own #more-links-results box so a poll tick can refresh just that
  // (moreLinksPoll below) without touching the textarea and losing whatever you're typing into it.
  return `<h3>Paste links</h3>
    <p class="muted">YouTube, Spotify, SoundCloud or Audiomack links, one per line: a song, an album, a set or a playlist. A playlist downloads every song as one album (album = playlist name, tracks in order). Spotify links get Spotify's own tags and cover (through spotDL).</p>
    <textarea id="more-lines" class="dl-lines" rows="4" maxlength="20000" aria-label="YouTube, Spotify, SoundCloud or Audiomack links" placeholder="https://www.youtube.com/playlist?list=…">${escapeHtml(v.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-more="links">⬇ Download links</button></div>
    <div id="more-links-results">${moreLinksResultsHtml()}</div>`;
}

function moreLinksResultsHtml() {
  const v = moreView;
  if (!v.linkResults) return "";
  return `<ul class="dl-list dl-nofold">${v.linkResults.map((r, i) => r.reading ? `<li><span class="dl-badge partly">Reading…</span>
    <div><b>${escapeHtml(r.line)}</b></div></li>`
    : r.choose ? `<li><span class="dl-badge partly">Choose</span>
    <div><b>${escapeHtml(r.title || r.line)}</b><div class="muted">${escapeHtml(r.line)}</div></div>
    <span class="dl-row-actions">${r.has_song ? `<button type="button" class="button" data-more-pick="song" data-index="${i}">⬇ This song</button>` : ""}
    <button type="button" class="button primary" data-more-pick="${r.kind === "split" ? "split" : "list"}" data-index="${i}">⬇ ${moreListLabel(r)}</button></span></li>`
    : `<li><span class="dl-badge ${r.ok ? "have" : "partly"}">${r.ok ? "Queued" : "Failed"}</span>
    <div><b>${escapeHtml(r.line)}</b><div class="dl-have">${escapeHtml(r.note)}</div></div></li>`).join("")}</ul>`;
}

// v63: a YouTube link with a list waits for a click: only that song, or the whole album, playlist or Mix.
// v105: a single video that looks like a full album offers a third choice - split into tracks.
function moreListLabel(r) {
  if (r.kind === "split") return `Split into ${r.count} tracks`;
  const what = {album: "Whole album", mix: "Whole Mix", playlist: "Whole playlist"}[r.kind] || "Whole playlist";
  return `${what} (${r.count} ${r.count === 1 ? "song" : "songs"})`;
}

// v107: reading links used to block the whole page until every line was read. Now the request
// returns a job right away (id, state, results-so-far) and the background poll loop
// (moreLinksPoll, called from downloadsPoll/loadDownloads) follows it to completion - so pasting
// a big batch, or clicking "Split into N tracks" on a slow video, no longer freezes the page.
async function moreStartLinks(lines) {
  const v = moreView;
  try {
    const job = await api("/api/more/links", {lines, mode: ""});
    v.linkJobs[job.id] = {kind: "links", submitted: lines};
    v.linkResults = job.results;
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); return; }
  downloadsPoll();
  renderDownloads();
}

async function moreStartPick(index, mode) {
  const v = moreView;
  const row = v.linkResults?.[index];
  if (!row) return;
  try {
    const job = await api("/api/more/links", {lines: row.line, mode});
    v.linkJobs[job.id] = {kind: "pick", index};
    v.linkResults[index] = {...row, reading: true};
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); return; }
  downloadsPoll();
  if ($("#more-links-results")) $("#more-links-results").innerHTML = moreLinksResultsHtml(); else renderDownloads();
}

// v107: called every poll tick with the current /api/more/links/active jobs - finishes off
// whichever of moreView's own jobs have moved past "running" and drops them once applied.
function moreLinksPoll(jobs) {
  const v = moreView;
  if (!Object.keys(v.linkJobs).length) return;
  let rowsChanged = false, linesChanged = false;
  for (const job of jobs || []) {
    const meta = v.linkJobs[job.id];
    if (!meta) continue;
    if (meta.kind === "links") { v.linkResults = job.results || []; rowsChanged = true; }
    else if (meta.kind === "pick" && v.linkResults?.[meta.index] !== undefined && job.results?.length) {
      v.linkResults[meta.index] = job.results[0]; rowsChanged = true;
    }
    if (job.state === "running") continue;  // show the rows resolved so far; keep tracking this job
    if (meta.kind === "links") {
      const submitted = meta.submitted, after = $("#more-lines")?.value || "";
      // v99: a line pasted in while this read was in flight must not be lost - the box isn't
      // disabled while reading, so keep anything appended to the end since we started.
      const added = after.startsWith(submitted) ? after.slice(submitted.length).replace(/^\s+/, "") : "";
      const kept = dropQueuedLines(submitted, v.linkResults);  // v72: queued links leave the box, failed ones stay
      v.lines = added ? (kept ? `${kept}\n${added}` : added) : kept;
      linesChanged = true;
    } else if (meta.kind === "pick" && v.linkResults[meta.index]?.ok) {
      v.lines = dropQueuedLines(v.lines, [v.linkResults[meta.index]]);
      linesChanged = true;
    }
    delete v.linkJobs[job.id];
  }
  // A line queued just now must disappear from the textarea too, so only that needs the full
  // re-render; otherwise just refresh the results list (never the textarea you may be typing in).
  if (linesChanged) renderDownloads();
  else if (rowsChanged && $("#more-links-results")) $("#more-links-results").innerHTML = moreLinksResultsHtml();
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
  else if (action === "links") await moreStartLinks($("#more-lines")?.value || "");
});

document.addEventListener("click", async event => {
  const pick = event.target.closest?.("[data-more-pick]");
  if (!pick) return;
  const i = Number(pick.dataset.index);
  if (!moreView.linkResults?.[i] || moreView.linkResults[i].reading) return;
  await moreStartPick(i, pick.dataset.morePick);
});
