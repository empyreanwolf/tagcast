"use strict";

// Play, shuffle or add to a playlist everything the albums page currently shows (fork v52): the
// search text, the filter and the artist all count; the page size does not.

const RESULTS_ALBUM_LIMIT = 400;
const resultsActions = {resumeOffered: false};

function resultsAlbums() {
  return filteredAlbums().filter(a => a.track_count > 0);
}

function resultsLabel(albums, total) {
  const query = $("#search").value.trim();
  const what = query ? `“${query}”` : state.artist ? state.artist : "the current list";
  return total > albums.length ? `${what} (first ${albums.length} albums)` : what;
}

function updateResultsActions() {
  const albums = $("#results-play");
  if (!albums) return;
  const off = !live() || state.loading || player.shuffleLoading || quickPlaylist.busy || !filteredAlbums().some(a => a.track_count > 0);
  for (const id of ["#results-play", "#results-shuffle", "#results-add"]) {
    $(id).disabled = off;
    $(id).hidden = state.selected.size > 0;
  }
  if (typeof updateResumeControls === "function") updateResumeControls();
}

function playResults(shuffle) {
  const all = resultsAlbums(), albums = all.slice(0, RESULTS_ALBUM_LIMIT);
  if (!albums.length) return;
  return playShuffleGroup(albums, {ordered: !shuffle, label: resultsLabel(albums, all.length)});
}

async function addResults(choose = false) {
  const all = resultsAlbums(), albums = all.slice(0, RESULTS_ALBUM_LIMIT);
  if (!albums.length) return;
  const tracks = albums.reduce((sum, a) => sum + a.track_count, 0);
  if (tracks > 1000 && !window.confirm(`Add about ${tracks.toLocaleString("en")} tracks from ${albums.length} albums to your quick playlist?`)) return;
  return quickAddAlbums(albums, choose);
}

$("#results-play").addEventListener("click", () => playResults(false));
$("#results-shuffle").addEventListener("click", () => playResults(true));
$("#results-add").addEventListener("click", event => addResults(event.shiftKey));
