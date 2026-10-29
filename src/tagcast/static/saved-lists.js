"use strict";

// Smart playlists (fork v52): a saved search + filter + sort (+ artist). They are checked against
// the library every time you open them, so a list like "Missing year" or "rock, newest first" stays
// up to date by itself. They live in this browser only; nothing is written to iBroadcast.

const SAVED_LISTS_KEY = "tagcast-saved-lists";
const SAVED_LISTS_LIMIT = 40;

function readSavedLists() {
  try {
    const lists = JSON.parse(localStorage.getItem(SAVED_LISTS_KEY) || "[]");
    return Array.isArray(lists) ? lists.filter(l => l && typeof l.name === "string" && l.name).slice(0, SAVED_LISTS_LIMIT)
      .map(l => ({name: l.name.slice(0, 80), search: String(l.search || ""), filter: String(l.filter || "all"), sort: String(l.sort || "artist"), artist: l.artist ? String(l.artist) : ""})) : [];
  } catch { return []; }
}

function writeSavedLists(lists) {
  try { localStorage.setItem(SAVED_LISTS_KEY, JSON.stringify(lists.slice(0, SAVED_LISTS_LIMIT))); return true; }
  catch { toast("Could not save: this browser is blocking storage."); return false; }
}

function savedListSummary(list) {
  const parts = [];
  if (list.search) parts.push(`“${list.search}”`);
  if (list.artist) parts.push(list.artist);
  parts.push(FILTERS[list.filter]?.[0] || "All albums");
  parts.push(list.sort === "random" ? "random order" : `sorted by ${list.sort}`);
  return parts.filter(Boolean).join(" · ");
}

function renderSavedLists() {
  const lists = readSavedLists();
  $("#saved-lists-list").innerHTML = lists.length ? lists.map((l, i) => `<div class="saved-list-row"><div class="saved-list-text"><strong>${escapeHtml(l.name)}</strong><small>${escapeHtml(savedListSummary(l))}</small></div><div class="saved-list-actions"><button type="button" class="button small" data-saved-open="${i}">Open</button><button type="button" class="button small" data-saved-play="${i}" title="Play everything in this list in order">▶</button><button type="button" class="button small" data-saved-shuffle="${i}" title="Shuffle everything in this list">🔀</button><button type="button" class="button small danger" data-saved-delete="${i}" aria-label="Delete ${escapeHtml(l.name)}">×</button></div></div>`).join("")
    : '<p class="muted">No smart playlists yet. Set a search, filter or sort on the albums page, then save it here.</p>';
  const current = {search: $("#search").value.trim(), filter: $("#filter").value, sort: $("#sort").value, artist: state.artist || ""};
  $("#saved-lists-current").textContent = savedListSummary(current);
}

function applySavedList(list) {
  state.selected.clear();
  state.artist = list.artist && state.albums.some(a => albumHasArtist(a, list.artist)) ? list.artist : null;
  $("#search").value = list.search;
  $("#filter").value = FILTERS[list.filter] ? list.filter : "all";
  $("#sort").value = SORTS[list.sort] ? list.sort : "artist";
  state.page = 0;
  if ($("#saved-lists-dialog").open) $("#saved-lists-dialog").close();
  showScreen("albums"); render();
}

function saveCurrentList() {
  const input = $("#saved-lists-name"), name = input.value.trim().slice(0, 80);
  if (!name) { toast("Type a name for this smart playlist."); input.focus(); return; }
  const lists = readSavedLists().filter(l => l.name.toLowerCase() !== name.toLowerCase());
  lists.unshift({name, search: $("#search").value.trim(), filter: $("#filter").value, sort: $("#sort").value, artist: state.artist || ""});
  if (!writeSavedLists(lists)) return;
  input.value = "";
  renderSavedLists();
  toast(`Saved “${name}”. It updates itself as your library changes.`);
}

$("#saved-lists-open").addEventListener("click", () => { renderSavedLists(); $("#saved-lists-dialog").showModal(); });
$("#saved-lists-save").addEventListener("click", saveCurrentList);
$("#saved-lists-dialog").addEventListener("click", event => {
  const lists = readSavedLists(), pick = key => { const b = event.target.closest?.(`[data-saved-${key}]`); return b ? lists[Number(b.dataset[`saved${key[0].toUpperCase()}${key.slice(1)}`])] : null; };
  const open = pick("open"), play = pick("play"), shuffle = pick("shuffle");
  const del = event.target.closest?.("[data-saved-delete]");
  if (del) {
    const index = Number(del.dataset.savedDelete), removed = lists[index];
    if (removed) { lists.splice(index, 1); writeSavedLists(lists); renderSavedLists(); toast(`Deleted “${removed.name}”.`); }
    return;
  }
  if (open) return applySavedList(open);
  if (play || shuffle) {
    applySavedList(play || shuffle);
    return playResults(Boolean(shuffle));
  }
});
