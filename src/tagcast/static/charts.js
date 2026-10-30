"use strict";

// Charts (fork v20): outside hip-hop charts, each song checked against your iBroadcast
// library and download history first. "Get it" queues a NetEase chart song straight through
// Galaxy (a real track id); everything else (title + artist only) tries Bandcamp -> Soulseek
// -> Galaxy, one song at a time, same as Discography's "Get the missing". There is no
// "underground hip-hop" tab: nothing online actually publishes that as a fetchable chart --
// see the note on the page.
const chartsTabs = [["netease_cn_rap", "Chinese Rap (NetEase)"], ["hotnewhiphop_top100", "HotNewHipHop Top 100"],
  ["deezer_hiphop", "Deezer Hip-Hop"], ["apple_hiphop", "Apple Music"], ["spotify_rapcaviar", "Spotify RapCaviar"], ["soundcloud_hiphop", "SoundCloud Hip-hop"],
  ["recommended", "Recommended for you"], ["browse", "Browse"]];
const chartsView = {tab: "netease_cn_rap", data: {}, picked: new Set(), jobs: null, timer: null, jobTimer: null, busy: false,
  browse: {source: "apple", country: "", genre: "", options: null, key: null}};

function showCharts(tab) {
  if (tab) chartsView.tab = tab;
  showScreen("charts");
  renderCharts();
  if (chartsView.tab === "recommended") { recommendationsLoad(); recommendationsJobsLoad(); }
  else { chartsLoad(); chartsJobsLoad(); }
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
  if (chartsView.tab === "browse") return browseBodyHtml();
  if (chartsView.tab === "recommended") return `<p class="muted">Artists related to the ones you follow on New releases, per Deezer. No AI.</p>${recommendationsBodyHtml()}`;
  const d = chartsView.data[chartsView.tab];
  const note = `<p class="muted">There's no "underground hip-hop" tab here: no platform actually publishes one as a fetchable
      chart -- everything calling itself that is either locked behind a login or just a blog post naming other platforms, with no
      song list to read. This page lists what genuinely exists. Browse can read any public Spotify playlist from its link.</p>`;
  if (!d) { chartsLoad(); return `${note}<p class="muted">Loading…</p>`; }
  return `${note}${chartsResultBody(d, "data-charts", "data-charts-all")}`;
}

function chartsResultBody(d, actionAttr, allAttr) {
  if (!d) return `<p class="muted">Pick a source, then Browse.</p>`;
  if (d.error) return `<div class="dl-card"><p class="error-text">${escapeHtml(d.error)}</p>
      <div class="dl-actions"><button type="button" class="button" ${actionAttr}="refresh">Try again</button></div></div>`;
  const rows = d.rows || [];
  const picked = rows.filter(r => chartsView.picked.has(r.rank)).length;
  const p = d.progress;
  return `<div class="dl-actions"><a href="${escapeHtml(d.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(d.url)}</a>
      <button type="button" class="button" ${actionAttr}="refresh" ${p ? "disabled" : ""}>Refresh</button>
      <span class="muted">${d.when ? `Fetched ${missingDate(d.when)}` : ""}</span></div>
    ${p ? `<p class="muted" role="status">Getting songs: ${p.done} of ${p.total}${p.title ? ` — ${escapeHtml(p.title)}` : ""}…</p>` : ""}
    ${rows.length ? `<div class="dl-actions">
        <label><input type="checkbox" ${allAttr} ${rows.every(r => chartsView.picked.has(r.rank)) ? "checked" : ""}> Select all (${rows.length})</label>
        <button type="button" class="button primary" ${actionAttr}="get" ${picked ? "" : "disabled"}>⬇ Get it (${picked})</button></div>
      <ul class="dl-list">${rows.map(chartsRowHtml).join("")}</ul>` : `<p class="muted">No songs read from this chart.</p>`}
    ${chartsJobsHtml()}`;
}

function browseDataKey(b) { return `browse:${b.source}:${b.country}:${b.genre}`; }

function browseBodyHtml() {
  const b = chartsView.browse;
  const opts = b.options;
  if (!opts) { browseOptionsLoad(); return `<p class="muted">Loading…</p>`; }
  const src = opts[b.source] || {};
  const d = b.key === browseDataKey(b) ? chartsView.data[b.key] : null;
  return `<form id="browse-form" class="dl-actions">
      <label>Source <select data-browse-field="source">${Object.entries(opts).map(([k, o]) =>
        `<option value="${escapeHtml(k)}" ${b.source === k ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></label>
      ${src.needs_country ? (src.countries
          ? `<label>Country <select data-browse-field="country"><option value="">Pick one…</option>${src.countries.map(c =>
              `<option value="${escapeHtml(c)}" ${b.country === c ? "selected" : ""}>${escapeHtml(c)}</option>`).join("")}</select></label>`
          : `<label>Country <input type="text" data-browse-field="country" maxlength="2" placeholder="us" value="${escapeHtml(b.country)}"></label>`) : ""}
      ${src.needs_genre ? (src.genres
          ? `<label>Genre <select data-browse-field="genre">${src.genres.map(g =>
              `<option value="${escapeHtml(g)}" ${b.genre === g ? "selected" : ""}>${escapeHtml(g)}</option>`).join("")}</select></label>`
          : `<label>${escapeHtml(src.genre_label || "Genre")} <input type="text" data-browse-field="genre" placeholder="${escapeHtml(src.genre_placeholder || "pop")}" value="${escapeHtml(b.genre)}"></label>`) : ""}
      <button type="submit" class="button primary">Browse</button></form>
    ${src.note ? `<p class="muted">${escapeHtml(src.note)}</p>` : ""}
    ${chartsResultBody(d, "data-browse-action", "data-browse-all")}`;
}

async function browseOptionsLoad() {
  try { chartsView.browse.options = await api("/api/charts/browse/options"); } catch (error) { /* quietly skip */ }
  if (chartsView.tab === "browse") renderCharts();
}

async function browseLoad(refresh) {
  const b = chartsView.browse;
  const key = browseDataKey(b);
  const params = new URLSearchParams({source: b.source, country: b.country, genre: b.genre});
  if (refresh) params.set("refresh", "1");
  try { chartsView.data[key] = await api(`/api/charts/browse/view?${params}`); }
  catch (error) { chartsView.data[key] = {error: error.message, rows: []}; }
  chartsView.browse.key = key;
  renderCharts();
}

function chartsRowHtml(r) {
  const status = r.status && r.status !== "new" ? `<span class="pill ${r.status === "have" ? "" : "missing"}">${escapeHtml(downloadsStatusLabels[r.status] || r.status)}${r.status === "downloaded" && r.pc ? ` at ${escapeHtml(r.pc)}, not uploaded yet` : ""}</span>` : "";
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
  if (tab) {
    chartsView.tab = tab.dataset.chartsTab;
    chartsView.picked.clear();
    renderCharts();
    if (chartsView.tab === "recommended") { recommendationsLoad(); recommendationsJobsLoad(); }
    else if (chartsView.tab !== "browse") chartsLoad();
    return;
  }
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
    return;
  }
  const browseAction = target.closest("[data-browse-action]")?.dataset.browseAction;
  if (browseAction === "refresh") { chartsRun(() => browseLoad(true)); return; }
  if (browseAction === "get") {
    chartsRun(async () => {
      const b = chartsView.browse;
      await api("/api/charts/browse/get", {source: b.source, country: b.country, genre: b.genre, ranks: [...chartsView.picked]});
      chartsView.picked.clear();
      await browseLoad(false);
      chartsJobsLoad();
    });
    return;
  }
  const browseAll = target.closest("[data-browse-all]");
  if (browseAll) {
    const rows = chartsView.data[chartsView.browse.key]?.rows || [];
    if (rows.every(r => chartsView.picked.has(r.rank))) rows.forEach(r => chartsView.picked.delete(r.rank));
    else rows.forEach(r => chartsView.picked.add(r.rank));
    renderCharts();
  }
});

document.addEventListener("submit", event => {
  if (event.target?.id !== "browse-form") return;
  event.preventDefault();
  chartsView.picked.clear();
  chartsRun(() => browseLoad(false));
});

document.addEventListener("change", event => {
  const field = event.target.closest?.("[data-browse-field]");
  if (field) {
    chartsView.browse[field.dataset.browseField] = field.value;
    if (field.dataset.browseField === "source") { chartsView.browse.country = ""; chartsView.browse.genre = ""; }
    renderCharts();
    return;
  }
  const pick = event.target.closest?.("[data-charts-pick]");
  if (!pick) return;
  const rank = Number(pick.dataset.chartsPick);
  event.target.checked ? chartsView.picked.add(rank) : chartsView.picked.delete(rank);
  renderCharts();
});
