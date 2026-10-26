"use strict";

// Charts (fork v20): outside hip-hop charts, each song checked against your iBroadcast
// library and download history first. "Get it" queues a NetEase chart song straight through
// Galaxy (a real track id); everything else (title + artist only) tries Bandcamp -> Soulseek
// -> Galaxy, one song at a time, same as Discography's "Get the missing". There is no
// "underground hip-hop" tab: nothing online actually publishes that as a fetchable chart --
// see the note on the page.
const chartsTabs = [["netease_cn_rap", "Chinese Rap (NetEase)"], ["hotnewhiphop_top100", "HotNewHipHop Top 100"],
  ["deezer_hiphop", "Deezer Hip-Hop"], ["apple_hiphop", "Apple Music"], ["shazam_hiphop_us", "Shazam (US)"],
  ["billboard_rap", "Billboard Hot Rap"]];
const chartsView = {tab: "netease_cn_rap", data: {}, picked: new Set(), jobs: null, timer: null, jobTimer: null, busy: false};

function showCharts(tab) {
  if (tab) chartsView.tab = tab;
  showScreen("charts");
  renderCharts();
  chartsLoad();
  chartsJobsLoad();
}

function chartsTabsHtml() {
  return chartsTabs.map(([key, label]) => `<button type="button" role="tab" id="charts-tab-${key}"
      aria-selected="${chartsView.tab === key}" tabindex="${chartsView.tab === key ? 0 : -1}"
      data-charts-tab="${key}">${label}</button>`).join("");
}

function renderCharts() {
  $("#charts-tabs").innerHTML = chartsTabsHtml();
  $("#charts-content").innerHTML = chartsBodyHtml();
}

function chartsBodyHtml() {
  const d = chartsView.data[chartsView.tab];
  const note = `<p class="muted">There's no "underground hip-hop" tab here: no platform actually publishes one as a fetchable
      chart -- everything calling itself that is either locked behind a login (Spotify/Apple Music editorial playlists)
      or just a blog post naming other platforms, with no song list to read. This page lists what genuinely exists.</p>`;
  if (!d) { chartsLoad(); return `${note}<p class="muted">Loading…</p>`; }
  if (d.error) return `${note}<div class="dl-card"><p class="error-text">${escapeHtml(d.error)}</p>
      <div class="dl-actions"><button type="button" class="button" data-charts="refresh">Try again</button></div></div>`;
  const rows = d.rows || [];
  const picked = rows.filter(r => chartsView.picked.has(r.rank)).length;
  const p = d.progress;
  return `${note}
    <div class="dl-actions"><a href="${escapeHtml(d.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(d.url)}</a>
      <button type="button" class="button" data-charts="refresh" ${p ? "disabled" : ""}>Refresh</button>
      <span class="muted">${d.when ? `Fetched ${missingDate(d.when)}` : ""}</span></div>
    ${p ? `<p class="muted" role="status">Getting songs: ${p.done} of ${p.total}${p.title ? ` — ${escapeHtml(p.title)}` : ""}…</p>` : ""}
    ${rows.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-charts-all ${rows.every(r => chartsView.picked.has(r.rank)) ? "checked" : ""}> Select all (${rows.length})</label>
        <button type="button" class="button primary" data-charts="get" ${picked ? "" : "disabled"}>⬇ Get it (${picked})</button></div>
      <ul class="dl-list">${rows.map(chartsRowHtml).join("")}</ul>` : `<p class="muted">No songs read from this chart.</p>`}
    ${chartsJobsHtml()}`;
}

function chartsRowHtml(r) {
  const status = r.status && r.status !== "new" ? `<span class="pill ${r.status === "have" ? "" : "missing"}">${escapeHtml(downloadsStatusLabels[r.status] || r.status)}</span>` : "";
  return `<li><input type="checkbox" data-charts-pick="${r.rank}" aria-label="Select ${escapeHtml(r.title)}" ${chartsView.picked.has(r.rank) ? "checked" : ""}>
      <div><b>#${r.rank}</b> ${escapeHtml(r.title)} · ${escapeHtml(r.artist)}${r.album ? ` · <i>${escapeHtml(r.album)}</i>` : ""} ${status}</div></li>`;
}

function chartsJobsHtml() {
  const j = chartsView.jobs;
  if (!j || !j.jobs.length) return "";
  return `<h3>Recently gotten</h3><ul class="dl-list">${j.jobs.slice(0, 20).map(e =>
    `<li><span class="dl-badge ${e.source ? "have" : "partly"}">${escapeHtml(e.source || "Not found")}</span>
       <div><b>${escapeHtml(e.title)}</b> · ${escapeHtml(e.artist)}<br><span class="muted">${escapeHtml(e.note || "")}</span></div></li>`).join("")}</ul>`;
}

async function chartsLoad() {
  const tab = chartsView.tab;
  try {
    const d = await api(`/api/charts/view?chart=${encodeURIComponent(tab)}`);
    chartsView.data[tab] = d;
  } catch (error) { chartsView.data[tab] = {error: error.message, rows: []}; }
  if (chartsView.tab === tab) renderCharts();
  clearTimeout(chartsView.timer);
  if (chartsView.data[tab]?.progress && state.screen === "charts") chartsView.timer = setTimeout(() => { chartsLoad(); chartsJobsLoad(); }, 1500);
}

async function chartsJobsLoad() {
  try { chartsView.jobs = await api("/api/charts/jobs"); } catch (error) { /* quietly skip */ }
  if (state.screen === "charts") $("#charts-content") && renderCharts();
  clearTimeout(chartsView.jobTimer);
  if (chartsView.jobs?.progress && Object.keys(chartsView.jobs.progress).length && state.screen === "charts") chartsView.jobTimer = setTimeout(chartsJobsLoad, 1500);
}

async function chartsRun(work) {
  if (chartsView.busy) return;
  chartsView.busy = true;
  try { await work(); } catch (error) { setActionStatus("#charts-status", error.message || String(error)); } finally { chartsView.busy = false; }
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("#show-charts")) { showCharts(); return; }
  if (!target.closest("#charts-page")) return;
  const tab = target.closest("[data-charts-tab]");
  if (tab) { chartsView.tab = tab.dataset.chartsTab; chartsView.picked.clear(); renderCharts(); chartsLoad(); return; }
  const action = target.closest("[data-charts]")?.dataset.charts;
  if (action === "refresh") {
    chartsRun(async () => {
      chartsView.data[chartsView.tab] = await api(`/api/charts/view?chart=${encodeURIComponent(chartsView.tab)}&refresh=1`);
      chartsView.picked.clear(); renderCharts();
    });
    return;
  }
  if (action === "get") {
    chartsRun(async () => {
      await api("/api/charts/get", {chart: chartsView.tab, ranks: [...chartsView.picked]});
      chartsView.picked.clear();
      chartsView.data[chartsView.tab] = await api(`/api/charts/view?chart=${encodeURIComponent(chartsView.tab)}`);
      renderCharts(); chartsJobsLoad();
    });
    return;
  }
  const all = target.closest("[data-charts-all]");
  if (all) {
    const rows = chartsView.data[chartsView.tab]?.rows || [];
    if (rows.every(r => chartsView.picked.has(r.rank))) rows.forEach(r => chartsView.picked.delete(r.rank));
    else rows.forEach(r => chartsView.picked.add(r.rank));
    renderCharts();
  }
});

document.addEventListener("change", event => {
  const pick = event.target.closest?.("[data-charts-pick]");
  if (!pick) return;
  const rank = Number(pick.dataset.chartsPick);
  event.target.checked ? chartsView.picked.add(rank) : chartsView.picked.delete(rank);
  renderCharts();
});
