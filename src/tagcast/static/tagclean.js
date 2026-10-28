"use strict";

// Tag clean-up before upload (fork v19): the artist and genre fixes you'd otherwise do by
// hand in Mp3tag before uploading. A combined artist field is split on your own separators,
// the first one becomes the album artist, and for hip-hop/rap, the genre can be collapsed
// into whichever of your own iBroadcast genres already covers "underground" and "chinese"
// hip-hop -- never a genre you don't already have. A track MusicBrainz can't place with
// confidence is grouped by artist, so you can pick a genre once and apply it to everything
// by that artist, the same "select all, set genre" you'd do in Mp3tag.
const tagcleanView = {data: null, scope: "folder", path: "", pickedArtists: new Set(), pickedGenre: new Set(),
  genreChoice: {}, timer: null};

function tagcleanHtml() {
  // Two tabs (fork v38): the scan-before-upload tool and Recently uploaded (uploads.js).
  return uploadsTabsHtml() + (uploadsView.sub === "uploads" ? uploadsHtml() : tagcleanScanHtml());
}

function tagcleanScanHtml() {
  const v = tagcleanView;
  if (!v.data) { tagcleanLoad(); return '<p class="muted">Loading…</p>'; }
  const d = v.data;
  const p = d.progress;
  return `<section class="dl-card">
      <h3>Scan before you upload</h3>
      <p class="muted">Splits a combined artist field (feat., &amp;, / and the rest below) into separate artists joined by "; ", sets the first as album artist, and for hip-hop/rap, offers a genre already in your iBroadcast library -- nothing new is ever invented.</p>
      ${d.error ? `<p class="error-text">${escapeHtml(d.error)}</p>` : ""}
      <div class="dl-actions">
        <label><input type="radio" name="tagclean-scope" value="folder" ${v.scope === "folder" ? "checked" : ""}> A folder of files</label>
        <label><input type="radio" name="tagclean-scope" value="library" ${v.scope === "library" ? "checked" : ""}> My iBroadcast library</label></div>
      ${v.scope === "folder" ? `<div class="dl-actions"><input id="tagclean-path" class="dl-path" maxlength="500" value="${escapeHtml(v.path)}" placeholder="Folder of files to clean up" aria-label="Folder to scan">
        <button type="button" class="button primary" data-tagclean="scan" ${p ? "disabled" : ""}>Scan this folder</button></div>`
        : `<div class="dl-actions"><button type="button" class="button primary" data-tagclean="scan" ${p ? "disabled" : ""}>Scan my library</button></div>`}
      <p class="muted" role="status">${p ? `Scanning${p.title ? `: ${escapeHtml(p.title)}` : ""} (${p.done} of ${p.total})…`
        : d.scanned ? `Last scanned: ${missingDate(d.scanned)}` : "Not scanned yet."}</p>
      <details class="dl-card"><summary>Separators that split a combined artist field (${d.separators.length})</summary>
        <div class="releases-artists">${d.separators.map(s => `<span class="pill">${escapeHtml(s)} <button type="button" class="text-action" data-tagclean-remove-sep="${escapeHtml(s)}" aria-label="Remove separator ${escapeHtml(s)}">✕</button></span>`).join(" ") || '<span class="muted">None -- nothing will be split.</span>'}</div>
        <div class="dl-actions"><input id="tagclean-sep" class="dl-path" maxlength="20" placeholder="Add a separator, e.g. x" aria-label="Add a separator">
          <button type="button" class="button" data-tagclean="add-sep">Add</button></div></details>
    </section>
    ${tagcleanArtistHtml()}${tagcleanGenreHtml()}${tagcleanDecisionHtml()}`;
}

function tagcleanWhere(r) {
  return r.scope === "folder" ? `<br><span class="muted">${escapeHtml(r.ref)}</span>` : "";
}

function tagcleanArtistHtml() {
  const v = tagcleanView, rows = v.data.artist_ready || [];
  if (!rows.length) return "";
  return `<section class="dl-card"><h3>Artist fixes (${rows.length})</h3><ul class="dl-list">
      ${rows.map(r => `<li><input type="checkbox" data-tagclean-pick-artist="${r.id}" aria-label="Select ${escapeHtml(r.title || r.ref)}" ${v.pickedArtists.has(r.id) ? "checked" : ""}>
          <div><b>${escapeHtml(r.title || "(untitled)")}</b>${tagcleanWhere(r)}<br>
          ${(r.current_artists || []).length > 1 ? `<span class="pill missing">${r.current_artists.length} separate values</span> ` : ""}“${escapeHtml((r.current_artists || []).join("; ") || r.current_album_artist || "")}” → <b>${escapeHtml((r.new_artists || []).join("; "))}</b>
          (album artist: ${escapeHtml(r.new_album_artist || "")})</div></li>`).join("")}</ul>
      <div class="dl-actions"><label><input type="checkbox" data-tagclean-all="artist" ${rows.every(r => v.pickedArtists.has(r.id)) ? "checked" : ""}> Select all</label>
        <button type="button" class="button primary" data-tagclean="apply-artists" ${v.pickedArtists.size ? "" : "disabled"}>Fix selected (${v.pickedArtists.size})</button></div></section>`;
}

function tagcleanGenreHtml() {
  const v = tagcleanView, rows = v.data.genre_ready || [];
  if (!rows.length) return "";
  return `<section class="dl-card"><h3>Genre fixes (${rows.length})</h3><ul class="dl-list">
      ${rows.map(r => `<li><input type="checkbox" data-tagclean-pick-genre="${r.id}" aria-label="Select ${escapeHtml(r.title || r.ref)}" ${v.pickedGenre.has(r.id) ? "checked" : ""}>
          <div><b>${escapeHtml(r.title || "(untitled)")}</b> · ${escapeHtml(r.artist)}${tagcleanWhere(r)}<br>
          ${escapeHtml(r.current_genre || "(no genre)")} → <b>${escapeHtml(r.new_genre)}</b></div></li>`).join("")}</ul>
      <div class="dl-actions"><label><input type="checkbox" data-tagclean-all="genre" ${rows.every(r => v.pickedGenre.has(r.id)) ? "checked" : ""}> Select all</label>
        <button type="button" class="button primary" data-tagclean="apply-genre" ${v.pickedGenre.size ? "" : "disabled"}>Fix selected (${v.pickedGenre.size})</button></div></section>`;
}

function tagcleanDecisionHtml() {
  const v = tagcleanView, groups = v.data.needs_decision || [];
  if (!groups.length) return "";
  return `<section class="dl-card"><h3>Needs your decision (${groups.length} artist${groups.length === 1 ? "" : "s"})</h3>
      <p class="muted">No genre yet, or MusicBrainz couldn't place the artist with confidence, so nothing was guessed. Type a genre or pick one of yours, and it's applied to every song listed for that artist at once, like select all → Genre in Mp3tag.</p>
      <datalist id="tagclean-genres">${v.data.genres.map(gn => `<option value="${escapeHtml(gn)}"></option>`).join("")}</datalist>
      ${groups.map(g => {
        const ids = g.rows.map(r => r.id).join(",");
        return `<div class="dl-card"><b>${escapeHtml(g.artist)}</b> (${g.rows.length})
          <ul class="dl-list">${g.rows.map(r => `<li><div>${escapeHtml(r.title)}${tagcleanWhere(r)} (${r.current_genre ? `currently “${escapeHtml(r.current_genre)}”` : "no genre"})</div></li>`).join("")}</ul>
          <div class="dl-actions"><input class="dl-path" list="tagclean-genres" maxlength="100" data-tagclean-decide="${escapeHtml(g.artist)}" value="${escapeHtml(v.genreChoice[g.artist] || "")}" placeholder="Type or choose a genre" aria-label="Genre for ${escapeHtml(g.artist)}">
            <button type="button" class="button primary" data-tagclean-apply-decide="${escapeHtml(g.artist)}" data-ids="${ids}">Apply to ${escapeHtml(g.artist)}</button></div></div>`;
      }).join("")}</section>`;
}

async function tagcleanLoad() {
  try { tagcleanView.data = await api("/api/tagclean"); } catch (error) { tagcleanView.data = {error: error.message, separators: [], genres: [], artist_ready: [], genre_ready: [], needs_decision: []}; }
  tagcleanRefresh();
  clearTimeout(tagcleanView.timer);
  if (tagcleanView.data?.progress && state.screen === "downloads" && downloadsView.tab === "tagclean") tagcleanView.timer = setTimeout(tagcleanLoad, 1500);
}

function tagcleanRefresh() {
  if (downloadsView.tab === "tagclean" && $("#downloads-content")) $("#downloads-content").innerHTML = tagcleanHtml();
}

document.addEventListener("click", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "tagclean") return;
  const v = tagcleanView;
  const action = target.closest("[data-tagclean]")?.dataset.tagclean;
  if (action === "scan") {
    downloadsRun(async () => { v.data = await api("/api/tagclean/scan", {scope: v.scope, path: v.path}); tagcleanRefresh(); tagcleanLoad(); });
    return;
  }
  if (action === "add-sep") {
    const input = $("#tagclean-sep"), value = input?.value.trim();
    if (value) downloadsRun(async () => { v.data = await api("/api/tagclean/settings", {separators: [...v.data.separators, value]}); tagcleanRefresh(); });
    return;
  }
  if (action === "apply-artists") {
    downloadsRun(async () => { v.data = await api("/api/tagclean/apply-artists", {ids: [...v.pickedArtists]}); v.pickedArtists.clear(); tagcleanRefresh(); });
    return;
  }
  if (action === "apply-genre") {
    downloadsRun(async () => { v.data = await api("/api/tagclean/apply-genre", {ids: [...v.pickedGenre]}); v.pickedGenre.clear(); tagcleanRefresh(); });
    return;
  }
  const removeSep = target.closest("[data-tagclean-remove-sep]")?.dataset.tagcleanRemoveSep;
  if (removeSep !== undefined) {
    downloadsRun(async () => { v.data = await api("/api/tagclean/settings", {separators: v.data.separators.filter(s => s !== removeSep)}); tagcleanRefresh(); });
    return;
  }
  const decideButton = target.closest("[data-tagclean-apply-decide]");
  if (decideButton) {
    const artist = decideButton.dataset.tagcleanApplyDecide, ids = decideButton.dataset.ids.split(",").filter(Boolean);
    const genre = (decideButton.closest(".dl-actions")?.querySelector("[data-tagclean-decide]")?.value || v.genreChoice[artist] || "").trim();
    if (!genre) { toast("Type or choose a genre first."); return; }
    downloadsRun(async () => { v.data = await api("/api/tagclean/assign-genre", {ids, genre}); delete v.genreChoice[artist]; tagcleanRefresh(); });
    return;
  }
  const all = target.closest("[data-tagclean-all]");
  if (all) {
    const kind = all.dataset.tagcleanAll, rows = (kind === "artist" ? v.data.artist_ready : v.data.genre_ready) || [];
    const set = kind === "artist" ? v.pickedArtists : v.pickedGenre;
    if (rows.every(r => set.has(r.id))) rows.forEach(r => set.delete(r.id)); else rows.forEach(r => set.add(r.id));
    tagcleanRefresh();
  }
});

document.addEventListener("change", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "tagclean") return;
  const v = tagcleanView;
  if (target.name === "tagclean-scope") { v.scope = target.value; tagcleanRefresh(); return; }
  const pickArtist = target.closest("[data-tagclean-pick-artist]");
  if (pickArtist) { const id = pickArtist.dataset.tagcleanPickArtist; target.checked ? v.pickedArtists.add(id) : v.pickedArtists.delete(id); tagcleanRefresh(); return; }
  const pickGenre = target.closest("[data-tagclean-pick-genre]");
  if (pickGenre) { const id = pickGenre.dataset.tagcleanPickGenre; target.checked ? v.pickedGenre.add(id) : v.pickedGenre.delete(id); tagcleanRefresh(); return; }
  const decide = target.closest("[data-tagclean-decide]");
  if (decide) { v.genreChoice[decide.dataset.tagcleanDecide] = target.value; }
});

document.addEventListener("input", event => {
  const target = event.target;
  if (target.id === "tagclean-path") tagcleanView.path = target.value;
});
