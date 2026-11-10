"use strict";

// Track numbers from a real source (fork v91): a slow background run on the server. For each album with unnumbered
// songs it reads the track list from MusicBrainz, then Discogs, and numbers only songs whose title matches exactly
// one place on it. Albums no source could settle are listed, never guessed.
const trackNumbersView = {data: null, loading: false, timer: null};

function showTrackNumbers() {
  showScreen("track-numbers");
  renderTrackNumbers();
  loadTrackNumbers();
}

async function loadTrackNumbers() {
  if (!live()) { renderTrackNumbers(); return; }
  if (trackNumbersView.loading) return;
  trackNumbersView.loading = true;
  try {
    trackNumbersView.data = await api("/api/track-numbers");
  } catch (error) {
    setActionStatus("#track-numbers-status", error.message);
  } finally {
    trackNumbersView.loading = false;
    renderTrackNumbers();
    clearTimeout(trackNumbersView.timer);
    if (trackNumbersView.data?.running && !$("#track-numbers-page")?.hidden) trackNumbersView.timer = setTimeout(loadTrackNumbers, 2000);
  }
}

const trackNumbersLabel = {numbered: "Numbered", partly: "Partly numbered", none: "No source had it"};

function renderTrackNumbers() {
  const content = $("#track-numbers-content");
  if (!content) return;
  if (!live()) { content.innerHTML = `<p class="muted">Connect your iBroadcast account to number your tracks.</p>`; return; }
  const d = trackNumbersView.data;
  if (!d) { content.innerHTML = `<p class="muted">Loading…</p>`; return; }
  const p = d.progress, c = d.counts || {};
  const status = d.running
    ? `<p role="status" class="operation-status" data-working="true">${d.paused ? "Paused" : "Running"}: ${p ? `${fmt(p.done)} of ${fmt(p.total)}${p.name ? ` · ${escapeHtml(p.name)}` : ""}${p.phase && !d.paused ? ` · ${escapeHtml(p.phase)}` : ""}` : "starting…"}</p>`
    : `<p class="muted">${d.remaining ? `${fmt(d.remaining)} of ${fmt(d.total)} albums not checked yet.` : d.total ? "Every album has been checked." : "Every song already has a track number."}</p>`;
  const counts = `<p class="muted">Numbered ${fmt(c.numbered || 0)} · partly ${fmt(c.partly || 0)} · no source had it ${fmt(c.none || 0)}${d.discogs ? "" : " · Discogs is off (add a token in Settings for more)"}</p>`;
  const buttons = d.running
    ? `${d.paused ? `<button type="button" class="button primary" id="tn-resume">Resume</button>` : `<button type="button" class="button" id="tn-pause">Pause</button>`}
       <button type="button" class="button danger" id="tn-stop">Stop</button>`
    : `<button type="button" class="button primary" id="tn-run" ${d.remaining ? "" : "disabled"}>${(c.numbered || c.partly || c.none) ? "Continue" : "Run now"}${d.remaining ? ` (${fmt(d.remaining)})` : ""}</button>
       ${(c.none || c.partly) ? `<button type="button" class="button" id="tn-retry">Try the unsettled again</button>` : ""}`;
  const review = (d.review || []).map(row => `<li><div><strong>${escapeHtml(row.album)}</strong> <span class="muted">${escapeHtml(row.artist)} · ${escapeHtml(row.source)}</span>
    <br><span class="muted">Not on that release: ${row.missing.map(escapeHtml).join(", ")}</span>
    <br><button type="button" class="button small" data-tn-dismiss="${escapeHtml(row.album_id)}">Dismiss</button></div></li>`).join("");
  content.innerHTML = `${status}${d.message ? `<p class="muted">${escapeHtml(d.message)}</p>` : ""}${counts}
    <div class="dl-actions">${buttons}</div>
    ${review ? `<h3>Some songs left without a number (${fmt(d.review_count)})</h3><ul class="dl-list">${review}</ul>` : ""}
    <h3>Recent outcomes</h3>
    ${d.jobs.length ? `<ul class="dl-list">${d.jobs.map(job => `<li><div><strong>${escapeHtml(job.album)}</strong> <span class="muted">${escapeHtml(job.artist)}</span> <span class="pill">${escapeHtml(trackNumbersLabel[job.status] || job.status)}</span>${job.source ? ` <span class="muted">${escapeHtml(job.source)}</span>` : ""}${job.note ? `<br><span class="muted">${escapeHtml(job.note)}</span>` : ""}</div></li>`).join("")}</ul>` : `<p class="muted">Nothing run yet.</p>`}`;
}

async function trackNumbersCall(path, body) {
  try { await api(path, body || {}); setActionStatus("#track-numbers-status", ""); }
  catch (error) { setActionStatus("#track-numbers-status", error.message); }
  await loadTrackNumbers();
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("[data-track-numbers-open]")) { showTrackNumbers(); return; }
  if (target.closest("[data-track-numbers-back]")) { clearTimeout(trackNumbersView.timer); showScreen("overview"); return; }
  if (target.closest("#tn-run")) { trackNumbersCall("/api/track-numbers/start"); return; }
  if (target.closest("#tn-retry")) { trackNumbersCall("/api/track-numbers/start", {retry: true}); return; }
  if (target.closest("#tn-pause")) { trackNumbersCall("/api/track-numbers/pause"); return; }
  if (target.closest("#tn-resume")) { trackNumbersCall("/api/track-numbers/resume"); return; }
  if (target.closest("#tn-stop")) { trackNumbersCall("/api/track-numbers/stop"); return; }
  const dismiss = target.closest("[data-tn-dismiss]");
  if (dismiss) trackNumbersCall("/api/track-numbers/dismiss", {album_id: dismiss.dataset.tnDismiss});
});
