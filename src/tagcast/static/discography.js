"use strict";

// Discography & Top 30 (fork v15). An artist's albums, EPs, singles and compilations from
// Deezer and Apple Music (MusicBrainz on request), each marked Have / Partly / Downloaded
// before / Missing, plus their 30 most popular songs with 30-second previews. "Get the missing"
// tries Bandcamp, then Soulseek, then Galaxy; anything you may have waits in Needs your decision.
const discographyView = {
  name: "", data: null, top: null, loading: false, request: 0, types: new Set(["album", "ep"]),
  picked: new Set(), pickedSongs: new Set(), job: null, jobTimer: null, preview: null,
};
const discographyTypes = [["album", "Albums"], ["ep", "EPs"], ["single", "Singles"], ["compile", "Compilations"]];
const discographyStatus = {have: "Have", partly: "Partly", downloaded: "Downloaded before", folder: "In your download folder", missing: "Missing"};

function discographyBadge(status) {
  return `<span class="dl-badge ${status === "missing" ? "new" : status}">${discographyStatus[status] || status}</span>`;
}

function discographyHtml() {
  const view = discographyView;
  return `<div class="galaxy-search">
      <input id="discography-name" class="dl-path" maxlength="200" placeholder="Artist name, e.g. Coldplay" aria-label="Artist" value="${escapeHtml(view.name)}">
      <button type="button" class="button primary" data-discography="load" ${view.loading ? "disabled" : ""}>Show discography</button></div>
    ${view.job ? discographyJobHtml() : ""}
    ${view.loading ? '<p class="muted">Asking Deezer and Apple Music…</p>' : view.data ? discographyResultsHtml() : '<p class="muted">Every release by an artist with what you already have, and their top 30 songs. Also opens from an album artist page (Discography & top songs).</p>'}`;
}

function discographyResultsHtml() {
  const view = discographyView, d = view.data, artist = d.artist;
  const shown = d.releases.filter(r => view.types.has(r.type));
  const counts = Object.keys(discographyStatus).map(s => [s, shown.filter(r => r.status === s).length]).filter(([, n]) => n);
  const picked = shown.filter(r => view.picked.has(r.title)).length;
  const others = (d.candidates || []).filter(c => !artist || c.id !== artist.id);
  return `<div class="discography-head">${artist?.picture ? `<img src="${escapeHtml(artist.picture)}" alt="" class="discography-photo" onerror="this.remove()">` : ""}
      <div><h2>${escapeHtml(d.name)}</h2><p class="muted">${artist ? `Deezer: ${Number(artist.fans || 0).toLocaleString("en")} fans · ${artist.albums} releases` : "Not found on Deezer"}
        ${d.notes?.length ? ` · ${escapeHtml(d.notes.join("; "))}` : ""}</p>
        ${others.length ? `<p class="muted">Other artists called “${escapeHtml(d.name)}”: ${others.map(c => `<button type="button" class="text-action" data-discography-artist="${c.id}">${escapeHtml(c.name)} (${Number(c.fans || 0).toLocaleString("en")} fans)</button>`).join(", ")}</p>` : ""}</div></div>
    <h3>Releases</h3>
    <div class="dl-actions">${discographyTypes.map(([t, label]) => `<label><input type="checkbox" data-discography-type="${t}" ${view.types.has(t) ? "checked" : ""}> ${label} (${d.releases.filter(r => r.type === t).length})</label>`).join("")}
      <button type="button" class="text-action" data-discography="musicbrainz">Also check MusicBrainz</button>
      <button type="button" class="text-action" data-discography="refresh">Refresh</button></div>
    <div class="dl-actions">${counts.map(([s, n]) => `<span class="dl-badge ${s === "missing" ? "new" : s}">${n} ${discographyStatus[s]}</span>`).join(" ")}</div>
    <div class="dl-actions"><button type="button" class="button primary" data-discography="get-albums" ${picked ? "" : "disabled"}>⬇ Get the missing (${picked} selected)</button>
      <button type="button" class="text-action" data-discography="pick-missing">Select missing &amp; partly</button>
      <button type="button" class="text-action" data-discography="pick-none">Select none</button>
      <span class="muted">Order: Bandcamp → Galaxy → Soulseek.</span></div>
    <ul class="discography-list">${shown.map(r => `<li class="${r.status}">
      <input type="checkbox" data-discography-pick="${escapeHtml(r.title)}" aria-label="Select ${escapeHtml(r.title)}" ${view.picked.has(r.title) ? "checked" : ""}>
      ${bandcampThumb(r.cover, "galaxy-album-art")}
      <div><b>${escapeHtml(r.title)}</b><div class="muted">${[r.year, ({album: "Album", ep: "EP", single: "Single", compile: "Compilation"})[r.type], r.tracks ? `${r.tracks} tracks` : "", r.genre].filter(Boolean).map(escapeHtml).join(" · ")}</div>
        <div class="muted discography-sources">${escapeHtml(r.sources.join(", "))}</div></div>
      <div>${discographyBadge(r.status)}<div class="dl-have">${downloadsYouHave(r.you_have)}</div></div></li>`).join("") || '<li class="muted">No releases of these types.</li>'}</ul>
    ${discographyTopHtml()}`;
}

function discographyTopHtml() {
  const view = discographyView, top = view.top;
  if (!top) return '<h3>Top 30 songs</h3><p class="muted">Loading…</p>';
  const missing = top.songs.filter(s => s.status === "missing").length;
  const picked = top.songs.filter(s => view.pickedSongs.has(s.title)).length;
  return `<h3>Top ${top.songs.length} songs <span class="muted">(ranking from ${escapeHtml(top.source)})</span></h3>
    <div class="dl-actions"><button type="button" class="button primary" data-discography="get-songs" ${picked ? "" : "disabled"}>⬇ Get the missing songs (${picked} selected)</button>
      <button type="button" class="text-action" data-discography="pick-songs">Select all ${missing} missing</button></div>
    <table class="dl-table galaxy-table"><tbody>${top.songs.map((s, i) => `<tr>
      <td><input type="checkbox" data-discography-song="${escapeHtml(s.title)}" aria-label="Select ${escapeHtml(s.title)}" ${view.pickedSongs.has(s.title) ? "checked" : ""}></td>
      <td>${i + 1}</td><td>${s.preview ? `<button type="button" class="button small" data-discography-play="${escapeHtml(s.preview)}" aria-label="Play a 30-second preview of ${escapeHtml(s.title)}">${view.preview === s.preview ? "■" : "▶"}</button>` : ""}</td>
      <td><b>${escapeHtml(s.title)}</b>${s.album ? ` <span class="muted">· ${escapeHtml(s.album)}</span>` : ""}</td>
      <td>${discographyBadge(s.status)}</td></tr>`).join("")}</tbody></table>`;
}

function discographyJobHtml() {
  const job = discographyView.job;
  return `<section class="dl-card" role="status"><h3>Getting the missing ${job.state === "done" ? "· done" : `· ${job.done} of ${job.total}`}</h3>
    <ul class="dl-list">${job.log.map(e => `<li><span class="dl-badge ${e.source ? "have" : "partly"}">${escapeHtml(e.source || "Not found")}</span>
      <div><b>${escapeHtml(e.title)}</b> <span class="muted">· ${escapeHtml(e.note || "")}</span></div></li>`).join("")}</ul>
    ${job.state === "done" ? '<div class="dl-actions"><button type="button" class="button small" data-downloads-tab="queue">Open the queue</button><button type="button" class="text-action" data-discography="close-job">Close</button></div>' : ""}</section>`;
}

async function discographyLoad(options = {}) {
  const view = discographyView;
  view.name = ($("#discography-name")?.value ?? view.name).trim();
  if (!view.name) { toast("Type an artist name."); return; }
  const request = ++view.request;
  view.loading = !options.keep; view.top = options.keep ? view.top : null;
  if (!options.keep) { view.picked.clear(); view.pickedSongs.clear(); }
  renderDownloads();
  try {
    const data = await api("/api/discography", {name: view.name, deezer_id: options.deezerId || null, musicbrainz: Boolean(options.musicbrainz), refresh: Boolean(options.refresh)});
    if (request !== view.request) return;
    view.data = data;
    if (!options.keep) for (const r of data.releases) if (["missing", "partly"].includes(r.status) && view.types.has(r.type)) view.picked.add(r.title);
    setActionStatus("#downloads-status", "");
  } catch (error) {
    if (request === view.request) downloadsError(error);
  } finally {
    if (request === view.request) { view.loading = false; renderDownloads(); }
  }
  if (request !== view.request || !view.data || (options.keep && view.top)) return;
  try {
    view.top = await api("/api/discography/top", {name: view.name, deezer_id: view.data.artist?.id || null});
  } catch (error) { view.top = {source: "nowhere", songs: [], error: error.message}; }
  if (request === view.request && downloadsView.tab === "discography") renderDownloads();
}

async function discographyGet(albums, songs) {
  const view = discographyView;
  view.job = await api("/api/discography/get", {name: view.data.name, albums, songs});
  renderDownloads();
  clearTimeout(view.jobTimer);
  const poll = async () => {
    try { view.job = await api(`/api/discography/get-status?id=${encodeURIComponent(view.job.id)}`); } catch { return; }
    if (downloadsView.tab === "discography") renderDownloads();
    if (view.job.state !== "done") view.jobTimer = setTimeout(poll, 1500);
    else {
      if (view.job.log.some(e => !e.source)) artistCleanupAlarm();
      loadDownloads(); discographyLoad({keep: true});
    }
  };
  view.jobTimer = setTimeout(poll, 1000);
}

function showDiscography(name) {
  discographyView.name = name;
  showDownloads("discography");
  discographyLoad();
}

let discographyAudio = null;

document.addEventListener("click", event => {
  const target = event.target;
  const fromArtist = target.closest?.("[data-artist-discography]");
  if (fromArtist) { showDiscography(fromArtist.dataset.artistDiscography); return; }
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "discography") return;
  const view = discographyView;
  const play = target.closest("[data-discography-play]");
  if (play) {
    const url = play.dataset.discographyPlay;
    if (discographyAudio) discographyAudio.pause();
    if (view.preview === url) { view.preview = null; renderDownloads(); return; }
    discographyAudio = new Audio(url); view.preview = url;
    discographyAudio.addEventListener("ended", () => { view.preview = null; renderDownloads(); });
    discographyAudio.play().catch(() => { view.preview = null; toast("The preview could not be played."); renderDownloads(); });
    renderDownloads(); return;
  }
  const other = target.closest("[data-discography-artist]");
  if (other) { discographyLoad({deezerId: other.dataset.discographyArtist}); return; }
  const button = target.closest("[data-discography]");
  if (!button) return;
  const action = button.dataset.discography;
  if (action === "load") discographyLoad();
  else if (action === "musicbrainz") discographyLoad({keep: true, musicbrainz: true, deezerId: view.data?.artist?.id});
  else if (action === "refresh") discographyLoad({refresh: true, deezerId: view.data?.artist?.id});
  else if (action === "pick-missing") {
    view.picked = new Set(view.data.releases.filter(r => view.types.has(r.type) && ["missing", "partly"].includes(r.status)).map(r => r.title)); renderDownloads();
  } else if (action === "pick-none") { view.picked.clear(); renderDownloads(); }
  else if (action === "pick-songs") { view.pickedSongs = new Set(view.top.songs.filter(s => s.status === "missing").map(s => s.title)); renderDownloads(); }
  else if (action === "close-job") { view.job = null; renderDownloads(); }
  else if (action === "get-albums") {
    const albums = view.data.releases.filter(r => view.types.has(r.type) && view.picked.has(r.title))
      .map(r => ({title: r.title, status: r.status, deezer_id: r.deezer_id, tracks: r.tracks}));
    downloadsRun(() => discographyGet(albums, []));
  } else if (action === "get-songs") {
    const songs = view.top.songs.filter(s => view.pickedSongs.has(s.title)).map(s => ({title: s.title, album: s.album}));
    downloadsRun(() => discographyGet([], songs));
  }
});

document.addEventListener("change", event => {
  const target = event.target, view = discographyView;
  if (!target.closest?.("#downloads-page")) return;
  if (target.matches("[data-discography-type]")) { view.types[target.checked ? "add" : "delete"](target.dataset.discographyType); renderDownloads(); }
  else if (target.matches("[data-discography-pick]")) { view.picked[target.checked ? "add" : "delete"](target.dataset.discographyPick); renderDownloads(); }
  else if (target.matches("[data-discography-song]")) { view.pickedSongs[target.checked ? "add" : "delete"](target.dataset.discographySong); renderDownloads(); }
});
document.addEventListener("input", event => { if (event.target.id === "discography-name") discographyView.name = event.target.value; });
document.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.id === "discography-name") { event.preventDefault(); discographyLoad(); }
});
