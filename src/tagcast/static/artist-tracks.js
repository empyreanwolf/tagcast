"use strict";

// Album artist page: Albums | Tracks (fork v33). Like iBroadcast's own artist page, the
// Tracks tab lists every song on this artist's albums in one table, so nothing needs
// opening album by album. Its tracks come from the same album details the album page uses.

const artistTracks = {tab: storedArtistTab(), artist: null, details: null, rows: [], loading: false, error: "",
  request: 0, sort: "album", query: ""};

function storedArtistTab() {
  try { return localStorage.getItem("tagcast-artist-tab") === "tracks" ? "tracks" : "albums"; } catch { return "albums"; }
}

const artistTrackSorts = {
  album: (a, b) => foldText(a.album.name).localeCompare(foldText(b.album.name)) || (a.album.disc || 0) - (b.album.disc || 0)
    || (a.track.track || 9999) - (b.track.track || 9999) || foldText(a.track.title).localeCompare(foldText(b.track.title)),
  title: (a, b) => foldText(a.track.title).localeCompare(foldText(b.track.title)) || artistTrackSorts.album(a, b),
  newest: (a, b) => (Number(b.track.year || b.album.year) || 0) - (Number(a.track.year || a.album.year) || 0) || artistTrackSorts.album(a, b),
  oldest: (a, b) => (Number(a.track.year || a.album.year) || 9999) - (Number(b.track.year || b.album.year) || 9999) || artistTrackSorts.album(a, b),
};

// The albums the artist page shows, before its filters: every disc of each.
function artistTrackAlbumIds() {
  return albumShelf().filter(a => albumHasArtist(a, state.artist)).flatMap(itemIds);
}

async function loadArtistTracks() {
  const request = ++artistTracks.request, details = state.details, artist = state.artist;
  if (artistTracks.artist !== artist) artistTracks.query = "";  // a new artist starts unfiltered
  Object.assign(artistTracks, {artist, details, rows: [], loading: true, error: ""});
  renderArtistTracks();
  try {
    const albums = await albumDetails(artistTrackAlbumIds());
    if (request !== artistTracks.request) return;
    const seen = new Set();
    artistTracks.rows = albums.flatMap(album => (album.tracks || []).filter(t => !seen.has(String(t.id)) && seen.add(String(t.id)))
      .map(track => ({album, track})));
  } catch (error) {
    if (request === artistTracks.request) artistTracks.error = error.message;
  } finally {
    if (request === artistTracks.request) { artistTracks.loading = false; renderArtistTracks(); }
  }
}

function visibleArtistTracks() {
  const words = foldText(artistTracks.query).split(" ").filter(Boolean);
  return artistTracks.rows.filter(({album, track}) => words.every(w => foldText([track.title, track.artist, album.name].join(" ")).includes(w)))
    .sort(artistTrackSorts[artistTracks.sort] || artistTrackSorts.album);
}

// Tabs show on an album artist's page only; Tracks hides the album grid and its controls.
function renderArtistTabs() {
  const tabs = $("#artist-tabs"), onArtist = Boolean(state.artist) && (!state.screen || state.screen === "albums");
  const tracks = onArtist && artistTracks.tab === "tracks";
  tabs.hidden = !onArtist;
  for (const button of tabs.querySelectorAll?.("[data-artist-tab]") || []) {
    const selected = button.dataset.artistTab === (tracks ? "tracks" : "albums");
    button.setAttribute("aria-selected", String(selected)); button.setAttribute("aria-pressed", String(selected));
  }
  for (const id of ["#albums-toolbar", "#albums-results-line", "#albums", "#pagination"]) $(id).hidden = tracks;
  $("#artist-tracks").hidden = !tracks;
  if (!tracks) return;
  if (artistTracks.artist !== state.artist || artistTracks.details !== state.details) loadArtistTracks();
  else renderArtistTracks();
}

function renderArtistTracks() {
  const box = $("#artist-tracks");
  if (box.hidden) return;
  const rows = visibleArtistTracks(), all = artistTracks.rows.length;
  const total = rows.reduce((sum, r) => sum + (Number(r.track.length) || 0), 0);
  const canPlay = live() && rows.length > 0;
  const head = `<div class="toolbar artist-tracks-toolbar"><div class="search"><span aria-hidden="true">⌕</span><input id="artist-tracks-search" maxlength="200" placeholder="Search this artist's tracks" aria-label="Search this artist's tracks" value="${escapeHtml(artistTracks.query)}"></div>
    <select id="artist-tracks-sort" aria-label="Sort tracks">${[["album", "Album · A–Z"], ["title", "Title · A–Z"], ["newest", "Release year · newest first"], ["oldest", "Release year · oldest first"]]
      .map(([value, label]) => `<option value="${value}" ${artistTracks.sort === value ? "selected" : ""}>${label}</option>`).join("")}</select>
    <button type="button" class="button primary" data-artist-tracks-play ${canPlay ? "" : "disabled"}>▶ Play all</button>
    <button type="button" class="button" data-artist-tracks-shuffle ${canPlay ? "" : "disabled"}>Shuffle</button></div>`;
  const line = artistTracks.loading ? "" : `<p class="muted" role="status">${rows.length.toLocaleString("en")}${rows.length !== all ? ` of ${all.toLocaleString("en")}` : ""} ${all === 1 ? "track" : "tracks"}${total ? ` · ${trackDuration(total)}` : ""}</p>`;
  const body = artistTracks.loading ? '<div class="empty"><span class="spinner"></span>Loading this artist\'s tracks…</div>'
    : artistTracks.error ? `<p class="error-box" role="alert">${escapeHtml(artistTracks.error)}</p>`
    : !rows.length ? `<div class="empty">${all ? "No tracks match this search." : "No tracks on this artist's albums."}</div>`
    : `<table class="album-tracks album-tracklist track-results artist-track-table"><thead><tr><th class="track-number"><span class="track-number-label">#</span></th><th>Title / Artist</th><th>Album</th><th class="track-time">Time</th><th aria-label="Queue"></th></tr></thead><tbody>${rows.map(({album, track}) => `<tr data-row="${escapeHtml(track.id)}">
      <td class="track-number">${trackNumberControl(track, track.track || "–", `data-artist-track-play="${escapeHtml(track.id)}"`, live())}</td>
      <td><strong>${escapeHtml(track.title)}</strong><small class="role-artist">${escapeHtml(track.artist || "")}</small></td>
      <td><button class="text-action role-album" data-album="${escapeHtml(album.id)}">${escapeHtml(album.name)}</button>${album.year ? ` <small class="muted">${escapeHtml(album.year)}</small>` : ""}</td>
      <td class="track-time">${trackDuration(track.length)}${trackFormatLabel(track)}</td>
      <td>${quickAddButton(album.id, track)}${queueButton(album.id, track)}</td></tr>`).join("")}</tbody></table>`;
  const focused = document.activeElement?.id === "artist-tracks-search";
  box.innerHTML = head + line + body;
  if (focused) { const input = $("#artist-tracks-search"); input.focus?.(); input.setSelectionRange?.(input.value.length, input.value.length); }
}

// One queue across albums, in the order shown, starting at the clicked track.
function playArtistTracks(startId = null, shuffle = false) {
  if (!live() || artistTracks.loading) return;
  if (typeof localPlaybackAllowed === "function" && !localPlaybackAllowed()) { toast("Use Devices to control the connected queue."); return; }
  const queue = visibleArtistTracks().map(({album, track}) => queueTracks(album, [track])[0]);
  if (!queue.length) return;
  cancelRandomPlay();
  player.epoch += 1;
  player.shuffle = shuffle;
  if (shuffle) for (let i = queue.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [queue[i], queue[j]] = [queue[j], queue[i]];
  }
  player.queue = queue;
  if (typeof gaplessAlbumQueue === "function") gaplessAlbumQueue(player.queue, false);
  player.index = startId === null ? 0 : Math.max(0, queue.findIndex(t => t.id === String(startId)));
  playCurrent();
}

document.addEventListener("click", event => {
  const tab = event.target.closest("[data-artist-tab]");
  if (tab) {
    artistTracks.tab = tab.dataset.artistTab === "tracks" ? "tracks" : "albums";
    if (artistTracks.error) artistTracks.details = null;  // a failed load is tried again
    try { localStorage.setItem("tagcast-artist-tab", artistTracks.tab); } catch { /* private window */ }
    renderArtistTabs();
    return;
  }
  const play = event.target.closest("[data-artist-track-play]");
  if (play) { playArtistTracks(play.dataset.artistTrackPlay); return; }
  if (event.target.closest("[data-artist-tracks-play]")) { playArtistTracks(); return; }
  if (event.target.closest("[data-artist-tracks-shuffle]")) { playArtistTracks(null, true); return; }
});

document.addEventListener("input", event => {
  if (event.target.id !== "artist-tracks-search") return;
  artistTracks.query = event.target.value;
  renderArtistTracks();
});

document.addEventListener("change", event => {
  if (event.target.id !== "artist-tracks-sort") return;
  artistTracks.sort = event.target.value in artistTrackSorts ? event.target.value : "album";
  renderArtistTracks();
});
