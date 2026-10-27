"use strict";

// Merge two albums by the same album artist (fork v33), like dragging one album onto
// another in iBroadcast. iBroadcast joins albums that end up with the same title and
// album artist, so a merge is one ordinary album edit on the album being merged: its
// title (and year, when they differ) become the target's. Track numbers, track tags and
// covers are not touched; the joined album shows the cover of its first track. The edit
// goes through the usual Review dialog, so nothing is written until Save is pressed.

const albumMerge = {dragging: null, busy: false};

// Album cards on an artist page can be dragged onto each other.
function albumMergeAttrs(a) {
  return live() && state.artist ? `draggable="true" data-merge-album="${escapeHtml(a.id)}"` : "";
}

const shelfItem = id => albumShelf().find(a => String(a.id) === String(id));

async function startAlbumMerge(sourceId, targetId) {
  if (albumMerge.busy || String(sourceId) === String(targetId)) return;
  const source = shelfItem(sourceId), target = shelfItem(targetId);
  if (!source || !target) { toast("One of these albums is no longer in your library. Reload and try again."); return; }
  if (foldText(source.artist) !== foldText(target.artist)) {
    toast(`Only albums with the same album artist can be merged (“${source.artist}” and “${target.artist}”).`); return;
  }
  albumMerge.busy = true;
  try {
    const discs = await albumDetails(itemIds(source));
    const year = Number(target.year) || 0;
    const changes = discs.map(disc => {
      const fields = {};
      if (disc.name !== target.name) fields.name = {before: disc.name, after: target.name};
      if (year && Number(disc.year) !== year) fields.year = {before: disc.year, after: year};
      return {kind: "album", id: disc.id, albumId: disc.id, label: `${disc.name} → into “${target.name}”`, fields};
    }).filter(c => Object.keys(c.fields).length);
    if (!changes.length) {
      toast(`“${source.name}” already has the same title and year as “${target.name}”. iBroadcast keeps them apart for another reason, such as the disc number.`);
      return;
    }
    pending = {scope: "merge", selection: [source, target].map(a => ({id: a.id, name: a.name, artist: a.artist})), changes};
    showReview(changes);
    $("#review-intro").textContent = `Merging “${source.name}” (${source.track_count} ${source.track_count === 1 ? "track" : "tracks"}) into “${target.name}” (${target.track_count}). iBroadcast joins albums with the same title and album artist; track numbers and covers stay as they are. ${$("#review-intro").textContent}`;
  } catch (error) {
    toast(`Could not prepare the merge: ${error.message}`);
  } finally {
    albumMerge.busy = false;
  }
}

// Exactly two albums selected on an artist page: merge the smaller into the larger, so
// the album with more tracks keeps its title. Works without dragging (phones, keyboard).
function selectedMergePair() {
  if (!live() || !state.artist) return null;
  const chosen = albumShelf().filter(a => itemIds(a).some(id => state.selected.has(id)));
  if (chosen.length !== 2) return null;
  const [a, b] = chosen;
  return (a.track_count || 0) >= (b.track_count || 0) ? {source: b, target: a} : {source: a, target: b};
}

function updateAlbumMergeControls() {
  const button = $("#merge-selection");
  const pair = selectedMergePair();
  button.hidden = !pair;
  if (pair) button.title = `Merge “${pair.source.name}” into “${pair.target.name}”`;
}

function clearMergeTargets() {
  for (const card of document.querySelectorAll(".merge-target")) card.classList.remove("merge-target");
}

document.addEventListener("dragstart", event => {
  const card = event.target.closest?.("[data-merge-album]");
  if (!card) return;
  albumMerge.dragging = card.dataset.mergeAlbum;
  event.dataTransfer?.setData("text/plain", card.dataset.mergeAlbum);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
});

document.addEventListener("dragover", event => {
  const card = event.target.closest?.("[data-merge-album]");
  if (!card || !albumMerge.dragging || card.dataset.mergeAlbum === albumMerge.dragging) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  if (!card.classList.contains("merge-target")) { clearMergeTargets(); card.classList.add("merge-target"); }
});

document.addEventListener("dragleave", event => {
  const card = event.target.closest?.("[data-merge-album]");
  if (card && !card.contains(event.relatedTarget)) card.classList.remove("merge-target");
});

document.addEventListener("drop", event => {
  const card = event.target.closest?.("[data-merge-album]");
  const source = albumMerge.dragging;
  albumMerge.dragging = null; clearMergeTargets();
  if (!card || !source || card.dataset.mergeAlbum === source) return;
  event.preventDefault();
  startAlbumMerge(source, card.dataset.mergeAlbum);
});

document.addEventListener("dragend", () => { albumMerge.dragging = null; clearMergeTargets(); });

document.addEventListener("click", event => {
  if (!event.target.closest("#merge-selection")) return;
  const pair = selectedMergePair();
  if (pair) startAlbumMerge(pair.source.id, pair.target.id);
});
