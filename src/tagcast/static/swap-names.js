"use strict";

// Fork v74: "Swap artist ↔ album": for albums tagged with the album artist and the album name the wrong way round.
// It builds one change per selected album (album artist becomes the album name, album name becomes the album artist)
// and opens the normal Review dialog, so nothing is saved until you press Save there.
async function swapNamesChanges(ids) {
  const details = await albumDetails(ids.map(String));
  return details.map(d => ({kind: "album", id: d.id, albumId: d.id, label: `${d.name} (${d.artist}) → ${d.artist} (${d.name})`,
    fields: {artist: {before: d.artist, after: d.name}, name: {before: d.name, after: d.artist}}}))
    .filter(c => c.fields.artist.before !== c.fields.artist.after);
}

async function swapSelectedNames() {
  if (!live()) { toast("Connect iBroadcast to change tags."); return; }
  if (pending || $("#editor").open || $("#review").open) { toast("Finish or close the open edit first."); return; }
  const ids = [...state.selected];
  if (!ids.length) return;
  const button = $("#swap-selection");
  button.disabled = true;
  try {
    const changes = await swapNamesChanges(ids);
    if (!changes.length) { toast("Nothing to swap."); return; }
    pending = {scope: "album", selection: changes.map(c => ({id: c.id, name: c.fields.name.before, artist: c.fields.artist.before})), changes};
    showReview(changes);
    $("#review-intro").textContent = `Swapping album artist and album name on ${changes.length} ${changes.length === 1 ? "album" : "albums"}. Check the list, then Save. Track artists are not changed. ${$("#review-intro").textContent}`;
  } catch (error) {
    toast(`Could not prepare the swap: ${error.message || error}`);
  } finally { button.disabled = false; }
}

document.addEventListener("click", event => { if (event.target.closest?.("#swap-selection")) swapSelectedNames(); });
