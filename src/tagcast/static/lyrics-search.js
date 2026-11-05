"use strict";

// Search lyrics… (fork v78): when Now playing finds no lyrics (or the wrong ones), type the song's real title and
// artist and look again. The words only apply to the song they were typed for.
function lsSong() {
  const track = typeof player !== "undefined" ? player.queue?.[player.index] : null;
  const selection = track && typeof lyricsSelection === "function" ? lyricsSelection(track) : null;
  return selection ? {selection, key: lyricsKey(selection), track} : null;
}

function lsApply(title, artist) {
  const song = lsSong();
  if (!song) return;
  playbackLyricsView.manual = title && artist ? {key: song.key, title, artist} : null;
  playbackLyricsView.cache.clear();
  suspendPlaybackLyrics(); renderPlaybackLyrics();
}

function lsInstall() {
  const retry = document.querySelector("#visual-lyrics-retry");
  if (!retry?.parentNode || typeof document.createElement !== "function" || document.querySelector("#visual-lyrics-search")) return;
  const button = document.createElement("button");
  button.type = "button"; button.className = "button small"; button.id = "visual-lyrics-search"; button.textContent = "Search lyrics…";
  const form = document.createElement("form");
  form.id = "visual-lyrics-search-form"; form.className = "lyrics-search-form"; form.hidden = true;
  form.innerHTML = `<label>Song title <input name="title" maxlength="200" autocomplete="off"></label><label>Artist <input name="artist" maxlength="200" autocomplete="off"></label>
    <span class="lyrics-search-actions"><button type="submit" class="button small primary">Search</button><button type="button" class="button small" data-ls-reset>Use the tags again</button></span>`;
  retry.parentNode.insertBefore(button, retry.nextSibling);
  button.parentNode.insertBefore(form, button.nextSibling);
  button.addEventListener("click", () => {
    const song = lsSong(), manual = song ? playbackLyricsManual(song.selection) : null;
    form.hidden = !form.hidden;
    if (!form.hidden && song) {
      form.elements.title.value = manual?.title || song.track.title || "";
      form.elements.artist.value = manual?.artist || song.track.artist || "";
      form.elements.title.focus?.();
    }
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    const title = form.elements.title.value.trim(), artist = form.elements.artist.value.trim();
    if (!title || !artist) { if (typeof toast === "function") toast("Enter both a title and an artist."); return; }
    form.hidden = true; lsApply(title, artist);
  });
  form.addEventListener("click", event => { if (event.target.closest?.("[data-ls-reset]")) { form.hidden = true; lsApply("", ""); } });
}

try { lsInstall(); } catch { /* the lyrics panel is optional */ }
