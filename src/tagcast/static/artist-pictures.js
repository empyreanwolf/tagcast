"use strict";

// Artist pictures (fork v27): a one-time "Run now" job that gives every album artist with
// no image one, in order -- a face found in one of their own album covers, else a real
// photo from TheAudioDB, else just one of their own covers (better than the blank default).
// Only artists with no image yet are touched; the existing "Change artwork" dialog still
// overrides anything this picks, any time.
const artistPicturesView = {data: null, loading: false, timer: null};

function showArtistPictures() {
  showScreen("artist-pictures");
  renderArtistPictures();
  loadArtistPictures();
}

async function loadArtistPictures() {
  if (!live()) { renderArtistPictures(); return; }
  if (artistPicturesView.loading) return;
  artistPicturesView.loading = true;
  try {
    artistPicturesView.data = await api("/api/artist-pictures");
  } catch (error) {
    setActionStatus("#artist-pictures-status", error.message);
  } finally {
    artistPicturesView.loading = false;
    renderArtistPictures();
    scheduleArtistPicturesPoll();
  }
}

function scheduleArtistPicturesPoll() {
  clearTimeout(artistPicturesView.timer);
  if (artistPicturesView.data?.running) artistPicturesView.timer = setTimeout(loadArtistPictures, 1500);
}

const artistPicturesSourceLabel = {face: "Face found", theaudiodb: "TheAudioDB photo", cover: "Used a cover", "": "Skipped"};

function artistPicturesJobHtml(job) {
  const label = artistPicturesSourceLabel[job.source] ?? job.source;
  return `<li><div><strong>${escapeHtml(job.artist)}</strong> <span class="pill">${escapeHtml(label)}</span><br><span class="muted">${escapeHtml(job.note || "")}</span></div></li>`;
}

function renderArtistPictures() {
  const content = $("#artist-pictures-content");
  if (!content) return;
  if (!live()) { content.innerHTML = `<p class="muted">Connect your iBroadcast account to give your album artists pictures.</p>`; return; }
  const d = artistPicturesView.data;
  if (!d) { content.innerHTML = `<p class="muted">Loading…</p>`; return; }
  const running = d.running, progress = d.progress;
  const statusLine = running
    ? `<p role="status" class="operation-status" data-working="true">Running: ${progress ? `${fmt(progress.done)} of ${fmt(progress.total)}${progress.name ? ` · now checking ${escapeHtml(progress.name)}` : ""}` : "starting…"}</p>`
    : `<p class="muted">${d.missing ? `${fmt(d.missing)} album ${d.missing === 1 ? "artist" : "artists"} currently ${d.missing === 1 ? "has" : "have"} no image.` : "Every album artist already has an image."}</p>`;
  content.innerHTML = `${statusLine}
    <div class="dl-actions">
      ${running ? `<button type="button" class="button danger" id="ap-stop">Stop after this artist</button>`
        : `<button type="button" class="button primary" id="ap-run" ${d.missing ? "" : "disabled"}>Run now${d.missing ? ` (${fmt(d.missing)})` : ""}</button>`}
    </div>
    <h3>Recent outcomes</h3>
    ${d.jobs.length ? `<ul class="dl-list">${d.jobs.map(artistPicturesJobHtml).join("")}</ul>` : `<p class="muted">Nothing run yet.</p>`}`;
}

document.addEventListener("click", event => {
  const target = event.target;
  // Opened from Overview → Metadata health (fork v33); it no longer has its own sidebar entry.
  if (target.closest("[data-artist-pictures-open]")) { showArtistPictures(); return; }
  if (target.closest("[data-artist-pictures-back]")) { showScreen("overview"); return; }
  if (target.closest("#ap-run")) {
    (async () => {
      try { await api("/api/artist-pictures/start", {}); setActionStatus("#artist-pictures-status", ""); await loadArtistPictures(); }
      catch (error) { setActionStatus("#artist-pictures-status", error.message); }
    })();
    return;
  }
  if (target.closest("#ap-stop")) {
    (async () => {
      try { await api("/api/artist-pictures/stop", {}); await loadArtistPictures(); }
      catch (error) { setActionStatus("#artist-pictures-status", error.message); }
    })();
  }
});
