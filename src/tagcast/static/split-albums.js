"use strict";

// Split albums (fork v62). iBroadcast groups an album by title + album artist, so one album whose tracks
// got different album artists (a bulk edit, a compilation) shows as many one-track albums. This finds
// them and joins each with one click: every part gets the same album artist (the main artist when one
// artist holds 60%+ of the tracks, else Various Artists), no extra album artists, and one year.
// Track artists stay as they are. The Albums filter "Split albums" lists them.

const splitView = {cache: null, busy: new Set(), done: new Set(), choice: new Map(), all: false};
const SPLIT_GENERIC = /^(greatest hits|the best of|best of|hits|singles|untitled|unknown album|untitled album|demo|demos|live|ep|mixtape|remixes|instrumentals)$/;

function splitTitleKey(name) {
  return foldText(name).replace(/\bvol(ume)?\b\.?/g, "vol").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

// Groups of album parts that look like one album: same title and disc, 3+ album artists, most parts 1–3 tracks.
function splitGroups() {
  if (splitView.cache?.albums === state.albums) return splitView.cache.groups;
  const byKey = new Map();
  for (const a of state.albums) {
    const key = splitTitleKey(a.name);
    if (!key) continue;
    const k = `${key}\u0000${a.disc || 0}`;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(a);
  }
  const groups = [];
  for (const [k, parts] of byKey) {
    const artists = new Set(parts.map(a => foldText(a.artist)));
    if (artists.size < 3) continue;
    const small = parts.filter(a => a.track_count <= 3).length;
    if (small * 2 < parts.length) continue;
    const count = values => { const m = new Map(); for (const v of values) if (v) m.set(v, (m.get(v) || 0) + 1); return [...m].sort((x, y) => y[1] - x[1]); };
    const art = count(parts.map(a => a.artwork))[0], year = count(parts.map(a => a.year))[0];
    const dates = count(parts.flatMap(a => a.upload_dates || []))[0];
    const shared = (art && art[1] * 2 >= parts.length) || (dates && dates[1] * 2 >= parts.length) || (year && year[1] * 2 >= parts.length);
    const generic = SPLIT_GENERIC.test(k.split("\u0000")[0]);
    const tracks = parts.reduce((n, a) => n + a.track_count, 0);
    const byArtist = new Map();
    for (const a of parts) byArtist.set(a.artist, (byArtist.get(a.artist) || 0) + a.track_count);
    const [topArtist, topTracks] = [...byArtist].sort((x, y) => y[1] - x[1])[0];
    groups.push({key: k, name: parts.find(a => a.artwork)?.name || parts[0].name, parts, tracks, year: year?.[0] || 0,
      artwork: art?.[0] || "", confident: Boolean(shared) && !generic,
      suggested: topTracks / tracks >= 0.6 && topArtist !== "Various Artists" ? topArtist : "Various Artists",
      artists: [...byArtist.keys()].sort((x, y) => byArtist.get(y) - byArtist.get(x))});
  }
  groups.sort((x, y) => y.confident - x.confident || y.parts.length - x.parts.length);
  const ids = new Set(groups.flatMap(g => g.parts.map(a => String(a.id))));
  splitView.cache = {albums: state.albums, groups, ids};
  return groups;
}

function isSplitAlbum(a) {
  splitGroups();
  return itemIds(a).some(id => splitView.cache.ids.has(id));
}

// The album edits that join one group: same album artist on every part, no extra album artists, one year.
async function splitChanges(group, target) {
  const details = await albumDetails(group.parts.map(a => String(a.id)));
  const changes = [];
  for (const d of details) {
    const fields = {};
    if (target === "Various Artists") {
      if (d.artist_id !== 0 || d.artist !== "Various Artists") fields.artist_id = {before: d.artist_id, after: 0, before_name: d.artist, after_name: "Various Artists"};
    } else if (d.artist !== target) fields.artist = {before: d.artist, after: target};
    if ((d.credits || []).length) fields.credits = {before: d.credits, after: []};
    if (group.year && Number(d.year) !== group.year) fields.year = {before: d.year, after: group.year};
    if (Object.keys(fields).length) changes.push({kind: "album", id: d.id, albumId: d.id, label: `${d.name} (${d.artist}) → ${target}`, fields});
  }
  return changes;
}

async function mergeSplitGroups(groups) {
  if (!live()) { toast("Connect iBroadcast to merge albums."); return; }
  if (pending || $("#editor").open || $("#review").open) { toast("Finish or close the open edit first."); return; }
  groups.forEach(g => splitView.busy.add(g.key)); renderSplitAlbums();
  try {
    const changes = [];
    for (const g of groups) changes.push(...await splitChanges(g, splitView.choice.get(g.key) || g.suggested));
    if (!changes.length) { toast("These albums already share one album artist."); return; }
    groups.forEach(g => splitView.done.add(g.key));
    pending = {scope: "split", splitMerge: true, selection: groups.flatMap(g => g.parts.map(a => ({id: a.id, name: a.name, artist: a.artist}))), changes};
    saveDraft();
  } catch (error) {
    toast(`Could not merge: ${error.message || error}`);
  } finally {
    groups.forEach(g => splitView.busy.delete(g.key)); renderSplitAlbums();
  }
}

function splitGroupHtml(g) {
  const done = splitView.done.has(g.key), busy = splitView.busy.has(g.key);
  const target = splitView.choice.get(g.key) || g.suggested;
  const options = ["Various Artists", ...g.artists.filter(a => a !== "Various Artists")];
  return `<li class="split-row">${g.artwork ? `<img src="${escapeHtml(g.artwork)}" alt="" loading="lazy">` : '<span class="split-noart"></span>'}
    <details><summary><b>${escapeHtml(g.name)}</b> <span class="muted">${g.parts.length} album artists · ${g.tracks} tracks${g.year ? ` · ${g.year}` : ""}</span>${g.confident ? "" : ' <span class="dl-badge partly">Check</span>'}</summary>
      <ul class="muted">${g.parts.map(a => `<li>${escapeHtml(a.artist)} · ${a.track_count} ${a.track_count === 1 ? "track" : "tracks"}${a.year ? ` · ${a.year}` : ""}</li>`).join("")}</ul></details>
    <label class="split-target">→ <select data-split-target="${escapeHtml(g.key)}" aria-label="Album artist after merging" ${done ? "disabled" : ""}>${options.map(o => `<option ${o === target ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}</select></label>
    <button type="button" class="button ${done ? "" : "primary"}" data-split-merge="${escapeHtml(g.key)}" ${done || busy ? "disabled" : ""}>${done ? "Merged" : busy ? "Merging…" : "Merge"}</button></li>`;
}

function renderSplitAlbums() {
  const box = $("#split-albums");
  if (!box) return;
  const show = $("#filter").value === "split" && !state.artist;
  box.hidden = !show;
  if (!show) return;
  const groups = splitGroups(), open = groups.filter(g => !splitView.done.has(g.key));
  const ready = open.filter(g => g.confident);
  box.innerHTML = groups.length ? `<div class="dl-actions"><strong>${groups.length} split ${groups.length === 1 ? "album" : "albums"}</strong>
      <span class="muted">Each part gets one album artist; track artists stay.</span>
      <button type="button" class="button primary" data-split-merge-all ${ready.length ? "" : "disabled"}>Merge all (${ready.length})</button>
      ${open.length > ready.length ? `<span class="muted">${open.length - ready.length} marked Check are skipped by Merge all.</span>` : ""}</div>
    <ul class="split-list">${groups.map(splitGroupHtml).join("")}</ul>`
    : '<p class="muted">No split albums found.</p>';
}

document.addEventListener("change", event => {
  const select = event.target.closest?.("[data-split-target]");
  if (select) splitView.choice.set(select.dataset.splitTarget, select.value);
});

document.addEventListener("click", event => {
  const one = event.target.closest?.("[data-split-merge]");
  if (one) { const g = splitGroups().find(x => x.key === one.dataset.splitMerge); if (g) mergeSplitGroups([g]); return; }
  if (event.target.closest?.("[data-split-merge-all]")) mergeSplitGroups(splitGroups().filter(g => g.confident && !splitView.done.has(g.key)));
});
