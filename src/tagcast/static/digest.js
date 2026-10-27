"use strict";

// Daily Digest (fork v24): one read-first page pulling together what New releases,
// Wishlist, Charts and Recommendations already track, instead of checking four pages.
// Nothing here downloads anything -- each section links back to its own page for that.
const digestView = {data: null, busy: false};

function showDigest() {
  showScreen("digest");
  renderDigest();
  digestLoad();
}

async function digestLoad() {
  try { digestView.data = await api("/api/digest"); } catch (error) { setActionStatus("#digest-status", error.message); }
  renderDigest();
}

function renderDigest() {
  const d = digestView.data;
  $("#digest-content").innerHTML = d ? digestBodyHtml(d) : `<p class="muted">Loading…</p>`;
}

function digestSectionHtml(ok, error, body) {
  if (!ok) return `<p class="error-text">${escapeHtml(error)}</p>`;
  return body;
}

function digestBodyHtml(d) {
  return `
    <div class="dl-card">
      <h2>New releases</h2>
      ${digestSectionHtml(d.new_releases.ok, d.new_releases.error, digestNewReleasesHtml(d.new_releases))}
    </div>
    <div class="dl-card">
      <h2>Wishlist, just found ${d.wishlist_found.ok && d.wishlist_found.new_count
        ? `<span class="pill">${d.wishlist_found.new_count} new</span>` : ""}</h2>
      ${digestSectionHtml(d.wishlist_found.ok, d.wishlist_found.error, digestWishlistFoundHtml(d.wishlist_found))}
    </div>
    <div class="dl-card">
      <h2>Chart movement</h2>
      ${Object.entries(d.charts).map(([chartId, section]) => digestChartHtml(chartId, section)).join("")}
    </div>
    <div class="dl-card">
      <h2>Recommendations</h2>
      ${digestRecommendationHtml("deezer", "Deezer (related artists)", d.recommendations.deezer)}
      ${digestRecommendationHtml("gemini", "Gemini", d.recommendations.gemini)}
    </div>`;
}

function digestNewReleasesHtml(section) {
  if (!section.total) return `<p class="muted">No new releases waiting right now.</p>`;
  return `<ul class="dl-list">${section.results.map(r =>
      `<li><div>${escapeHtml(r.artist)} · <i>${escapeHtml(r.title)}</i></div></li>`).join("")}</ul>
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

function digestChartHtml(chartId, section) {
  if (!section.ok) return `<p>${escapeHtml(chartsTabs.find(([k]) => k === chartId)?.[1] || chartId)}:
      <span class="error-text">${escapeHtml(section.error)}</span></p>`;
  const body = section.first_scan ? `<p class="muted">First look at this chart -- movement shows up from the next visit here.</p>`
    : section.new_entries.length ? `<ul class="dl-list">${section.new_entries.map(e =>
        `<li><div>${escapeHtml(e.title)} · ${escapeHtml(e.artist)}</div></li>`).join("")}</ul>`
    : `<p class="muted">No new entries since last time.</p>`;
  return `<div class="digest-chart"><h3>${escapeHtml(section.label)}</h3>${body}
    <button type="button" class="button" data-digest-chart="${escapeHtml(chartId)}">Open this chart</button></div>`;
}

function digestRecommendationHtml(source, label, section) {
  if (!section.ok) return `<p>${escapeHtml(label)}: <span class="error-text">${escapeHtml(section.error)}</span></p>`;
  if (!section.total) return `<p class="muted">${escapeHtml(label)}: nothing suggested yet.</p>`;
  return `<div class="digest-recs"><h3>${escapeHtml(label)}</h3>
    <ul class="dl-list">${section.rows.map(r =>
      `<li><div>${escapeHtml(r.artist)} · <i>${escapeHtml(r.title)}</i></div></li>`).join("")}</ul>
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
      try { digestView.data = await api("/api/digest/mark-found-seen", {}); renderDigest(); }
      catch (error) { setActionStatus("#digest-status", error.message); }
    })();
    return;
  }
  const chart = target.closest("[data-digest-chart]")?.dataset.digestChart;
  if (chart) { showCharts(chart); return; }
  const recs = target.closest("[data-digest-recs]")?.dataset.digestRecs;
  if (recs) { showRecommendations(recs); return; }
});
