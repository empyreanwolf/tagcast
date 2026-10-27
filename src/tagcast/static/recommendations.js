"use strict";

// Recommendations (fork v23): two separate lists -- Deezer's related-artist data
// (deterministic, no AI) and Ask Gemini (conversational, same key/model as that page) --
// both seeded from the artists New releases already tracks. Every song is checked against
// the library and download history first; "Get it" queues it the same Bandcamp -> Soulseek
// -> Galaxy order as Charts, one at a time, landing on the wishlist if nothing has it.
const recommendationsTabs = [["deezer", "Deezer (related artists)"], ["gemini", "Gemini"]];
const recommendationsView = {tab: "deezer", data: {}, picked: new Set(), jobs: null, timer: null, jobTimer: null, busy: false};

function showRecommendations(tab) {
  if (tab) recommendationsView.tab = tab;
  showScreen("recommendations");
  renderRecommendations();
  recommendationsLoad();
  recommendationsJobsLoad();
}

function recommendationsTabsHtml() {
  return recommendationsTabs.map(([key, label]) => `<button type="button" role="tab" id="recommendations-tab-${key}"
      aria-selected="${recommendationsView.tab === key}" tabindex="${recommendationsView.tab === key ? 0 : -1}"
      data-recommendations-tab="${key}">${label}</button>`).join("");
}

function renderRecommendations() {
  $("#recommendations-tabs").innerHTML = recommendationsTabsHtml();
  $("#recommendations-content").innerHTML = recommendationsBodyHtml();
}

function recommendationsBodyHtml() {
  const d = recommendationsView.data[recommendationsView.tab];
  if (!d) { recommendationsLoad(); return `<p class="muted">Loading…</p>`; }
  if (d.error) return `<div class="dl-card"><p class="error-text">${escapeHtml(d.error)}</p>
      <div class="dl-actions"><button type="button" class="button" data-recommendations="refresh">Try again</button></div></div>`;
  const rows = d.rows || [];
  const picked = rows.filter(r => recommendationsView.picked.has(r.rank)).length;
  const p = d.progress;
  return `<div class="dl-actions">
      <button type="button" class="button" data-recommendations="refresh" ${p ? "disabled" : ""}>Refresh</button>
      <span class="muted">${d.when ? `Scanned ${missingDate(d.when)}` : ""}${d.seeds?.length ? ` · based on ${d.seeds.length} artist(s) from New releases` : ""}</span></div>
    ${p ? `<p class="muted" role="status">Getting songs: ${p.done} of ${p.total}${p.title ? ` — ${escapeHtml(p.title)}` : ""}…</p>` : ""}
    ${rows.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-recommendations-all ${rows.every(r => recommendationsView.picked.has(r.rank)) ? "checked" : ""}> Select all (${rows.length})</label>
        <button type="button" class="button primary" data-recommendations="get" ${picked ? "" : "disabled"}>⬇ Get it (${picked})</button></div>
      <ul class="dl-list">${rows.map(recommendationsRowHtml).join("")}</ul>` : `<p class="muted">Nothing suggested yet.</p>`}
    ${recommendationsJobsHtml()}`;
}

function recommendationsRowHtml(r) {
  const status = r.status && r.status !== "new" ? `<span class="pill ${r.status === "have" ? "" : "missing"}">${escapeHtml(downloadsStatusLabels[r.status] || r.status)}</span>` : "";
  return `<li><input type="checkbox" data-recommendations-pick="${r.rank}" aria-label="Select ${escapeHtml(r.title)}" ${recommendationsView.picked.has(r.rank) ? "checked" : ""}>
      <div>${escapeHtml(r.title)} · ${escapeHtml(r.artist)}${r.album ? ` · <i>${escapeHtml(r.album)}</i>` : ""} ${status}
      ${r.why ? `<br><span class="muted">${escapeHtml(r.why)}</span>` : ""}</div></li>`;
}

function recommendationsJobsHtml() {
  const j = recommendationsView.jobs;
  if (!j || !j.jobs.length) return "";
  return `<h3>Recently gotten</h3><ul class="dl-list">${j.jobs.slice(0, 20).map(e =>
    `<li><span class="dl-badge ${e.source ? "have" : "partly"}">${escapeHtml(e.source || "Not found")}</span>
       <div><b>${escapeHtml(e.title)}</b> · ${escapeHtml(e.artist)}<br><span class="muted">${escapeHtml(e.note || "")}</span></div></li>`).join("")}</ul>`;
}

async function recommendationsLoad() {
  const tab = recommendationsView.tab;
  try {
    const d = await api(`/api/recommendations?source=${encodeURIComponent(tab)}`);
    recommendationsView.data[tab] = d;
  } catch (error) { recommendationsView.data[tab] = {error: error.message, rows: []}; }
  if (recommendationsView.tab === tab) renderRecommendations();
  clearTimeout(recommendationsView.timer);
  if (recommendationsView.data[tab]?.progress && state.screen === "recommendations")
    recommendationsView.timer = setTimeout(() => { recommendationsLoad(); recommendationsJobsLoad(); }, 1500);
}

async function recommendationsJobsLoad() {
  try { recommendationsView.jobs = await api("/api/recommendations/jobs"); } catch (error) { /* quietly skip */ }
  if (state.screen === "recommendations") $("#recommendations-content") && renderRecommendations();
  clearTimeout(recommendationsView.jobTimer);
  if (recommendationsView.jobs?.progress && Object.keys(recommendationsView.jobs.progress).length && state.screen === "recommendations")
    recommendationsView.jobTimer = setTimeout(recommendationsJobsLoad, 1500);
}

async function recommendationsRun(work) {
  if (recommendationsView.busy) return;
  recommendationsView.busy = true;
  try { await work(); } catch (error) { setActionStatus("#recommendations-status", error.message || String(error)); }
  finally { recommendationsView.busy = false; }
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("#show-recommendations")) { showRecommendations(); return; }
  if (!target.closest("#recommendations-page")) return;
  const tab = target.closest("[data-recommendations-tab]");
  if (tab) { recommendationsView.tab = tab.dataset.recommendationsTab; recommendationsView.picked.clear(); renderRecommendations(); recommendationsLoad(); return; }
  const action = target.closest("[data-recommendations]")?.dataset.recommendations;
  if (action === "refresh") {
    recommendationsRun(async () => {
      recommendationsView.data[recommendationsView.tab] = await api(
        `/api/recommendations?source=${encodeURIComponent(recommendationsView.tab)}&refresh=1`);
      recommendationsView.picked.clear(); renderRecommendations();
    });
    return;
  }
  if (action === "get") {
    recommendationsRun(async () => {
      await api("/api/recommendations/get", {source: recommendationsView.tab, ranks: [...recommendationsView.picked]});
      recommendationsView.picked.clear();
      recommendationsView.data[recommendationsView.tab] = await api(
        `/api/recommendations?source=${encodeURIComponent(recommendationsView.tab)}`);
      renderRecommendations(); recommendationsJobsLoad();
    });
    return;
  }
  const all = target.closest("[data-recommendations-all]");
  if (all) {
    const rows = recommendationsView.data[recommendationsView.tab]?.rows || [];
    if (rows.every(r => recommendationsView.picked.has(r.rank))) rows.forEach(r => recommendationsView.picked.delete(r.rank));
    else rows.forEach(r => recommendationsView.picked.add(r.rank));
    renderRecommendations();
  }
});

document.addEventListener("change", event => {
  const pick = event.target.closest?.("[data-recommendations-pick]");
  if (!pick) return;
  const rank = Number(pick.dataset.recommendationsPick);
  event.target.checked ? recommendationsView.picked.add(rank) : recommendationsView.picked.delete(rank);
  renderRecommendations();
});
