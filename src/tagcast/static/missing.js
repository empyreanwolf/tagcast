"use strict";

// "Missing" on the Downloads page (fork v16+): New releases of the artists you follow,
// albums in your library that are only partly there, and the wishlist of things no source had.
// "Get the missing" sends each artist's albums through Bandcamp → Soulseek → Galaxy.
const missingView = {settingsOpen: false, sub: "releases", releases: null, picked: new Set(), jobs: [], timer: null, jobTimer: null};
const missingSubs = [["releases", "New releases"]];

function missingHtml() {
  const view = missingView;
  return `<div class="missing-subs" role="tablist" aria-label="Missing music">${missingSubs.map(([key, label]) =>
      `<button type="button" role="tab" aria-selected="${view.sub === key}" data-missing-sub="${key}">${label}${key === "releases" && view.releases?.unseen ? ` <b class="dl-count">${view.releases.unseen}</b>` : ""}</button>`).join("")}</div>
    ${missingJobsHtml()}
    <div id="missing-body">${missingBodyHtml()}</div>`;
}

function missingBodyHtml() {
  const body = {releases: releasesHtml};
  return (body[missingView.sub] || (() => ""))();
}

function missingRefresh() {
  if (downloadsView.tab === "missing" && $("#missing-body")) $("#missing-body").innerHTML = missingBodyHtml();
}

function missingDate(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "never";
}

// -- new releases ----------------------------------------------------------------------------

async function releasesLoad() {
  try {
    missingView.releases = await api("/api/releases");
    if (missingView.releases.unseen && downloadsView.tab === "missing" && missingView.sub === "releases") api("/api/releases/seen", {}).catch(() => {});
  } catch (error) { missingView.releases = {error: error.message}; }
  missingRefresh();
  clearTimeout(missingView.timer);
  if (missingView.releases?.progress && state.screen === "downloads" && downloadsView.tab === "missing") missingView.timer = setTimeout(releasesLoad, 1500);
}

function releasesHtml() {
  const r = missingView.releases;
  if (!r) { releasesLoad(); return '<p class="muted">Loading…</p>'; }
  if (r.error) return `<p class="error-text">${escapeHtml(r.error)}</p>`;
  const picked = r.results.filter(x => missingView.picked.has(x.id)).length;
  const p = r.progress;
  return `<div class="dl-actions">
      <button type="button" class="button primary" data-releases="check" ${p ? "disabled" : ""}>Check now</button>
      <span class="muted" role="status">${p ? `Checking ${p.done} of ${p.total}${p.artist ? `: ${escapeHtml(p.artist)}` : ""}…`
        : `Last check: ${missingDate(r.last_check)}${r.next_check ? ` · next: ${missingDate(r.next_check)} (while Tagcast is open)` : ""}`}</span></div>
    <details class="dl-card releases-settings" ${missingView.settingsOpen ? "open" : ""}><summary>Which artists (${r.artists.length} picked${r.auto ? " + every artist with 3+ albums in your library" : ""}) · every ${r.every_days} days</summary>
      <label class="checkline"><input type="checkbox" data-releases-auto ${r.auto ? "checked" : ""}> Also check every album artist with 3 or more albums in my library</label>
      <label>Check every <input id="releases-days" type="number" min="1" max="30" value="${r.every_days}" class="releases-days"> days</label>
      <div class="dl-actions"><input id="releases-add" class="dl-path" maxlength="200" placeholder="Add an artist to follow" aria-label="Artist to follow">
        <button type="button" class="button" data-releases="add">Follow</button></div>
      <div class="releases-artists">${r.artists.map(a => `<span class="pill">${escapeHtml(a)} <button type="button" class="text-action" data-releases-remove="${escapeHtml(a)}" aria-label="Stop following ${escapeHtml(a)}">✕</button></span>`).join(" ") || '<span class="muted">No artists picked.</span>'}</div></details>
    ${r.results.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-releases-all ${r.results.every(x => missingView.picked.has(x.id)) ? "checked" : ""}> Select all (${r.results.length})</label>
        <button type="button" class="button primary" data-releases="get" ${picked ? "" : "disabled"}>⬇ Get the missing (${picked})</button>
        <button type="button" class="button" data-releases="dismiss" ${picked ? "" : "disabled"}>Hide selected</button>
        <span class="muted">Order: Bandcamp → Soulseek → Galaxy.</span></div>
      <ul class="discography-list">${r.results.map(x => `<li>
        <input type="checkbox" data-releases-pick="${x.id}" aria-label="Select ${escapeHtml(x.title)}" ${missingView.picked.has(x.id) ? "checked" : ""}>
        ${bandcampThumb(x.cover, "galaxy-album-art")}
        <div><b>${escapeHtml(x.title)}</b> <span class="muted">· ${escapeHtml(x.artist)}</span>
          <div class="muted">${escapeHtml([x.date, ({album: "Album", ep: "EP", single: "Single", compile: "Compilation"})[x.type], x.tracks ? `${x.tracks} tracks` : ""].filter(Boolean).join(" · "))}</div></div>
        <div>${discographyBadge(x.status)}</div></li>`).join("")}</ul>`
      : `<p class="muted">${r.last_check ? "Nothing new that you don't have yet." : "Not checked yet. Check now, or wait for the weekly check."}</p>`}`;
}

// -- get the missing, grouped by artist ----------------------------------------------------

async function missingGet(groups) {
  for (const [name, albums] of groups) {
    const job = await api("/api/discography/get", {name, albums, songs: []});
    missingView.jobs.push(job);
  }
  missingJobsRefresh();
  clearTimeout(missingView.jobTimer);
  const poll = async () => {
    const running = missingView.jobs.filter(j => j.state !== "done");
    for (const job of running) {
      try { Object.assign(job, await api(`/api/discography/get-status?id=${encodeURIComponent(job.id)}`)); } catch { job.state = "done"; }
    }
    missingJobsRefresh();
    if (missingView.jobs.some(j => j.state !== "done")) missingView.jobTimer = setTimeout(poll, 1500);
    else {
      if (missingView.jobs.some(j => j.log.some(e => !e.source))) artistCleanupAlarm();
      loadDownloads();
      if (missingView.sub === "releases") releasesLoad(); else missingRefresh();
    }
  };
  missingView.jobTimer = setTimeout(poll, 1000);
}

function missingJobsHtml() {
  const jobs = missingView.jobs;
  if (!jobs.length) return '<div id="missing-jobs"></div>';
  const done = jobs.reduce((n, j) => n + j.done, 0), total = jobs.reduce((n, j) => n + j.total, 0);
  const finished = jobs.every(j => j.state === "done");
  return `<div id="missing-jobs"><section class="dl-card" role="status"><h3>Getting the missing ${finished ? "· done" : `· ${done} of ${total}`}</h3>
    <ul class="dl-list">${jobs.flatMap(j => j.log.map(e => `<li><span class="dl-badge ${e.source ? "have" : "partly"}">${escapeHtml(e.source || "Not found")}</span>
      <div><b>${escapeHtml(e.title)}</b> <span class="muted">· ${escapeHtml(j.name)} · ${escapeHtml(e.note || "")}</span></div></li>`)).join("")}</ul>
    ${finished ? '<div class="dl-actions"><button type="button" class="button small" data-downloads-tab="queue">Open the queue</button><button type="button" class="text-action" data-missing="close-jobs">Close</button></div>' : ""}</section></div>`;
}

function missingJobsRefresh() {
  if (downloadsView.tab === "missing" && $("#missing-jobs")) $("#missing-jobs").outerHTML = missingJobsHtml();
}

function missingGroups(rows) {
  const groups = new Map();
  for (const x of rows) {
    if (!groups.has(x.artist)) groups.set(x.artist, []);
    groups.get(x.artist).push({title: x.title, status: x.status, deezer_id: x.deezer_id || null, tracks: x.tracks || null});
  }
  return [...groups];
}

// -- events ------------------------------------------------------------------------------------

document.addEventListener("click", event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "missing") return;
  const view = missingView;
  const sub = target.closest("[data-missing-sub]");
  if (sub) { view.sub = sub.dataset.missingSub; view.picked.clear(); renderDownloads(); return; }
  if (target.closest('[data-missing="close-jobs"]')) { view.jobs = []; missingJobsRefresh(); return; }
  const remove = target.closest("[data-releases-remove]");
  if (remove) { downloadsRun(async () => { view.releases = await api("/api/releases/settings", {remove: remove.dataset.releasesRemove}); missingRefresh(); }); return; }
  const button = target.closest("[data-releases]");
  if (!button) return;
  const action = button.dataset.releases, r = view.releases;
  if (action === "check") downloadsRun(async () => { view.releases = await api("/api/releases/check", {}); missingRefresh(); releasesLoad(); });
  else if (action === "add") {
    const name = $("#releases-add").value.trim();
    if (name) downloadsRun(async () => { view.releases = await api("/api/releases/settings", {add: name}); missingRefresh(); });
  } else if (action === "dismiss") {
    downloadsRun(async () => { view.releases = await api("/api/releases/dismiss", {ids: [...view.picked]}); view.picked.clear(); missingRefresh(); });
  } else if (action === "get") {
    downloadsRun(() => missingGet(missingGroups(r.results.filter(x => view.picked.has(x.id)))));
  }
});

document.addEventListener("change", event => {
  const target = event.target, view = missingView;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "missing") return;
  if (target.matches("[data-releases-pick]")) { view.picked[target.checked ? "add" : "delete"](target.dataset.releasesPick); missingRefresh(); }
  else if (target.matches("[data-releases-all]")) { view.picked = new Set(target.checked ? view.releases.results.map(x => x.id) : []); missingRefresh(); }
  else if (target.matches("[data-releases-auto]")) downloadsRun(async () => { view.releases = await api("/api/releases/settings", {auto: target.checked}); missingRefresh(); });
  else if (target.id === "releases-days") downloadsRun(async () => { view.releases = await api("/api/releases/settings", {every_days: target.value}); missingRefresh(); });
});

document.addEventListener("toggle", event => {
  if (event.target.classList?.contains("releases-settings")) missingView.settingsOpen = event.target.open;
}, true);

document.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.id === "releases-add") { event.preventDefault(); $('[data-releases="add"]').click(); }
});

// After Tagcast starts: say so when the weekly check found new releases.
window.addEventListener("DOMContentLoaded", () => {
  let tries = 0;
  const look = async () => {
    if (!live()) { if (++tries < 40) setTimeout(look, 3000); return; }
    try {
      const {unseen} = await api("/api/releases/summary");
      if (!unseen) return;
      const text = `${unseen} new ${unseen === 1 ? "release" : "releases"} from artists you follow. See Downloads → Missing.`;
      toast(text);
      if ("Notification" in window && Notification.permission === "granted") {
        const note = new Notification("Tagcast · New releases", {body: text, tag: "tagcast-new-releases"});
        note.onclick = () => { window.focus(); missingView.sub = "releases"; showDownloads("missing"); note.close(); };
      }
    } catch { /* not important enough to interrupt */ }
  };
  setTimeout(look, 4000);
});
