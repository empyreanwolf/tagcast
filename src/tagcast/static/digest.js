"use strict";

// Daily Digest (fork v24): one read-first page pulling together what New releases,
// Wishlist, Charts and Recommendations already track, instead of checking four pages.
// Nothing here downloads anything -- each section links back to its own page for that.
const digestView = {data: null, busy: false, loading: null, loadedAt: 0};
const DIGEST_FRESH_MS = 10 * 60 * 1000, DIGEST_GAP_MS = 400;

function showDigest() {
  showScreen("digest");
  renderDigest();
  digestLoad();
}

// v54: the sections load one after another with a short rest between them (never all at once), and the same
// queue is started quietly a few seconds after login, so the page is usually ready when you open it.
function digestApply(part, section) {
  const d = digestView.data ||= {new_releases: null, wishlist_found: null, charts: {}, recommendations: {}};
  if (part === "new_releases" || part === "wishlist_found") d[part] = section;
  else if (part.startsWith("chart:")) d.charts[part.slice(6)] = section;
  else if (part.startsWith("recs:")) d.recommendations[part.slice(5)] = section;
}

function digestLoad(force) {
  if (digestView.loading) return digestView.loading;
  if (!force && digestView.data && Date.now() - digestView.loadedAt < DIGEST_FRESH_MS) { renderDigest(); return Promise.resolve(); }
  digestView.loading = (async () => {
    try {
      const {parts} = await api("/api/digest?part=list");
      for (const part of parts) {
        const result = await api(`/api/digest?part=${encodeURIComponent(part)}`);
        digestApply(part, result.section);
        if (state.screen === "digest") renderDigest();
        await new Promise(resolve => setTimeout(resolve, DIGEST_GAP_MS));
      }
      digestView.loadedAt = Date.now();
    } catch (error) { setActionStatus("#digest-status", error.message); }
    digestView.loading = null;
    if (state.screen === "digest") renderDigest();
  })();
  return digestView.loading;
}

function digestPrefetch() {
  if (digestView.data || digestView.loading) return;
  digestLoad().catch(() => {});
}

function renderDigest() {
  const d = digestView.data;
  $("#digest-content").innerHTML = d ? digestBodyHtml(d) : `<p class="muted">Loading…</p>`;
}

const digestWaiting = '<p class="muted">Loading…</p>';

function digestSectionHtml(ok, error, body) {
  if (!ok) return `<p class="error-text">${escapeHtml(error)}</p>`;
  return body;
}

function digestBodyHtml(d) {
  return `
    <div class="dl-card">
      <h2>New releases</h2>
      ${d.new_releases ? digestSectionHtml(d.new_releases.ok, d.new_releases.error, digestNewReleasesHtml(d.new_releases)) : digestWaiting}
    </div>
    <div class="dl-card">
      <h2>Wishlist, just found ${d.wishlist_found?.ok && d.wishlist_found.new_count
        ? `<span class="pill">${d.wishlist_found.new_count} new</span>` : ""}</h2>
      ${d.wishlist_found ? digestSectionHtml(d.wishlist_found.ok, d.wishlist_found.error, digestWishlistFoundHtml(d.wishlist_found)) : digestWaiting}
    </div>
    <div class="dl-card">
      <h2>Chart movement</h2>
      ${Object.keys(d.charts).length ? digestChartsHtml(d.charts) : digestWaiting}
    </div>
    <div class="dl-card">
      <h2>Recommendations</h2>
      ${d.recommendations.deezer ? digestRecommendationHtml("deezer", "Related to your artists (Deezer)", d.recommendations.deezer) : digestWaiting}
    </div>`;
}

function digestNewReleasesHtml(section) {
  if (!section.total) return `<p class="muted">No new releases waiting right now.</p>`;
  return `<ul class="dl-list">${section.results.map(r =>
      `<li><div>${escapeHtml(r.artist)} · <i>${escapeHtml(r.title)}</i>${digestPill(r)}</div></li>`).join("")}</ul>
    <button type="button" class="button" data-digest="see-missing">See all (${section.total}) in Downloads → Missing</button>`;
}

function digestWishlistFoundHtml(section) {
  if (!section.items.length) return `<p class="muted">Nothing found from the wishlist recently.</p>`;
  return `<ul class="dl-list">${section.items.map(i =>
      `<li><div>${i.is_new ? `<span class="pill">New</span> ` : ""}${escapeHtml(i.artist)} · <i>${escapeHtml(i.title)}</i>
       · <span class="muted">${escapeHtml(i.source || "")}</span></div></li>`).join("")}</ul>
    <div class="dl-actions">
      <button type="button" class="button" data-digest="see-missing">See in Downloads → Missing</button>
      ${section.new_count ? `<button type="button" class="button" data-digest="mark-found-seen">Mark seen</button>` : ""}
    </div>`;
}

// Charts that cannot be read right now are named in one line, not shown as red errors.
function digestPill(r) {
  if (!r.status || r.status === "new") return "";
  const at = r.status === "downloaded" && r.pc ? ` at ${escapeHtml(r.pc)}, not uploaded yet` : "";
  return ` <span class="pill ${r.status === "have" ? "" : "missing"}">${escapeHtml(downloadsStatusLabels[r.status] || r.status)}${at}</span>`;
}

function digestChartsHtml(charts) {
  const entries = Object.entries(charts), working = entries.filter(([, s]) => s.ok), broken = entries.filter(([, s]) => !s.ok);
  const names = broken.map(([id]) => chartsTabs.find(([k]) => k === id)?.[1] || id);
  return `${working.map(([id, section]) => digestChartHtml(id, section)).join("")}
    ${broken.length ? `<p class="muted">Couldn't read right now: ${escapeHtml(names.join(", "))}.</p>` : ""}`;
}

function digestChartHtml(chartId, section) {
  if (!section.ok) return `<p>${escapeHtml(chartsTabs.find(([k]) => k === chartId)?.[1] || chartId)}:
      <span class="error-text">${escapeHtml(section.error)}</span></p>`;
  const body = section.first_scan ? `<p class="muted">First look at this chart -- movement shows up from the next visit here.</p>`
    : section.new_entries.length ? `<ul class="dl-list">${section.new_entries.map(e =>
        `<li><div>${escapeHtml(e.title)} · ${escapeHtml(e.artist)}${digestPill(e)}</div></li>`).join("")}</ul>`
    : `<p class="muted">No new entries since last time.</p>`;
  return `<div class="digest-chart"><h3>${escapeHtml(section.label)}</h3>${body}
    <button type="button" class="button" data-digest-chart="${escapeHtml(chartId)}">Open this chart</button></div>`;
}

function digestRecommendationHtml(source, label, section) {
  if (!section.ok) return `<p>${escapeHtml(label)}: <span class="error-text">${escapeHtml(section.error)}</span></p>`;
  if (!section.total) return `<p class="muted">${escapeHtml(label)}: nothing suggested yet.</p>`;
  return `<div class="digest-recs"><h3>${escapeHtml(label)}</h3>
    <ul class="dl-list">${section.rows.map(r =>
      `<li><div>${escapeHtml(r.artist)} · <i>${escapeHtml(r.title)}</i>${digestPill(r)}</div></li>`).join("")}</ul>
    <button type="button" class="button" data-digest-recs="${escapeHtml(source)}">See all (${section.total})</button></div>`;
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("#show-digest")) { showDigest(); return; }
  if (!target.closest("#digest-page")) return;
  const action = target.closest("[data-digest]")?.dataset.digest;
  if (action === "see-missing") { showDownloads("missing"); return; }
  if (action === "mark-found-seen") {
    (async () => {
      try { await api("/api/digest/mark-found-seen", {}); digestView.data = null; digestView.loadedAt = 0; renderDigest(); digestLoad(true); }
      catch (error) { setActionStatus("#digest-status", error.message); }
    })();
    return;
  }
  const chart = target.closest("[data-digest-chart]")?.dataset.digestChart;
  if (chart) { showCharts(chart); return; }
  const recs = target.closest("[data-digest-recs]")?.dataset.digestRecs;
  if (recs) { showCharts("recommended"); return; }
});
