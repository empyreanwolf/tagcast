"use strict";

// Artist cleanup: merge duplicate entries, split combined names, link featured guests.
// Suggestions and previews are read-only; only "Start" after approval writes to iBroadcast.
// Closing the dialog never stops a running job.
const artistCleanupView = {
  tab: "merge", data: null, offset: 0, excluded: new Set(), busy: false, timer: null, request: 0,
  merge: {groups: null, page: 0, filter: "all", query: "", choices: new Map(), loading: false},
  split: {items: null, page: 0, filter: "ticked", query: "", chosen: new Set(), loading: false},
  feat: {data: null, loading: false},
  search: {query: "", results: [], picked: new Map(), keep: "", custom: "", loading: false},
};
const artistCleanupTabs = [["merge", "Duplicates"], ["split", "Combined names"], ["feat", "Featured in titles"],
  ["search", "Search & merge"], ["review", "Review & run"]];
const artistCleanupModes = {merge: "Merge duplicates", split: "Split combined names", feat: "Link featured artists"};
const artistCleanupPage = 25;

function resetArtistCleanup(close = false) {
  const view = artistCleanupView;
  view.request += 1; clearTimeout(view.timer);
  view.data = null; view.offset = 0; view.excluded.clear(); view.busy = false;
  view.merge.groups = null; view.merge.choices.clear(); view.merge.page = 0;
  view.split.items = null; view.split.chosen.clear(); view.split.page = 0;
  view.feat.data = null;
  view.search.results = []; view.search.picked.clear(); view.search.keep = ""; view.search.custom = "";
  if ($("#artist-cleanup-content")) $("#artist-cleanup-content").innerHTML = '<p class="muted">Library reloaded. Reopen artist cleanup to see fresh suggestions.</p>';
  if (close && $("#artist-cleanup")?.open) $("#artist-cleanup").close();
}

async function openArtistCleanup(tab) {
  if (!live()) { toast("Connect iBroadcast to clean up artists."); return; }
  $("#artist-cleanup").showModal();
  await fetchArtistCleanupTask();
  const task = artistCleanupView.data;
  artistCleanupView.tab = tab || (task && ["running", "stopped", "error", "preview"].includes(task.state) ? "review" : artistCleanupView.tab);
  renderArtistCleanup();
  loadArtistCleanupTab();
}

function loadArtistCleanupTab() {
  const view = artistCleanupView;
  if (view.tab === "merge" && !view.merge.groups && !view.merge.loading) return fetchArtistSuggestions("merge");
  if (view.tab === "split" && !view.split.items && !view.split.loading) return fetchArtistSuggestions("split");
  if (view.tab === "feat" && !view.feat.data && !view.feat.loading) return fetchArtistSuggestions("feat");
}

async function fetchArtistCleanupTask() {
  const view = artistCleanupView, request = ++view.request, details = state.details;
  clearTimeout(view.timer);
  try {
    const data = await api(`/api/artist-cleanup?offset=${view.offset}&limit=${artistCleanupPage}`);
    if (request !== view.request || details !== state.details || !live()) return;
    if (data.id !== view.data?.id || data.state !== "preview") view.excluded = new Set(data.excluded || []);
    view.data = data;
    renderArtistCleanup();
    if (data.state === "running" && $("#artist-cleanup").open) view.timer = setTimeout(fetchArtistCleanupTask, 1500);
  } catch (error) {
    if (request === view.request) setActionStatus("#artist-cleanup-status", error.message);
  }
}

async function fetchArtistSuggestions(kind) {
  const part = artistCleanupView[kind], details = state.details;
  part.loading = true; renderArtistCleanup();
  try {
    const data = await api(`/api/artist-cleanup/suggestions?kind=${kind}`);
    if (details !== state.details || !live()) return;
    if (kind === "merge") {
      part.groups = data.groups; part.choices.clear();
      for (const group of data.groups) part.choices.set(group.id, {
        include: group.members.filter(m => m.ticked).length > 1,
        ticked: new Set(group.members.filter(m => m.ticked).map(m => m.id)), target: "", custom: ""});
    } else if (kind === "split") {
      part.items = data.items; part.chosen = new Set(data.items.filter(i => i.ticked).map(i => i.id));
    } else part.data = data;
  } catch (error) {
    setActionStatus("#artist-cleanup-status", error.message);
  } finally {
    part.loading = false; renderArtistCleanup();
  }
}

// -- merge suggestions --------------------------------------------------------

function mergeTargetOptions(group, choice) {
  const members = group.members.filter(m => choice.ticked.has(m.id));
  const options = members.map(m => ({value: `id:${m.id}`, label: `Keep “${m.name}” (${m.tracks.toLocaleString("en")} tracks)`}));
  if (group.target.name) options.unshift({value: `new:${group.target.name}`, label: `Use new name “${group.target.name}”`});
  options.push({value: "custom", label: "Type another name…"});
  return options;
}

function mergeTarget(group, choice) {
  const options = mergeTargetOptions(group, choice);
  if (choice.target && options.some(o => o.value === choice.target)) return choice.target;
  if (group.target.id && choice.ticked.has(group.target.id)) return `id:${group.target.id}`;
  return options[0].value;
}

function mergeReady(group, choice) {
  if (choice.ticked.size >= 2) return true;
  const target = mergeTarget(group, choice);
  return choice.ticked.size === 1 && (target.startsWith("new:") || target === "custom");
}

function mergeGroupPayload(group, choice) {
  const target = mergeTarget(group, choice);
  return {members: [...choice.ticked], target: target === "custom" ? {name: choice.custom.trim()}
    : target.startsWith("id:") ? {id: Number(target.slice(3))} : {name: target.slice(4)}};
}

function visibleMergeGroups() {
  const {groups, filter, query} = artistCleanupView.merge;
  const q = query.trim().toLocaleLowerCase();
  return (groups || []).filter(g => (filter === "all" || (filter === "check" ? g.members.some(m => !m.ticked) : g.kind === filter))
    && (!q || g.members.some(m => m.name.toLocaleLowerCase().includes(q))));
}

function renderMergeTab(disabled) {
  const part = artistCleanupView.merge;
  if (part.loading) return '<p class="muted" role="status">Looking for duplicate artists…</p>';
  if (!part.groups) return "";
  const shown = visibleMergeGroups(), page = shown.slice(part.page * artistCleanupPage, (part.page + 1) * artistCleanupPage);
  const included = part.groups.filter(g => part.choices.get(g.id)?.include);
  const kinds = [["all", "All"], ["same", "Case or spaces"], ["script", "Simplified / traditional"], ["bilingual", "English + Chinese"], ["check", "Needs a look"]];
  return `<p class="muted">Each card is one artist that appears as several entries. Ticked entries are merged into the name chosen on the right. Unticked entries stay separate; they are usually matches in a different genre or very short names.</p>
    <div class="cleanup-controls"><div class="field clearable-field"><label for="ac-merge-search">Filter by name</label>${clearableSearchInput("ac-merge-search", "", part.query, disabled)}</div>
    <label class="field">Show<select id="ac-merge-filter" ${disabled ? "disabled" : ""}>${kinds.map(([k, l]) => `<option value="${k}" ${k === part.filter ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    <div class="cleanup-controls"><button class="text-action" data-ac-merge-all="1">Include all shown</button><button class="text-action" data-ac-merge-all="0">Exclude all shown</button><span role="status">${included.length.toLocaleString("en")} of ${part.groups.length.toLocaleString("en")} artists included · ${shown.length.toLocaleString("en")} shown</span></div>
    <div class="ac-cards">${page.map(group => mergeCard(group, disabled)).join("") || '<p class="muted">No duplicates found.</p>'}</div>
    ${artistCleanupPager("merge", shown.length)}
    <div class="dialog-footer"><span class="muted">Preparing a preview writes nothing.</span><button class="button primary" id="ac-merge-preview" ${disabled || !included.length ? "disabled" : ""}>Preview ${included.length.toLocaleString("en")} merges</button></div>`;
}

function mergeCard(group, disabled) {
  const choice = artistCleanupView.merge.choices.get(group.id), target = mergeTarget(group, choice);
  const kindLabel = {same: "Case or spaces", script: "Simplified / traditional", bilingual: "English + Chinese"}[group.kind] || group.kind;
  return `<section class="ac-card${choice.include ? "" : " ac-off"}" data-ac-group="${escapeHtml(group.id)}">
    <div class="ac-card-head"><label class="checkline"><input type="checkbox" data-ac-include ${choice.include ? "checked" : ""} ${disabled ? "disabled" : ""}> <strong>${escapeHtml(mergeTargetLabel(group, choice, target))}</strong></label><span class="pill">${escapeHtml(kindLabel)}</span></div>
    <ul class="ac-members">${group.members.map(m => `<li><label class="checkline"><input type="checkbox" data-ac-member="${m.id}" ${choice.ticked.has(m.id) ? "checked" : ""} ${disabled ? "disabled" : ""}> <span><span class="ac-name">${escapeHtml(m.name)}</span><span class="ac-meta">${m.tracks.toLocaleString("en")} ${m.tracks === 1 ? "track" : "tracks"} · ${escapeHtml(m.reason)}</span></span></label></li>`).join("")}</ul>
    <label class="field">Keep as<select data-ac-target ${disabled ? "disabled" : ""}>${mergeTargetOptions(group, choice).map(o => `<option value="${escapeHtml(o.value)}" ${o.value === target ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></label>
    ${target === "custom" ? `<label class="field">New name<input data-ac-custom maxlength="1000" value="${escapeHtml(choice.custom)}" ${disabled ? "disabled" : ""}></label>` : ""}
  </section>`;
}

function mergeTargetLabel(group, choice, target) {
  if (target === "custom") return choice.custom.trim() || "Type a name";
  if (target.startsWith("new:")) return target.slice(4);
  return group.members.find(m => `id:${m.id}` === target)?.name || group.target_name;
}

// -- combined names -----------------------------------------------------------

function visibleSplitItems() {
  const {items, filter, query, chosen} = artistCleanupView.split, q = query.trim().toLocaleLowerCase();
  const shown = {all: () => true, ticked: i => i.ticked, other: i => !i.ticked, chosen: i => chosen.has(i.id)}[filter];
  return (items || []).filter(i => shown(i) && (!q || i.name.toLocaleLowerCase().includes(q)));
}

function renderSplitTab(disabled) {
  const part = artistCleanupView.split;
  if (part.loading) return '<p class="muted" role="status">Looking for combined names…</p>';
  if (!part.items) return "";
  const shown = visibleSplitItems(), page = shown.slice(part.page * artistCleanupPage, (part.page + 1) * artistCleanupPage);
  const filters = [["ticked", "Suggested (/ ; 、)"], ["other", "Other separators (& , x vs and)"], ["chosen", "Chosen"], ["all", "All"]];
  return `<p class="muted">The first name becomes the primary artist; the others become additional artists, so the album appears under each of them. Names marked <span class="pill">existing</span> join that artist's entry. Separators like &amp; are only suggestions: many acts are one name, such as Simon &amp; Garfunkel.</p>
    <div class="cleanup-controls"><div class="field clearable-field"><label for="ac-split-search">Filter by name</label>${clearableSearchInput("ac-split-search", "", part.query, disabled)}</div>
    <label class="field">Show<select id="ac-split-filter" ${disabled ? "disabled" : ""}>${filters.map(([k, l]) => `<option value="${k}" ${k === part.filter ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    <div class="cleanup-controls"><button class="text-action" data-ac-split-all="1">Choose all shown</button><button class="text-action" data-ac-split-all="0">Clear all shown</button><span role="status">${part.chosen.size.toLocaleString("en")} chosen · ${shown.length.toLocaleString("en")} shown</span></div>
    <div class="ac-list">${page.map(item => `<label class="ac-row checkline"><input type="checkbox" data-ac-split="${item.id}" ${part.chosen.has(item.id) ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <span><span class="ac-name">${escapeHtml(item.name)}</span> <span class="muted">${item.tracks.toLocaleString("en")} tracks</span><br>→ ${item.resolved.map((r, n) => `${n ? " + " : ""}${escapeHtml(r.as)} <span class="pill${r.existing ? "" : " missing"}">${r.existing ? "existing" : "new"}</span>`).join("")}${item.note ? `<br><span class="ac-note">${escapeHtml(item.note)}</span>` : ""}</span></label>`).join("") || '<p class="muted">No combined names here.</p>'}</div>
    ${artistCleanupPager("split", shown.length)}
    <div class="dialog-footer"><span class="muted">Preparing a preview writes nothing.</span><button class="button primary" id="ac-split-preview" ${disabled || !part.chosen.size ? "disabled" : ""}>Preview ${part.chosen.size.toLocaleString("en")} splits</button></div>`;
}

// -- featured in titles -------------------------------------------------------

function renderFeatTab(disabled) {
  const part = artistCleanupView.feat;
  if (part.loading) return '<p class="muted" role="status">Reading track titles…</p>';
  if (!part.data) return "";
  const data = part.data;
  return `<p class="muted">Guests named in titles, such as “(feat. Lupe Fiasco)”, “[ft. A &amp; B]” or “(with JID)”, become additional track artists, so the song also appears under each guest. Titles stay as they are. Names are matched to existing entries first, including English + Chinese entries in the same genre. Run <strong>Duplicates</strong> first so guests join the merged entries, and check <strong>Duplicates</strong> again afterwards: guests spelled differently in titles (<em>NINEONE</em>, <em>乃万NINEONE</em>) show up there.</p>
    <div class="notice">${data.tracks.toLocaleString("en")} tracks name ${data.guests.toLocaleString("en")} different guests · ${data.new_guests.length.toLocaleString("en")} of them are new artist entries.</div>
    <details class="ac-details"><summary>New artist entries (${data.new_guests.length.toLocaleString("en")})</summary><p class="ac-names">${data.new_guests.map(escapeHtml).join(" · ") || "None"}</p></details>
    <details class="ac-details"><summary>Titles that could not be read (${data.problem_count.toLocaleString("en")})</summary><ul class="ac-problems">${data.problems.map(p => `<li>${escapeHtml(p.title)} <span class="muted">· ${escapeHtml(p.artist)} · ${escapeHtml(p.problem)}</span></li>`).join("") || "<li>None</li>"}</ul><p class="muted">These are left unchanged. Fix their titles in the album editor if you like.</p></details>
    <div class="dialog-footer"><span class="muted">Preparing a preview writes nothing.</span><button class="button primary" id="ac-feat-preview" ${disabled || !data.tracks ? "disabled" : ""}>Preview featured artists</button></div>`;
}

// -- search & merge -----------------------------------------------------------

function renderSearchTab(disabled) {
  const part = artistCleanupView.search, picked = [...part.picked.entries()];
  const keep = part.keep && (part.keep === "custom" || part.picked.has(Number(part.keep))) ? part.keep : picked.length ? String(picked.slice().sort((a, b) => b[1].tracks - a[1].tracks)[0][0]) : "";
  return `<p class="muted">Find entries by name, add the ones that are the same artist, then choose which name to keep.</p>
    <div class="cleanup-controls"><div class="field clearable-field"><label for="ac-search-input">Search artists</label>${clearableSearchInput("ac-search-input", "", part.query, disabled)}</div><button class="button small" id="ac-search-find" ${disabled || part.loading ? "disabled" : ""}>${part.loading ? "Searching…" : "Search"}</button></div>
    <ul class="ac-list">${part.results.map(a => `<li class="ac-row"><span><span class="ac-name">${escapeHtml(a.name)}</span> <span class="muted">${a.tracks.toLocaleString("en")} tracks${a.album_artist ? " · album artist" : ""}</span></span><button class="button small" data-ac-pick="${a.id}" ${disabled || part.picked.has(a.id) ? "disabled" : ""}>${part.picked.has(a.id) ? "Added" : "Add"}</button></li>`).join("")}</ul>
    <section class="ac-card"><h3>Merge these entries</h3>${picked.length ? `<fieldset class="ac-keep"><legend class="muted">Keep</legend>${picked.map(([id, a]) => `<label class="checkline"><input type="radio" name="ac-keep" value="${id}" ${keep === String(id) ? "checked" : ""} ${disabled ? "disabled" : ""}> ${escapeHtml(a.name)} <span class="muted">${a.tracks.toLocaleString("en")} tracks</span> <button class="text-action" data-ac-unpick="${id}" ${disabled ? "disabled" : ""}>Remove</button></label>`).join("")}
      <label class="checkline"><input type="radio" name="ac-keep" value="custom" ${keep === "custom" ? "checked" : ""} ${disabled ? "disabled" : ""}> Another name: <input id="ac-search-custom" maxlength="1000" value="${escapeHtml(part.custom)}" ${disabled ? "disabled" : ""}></label></fieldset>` : '<p class="muted">Nothing added yet.</p>'}</section>
    <div class="dialog-footer"><span class="muted">Preparing a preview writes nothing.</span><button class="button primary" id="ac-search-preview" ${disabled || picked.length < (keep === "custom" ? 1 : 2) || (keep === "custom" && !part.custom.trim()) ? "disabled" : ""}>Preview this merge</button></div>`;
}

// -- review & run -------------------------------------------------------------

function renderReviewTab() {
  const view = artistCleanupView, data = view.data || {state: "empty"};
  if (!data.id) return '<p class="muted">No preview yet. Prepare one from the other tabs.</p>';
  const preview = data.state === "preview", running = data.state === "running", counts = data.counts || {};
  const selected = (data.selection || []).filter(g => !view.excluded.has(g.id));
  return `<div class="notice">${escapeHtml(artistCleanupModes[data.mode] || data.mode)}: ${data.groups_total.toLocaleString("en")} ${data.groups_total === 1 ? "group" : "groups"} · ${data.records.toLocaleString("en")} track and album records. Batches hold up to ${data.batch_tracks} tracks from ${data.batch_groups} groups and are checked after saving.</div>
    ${data.new_artists.length ? `<details class="ac-details"><summary>New artist entries to create (${data.new_artists.length.toLocaleString("en")}${data.created ? ` · ${data.created.toLocaleString("en")} created` : ""})</summary><p class="ac-names">${data.new_artists.map(escapeHtml).join(" · ")}</p></details>` : ""}
    ${preview ? `<div class="cleanup-controls"><button class="text-action" id="ac-review-all">Select all</button><button class="text-action" id="ac-review-none">Exclude all</button><span role="status">${selected.length.toLocaleString("en")} groups · ${selected.reduce((n, g) => n + g.tracks, 0).toLocaleString("en")} tracks selected</span></div>`
      : `<p role="status" class="operation-status" data-working="${running}"><strong>${escapeHtml(data.state)}</strong> · ${counts.confirmed || 0} confirmed · ${counts.pending || 0} pending · ${counts.sent || 0} checking · ${counts.unconfirmed || 0} unconfirmed · ${counts.conflict || 0} conflicts · ${counts.blocked || 0} blocked · ${counts.excluded || 0} excluded</p>`}
    ${data.error ? `<p class="warn-box" role="status">${escapeHtml(data.error)}</p>` : ""}
    <div class="cleanup-albums">${data.groups.map(g => `<section class="cleanup-album"><h3>${preview ? `<label><input type="checkbox" data-ac-review="${escapeHtml(g.id)}" ${view.excluded.has(g.id) ? "" : "checked"}> ` : ""}${escapeHtml(g.title)}${preview ? "</label>" : ""}</h3><p class="muted">${g.records.toLocaleString("en")} records · ${g.tracks.toLocaleString("en")} tracks${preview ? "" : ` · ${Object.entries(g.statuses).map(([k, n]) => `${n} ${k}`).join(" · ")}`}</p>${g.examples.map(artistCleanupExample).join("")}</section>`).join("") || '<p class="muted">Nothing would change.</p>'}</div>
    ${artistCleanupPager("review", data.groups_total)}
    <p class="muted">The draft and report are saved on this computer. Stop prevents further writes; saved changes remain. Resume checks outstanding writes first. A new preview replaces this report, so export it first if you want to keep it.</p>
    ${preview && data.records ? '<label class="checkline"><input id="ac-approved" type="checkbox"> I reviewed this draft and approve changing the selected groups in iBroadcast.</label>' : ""}
    <div class="dialog-footer"><button class="button" id="ac-export">Export full report ↓</button>${running ? '<button class="button danger" id="ac-stop">Stop after current request</button>'
      : data.records && data.state !== "done" ? `<button class="button primary" id="ac-start" disabled>${preview ? "Start selected groups" : "Resume after checking"}</button>` : ""}<button class="button" id="ac-refresh">${running ? "Refresh progress" : "Refresh library"}</button></div>`;
}

function artistCleanupExample(example) {
  return `<div class="cleanup-example"><strong>${escapeHtml(example.title)}</strong>${example.kind === "track" ? ` <span class="muted">· ${escapeHtml(example.album)}</span>` : ""}<div><span>Before</span><span>${escapeHtml(example.before.join(" · "))}</span></div>${example.blocked
    ? `<p class="error-text">Blocked: ${escapeHtml(example.blocked)}</p>` : `<div><span>After</span><span>${escapeHtml(example.after.join(" · "))}</span></div>`}</div>`;
}

function artistCleanupPager(name, total) {
  const view = artistCleanupView, page = name === "review" ? view.offset / artistCleanupPage : view[name].page;
  if (total <= artistCleanupPage) return "";
  const pages = Math.ceil(total / artistCleanupPage);
  return `<div class="pagination"><button class="button small" data-ac-page="${name}" data-ac-step="-1" ${page <= 0 ? "disabled" : ""}>← Previous</button><span>${page + 1} / ${pages}</span><button class="button small" data-ac-page="${name}" data-ac-step="1" ${page + 1 >= pages ? "disabled" : ""}>Next →</button></div>`;
}

function renderArtistCleanup() {
  const view = artistCleanupView, dialog = $("#artist-cleanup");
  if (!dialog) return;
  const running = view.data?.state === "running", disabled = view.busy || running, scroll = dialog.scrollTop;
  const body = {merge: renderMergeTab, split: renderSplitTab, feat: renderFeatTab, search: renderSearchTab, review: renderReviewTab}[view.tab](disabled);
  $("#artist-cleanup-content").innerHTML = `<div class="favourites-tabs ac-tabs" role="tablist" aria-label="Artist cleanup steps">${artistCleanupTabs.map(([key, label]) => `<button type="button" role="tab" data-ac-tab="${key}" aria-selected="${key === view.tab}" tabindex="${key === view.tab ? 0 : -1}">${label}${key === "review" && view.data?.id ? ` <span>${escapeHtml(view.data.state)}</span>` : ""}</button>`).join("")}</div>
    ${running && view.tab !== "review" ? '<p class="warn-box">A cleanup is running. Open Review &amp; run to follow it; new previews wait until it stops.</p>' : ""}
    <div role="tabpanel">${body}</div>`;
  dialog.scrollTop = scroll;
  updateArtistCleanupStart();
}

function updateArtistCleanupStart() {
  const start = $("#ac-start"), data = artistCleanupView.data;
  if (!start || !data) return;
  const selected = (data.selection || []).filter(g => !artistCleanupView.excluded.has(g.id));
  start.disabled = artistCleanupView.busy || (data.state === "preview" && (!selected.length || !$("#ac-approved")?.checked));
}

// -- actions ------------------------------------------------------------------

async function artistCleanupAction(path, body, message) {
  const view = artistCleanupView;
  if (view.busy || !live()) return;
  if (path === "preview" && view.data?.id && view.data.state !== "done" && !confirm("Replace the current artist cleanup draft and report? Export it first if you want to keep a copy.")) return;
  view.busy = true; renderArtistCleanup();
  const request = ++view.request, details = state.details;
  clearTimeout(view.timer);
  setActionStatus("#artist-cleanup-status", message, true);
  try {
    const data = await api(`/api/artist-cleanup/${path}`, body);
    if (request !== view.request || details !== state.details || !live()) return;
    view.data = data; view.offset = 0;
    view.excluded = new Set(path === "preview" ? [] : data.excluded || []);
    view.tab = "review";
    if (data.state === "running") view.timer = setTimeout(fetchArtistCleanupTask, 1500);
    setActionStatus("#artist-cleanup-status", "");
  } catch (error) {
    if (request === view.request) setActionStatus("#artist-cleanup-status", error.message);
  } finally {
    if (request === view.request && details === state.details) { view.busy = false; renderArtistCleanup(); }
  }
}

async function searchArtistsForMerge() {
  const part = artistCleanupView.search;
  part.query = $("#ac-search-input")?.value ?? part.query;
  if (!part.query.trim()) return;
  part.loading = true; renderArtistCleanup();
  try {
    part.results = (await api(`/api/artist-cleanup/search?q=${encodeURIComponent(part.query)}`)).artists;
  } catch (error) { setActionStatus("#artist-cleanup-status", error.message); }
  finally { part.loading = false; renderArtistCleanup(); }
}

async function exportArtistCleanupReport() {
  try {
    const data = await api("/api/artist-cleanup/report");
    const link = document.createElement("a"), url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
    link.href = url; link.download = `tagcast-artist-cleanup-${data.mode}-${data.id}.json`; link.click(); URL.revokeObjectURL(url);
  } catch (error) { setActionStatus("#artist-cleanup-status", error.message); }
}

document.addEventListener("click", event => {
  if (event.target.closest("[data-artist-cleanup-open]")) openArtistCleanup();
});

$("#artist-cleanup").addEventListener("click", async event => {
  const view = artistCleanupView, target = event.target;
  const tab = target.closest("[data-ac-tab]");
  if (tab) { view.tab = tab.dataset.acTab; setActionStatus("#artist-cleanup-status", ""); renderArtistCleanup(); if (view.tab === "review") fetchArtistCleanupTask(); else loadArtistCleanupTab(); return; }
  const pager = target.closest("[data-ac-page]");
  if (pager) {
    const step = Number(pager.dataset.acStep);
    if (pager.dataset.acPage === "review") { view.offset = Math.max(0, view.offset + step * artistCleanupPage); return fetchArtistCleanupTask(); }
    view[pager.dataset.acPage].page = Math.max(0, view[pager.dataset.acPage].page + step);
    renderArtistCleanup(); $("#artist-cleanup").scrollTop = 0; return;
  }
  const mergeAll = target.closest("[data-ac-merge-all]");
  if (mergeAll) { for (const g of visibleMergeGroups()) { const c = view.merge.choices.get(g.id); c.include = mergeAll.dataset.acMergeAll === "1" && mergeReady(g, c); } return renderArtistCleanup(); }
  const splitAll = target.closest("[data-ac-split-all]");
  if (splitAll) { for (const i of visibleSplitItems()) splitAll.dataset.acSplitAll === "1" ? view.split.chosen.add(i.id) : view.split.chosen.delete(i.id); return renderArtistCleanup(); }
  const pick = target.closest("[data-ac-pick]");
  if (pick) { const a = view.search.results.find(r => r.id === Number(pick.dataset.acPick)); if (a) view.search.picked.set(a.id, a); return renderArtistCleanup(); }
  const unpick = target.closest("[data-ac-unpick]");
  if (unpick) { event.preventDefault(); view.search.picked.delete(Number(unpick.dataset.acUnpick)); return renderArtistCleanup(); }
  if (target.closest("#ac-search-find")) return searchArtistsForMerge();
  if (target.closest("#ac-merge-preview")) {
    const groups = view.merge.groups.filter(g => view.merge.choices.get(g.id).include).map(g => mergeGroupPayload(g, view.merge.choices.get(g.id)));
    if (groups.some(g => "name" in g.target && !g.target.name)) { setActionStatus("#artist-cleanup-status", "Type a name for every group set to “Type another name…”."); return; }
    return artistCleanupAction("preview", {mode: "merge", groups}, "Preparing a read-only preview…");
  }
  if (target.closest("#ac-split-preview")) return artistCleanupAction("preview", {mode: "split", ids: [...view.split.chosen]}, "Preparing a read-only preview…");
  if (target.closest("#ac-feat-preview")) return artistCleanupAction("preview", {mode: "feat"}, "Preparing a read-only preview…");
  if (target.closest("#ac-search-preview")) {
    const part = view.search, keep = document.querySelector('input[name="ac-keep"]:checked')?.value;
    const members = [...part.picked.keys()];
    return artistCleanupAction("preview", {mode: "merge", groups: [{members, target: keep === "custom" ? {name: part.custom.trim()} : {id: Number(keep)}}]}, "Preparing a read-only preview…");
  }
  if (target.closest("#ac-review-all, #ac-review-none")) {
    view.excluded = new Set(target.closest("#ac-review-none") ? (view.data.selection || []).map(g => g.id) : []);
    return renderArtistCleanup();
  }
  if (target.closest("#ac-start")) return artistCleanupAction("start", {id: view.data.id, excluded: [...view.excluded]}, "Checking your library and starting…");
  if (target.closest("#ac-stop")) return artistCleanupAction("stop", {id: view.data.id}, "Stopping after the current request…");
  if (target.closest("#ac-export")) return exportArtistCleanupReport();
  if (target.closest("#ac-refresh")) {
    if (view.data?.state === "running") return fetchArtistCleanupTask();
    const tab = view.tab;
    await loadLive();
    if (!$("#artist-cleanup").open) $("#artist-cleanup").showModal();
    view.tab = tab;
    await fetchArtistCleanupTask(); loadArtistCleanupTab();
  }
});

$("#artist-cleanup").addEventListener("change", event => {
  const view = artistCleanupView, target = event.target, card = target.closest("[data-ac-group]");
  if (card) {
    const choice = view.merge.choices.get(card.dataset.acGroup);
    const group = view.merge.groups.find(g => g.id === card.dataset.acGroup);
    if (target.matches("[data-ac-include]")) {
      // Switching a card on with fewer than two entries ticked ticks all of them.
      if (target.checked && !mergeReady(group, choice)) for (const m of group.members) choice.ticked.add(m.id);
      choice.include = target.checked;
    } else if (target.dataset.acMember) {
      const id = Number(target.dataset.acMember);
      if (target.checked) choice.ticked.add(id); else choice.ticked.delete(id);
      if (target.checked && choice.ticked.size >= 2) choice.include = true;
    } else if (target.matches("[data-ac-target]")) choice.target = target.value;
    if (!mergeReady(group, choice)) choice.include = false;
    return renderArtistCleanup();
  }
  if (target.id === "ac-merge-filter") { view.merge.filter = target.value; view.merge.page = 0; return renderArtistCleanup(); }
  if (target.id === "ac-split-filter") { view.split.filter = target.value; view.split.page = 0; return renderArtistCleanup(); }
  if (target.dataset.acSplit) { const id = Number(target.dataset.acSplit); if (target.checked) view.split.chosen.add(id); else view.split.chosen.delete(id); return renderArtistCleanup(); }
  if (target.name === "ac-keep") { view.search.keep = target.value; return renderArtistCleanup(); }
  if (target.dataset.acReview) { if (target.checked) view.excluded.delete(target.dataset.acReview); else view.excluded.add(target.dataset.acReview); return renderArtistCleanup(); }
  if (target.id === "ac-approved") updateArtistCleanupStart();
});

$("#artist-cleanup").addEventListener("input", event => {
  const view = artistCleanupView, target = event.target;
  if (target.matches("[data-ac-custom]")) { view.merge.choices.get(target.closest("[data-ac-group]").dataset.acGroup).custom = target.value; return; }
  if (target.id === "ac-search-custom") {
    view.search.custom = target.value;
    const button = $("#ac-search-preview");
    if (button && view.search.keep === "custom") button.disabled = !target.value.trim();
    return;
  }
  if (target.id === "ac-search-input") { view.search.query = target.value; return; }
  if (target.id === "ac-merge-search" || target.id === "ac-split-search") {
    const part = view[target.id === "ac-merge-search" ? "merge" : "split"];
    part.query = target.value; part.page = 0;
    clearTimeout(view.searchTimer);
    view.searchTimer = setTimeout(() => {
      const input = $(`#${target.id}`), at = input?.selectionStart;
      renderArtistCleanup();
      const again = $(`#${target.id}`); if (again) { again.focus(); again.setSelectionRange(at, at); }
    }, 250);
  }
});

$("#artist-cleanup").addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.id === "ac-search-input") { event.preventDefault(); searchArtistsForMerge(); }
});
$("#artist-cleanup").addEventListener("close", () => clearTimeout(artistCleanupView.timer));
// Closing during a run is safe (the server stops between batches), but ask first.
window.addEventListener("beforeunload", event => {
  if (artistCleanupView.data?.state === "running") { event.preventDefault(); event.returnValue = ""; }
});
