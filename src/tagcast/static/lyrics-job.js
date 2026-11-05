"use strict";

// Find lyrics for every song that has none (fork v81): a slow background run on the server. It looks on LRCLIB,
// then GD Studio, saves a sure match to the song's iBroadcast lyrics as timed LRC text, and keeps unsure ones in
// "Couldn't match" for one-click picking. Pause, Resume and Stop; it continues where it left off after a restart.
const lyricsJobView = {data: null, loading: false, timer: null, previews: {}};

function showLyricsJob() {
  showScreen("lyrics-job");
  renderLyricsJob();
  loadLyricsJob();
}

async function loadLyricsJob() {
  if (!live()) { renderLyricsJob(); return; }
  if (lyricsJobView.loading) return;
  lyricsJobView.loading = true;
  try {
    lyricsJobView.data = await api("/api/lyrics-job");
  } catch (error) {
    setActionStatus("#lyrics-job-status", error.message);
  } finally {
    lyricsJobView.loading = false;
    renderLyricsJob();
    clearTimeout(lyricsJobView.timer);
    if (lyricsJobView.data?.running && !$("#lyrics-job-page")?.hidden) lyricsJobView.timer = setTimeout(loadLyricsJob, 2000);
  }
}

const lyricsJobStatusLabel = {saved: "Saved", has: "Already had lyrics", instrumental: "Instrumental", none: "Not found", review: "Couldn't match", skipped: "Skipped"};

function lyricsJobCandidateHtml(trackId, item) {
  const key = `${trackId}:${item.source}:${item.lyric_id}`;
  const preview = lyricsJobView.previews[key];
  return `<li><div><strong>${escapeHtml(item.title)}</strong> <span class="muted">${escapeHtml(item.artist)}${item.album ? ` · ${escapeHtml(item.album)}` : ""} · ${escapeHtml(item.source)}</span>
    ${preview ? `<br><span class="muted">${preview.map(escapeHtml).join(" / ")}</span>` : ""}
    <br><button type="button" class="button small" data-lj-preview="${escapeHtml(key)}">Preview</button>
    <button type="button" class="button small primary" data-lj-pick="${escapeHtml(key)}">Use these lyrics</button></div></li>`;
}

function lyricsJobReviewHtml(row) {
  return `<li><div><strong>${escapeHtml(row.title)}</strong> <span class="muted">${escapeHtml(row.artist)}</span>
    <ul class="dl-list">${(row.candidates || []).map(item => lyricsJobCandidateHtml(row.track_id, item)).join("")}</ul>
    <button type="button" class="button small" data-lj-dismiss="${escapeHtml(row.track_id)}">None of these</button></div></li>`;
}

function renderLyricsJob() {
  const content = $("#lyrics-job-content");
  if (!content) return;
  if (!live()) { content.innerHTML = `<p class="muted">Connect your iBroadcast account to find lyrics for your songs.</p>`; return; }
  const d = lyricsJobView.data;
  if (!d) { content.innerHTML = `<p class="muted">Loading…</p>`; return; }
  const p = d.progress, c = d.counts || {};
  const status = d.running
    ? `<p role="status" class="operation-status" data-working="true">${d.paused ? "Paused" : "Running"}: ${p ? `${fmt(p.done)} of ${fmt(p.total)}${p.name ? ` · ${escapeHtml(p.name)}` : ""}${p.phase && !d.paused ? ` · ${escapeHtml(p.phase)}` : ""}` : "starting…"}</p>`
    : `<p class="muted">${d.remaining ? `${fmt(d.remaining)} of ${fmt(d.total)} songs not checked yet.` : "Every song has been checked."}</p>`;
  const counts = `<p class="muted">Saved ${fmt(c.saved || 0)} · already had lyrics ${fmt(c.has || 0)} · instrumental ${fmt(c.instrumental || 0)} · not found ${fmt(c.none || 0)} · couldn't match ${fmt(d.review_count || 0)}${d.gd ? ` · GD Studio ${fmt(d.gd.used)}/${fmt(d.gd.quota)} calls${d.gd.wait ? `, waiting ${fmt(d.gd.wait)}s` : ""}` : ""}</p>`;
  const buttons = d.running
    ? `${d.paused ? `<button type="button" class="button primary" id="lj-resume">Resume</button>` : `<button type="button" class="button" id="lj-pause">Pause</button>`}
       <button type="button" class="button danger" id="lj-stop">Stop</button>`
    : `<button type="button" class="button primary" id="lj-run" ${d.remaining && d.premium ? "" : "disabled"}>${(c.saved || c.has || c.none) ? "Continue" : "Run now"}${d.remaining ? ` (${fmt(d.remaining)})` : ""}</button>
       ${c.none ? `<button type="button" class="button" id="lj-retry">Try the not found again</button>` : ""}`;
  content.innerHTML = `${d.premium ? "" : `<p class="error-text">Saving lyrics to iBroadcast needs Premium.</p>`}
    ${status}${d.message ? `<p class="muted">${escapeHtml(d.message)}</p>` : ""}${counts}
    <div class="dl-actions">${buttons}</div>
    ${d.review.length ? `<h3>Couldn't match (${fmt(d.review_count)})</h3><ul class="dl-list">${d.review.map(lyricsJobReviewHtml).join("")}</ul>` : ""}
    <h3>Recent outcomes</h3>
    ${d.jobs.length ? `<ul class="dl-list">${d.jobs.map(job => `<li><div><strong>${escapeHtml(job.title)}</strong> <span class="muted">${escapeHtml(job.artist)}</span> <span class="pill">${escapeHtml(lyricsJobStatusLabel[job.status] || job.status)}</span>${job.source ? ` <span class="muted">${escapeHtml(job.source)}</span>` : ""}${job.note ? `<br><span class="muted">${escapeHtml(job.note)}</span>` : ""}</div></li>`).join("")}</ul>` : `<p class="muted">Nothing run yet.</p>`}`;
}

async function lyricsJobCall(path, body) {
  try { await api(path, body || {}); setActionStatus("#lyrics-job-status", ""); }
  catch (error) { setActionStatus("#lyrics-job-status", error.message); }
  await loadLyricsJob();
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("[data-lyrics-job-open]")) { showLyricsJob(); return; }
  if (target.closest("[data-lyrics-job-back]")) { clearTimeout(lyricsJobView.timer); showScreen("overview"); return; }
  if (target.closest("#lj-run")) { lyricsJobCall("/api/lyrics-job/start"); return; }
  if (target.closest("#lj-retry")) { lyricsJobCall("/api/lyrics-job/start", {retry: true}); return; }
  if (target.closest("#lj-pause")) { lyricsJobCall("/api/lyrics-job/pause"); return; }
  if (target.closest("#lj-resume")) { lyricsJobCall("/api/lyrics-job/resume"); return; }
  if (target.closest("#lj-stop")) { lyricsJobCall("/api/lyrics-job/stop"); return; }
  const dismiss = target.closest("[data-lj-dismiss]");
  if (dismiss) { lyricsJobCall("/api/lyrics-job/dismiss", {track_id: dismiss.dataset.ljDismiss}); return; }
  const pick = target.closest("[data-lj-pick]");
  if (pick) {
    const [track_id, source, lyric_id] = pick.dataset.ljPick.split(":");
    lyricsJobCall("/api/lyrics-job/pick", {track_id, source, lyric_id});
    return;
  }
  const preview = target.closest("[data-lj-preview]");
  if (preview) {
    const key = preview.dataset.ljPreview, [track_id, source, lyric_id] = key.split(":");
    (async () => {
      try {
        const answer = await api(`/api/lyrics-job/preview?track_id=${encodeURIComponent(track_id)}&source=${encodeURIComponent(source)}&lyric_id=${encodeURIComponent(lyric_id)}`);
        lyricsJobView.previews[key] = answer.lines || [];
        renderLyricsJob();
      } catch (error) { setActionStatus("#lyrics-job-status", error.message); }
    })();
  }
});
