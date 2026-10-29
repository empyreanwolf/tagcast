"use strict";

// One-click "add to my quick playlist" (fork v49).
// The ♫+ button on a track or album appends it to the quick playlist straight away: no dialog,
// no review step. The first click (or Shift-click) asks which playlist to use, once. The write is
// the same single "append" the review dialog sends (conflict check, read-back, no automatic retry);
// only the extra clicks are gone. The longer "Add to playlist…" dialog stays for new playlists,
// picking tracks and replacing.

const QUICK_PLAYLIST_KEY = "tagcast-quick-playlist";
const quickPlaylist = {value: null, busy: false, picker: null};

function readQuickPlaylist() {
  try {
    const saved = JSON.parse(localStorage.getItem(QUICK_PLAYLIST_KEY) || "null");
    if (saved && /^\d+$/.test(String(saved.id)) && typeof saved.name === "string" && saved.name) return {id: String(saved.id), name: saved.name};
  } catch { /* private window or damaged value */ }
  return null;
}

const RECENT_PLAYLISTS_KEY = "tagcast-recent-playlists";
function readRecentPlaylists() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_PLAYLISTS_KEY) || "[]");
    return Array.isArray(list) ? list.filter(p => p && /^\d+$/.test(String(p.id)) && typeof p.name === "string" && p.name).slice(0, 5).map(p => ({id: String(p.id), name: p.name})) : [];
  } catch { return []; }
}

function rememberQuickPlaylist(value) {
  quickPlaylist.value = value;
  if (value) {
    const recent = [value, ...readRecentPlaylists().filter(p => p.id !== value.id)].slice(0, 5);
    try { localStorage.setItem(RECENT_PLAYLISTS_KEY, JSON.stringify(recent)); } catch { /* optional */ }
  }
  try { if (value) localStorage.setItem(QUICK_PLAYLIST_KEY, JSON.stringify(value)); else localStorage.removeItem(QUICK_PLAYLIST_KEY); }
  catch { /* the choice then lasts until the page is closed */ }
  refreshQuickAddLabels();
}
quickPlaylist.value = readQuickPlaylist();

function quickAddLabel(what) {
  const name = quickPlaylist.value?.name;
  return name ? `Add ${what} to “${name}” (Shift-click: choose another playlist)` : `Add ${what} to a playlist you choose once`;
}

// albumId is required; trackId is empty for a whole album.
function quickAddButton(albumId, track = null, wide = false) {
  const what = track ? track.title : "this album";
  const label = quickAddLabel(what);
  return `<button type="button" class="quick-add ${wide ? "button small quick-add-wide" : ""}" data-quick-add-album="${escapeHtml(albumId)}" ${track ? `data-quick-add-track="${escapeHtml(track.id)}"` : ""} data-quick-add-what="${escapeHtml(what)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" ${!live() ? "disabled" : ""}>${wide ? "♫＋ Quick add" : "♫＋"}</button>`;
}

function refreshQuickAddLabels() {
  for (const button of document.querySelectorAll("[data-quick-add-album]")) {
    const label = quickAddLabel(button.dataset.quickAddWhat || "this");
    button.setAttribute("aria-label", label); button.title = label;
  }
  const chip = document.querySelector("#quick-playlist-name");
  if (chip) chip.textContent = quickPlaylist.value?.name || "not chosen yet";
}

// -- choosing the playlist (once) ---------------------------------------------------

function pickQuickPlaylist({allowNew = false} = {}) {
  return new Promise(resolve => {
    const dialog = $("#quick-playlist-dialog");
    if (dialog.open) { resolve(null); return; }
    const picker = quickPlaylist.picker = {resolve, allowNew, request: 0, done: false};
    $("#quick-playlist-new").hidden = !allowNew;
    $("#quick-playlist-new-name").value = "";
    $("#quick-playlist-query").value = "";
    $("#quick-playlist-list").innerHTML = '<p class="muted">Loading playlists…</p>';
    dialog.showModal();
    loadQuickPlaylistChoices("");
    dialog.addEventListener("close", () => finishPicker(picker, null), {once: true});
  });
}

function finishPicker(picker, value) {
  if (picker.done) return;
  picker.done = true;
  if (quickPlaylist.picker === picker) quickPlaylist.picker = null;
  if ($("#quick-playlist-dialog").open) $("#quick-playlist-dialog").close();
  picker.resolve(value);
}

async function loadQuickPlaylistChoices(query) {
  const picker = quickPlaylist.picker; if (!picker) return;
  const request = ++picker.request;
  try {
    const data = await api(`/api/playlists${query ? `?q=${encodeURIComponent(query)}` : ""}`);
    if (quickPlaylist.picker !== picker || request !== picker.request) return;
    const all = data.playlists || [], rows = all.filter(p => p.kind === "playlist");
    const recent = query ? [] : readRecentPlaylists();
    const choice = (p, count) => `<button type="button" class="quick-playlist-choice${quickPlaylist.value?.id === String(p.id) ? " current" : ""}" data-quick-choose="${escapeHtml(p.id)}" data-quick-name="${escapeHtml(p.name)}"><strong>${escapeHtml(p.name)}</strong>${count === undefined ? "" : `<small>${Number(count || 0).toLocaleString("en")} ${count === 1 ? "track" : "tracks"}</small>`}</button>`;
    $("#quick-playlist-list").innerHTML = (recent.length ? `<p class="muted quick-playlist-heading">Recent</p>${recent.map(p => choice(p)).join("")}<p class="muted quick-playlist-heading">All playlists</p>` : "") + (rows.length
      ? rows.map(p => `<button type="button" class="quick-playlist-choice${quickPlaylist.value?.id === String(p.id) ? " current" : ""}" data-quick-choose="${escapeHtml(p.id)}" data-quick-name="${escapeHtml(p.name)}"><strong>${escapeHtml(p.name)}</strong><small>${Number(p.count || 0).toLocaleString("en")} ${p.count === 1 ? "track" : "tracks"}</small></button>`).join("")
        + (data.total > all.length || all.some(p => p.kind === "folder") ? '<p class="muted">Playlists inside folders, and any beyond these, appear when you type part of their name.</p>' : "")
      : `<p class="muted">${query ? "No playlist has that name." : "You have no playlists yet."}</p>`);
  } catch (error) {
    if (quickPlaylist.picker === picker) $("#quick-playlist-list").innerHTML = `<p class="error-box">${escapeHtml(error.message)}</p>`;
  }
}

let quickPlaylistSearchTimer = 0;
$("#quick-playlist-query").addEventListener("input", event => {
  clearTimeout(quickPlaylistSearchTimer);
  quickPlaylistSearchTimer = setTimeout(() => loadQuickPlaylistChoices(event.target.value.trim()), 250);
});

$("#quick-playlist-dialog").addEventListener("click", event => {
  const picker = quickPlaylist.picker; if (!picker) return;
  const choice = event.target.closest?.("[data-quick-choose]");
  if (choice) return finishPicker(picker, {id: choice.dataset.quickChoose, name: choice.dataset.quickName});
  if (event.target.closest?.("#quick-playlist-new-go")) {
    const name = $("#quick-playlist-new-name").value.trim();
    if (!name) { toast("Type a name for the new playlist."); return; }
    finishPicker(picker, {create: name});
  }
  if (event.target.closest?.("[data-close='quick-playlist-dialog']")) finishPicker(picker, null);
});

// -- the one-click add -------------------------------------------------------------------

async function quickAdd(albumId, trackId, choose = false) {
  return quickRun(async () => {
    if (trackId) return [Number(trackId)];
    await albumDetails(shelfIds(albumId));
    return queueTracks(playableAlbum(albumId)).map(t => Number(t.id));
  }, choose);
}

// Every album ticked on the albums page, in one go (fork v51).
async function quickAddSelection(choose = false) {
  return quickAddAlbums(albumShelf().filter(a => itemIds(a).some(id => state.selected.has(id))), choose);
}

// A list of albums (ticked, or every search result: fork v52).
async function quickAddAlbums(albums, choose = false) {
  if (!albums.length) return;
  return quickRun(async () => {
    const ids = [];
    for (const album of albums) {
      await albumDetails(shelfIds(album.id));
      ids.push(...queueTracks(playableAlbum(album.id)).map(t => Number(t.id)));
    }
    return ids;
  }, choose);
}

async function quickRun(collect, choose) {
  if (!live() || state.loading || quickPlaylist.busy || collectionsView.busy || document.querySelector("dialog[open]")) return;
  const details = state.details, session = state.connection.playback_session;
  const current = () => details === state.details && session === state.connection.playback_session && live();
  quickPlaylist.busy = true;
  try {
    const ids = await collect();
    if (!current()) return;
    if (!ids.length) { toast("There are no tracks to add."); return; }
    let target = quickPlaylist.value;
    if (choose || !target) {
      const picked = await pickQuickPlaylist({allowNew: true});
      if (!picked || !current()) return;
      if (picked.create) return await quickCreate(picked.create, ids, current);
      target = picked;
    }
    await quickAppend(target, ids, current);
  } catch (error) {
    if (current()) toast(`Could not add: ${error.message}`);
  } finally { quickPlaylist.busy = false; }
}

async function quickAppend(target, ids, current) {
  ids = ids.slice();
  let playlist;
  try { playlist = await api(`/api/playlists?id=${encodeURIComponent(target.id)}`); }
  catch (error) {
    rememberQuickPlaylist(null);
    toast(`“${target.name}” is not available any more (${error.message}) Click ♫＋ again to choose another playlist.`);
    return;
  }
  if (!current()) return;
  // Songs already in the playlist are skipped (a playlist over 10,000 songs is not checked).
  const have = new Set(playlist.large ? [] : (playlist.tracks || []).map(String)), seen = new Set();
  const fresh = ids.filter(id => !have.has(String(id)) && !seen.has(String(id)) && seen.add(String(id)));
  const skipped = ids.length - fresh.length;
  if (!fresh.length) {
    rememberQuickPlaylist({id: String(playlist.id), name: playlist.name});
    toast(`${ids.length === 1 ? "That track is" : "Those tracks are"} already in “${playlist.name}”. Nothing was added.`);
    return;
  }
  ids = fresh;
  const body = {action: "append", tracks: ids, name: playlist.name, description: "", playlist: playlist.id, before_name: playlist.name,
    ...(playlist.before_token ? {before_token: playlist.before_token} : {before: playlist.before || playlist.tracks})};
  toast(`Adding to “${playlist.name}”…`);
  collectionsView.busy = true;
  try {
    const response = await api("/api/playlist", body);   // sent once; never repeated automatically
    const job = await collectionJob(response, current);
    if (job.results.some(r => r.status !== "saved")) throw Error("sent, but iBroadcast's list differs. Check the playlist before trying again.");
    rememberQuickPlaylist({id: String(playlist.id), name: playlist.name});
    toast(`Added ${ids.length} ${ids.length === 1 ? "track" : "tracks"} to “${playlist.name}”.${skipped ? ` ${skipped} already there, skipped.` : ""}`);
    if (state.screen === "playlists") renderCollections();
  } catch (error) {
    toast(`Check “${playlist.name}” before trying again: ${error.message}`);
  } finally { collectionsView.busy = false; }
}

async function quickCreate(name, ids, current) {
  toast(`Creating “${name}”…`);
  collectionsView.busy = true;
  try {
    const response = await api("/api/playlist", {action: "create", tracks: ids, name, description: ""});
    const job = await collectionJob(response, current);
    if (job.results.some(r => r.status !== "saved")) throw Error("sent, but iBroadcast's list differs. Check Playlists before creating it again.");
    const id = String(job.results[0].id);
    rememberQuickPlaylist({id, name});
    toast(`Created “${name}” with ${ids.length} ${ids.length === 1 ? "track" : "tracks"}. Your ♫＋ buttons now add to it.`);
    if (state.screen === "playlists") renderCollections();
  } catch (error) {
    toast(`Check Playlists before trying again: ${error.message}`);
  } finally { collectionsView.busy = false; }
}

document.addEventListener("click", event => {
  const button = event.target.closest?.("[data-quick-add-album]");
  if (!button) return;
  event.stopPropagation();
  if (button.disabled) return;
  quickAdd(button.dataset.quickAddAlbum, button.dataset.quickAddTrack || "", event.shiftKey);
}, true);

// On a playlist's own page: make it the target of every ♫＋ button.
document.addEventListener("click", event => {
  const use = event.target.closest?.("[data-quick-use]");
  if (!use) return;
  rememberQuickPlaylist({id: String(use.dataset.quickUse), name: use.dataset.quickName});
  toast(`Your ♫＋ buttons now add to “${use.dataset.quickName}”.`);
  if (state.screen === "playlists") renderCollections();
});

function quickUseButton(data) {
  if (!data?.id || data.is_folder) return "";
  const active = quickPlaylist.value?.id === String(data.id);
  return active ? '<span class="pill">♫＋ target</span>'
    : `<button type="button" class="button small" data-quick-use="${escapeHtml(data.id)}" data-quick-name="${escapeHtml(data.name)}" title="The ♫＋ buttons on tracks and albums will add to this playlist">Use for ♫＋ quick add</button>`;
}

document.addEventListener("click", event => {
  const button = event.target.closest?.("#quick-add-selection");
  if (button && !button.disabled) quickAddSelection(event.shiftKey);
});
