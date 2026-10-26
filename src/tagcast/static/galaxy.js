"use strict";

// Galaxy (fork v11): the GD Studio downloader on the Downloads page. Search by artist, album
// or track; every song shows whether you already have it (iBroadcast, download history or the
// download folder). Artists that share a name get separate cards (MusicBrainz + Deezer + your
// library); songs that fit no card wait under "Which artist?" and your pick is remembered.
// Downloads go to the shared queue; songs you may have go to Needs your decision.
const galaxyView = {
  mode: "artist", query: "", source: "netease", data: null, album: null, cards: null, cardsFor: "",
  cardFilter: "all", years: {}, selected: new Set(), sort: {key: "", dir: 1}, preview: "", loading: false, request: 0,
};
const galaxyModes = [["artist", "Artist"], ["album", "Album"], ["track", "Track"]];
const galaxySources = [["netease", ""], ["joox", ""], ["bilibili", ""], ["tencent", "often closed"], ["kuwo", "often closed"],
  ["tidal", "often closed"], ["qobuz", "often closed"], ["apple", "often closed"], ["ytmusic", "often closed"], ["spotify", "often closed"]];

const galaxyKey = t => `${t.source}:${t.id}`;

// A cover from the Tagcast server (cached there; NetEase covers cost no API call). The ♪ shows
// until the picture arrives, and stays when there is none.
function galaxyThumb(source, picId, size, cls = "") {
  const img = picId ? `<img src="/api/galaxy/cover?${new URLSearchParams({source, pic_id: picId, size: String(size)})}" alt="" loading="lazy" onerror="this.remove()">` : "";
  return `<span class="galaxy-thumb ${cls}" aria-hidden="true">♪${img}</span>`;
}

function galaxyStatusHtml() {
  const g = downloadsView.galaxy, queue = downloadsView.queue || [];
  if (!g) return "";
  const active = queue.find(i => i.state === "active");
  const waiting = queue.filter(i => i.state === "queued" && i.payload).length;
  const api = `API ${g.api.used}/${g.api.quota} in 5 min${g.api.wait ? ` · waiting ${g.api.wait} s for the API limit` : ""}`;
  const queueText = g.paused ? `Queue paused (${waiting} waiting)` : active ? `Downloading “${escapeHtml(active.title)}” ${active.progress || 0}%${waiting ? ` · ${waiting} waiting` : ""}`
    : waiting ? `${waiting} waiting${g.next_in ? ` · next in ${g.next_in} s` : ""}` : "Queue idle";
  return `${api} · ${queueText} <button type="button" class="text-action" data-downloads-tab="queue">Open queue</button>`;
}

function galaxyBadge(t) {
  if (t.queued) return `<span class="dl-badge downloaded">${t.queued === "active" ? "Downloading" : "In the queue"}</span>`;
  if (t.status && t.status !== "new") return `<span class="dl-badge ${t.status}" title="${escapeHtml((t.you_have || []).map(h => [h.title, h.artist, h.album].filter(Boolean).join(" · ")).join("\n"))}">${downloadsStatusLabels[t.status]}</span>`;
  if (t.on_disk) return `<span class="dl-badge folder" title="${escapeHtml(t.on_disk)}">In your download folder</span>`;
  return '<span class="dl-badge new">New</span>';
}

function galaxyHtml() {
  const view = galaxyView;
  const placeholder = {artist: "Artist name, e.g. Lotus", album: "Album name", track: "Track title, artist or both"}[view.mode];
  return `<div class="galaxy-search">
      <div class="galaxy-modes" role="radiogroup" aria-label="Search by">${galaxyModes.map(([key, label]) =>
        `<button type="button" role="radio" aria-checked="${view.mode === key}" data-galaxy-mode="${key}">${label}</button>`).join("")}</div>
      <input id="galaxy-query" class="dl-path" maxlength="200" placeholder="${placeholder}" aria-label="Search Galaxy" value="${escapeHtml(view.query)}">
      <select id="galaxy-source" aria-label="Source">${galaxySources.map(([s, note]) =>
        `<option value="${s}" ${view.source === s ? "selected" : ""}>${s}${note ? ` (${note})` : ""}</option>`).join("")}</select>
      <button type="button" class="button primary" data-galaxy-search ${view.loading ? "disabled" : ""}>Search</button></div>
    <p id="galaxy-status" class="muted" role="status">${galaxyStatusHtml()}</p>
    ${view.loading ? '<p class="muted">Searching GD Studio…</p>' : view.album ? galaxyAlbumHtml() : view.data ? galaxyResultsHtml() : galaxyIntroHtml()}
    <p class="muted galaxy-credit">Music data: music.gdstudio.xyz, for personal use. Files go to ${escapeHtml(downloadsView.info?.folder || "your download folder")} as Album Artist\\Album\\## - Title.</p>`;
}

function galaxyIntroHtml() {
  return `<p class="muted">Search for an artist to see their songs and albums, an album to see its exact tracklist, or any track.
    Every result shows whether you already have it. Songs you may have are never downloaded until you choose Download anyway.</p>`;
}

// -- search results --------------------------------------------------------------------

function galaxyFiltered() {
  const view = galaxyView, data = view.data;
  let tracks = data.tracks;
  if (data.mode === "artist" && view.cards && view.cardFilter !== "all") {
    tracks = tracks.filter(t => (view.cards.assign[t.album || "Unknown Album"] || "") === view.cardFilter);
  }
  if (view.sort.key) {
    const value = t => view.sort.key === "artists" ? t.artists.join(", ") : t[view.sort.key] || "";
    tracks = [...tracks].sort((a, b) => value(a).localeCompare(value(b), undefined, {sensitivity: "base"}) * view.sort.dir);
  }
  return tracks;
}

function galaxyResultsHtml() {
  const view = galaxyView, data = view.data, tracks = galaxyFiltered();
  const picked = data.tracks.filter(t => view.selected.has(galaxyKey(t))).length;
  const head = (key, label) => `<th><button type="button" class="text-action" data-galaxy-sort="${key}">${label}${view.sort.key === key ? (view.sort.dir > 0 ? " ▲" : " ▼") : ""}</button></th>`;
  const albums = data.mode === "artist" ? data.albums.filter(a => view.cardFilter === "all" || (view.cards?.assign[a.name] || "") === view.cardFilter) : [];
  return `${data.mode === "artist" ? galaxyCardsHtml() : ""}
    <div class="dl-actions"><strong>${tracks.length} ${tracks.length === 1 ? "song" : "songs"}${data.mode === "artist" ? ` by “${escapeHtml(data.query)}”` : ` for “${escapeHtml(data.query)}”`}</strong>
      <button type="button" class="button small" data-galaxy-dl-selected ${picked ? "" : "disabled"}>⬇ Download selected (${picked})</button>
      <button type="button" class="button small primary" data-galaxy-dl-all ${tracks.length ? "" : "disabled"}>⬇ Queue all ${tracks.length}</button>
      <span class="muted">Click an album to open it, an artist to search them.</span></div>
    ${tracks.length ? `<div class="galaxy-table-wrap"><table class="dl-table galaxy-table"><thead><tr><th><input type="checkbox" data-galaxy-select-all aria-label="Select all" ${tracks.every(t => view.selected.has(galaxyKey(t))) ? "checked" : ""}></th>
      <th aria-label="Cover"></th>${head("title", "Title")}${head("artists", "Artists")}${head("album", "Album")}<th>Status</th><th></th></tr></thead><tbody>${tracks.map(t => `<tr>
      <td><input type="checkbox" data-galaxy-select="${escapeHtml(galaxyKey(t))}" aria-label="Select ${escapeHtml(t.title)}" ${view.selected.has(galaxyKey(t)) ? "checked" : ""}></td>
      <td>${galaxyThumb(t.source, t.pic_id, 100)}</td>
      <td>${escapeHtml(t.title)}</td>
      <td>${t.artists.map(a => `<button type="button" class="text-action" data-galaxy-artist="${escapeHtml(a)}">${escapeHtml(a)}</button>`).join(", ") || "—"}</td>
      <td>${t.album ? `<button type="button" class="text-action" data-galaxy-album="${escapeHtml(t.album)}" data-artist="${escapeHtml(t.artists[0] || "")}">${escapeHtml(t.album)}</button>${view.years[t.album] ? ` <span class="muted">(${view.years[t.album]})</span>` : ""}` : "—"}</td>
      <td>${galaxyBadge(t)}</td>
      <td><button type="button" class="button small" data-galaxy-dl="${escapeHtml(galaxyKey(t))}" ${t.queued ? "disabled" : ""}>⬇ Download</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">${data.mode === "artist" ? `No songs where “${escapeHtml(data.query)}” is credited.` : "Nothing found."}</p>`}
    ${albums.length ? `<h3>Albums (${albums.length})</h3><ul class="galaxy-albums">${albums.map(a => `<li>
      <button type="button" class="galaxy-album-card" data-galaxy-album="${escapeHtml(a.name)}" data-artist="${escapeHtml(data.query)}" aria-label="Open ${escapeHtml(a.name)}">
        ${galaxyThumb(a.source, a.pic_id, 300, "galaxy-album-art")}<span><b>${escapeHtml(a.name)}</b>${view.years[a.name] ? `<br><span class="muted">${view.years[a.name]}</span>` : ""}</span></button>
      <span class="muted">${a.songs} ${a.songs === 1 ? "song" : "songs"} here</span>
      <button type="button" class="button small" data-galaxy-album-dl="${escapeHtml(a.name)}" data-artist="${escapeHtml(data.query)}" ${a.name === "Unknown Album" ? "disabled" : ""}>⬇ Download album</button></li>`).join("")}</ul>` : ""}`;
}

// -- same-name artist cards --------------------------------------------------------------

function galaxyCardsHtml() {
  const view = galaxyView, cards = view.cards;
  if (!cards) return `<p class="muted">Looking up artists called “${escapeHtml(view.data.query)}” on MusicBrainz and Deezer…</p>`;
  if (cards.error) return `<p class="muted">Artist cards unavailable: ${escapeHtml(cards.error)}</p>`;
  const unsure = Object.entries(cards.assign).filter(([, card]) => card === "unsure").map(([album]) => album);
  const count = id => view.data.tracks.filter(t => cards.assign[t.album || "Unknown Album"] === id).length;
  const library = cards.library_unplaced?.length ? `<p class="muted">In your iBroadcast as “${escapeHtml(cards.name)}”: ${cards.library_unplaced.map(a => `<i>${escapeHtml(a.title)}</i>${a.genres.length ? ` (${escapeHtml(a.genres.join(", "))})` : ""}`).join(", ")}</p>` : "";
  if (!cards.cards.length) return `${library}<p class="muted">No artist called exactly “${escapeHtml(cards.name)}” on MusicBrainz or Deezer.${cards.notes?.length ? ` ${escapeHtml(cards.notes.join("; "))}` : ""}</p>`;
  return `<div class="galaxy-cards-head"><h3>${cards.cards.length === 1 ? "The artist" : `${cards.cards.length} artists called “${escapeHtml(cards.name)}”`}</h3>
      ${cards.cards.length > 1 ? `<button type="button" class="button small ${view.cardFilter === "all" ? "primary" : ""}" data-galaxy-card="all">All results</button>` : ""}</div>
    ${cards.notes?.length ? `<p class="muted">${escapeHtml(cards.notes.join("; "))}</p>` : ""}
    <div class="galaxy-cards">${cards.cards.map(c => `<button type="button" class="galaxy-card ${view.cardFilter === c.id ? "selected" : ""}" data-galaxy-card="${escapeHtml(c.id)}" aria-pressed="${view.cardFilter === c.id}">
      ${c.picture ? `<img src="${escapeHtml(c.picture)}" alt="" loading="lazy" onerror="this.hidden=true">` : '<span class="galaxy-card-letter" aria-hidden="true">♪</span>'}
      <span class="galaxy-card-body"><b>${escapeHtml(c.name)}</b>
        ${c.description ? `<span>${escapeHtml(c.description)}</span>` : ""}
        <span class="muted">${escapeHtml([c.type, c.country, c.years].filter(Boolean).join(" · ") || "No MusicBrainz details")}</span>
        <span class="muted">${c.album_count ? `${c.album_count} albums` : ""}${c.fans ? ` · ${Number(c.fans).toLocaleString("en")} Deezer fans` : ""}</span>
        ${c.yours?.length ? `<span class="galaxy-yours">You have: ${c.yours.slice(0, 3).map(a => escapeHtml(a.title)).join(", ")}${c.yours.length > 3 ? ` +${c.yours.length - 3}` : ""}${c.genres?.length ? ` (${escapeHtml(c.genres.join(", "))})` : ""}</span>` : ""}
        <span class="muted">${count(c.id)} ${count(c.id) === 1 ? "song" : "songs"} in these results</span></span></button>`).join("")}</div>
    ${library}
    ${unsure.length ? `<section class="dl-card galaxy-unsure"><h3>Which artist? <span class="muted">(${unsure.length} ${unsure.length === 1 ? "album" : "albums"})</span></h3>
      <p class="muted">These albums fit none of the artists, or more than one. Pick the right one; Tagcast remembers it for this name and album.</p>
      <ul class="dl-list">${unsure.map(album => `<li><div><b>${escapeHtml(album)}</b> <span class="muted">· ${view.data.tracks.filter(t => (t.album || "Unknown Album") === album).length} songs</span></div>
        <select data-galaxy-pick="${escapeHtml(album)}" aria-label="Which artist made ${escapeHtml(album)}?"><option value="">Not sure</option>${cards.cards.map(c =>
          `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}${c.description ? ` — ${escapeHtml(c.description)}` : c.country ? ` — ${escapeHtml(c.country)}` : ""}</option>`).join("")}</select></li>`).join("")}</ul></section>` : ""}`;
}

// -- album view ----------------------------------------------------------------------------

function galaxyAlbumHtml() {
  const view = galaxyView, a = view.album;
  const back = view.data ? '<button type="button" class="button small" data-galaxy-back>← Back to results</button>' : "";
  if (!a.found) return `${back}<p class="warn-box">${escapeHtml(a.note)}</p>`;
  const have = a.tracks.filter(t => t.status !== "new" || t.on_disk).length;
  const picked = a.tracks.filter(t => view.selected.has(galaxyKey(t))).length;
  return `${back}<div class="galaxy-album-head">${galaxyThumb(a.cover?.source || a.source, a.cover?.pic_id || "", 500, "galaxy-cover")}
      <div><div class="eyebrow">ALBUM · ${escapeHtml(a.source)}</div><h2>${escapeHtml(a.album)}</h2>
      <p>${escapeHtml(a.album_artist)}${view.years[a.album] ? ` · ${view.years[a.album]}` : ""} · ${a.tracks.length} tracks${have ? ` · ✓ you have ${have}` : ""}</p>
      <div class="dl-actions"><button type="button" class="button primary" data-galaxy-album-all>⬇ Download album (${a.tracks.length})</button>
        <button type="button" class="button" data-galaxy-dl-selected ${picked ? "" : "disabled"}>⬇ Download selected (${picked})</button></div>
      <p class="muted">Missing songs are queued; songs you may have wait in Needs your decision. Click a song to see its tags and file name.</p></div></div>
    <table class="dl-table galaxy-table"><thead><tr><th></th><th></th><th>#</th><th>Title</th><th>Artists</th><th>Status</th></tr></thead><tbody>${a.tracks.map(t => `<tr class="${view.preview === galaxyKey(t) ? "selected" : ""}">
      <td><input type="checkbox" data-galaxy-select="${escapeHtml(galaxyKey(t))}" aria-label="Select ${escapeHtml(t.title)}" ${view.selected.has(galaxyKey(t)) ? "checked" : ""}></td>
      <td>${galaxyThumb(t.source, t.pic_id, 100)}</td><td>${t.track_no}</td><td><button type="button" class="text-action" data-galaxy-preview="${escapeHtml(galaxyKey(t))}">${escapeHtml(t.title)}</button></td>
      <td>${escapeHtml(t.artists.join(", "))}</td><td>${galaxyBadge(t)}</td></tr>
      ${view.preview === galaxyKey(t) ? `<tr class="galaxy-preview"><td></td><td colspan="5"><dl>${t.preview.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
        <dt>File</dt><dd>${escapeHtml(t.on_disk ? `${t.on_disk} (exists)` : t.path.replace("{ext}", "mp3 / flac / m4a, whatever the server sends"))}</dd></dl></td></tr>` : ""}`).join("")}</tbody></table>`;
}

// -- actions ---------------------------------------------------------------------------------

async function galaxySearch() {
  const view = galaxyView;
  view.query = ($("#galaxy-query")?.value ?? view.query).trim();
  view.source = $("#galaxy-source")?.value || view.source;
  if (!view.query) { toast("Type what to search for."); return; }
  const request = ++view.request;
  Object.assign(view, {loading: true, album: null, cards: null, cardFilter: "all", preview: "", sort: {key: "", dir: 1}});
  view.selected.clear(); renderDownloads();
  try {
    const data = await api("/api/galaxy/search", {mode: view.mode, q: view.query, source: view.source});
    if (request !== view.request) return;
    downloadsView.galaxy = {paused: data.paused, api: data.api, active: data.active, next_in: data.next_in, running: data.running};
    if (data.mode === "album") { view.album = data; view.data = null; } else view.data = data;
    setActionStatus("#downloads-status", "");
    if (data.mode === "artist" && data.tracks.length) {
      galaxyLoadCards(data, request);
      galaxyLoadYears(data.query, data.albums.map(a => a.name), request);
    }
    if (data.mode === "album" && data.found) galaxyLoadYears(data.album_artist, [data.album], request);
  } catch (error) {
    if (request === view.request) downloadsError(error);
  } finally {
    if (request === view.request) { view.loading = false; renderDownloads(); }
  }
}

async function galaxyLoadCards(data, request) {
  try {
    const cards = await api("/api/galaxy/cards", {name: data.query, albums: [...new Set(data.tracks.map(t => t.album || "Unknown Album"))]});
    if (request === galaxyView.request) galaxyView.cards = cards;
  } catch (error) {
    if (request === galaxyView.request) galaxyView.cards = {error: error.message, assign: {}, cards: []};
  }
  if (request === galaxyView.request && galaxyView.data) renderDownloads();
}

// Album years from MusicBrainz (about one album per second), filled in when they arrive.
async function galaxyLoadYears(artist, albums, request) {
  const wanted = albums.filter(a => a && a !== "Unknown Album" && !(a in galaxyView.years));
  if (!wanted.length) return;
  try {
    const {years} = await api("/api/galaxy/album-years", {artist, albums: wanted});
    Object.assign(galaxyView.years, years);
    if (request === galaxyView.request && !galaxyView.loading && downloadsView.tab === "galaxy" && state.screen === "downloads"
      && !document.activeElement?.closest?.("#downloads-content")) renderDownloads();
  } catch { /* years are a nicety; the search itself worked */ }
}

async function galaxyOpenAlbum(album, artist) {
  const view = galaxyView, request = ++view.request;
  Object.assign(view, {loading: true, preview: ""});
  view.selected.clear(); renderDownloads();
  try {
    view.album = await api("/api/galaxy/album", {album, artist, source: view.source});
    if (view.album.found && !view.years[view.album.album]) galaxyLoadYears(view.album.album_artist, [view.album.album], request);
  } catch (error) {
    if (request === view.request) downloadsError(error);
  } finally {
    if (request === view.request) { view.loading = false; renderDownloads(); }
  }
}

async function galaxyAdd(tracks, album) {
  if (!tracks.length) return;
  const body = album ? {tracks: tracks.map(t => ({...t, track_no: t.track_no})), album_artist: album.album_artist, total: album.tracks.length}
    : {tracks: tracks.map(({track_no, ...t}) => t)};
  const result = await api("/api/galaxy/add", body);
  downloadsNote(`${result.queued} queued${result.held ? `, ${result.held} waiting in Needs your decision` : ""}${result.skipped ? `, ${result.skipped} already listed` : ""}.`);
  const queued = new Set();
  galaxyView.selected.clear();
  await loadDownloads();
  for (const item of downloadsView.queue || []) if (item.payload && ["queued", "active"].includes(item.state)) queued.add(`${item.payload.source}:${item.payload.id}`);
  for (const list of [galaxyView.data?.tracks, galaxyView.album?.tracks]) for (const t of list || []) if (queued.has(galaxyKey(t))) t.queued = "queued";
  renderDownloads();
}

async function galaxyDownloadAlbum(album, artist) {
  const data = await api("/api/galaxy/album", {album, artist, source: galaxyView.source});
  if (!data.found) { toast(data.note); return; }
  await galaxyAdd(data.tracks, data);
}

function galaxyTrack(key) {
  return [...(galaxyView.album?.tracks || []), ...(galaxyView.data?.tracks || [])].find(t => galaxyKey(t) === key);
}

document.addEventListener("click", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "galaxy") return;
  const view = galaxyView;
  const mode = target.closest("[data-galaxy-mode]");
  if (mode) { view.query = $("#galaxy-query").value; view.mode = mode.dataset.galaxyMode; renderDownloads(); $("#galaxy-query").focus(); return; }
  if (target.closest("[data-galaxy-search]")) { galaxySearch(); return; }
  const artist = target.closest("[data-galaxy-artist]");
  if (artist) { Object.assign(view, {mode: "artist", query: artist.dataset.galaxyArtist}); galaxySearch(); return; }
  const album = target.closest("[data-galaxy-album]");
  if (album) { galaxyOpenAlbum(album.dataset.galaxyAlbum, album.dataset.artist); return; }
  if (target.closest("[data-galaxy-back]")) { view.album = null; view.selected.clear(); renderDownloads(); return; }
  const card = target.closest("[data-galaxy-card]");
  if (card) { view.cardFilter = view.cardFilter === card.dataset.galaxyCard ? "all" : card.dataset.galaxyCard; renderDownloads(); return; }
  const sort = target.closest("[data-galaxy-sort]");
  if (sort) {
    const key = sort.dataset.galaxySort;
    view.sort = view.sort.key === key ? (view.sort.dir > 0 ? {key, dir: -1} : {key: "", dir: 1}) : {key, dir: 1};
    renderDownloads(); return;
  }
  const preview = target.closest("[data-galaxy-preview]");
  if (preview) { view.preview = view.preview === preview.dataset.galaxyPreview ? "" : preview.dataset.galaxyPreview; renderDownloads(); return; }
  const one = target.closest("[data-galaxy-dl]");
  if (one) { const t = galaxyTrack(one.dataset.galaxyDl); downloadsRun(() => galaxyAdd([t], view.album?.tracks.includes(t) ? view.album : null)); return; }
  if (target.closest("[data-galaxy-dl-selected]")) {
    const list = view.album ? view.album.tracks : view.data.tracks;
    downloadsRun(() => galaxyAdd(list.filter(t => view.selected.has(galaxyKey(t))), view.album)); return;
  }
  if (target.closest("[data-galaxy-dl-all]")) { downloadsRun(() => galaxyAdd(galaxyFiltered(), null)); return; }
  if (target.closest("[data-galaxy-album-all]")) { downloadsRun(() => galaxyAdd(view.album.tracks, view.album)); return; }
  const albumDl = target.closest("[data-galaxy-album-dl]");
  if (albumDl) downloadsRun(() => galaxyDownloadAlbum(albumDl.dataset.galaxyAlbumDl, albumDl.dataset.artist));
});

document.addEventListener("change", event => {
  const target = event.target, view = galaxyView;
  if (!target.closest?.("#downloads-page")) return;
  if (target.id === "galaxy-source") { view.source = target.value; return; }
  if (target.matches("[data-galaxy-select]")) {
    view.selected[target.checked ? "add" : "delete"](target.dataset.galaxySelect); renderDownloads(); return;
  }
  if (target.matches("[data-galaxy-select-all]")) {
    for (const t of galaxyFiltered()) view.selected[target.checked ? "add" : "delete"](galaxyKey(t));
    renderDownloads(); return;
  }
  if (target.matches("[data-galaxy-pick]")) {
    const album = target.dataset.galaxyPick, card = target.value;
    downloadsRun(async () => {
      await api("/api/galaxy/pick", {name: view.cards.name, album, card});
      if (card) view.cards.assign[album] = card;
      renderDownloads();
      toast(card ? "Remembered for next time." : "Choice cleared.");
    });
  }
});

document.addEventListener("input", event => {
  if (event.target.id === "galaxy-query") galaxyView.query = event.target.value;
});

document.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.id === "galaxy-query") { event.preventDefault(); galaxySearch(); }
});
