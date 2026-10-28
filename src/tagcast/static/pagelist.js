"use strict";

// "From a page" on the Downloads page (fork v45): give Tagcast a link (or pasted text) and it
// lists every album on that page, says Have / Partly / New for each, and downloads the ticked
// ones through Bandcamp → Galaxy → Soulseek. Albums it isn't sure about can be edited and checked again.
const pagelistView = {link: "", text: "", textOpen: false, job: null, picked: new Set(), editing: null, busy: false, timer: null, getJobs: [], getIds: [], getCount: 0, touched: new Set(), autoTicked: false, getTimer: null, error: ""};

function pagelistHtml() {
  const v = pagelistView, job = v.job;
  const items = job?.items || [];
  const counts = {new: 0, have: 0, partly: 0, other: 0, unsure: 0};
  for (const i of items) {
    if (i.state === "unsure") counts.unsure++;
    if (i.status === "new") counts.new++; else if (i.status === "have") counts.have++; else if (i.status === "partly") counts.partly++; else if (i.status) counts.other++;
  }
  return `<p class="muted">Paste the link of a page with a list of albums — a "best of 2024" article, a Reddit thread, a Wikipedia list, a public Spotify playlist. Tagcast finds every artist and album on it and tells you which ones you don't have.</p>
    <div class="dl-actions"><input id="pagelist-link" type="url" class="grow" maxlength="2000" placeholder="https://…" value="${escapeHtml(v.link)}" aria-label="Page link">
      <button type="button" class="button primary" data-pagelist-read ${v.busy ? "disabled" : ""}>Read the page</button></div>
    <details ${v.textOpen ? "open" : ""} data-pagelist-text-box><summary>Paste the text instead</summary>
      <p class="muted">For sites that block Tagcast: select the list on the page, copy it, and paste it here.</p>
      <textarea id="pagelist-text" class="dl-lines" rows="6" maxlength="200000" aria-label="Page text">${escapeHtml(v.text)}</textarea></details>
    ${v.error ? `<p class="dl-error" role="alert">${escapeHtml(v.error)}</p>` : ""}
    ${job ? `<p class="dl-summary" role="status">${job.state === "running" ? `Checking ${job.done} of ${job.total} online… ` : ""}
      <span class="dl-badge new">${counts.new} New</span> <span class="dl-badge partly">${counts.partly} Partly</span> <span class="dl-badge have">${counts.have + counts.other} Have</span>
      ${counts.unsure ? `<span class="dl-badge">${counts.unsure} Not sure</span>` : ""}</p>
      <div class="dl-actions"><button type="button" class="button" data-pagelist-tick-new>Tick all New</button>
        <button type="button" class="button primary" data-pagelist-get ${v.picked.size && !v.busy ? "" : "disabled"}>Download chosen albums (${v.picked.size})</button></div>
      <ul class="dl-list">${items.map(pagelistRowHtml).join("")}</ul>` : ""}`;
}

function pagelistRowHtml(i) {
  const v = pagelistView, label = downloadsStatusLabels[i.status] || (i.state === "checking" ? "Checking…" : "");
  const badge = i.state === "checking" ? `<span class="dl-badge">Checking…</span>` : `<span class="dl-badge ${i.status === "new" ? "new" : i.status === "partly" ? "partly" : "have"}">${escapeHtml(label)}</span>`;
  const edit = v.editing === i.id;
  return `<li>${badge}
    <label><input type="checkbox" data-pagelist-pick="${escapeHtml(i.id)}" ${v.picked.has(i.id) ? "checked" : ""} ${i.album ? "" : "disabled"} aria-label="Choose ${escapeHtml(i.album || i.raw)}"></label>
    ${i.cover ? `<img class="dl-cover" src="${escapeHtml(i.cover)}" alt="" width="48" height="48" loading="lazy">` : ""}
    <div><div>${i.album ? `<b>${escapeHtml(i.album)}</b> · ${escapeHtml(i.artist)}${i.year ? ` · ${escapeHtml(i.year)}` : ""}` : `<i>${escapeHtml(i.raw)}</i>`}
      ${i.state === "unsure" ? ` <span class="dl-badge" title="No online source knew this exact album">Not sure</span> <button type="button" class="button" data-pagelist-edit="${escapeHtml(i.id)}">Edit</button>` : ""}</div>
      ${i.status && i.status !== "new" ? `<div class="dl-have">${downloadsYouHave(i.you_have)}</div>` : ""}
      ${edit ? `<div class="dl-actions" data-pagelist-edit-row><input data-pagelist-artist maxlength="200" value="${escapeHtml(i.artist)}" aria-label="Artist"> <input data-pagelist-album maxlength="200" value="${escapeHtml(i.album)}" aria-label="Album">
        <button type="button" class="button" data-pagelist-recheck="${escapeHtml(i.id)}">Check again</button></div>` : ""}</div></li>`;
}

function pagelistRender(scroll = false) {
  if (downloadsView.tab !== "pagelist") return;
  renderDownloads();
  if (scroll) $("#pagelist-progress")?.scrollIntoView?.({block: "center"});
}

function pagelistPoll() {
  clearTimeout(pagelistView.timer);
  pagelistView.timer = setTimeout(async () => {
    const v = pagelistView;
    if (!v.job) return;
    try { v.job = await api(`/api/pagelist/status?id=${encodeURIComponent(v.job.id)}`); } catch { return; }
    if (v.job.state === "done" && !v.autoTicked) {
      v.autoTicked = true;   // the sure-and-new ones start ticked; Not sure ones never do
      for (const i of v.job.items) if (i.status === "new" && i.state === "ok" && !v.touched.has(i.id)) v.picked.add(i.id);
    }
    // Keep what the person is typing in an open edit row.
    if (!document.activeElement?.closest?.("[data-pagelist-edit-row]")) pagelistRender();
    if (v.job.state !== "done") pagelistPoll();
  }, 1200);
}

document.addEventListener("click", async event => {
  const target = event.target;
  if (!target.closest?.("#downloads-page") || downloadsView.tab !== "pagelist") return;
  const v = pagelistView;
  if (target.closest("[data-pagelist-read]")) {
    v.link = $("#pagelist-link")?.value || v.link; v.text = $("#pagelist-text")?.value || v.text; v.error = "";
    if (!v.link.trim() && !v.text.trim()) { toast("Paste a page link, or the text of the page."); return; }
    v.busy = true; v.picked = new Set(); v.touched = new Set(); v.autoTicked = false; v.editing = null; v.getJobs = []; pagelistRender();
    try {
      v.job = await api("/api/pagelist/read", {link: v.text.trim() ? "" : v.link, text: v.text});
      pagelistPoll();
    } catch (error) { v.error = error.message; v.job = null; if (/Paste the text instead/i.test(error.message)) v.textOpen = true; }
    v.busy = false; pagelistRender();
    return;
  }
  if (target.closest("[data-pagelist-tick-new]")) {
    v.picked = new Set((v.job?.items || []).filter(i => i.status === "new" && i.album).map(i => i.id)); pagelistRender(); return;
  }
  const edit = target.closest("[data-pagelist-edit]");
  if (edit) { v.editing = v.editing === edit.dataset.pagelistEdit ? null : edit.dataset.pagelistEdit; pagelistRender(); return; }
  const recheck = target.closest("[data-pagelist-recheck]");
  if (recheck) {
    const row = recheck.closest("li");
    try {
      const item = await api("/api/pagelist/recheck", {id: v.job.id, item: recheck.dataset.pagelistRecheck,
        artist: row.querySelector("[data-pagelist-artist]").value, album: row.querySelector("[data-pagelist-album]").value});
      v.job.items = v.job.items.map(i => i.id === item.id ? item : i); v.editing = null;
    } catch (error) { toast(error.message); }
    pagelistRender(); return;
  }
  if (target.closest("[data-pagelist-get]")) {
    v.busy = true; pagelistRender();
    try {
      const data = await api("/api/pagelist/get", {id: v.job.id, ids: [...v.picked]});
      toast(`Getting ${data.queued} ${data.queued === 1 ? "album" : "albums"}: follow it in Queue & history.`);
      v.busy = false; downloadsView.tab = "queue"; showDownloads("queue"); return;
    } catch (error) { toast(error.message); }
    v.busy = false; pagelistRender(true);
  }
});

document.addEventListener("change", event => {
  const pick = event.target.closest?.("[data-pagelist-pick]");
  if (!pick) return;
  const v = pagelistView, id = pick.dataset.pagelistPick;
  v.touched.add(id);
  if (pick.checked) v.picked.add(id); else v.picked.delete(id);
  pagelistRender();
});

document.addEventListener("input", event => {
  if (event.target.id === "pagelist-link") pagelistView.link = event.target.value;
  if (event.target.id === "pagelist-text") pagelistView.text = event.target.value;
});

document.addEventListener("toggle", event => {
  if (event.target.matches?.("[data-pagelist-text-box]")) pagelistView.textOpen = event.target.open;
}, true);
