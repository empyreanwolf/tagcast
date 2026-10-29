"use strict";

// The heart can also add the song to a playlist you choose (fork v53), for example "Mix Favourite".
// Setting the rating to 5 stays exactly as before; the playlist add is a second, separate step that
// uses the same safe single "append" as the ♫＋ button. Un-hearting does not remove it from the playlist.

const HEART_PLAYLIST_KEY = "tagcast-heart-playlist";

function readHeartPlaylist() {
  try {
    const raw = localStorage.getItem(HEART_PLAYLIST_KEY);
    if (raw === "off") return "off";
    const saved = JSON.parse(raw || "null");
    if (saved && /^\d+$/.test(String(saved.id)) && typeof saved.name === "string" && saved.name) return {id: String(saved.id), name: saved.name};
  } catch { /* private window or damaged value */ }
  return null;
}

function writeHeartPlaylist(value) {
  try {
    if (value === null) localStorage.removeItem(HEART_PLAYLIST_KEY);
    else localStorage.setItem(HEART_PLAYLIST_KEY, value === "off" ? "off" : JSON.stringify(value));
  } catch { /* the choice then lasts until the page is closed */ }
  const label = $("#heart-playlist-name");
  if (label) label.textContent = heartPlaylistLabel();
}

function heartPlaylistLabel() {
  const value = readHeartPlaylist();
  return value === "off" ? "Off" : value ? value.name : "Not chosen yet (asked on your next heart)";
}

// Called after a heart (rating 5) was saved and confirmed.
async function heartAfterFavourite(trackId) {
  try {
    let target = readHeartPlaylist();
    if (target === "off") return;
    if (!target) {
      if (quickPlaylist.busy || collectionsView.busy || document.querySelector("dialog[open]")) return;
      const picked = await pickQuickPlaylist({allowNew: false});
      if (!picked || picked.create) { writeHeartPlaylist("off"); toast("The heart will not add to a playlist. You can change this in Settings → Playback."); return; }
      target = {id: String(picked.id), name: picked.name};
      writeHeartPlaylist(target);
    }
    if (quickPlaylist.busy || collectionsView.busy) { toast(`Rated 5, but “${target.name}” was busy. Use ♫＋ to add it.`); return; }
    quickPlaylist.busy = true;
    try { await quickAppendTo(target, [Number(trackId)]); }
    finally { quickPlaylist.busy = false; }
  } catch (error) {
    toast(`Rated 5, but could not add to the playlist: ${error.message}`);
  }
}

// quickAppend remembers its target as the ♫＋ playlist; the heart's playlist must not change that.
async function quickAppendTo(target, ids) {
  const previous = quickPlaylist.value;
  try { await quickAppend(target, ids, () => live()); }
  finally { rememberQuickPlaylist(previous); }
}

document.addEventListener("click", async event => {
  if (event.target.closest?.("#heart-playlist-off")) { writeHeartPlaylist("off"); toast("The heart will only set the rating."); return; }
  if (!event.target.closest?.("#heart-playlist-choose")) return;
  if (quickPlaylist.busy || document.querySelector("dialog[open]:not(#settings-dialog)")) return;
  const picked = await pickQuickPlaylist({allowNew: false});
  if (picked && !picked.create) { writeHeartPlaylist({id: String(picked.id), name: picked.name}); toast(`The heart will also add songs to “${picked.name}”.`); }
});

function heartPlaylistSettings() {
  return `<section class="settings-section" data-settings-category="playback"><h3>Heart also adds to a playlist</h3><p>When you press the heart (rating 5), also add the song to: <strong id="heart-playlist-name">${escapeHtml(heartPlaylistLabel())}</strong></p><div class="lyrics-actions"><button type="button" class="button small" id="heart-playlist-choose">Choose playlist…</button><button type="button" class="button small" id="heart-playlist-off">Heart only sets the rating</button></div><p class="muted">Songs already in that playlist are skipped. Removing the heart later does not remove the song from the playlist.</p></section>`;
}
