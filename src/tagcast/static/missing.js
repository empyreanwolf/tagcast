"use strict";

// "Missing" on the Downloads page (fork v16+): New releases of the artists you follow,
// albums in your library that are only partly there, and the wishlist of things no source had.
// "Get the missing" sends each artist's albums through Galaxy → Bandcamp → Soulseek.
const missingView = {complete: null, completeQuery: "", completeOpen: new Map(), wishlist: null, wishKind: "album", settingsOpen: false, sub: "releases", releases: null, picked: new Set(), jobs: [], timer: null, jobTimer: null};
const missingSubs = [["releases", "New releases"], ["complete", "Complete my albums"], ["wishlist", "Wishlist"]];

function missingHtml() {
  const view = missingView;
  return `<div class="missing-subs" role="tablist" aria-label="Missing music">${missingSubs.map(([key, label]) =>
      `<button type="button" role="tab" aria-selected="${view.sub === key}" data-missing-sub="${key}">${label}${key === "releases" && view.releases?.unseen ? ` <b class="dl-count">${view.releases.unseen}</b>` : ""}</button>`).join("")}</div>
    ${missingJobsHtml()}
    <div id="missing-body">${missingBodyHtml()}</div>`;
}

function missingBodyHtml() {
  const body = {releases: releasesHtml, complete: completeHtml, wishlist: wishlistHtml};
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
    <details class="dl-card releases-settings" ${missingView.settingsOpen ? "open" : ""}><summary>Which artists (${r.artists.length} picked${r.auto ? ` + every artist with 3+ albums in ${(r.genres || []).length ? (r.genres || []).join(", ") : "your library"}` : ""}) · every ${r.every_days} days</summary>
      <label class="checkline"><input type="checkbox" data-releases-auto ${r.auto ? "checked" : ""}> Also check every album artist with 3 or more albums in the genres below</label>
      <fieldset class="releases-genres"><legend>Genres to check (hip-hop by default)</legend>
        ${(r.available_genres || r.genres || []).map(g => `<label class="checkline"><input type="checkbox" data-releases-genre="${escapeHtml(g)}" ${(r.genres || []).includes(g) ? "checked" : ""}> ${escapeHtml(g)}</label>`).join("")}
        ${(r.genres || []).length ? "" : '<p class="muted">No genre ticked: every genre counts.</p>'}</fieldset>
      <label>Check every <input id="releases-days" type="number" min="1" max="30" value="${r.every_days}" class="releases-days"> days</label>
      <div class="dl-actions"><input id="releases-add" class="dl-path" maxlength="200" placeholder="Add an artist to follow" aria-label="Artist to follow">
        <button type="button" class="button" data-releases="add">Follow</button></div>
      <div class="releases-artists">${r.artists.map(a => `<span class="pill">${escapeHtml(a)} <button type="button" class="text-action" data-releases-remove="${escapeHtml(a)}" aria-label="Stop following ${escapeHtml(a)}">✕</button></span>`).join(" ") || '<span class="muted">No artists picked.</span>'}</div></details>
    ${r.results.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-releases-all ${r.results.every(x => missingView.picked.has(x.id)) ? "checked" : ""}> Select all (${r.results.length})</label>
        <button type="button" class="button primary" data-releases="get" ${picked ? "" : "disabled"}>⬇ Get the missing (${picked})</button>
        <button type="button" class="button" data-releases="dismiss" ${picked ? "" : "disabled"}>Hide selected</button>
        <span class="muted">Order: YouTube → Galaxy → Bandcamp → Soulseek.</span></div>
      <ul class="discography-list">${r.results.map(x => `<li>
        <input type="checkbox" data-releases-pick="${x.id}" aria-label="Select ${escapeHtml(x.title)}" ${missingView.picked.has(x.id) ? "checked" : ""}>
        ${bandcampThumb(x.cover, "galaxy-album-art")}
        <div><b>${escapeHtml(x.title)}</b> <span class="muted">· ${escapeHtml(x.artist)}</span>
          <div class="muted">${escapeHtml([x.date, ({album: "Album", ep: "EP", single: "Single", compile: "Compilation"})[x.type], x.tracks ? `${x.tracks} tracks` : ""].filter(Boolean).join(" · "))}</div></div>
        <div>${discographyBadge(x.status)}</div></li>`).join("")}</ul>`
      : `<p class="muted">${r.last_check ? "Nothing new that you don't have yet." : "Not checked yet. Check now, or wait for the weekly check."}</p>`}`;
}

// -- complete my albums -------------------------------------------------------------------------

async function completeLoad() {
  try { missingView.complete = await api("/api/complete"); } catch (error) { missingView.complete = {error: error.message}; }
  missingRefresh();
  clearTimeout(missingView.timer);
  if (missingView.complete?.progress && state.screen === "downloads" && downloadsView.tab === "missing") missingView.timer = setTimeout(completeLoad, 2000);
}

function completeRows() {
  const q = missingView.completeQuery.trim().toLowerCase(), c = missingView.complete;
  return (c?.albums || []).filter(a => !q || `${a.artist} ${a.title}`.toLowerCase().includes(q));
}

function completeHtml() {
  const c = missingView.complete;
  if (!c) { completeLoad(); return '<p class="muted">Loading…</p>'; }
  if (c.error) return `<p class="error-text">${escapeHtml(c.error)}</p>`;
  const p = c.progress, rows = completeRows(), picked = rows.filter(a => missingView.picked.has(a.key)).length;
  const head = `<p class="muted">Albums in your library that are only partly there: gaps in the track numbers, or fewer songs than Deezer lists. Discs of one set count together.</p>
    <div class="dl-actions"><button type="button" class="button primary" data-complete="scan" ${p ? "disabled" : ""}>${c.scanned ? "Scan again" : "Scan my library"}</button>
      <span class="muted" role="status">${p ? `Checking ${p.done} of ${p.total}: ${escapeHtml(p.title || "")}…` : c.scanned ? `Last scan: ${missingDate(c.scanned)} · ${c.total.toLocaleString("en")} albums checked` : "Not scanned yet. It asks Deezer about each album (about 7 per second)."}</span></div>`;
  if (!c.albums.length) return head + (c.scanned ? '<p class="muted">Every album looks complete.</p>' : "");
  return `${head}
    <div class="dl-actions"><div class="search"><span aria-hidden="true">⌕</span><input id="complete-search" maxlength="200" placeholder="Filter artists or albums" aria-label="Filter the report" value="${escapeHtml(missingView.completeQuery)}"></div>
      <label><input type="checkbox" data-complete-all ${rows.length && rows.every(a => missingView.picked.has(a.key)) ? "checked" : ""}> Select all (${rows.length})</label>
      <button type="button" class="button primary" data-complete="get" ${picked ? "" : "disabled"}>⬇ Get the missing (${picked})</button>
      <span class="muted">Order: YouTube → Galaxy → Bandcamp (whole albums from Soulseek would bring back what you have).</span></div>
    <ul class="discography-list">${rows.map(a => {
      const open = missingView.completeOpen.get(a.key);
      const holes = Object.entries(a.gaps || {}).map(([disc, n]) => `${Object.keys(a.gaps).length > 1 ? `disc ${disc}: ` : ""}#${n.join(", #")}`).join("; ");
      return `<li><input type="checkbox" data-complete-pick="${escapeHtml(a.key)}" aria-label="Select ${escapeHtml(a.title)}" ${missingView.picked.has(a.key) ? "checked" : ""}>
        ${bandcampThumb(a.cover, "galaxy-album-art")}
        <div><b>${escapeHtml(a.title)}</b> <span class="muted">· ${escapeHtml(a.artist)}</span>
          <div class="muted">You have ${a.have} of ${a.expected} (${escapeHtml(a.basis)})${holes ? ` · missing numbers ${escapeHtml(holes)}` : ""}</div>
          <button type="button" class="text-action" data-complete-open="${escapeHtml(a.key)}" aria-expanded="${Boolean(open)}">${open ? "Hide songs" : "Which songs?"}</button>
          ${open === "loading" ? '<div class="muted">Asking Deezer…</div>' : open?.error ? `<div class="error-text">${escapeHtml(open.error)}</div>`
            : open ? `${open.note ? `<div class="muted">${escapeHtml(open.note)}</div>` : ""}<ol class="complete-tracks">${open.tracks.map(t => `<li class="${t.have ? "have" : "missing"}">${escapeHtml(t.title)} ${t.have ? '<span class="muted">✓</span>' : '<span class="dl-badge new">Missing</span>'}</li>`).join("")}</ol>` : ""}</div>
        <div>${discographyBadge("partly")}</div></li>`;
    }).join("")}</ul>`;
}

// -- wishlist ---------------------------------------------------------------------------------

async function wishlistLoad() {
  try { missingView.wishlist = await api("/api/wishlist"); } catch (error) { missingView.wishlist = {error: error.message}; }
  missingRefresh();
  clearTimeout(missingView.timer);
  if (missingView.wishlist?.progress && state.screen === "downloads" && downloadsView.tab === "missing") missingView.timer = setTimeout(wishlistLoad, 1500);
  else if (missingView.wishlist && !missingView.wishlist.progress && missingView.wishlistWasRunning) { missingView.wishlistWasRunning = false; loadDownloads(); }
  if (missingView.wishlist?.progress) missingView.wishlistWasRunning = true;
}

function wishlistHtml() {
  const w = missingView.wishlist;
  if (!w) { wishlistLoad(); return '<p class="muted">Loading…</p>'; }
  if (w.error) return `<p class="error-text">${escapeHtml(w.error)}</p>`;
  const picked = w.items.filter(i => missingView.picked.has(i.id)).length, p = w.progress;
  return `<p class="muted">Albums and songs that Bandcamp, Galaxy and Soulseek didn't have. Tagcast tries them again every
      <input id="wishlist-days" type="number" min="1" max="30" value="${w.every_days}" class="releases-days" aria-label="Days between tries"> days while it is open.</p>
    <div class="dl-actions"><input id="wishlist-line" class="dl-path" maxlength="300" placeholder="${missingView.wishKind === "album" ? "Artist - Album" : "Artist - Title"}" aria-label="Add a wish">
      <select id="wishlist-kind" aria-label="Album or song"><option value="album" ${missingView.wishKind === "album" ? "selected" : ""}>Album</option><option value="song" ${missingView.wishKind === "song" ? "selected" : ""}>Song</option></select>
      <button type="button" class="button" data-wishlist="add">Add to wishlist</button></div>
    ${p ? `<p class="muted" role="status">Trying ${p.done + 1 > p.total ? p.total : p.done + 1} of ${p.total}: ${escapeHtml(p.title || "")}…</p>` : ""}
    ${w.items.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-wishlist-all ${w.items.every(i => missingView.picked.has(i.id)) ? "checked" : ""}> Select all (${w.items.length})</label>
        <button type="button" class="button primary" data-wishlist="retry" ${picked && !p ? "" : "disabled"}>Try selected now (${picked})</button>
        <button type="button" class="button" data-wishlist="remove" ${picked ? "" : "disabled"}>Remove</button></div>
      <ul class="dl-list">${w.items.map(i => `<li><input type="checkbox" data-wishlist-pick="${i.id}" aria-label="Select ${escapeHtml(i.title)}" ${missingView.picked.has(i.id) ? "checked" : ""}>
        <span class="dl-badge new">${i.kind === "album" ? "Album" : "Song"}</span>
        <div><b>${escapeHtml(i.title)}</b> <span class="muted">· ${escapeHtml(i.artist)}${i.album ? ` · ${escapeHtml(i.album)}` : ""}</span>
          <div class="muted">${i.tries} ${i.tries === 1 ? "try" : "tries"} · ${escapeHtml(i.note || "")} · next try ${missingDate(i.next_try)}</div></div></li>`).join("")}</ul>`
      : '<p class="muted">Your wishlist is empty.</p>'}
    ${w.found.length ? `<h3>Found lately</h3><ul class="dl-list">${w.found.slice(0, 20).map(f => `<li><span class="dl-badge have">${escapeHtml(f.source)}</span>
      <div><b>${escapeHtml(f.title)}</b> <span class="muted">· ${escapeHtml(f.artist)} · ${escapeHtml(f.note || "")} · ${missingDate(f.when)}</span></div></li>`).join("")}</ul>` : ""}`;
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
      if (missingView.sub === "releases") releasesLoad(); else if (missingView.sub === "wishlist") wishlistLoad(); else completeLoad();
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
  const comp = target.closest("[data-complete]");
  if (comp) {
    const action = comp.dataset.complete;
    if (action === "scan") downloadsRun(async () => { view.complete = await api("/api/complete/scan", {}); missingRefresh(); completeLoad(); });
    else if (action === "get") {
      const rows = completeRows().filter(a => view.picked.has(a.key)).map(a => ({...a, status: "partly", tracks: a.expected}));
      downloadsRun(() => missingGet(missingGroups(rows)));
    }
    return;
  }
  const openSongs = target.closest("[data-complete-open]");
  if (openSongs) {
    const key = openSongs.dataset.completeOpen;
    if (view.completeOpen.has(key)) { view.completeOpen.delete(key); missingRefresh(); return; }
    view.completeOpen.set(key, "loading"); missingRefresh();
    api("/api/complete/tracks", {key}).then(r => view.completeOpen.set(key, r)).catch(e => view.completeOpen.set(key, {error: e.message})).finally(missingRefresh);
    return;
  }
  const wish = target.closest("[data-wishlist]");
  if (wish) {
    const action = wish.dataset.wishlist;
    if (action === "add") {
      const line = $("#wishlist-line").value.trim(), at = line.indexOf(" - ");
      if (at <= 0) { toast("Type Artist - Title."); return; }
      const kind = $("#wishlist-kind").value, artist = line.slice(0, at), title = line.slice(at + 3);
      downloadsRun(async () => { await api("/api/wishlist/add", {kind, artist, title}); await wishlistLoad(); });
    } else if (action === "retry") {
      downloadsRun(async () => { view.wishlist = await api("/api/wishlist/retry", {ids: [...view.picked]}); view.picked.clear(); missingRefresh(); wishlistLoad(); });
    } else if (action === "remove") {
      downloadsRun(async () => { view.wishlist = await api("/api/wishlist/remove", {ids: [...view.picked]}); view.picked.clear(); missingRefresh(); });
    }
    return;
  }
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
  else if (target.matches("[data-releases-genre]")) {
    const genres = [...document.querySelectorAll("[data-releases-genre]")].filter(box => box.checked).map(box => box.dataset.releasesGenre);
    downloadsRun(async () => { view.releases = await api("/api/releases/settings", {genres}); missingRefresh(); });
  }
  else if (target.matches("[data-releases-auto]")) downloadsRun(async () => { view.releases = await api("/api/releases/settings", {auto: target.checked}); missingRefresh(); });
  else if (target.matches("[data-complete-pick]")) { view.picked[target.checked ? "add" : "delete"](target.dataset.completePick); missingRefresh(); }
  else if (target.matches("[data-complete-all]")) { view.picked = new Set(target.checked ? completeRows().map(a => a.key) : []); missingRefresh(); }
  else if (target.matches("[data-wishlist-pick]")) { view.picked[target.checked ? "add" : "delete"](target.dataset.wishlistPick); missingRefresh(); }
  else if (target.matches("[data-wishlist-all]")) { view.picked = new Set(target.checked ? view.wishlist.items.map(i => i.id) : []); missingRefresh(); }
  else if (target.id === "wishlist-kind") { view.wishKind = target.value; }
  else if (target.id === "wishlist-days") downloadsRun(async () => { view.wishlist = await api("/api/wishlist/settings", {every_days: target.value}); missingRefresh(); });
  else if (target.id === "releases-days") downloadsRun(async () => { view.releases = await api("/api/releases/settings", {every_days: target.value}); missingRefresh(); });
});

document.addEventListener("toggle", event => {
  if (event.target.classList?.contains("releases-settings")) missingView.settingsOpen = event.target.open;
}, true);

document.addEventListener("input", event => {
  if (event.target.id !== "complete-search") return;
  missingView.completeQuery = event.target.value;
  clearTimeout(missingView.searchTimer);
  missingView.searchTimer = setTimeout(() => {
    missingRefresh();
    const input = $("#complete-search");
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }, 250);
});

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
