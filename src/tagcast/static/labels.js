"use strict";

// Labels (fork v64): a record label's catalogue with what you are missing marked. A label name is
// looked up on MusicBrainz; a Bandcamp label link lists that page's releases. ⬇ is one click per
// release; "Download all missing" takes every release you don't have.
const labelsView = {query: "", singles: false, data: null, busy: false, error: "", got: new Set()};

function labelsMissing(r) { return r.status !== "have" && !labelsView.got.has(r.id); }

function labelsHtml() {
  const v = labelsView, d = v.data;
  const rows = d?.rows || [], missing = rows.filter(labelsMissing);
  const badge = r => labelsView.got.has(r.id) ? ["have", "Queued"] : r.status === "have" ? ["have", "Have it"]
    : r.status === "partly" ? ["partly", "Partly"] : r.status === "downloaded" ? ["downloaded", "Downloaded"] : ["new", "New"];
  return `<p class="muted">Type a label name (Rhymesayers, Stones Throw, Def Jux…) or paste a label's Bandcamp link. Tagcast lists its releases and marks what you are missing.</p>
    <form class="dl-actions" data-labels-form><input id="labels-query" type="search" class="dl-search" maxlength="300" value="${escapeHtml(v.query)}" placeholder="Label name or https://label.bandcamp.com" aria-label="Label">
      <label class="checkline"><input type="checkbox" data-labels-singles ${v.singles ? "checked" : ""}> Include singles</label>
      <button type="submit" class="button primary" ${v.busy ? "disabled" : ""}>${v.busy ? "Reading…" : "Show catalogue"}</button></form>
    ${v.error ? `<p class="dl-have">${escapeHtml(v.error)}</p>` : ""}
    ${d ? `<div class="dl-actions"><strong>${escapeHtml(d.label)}</strong> <a class="muted" href="${escapeHtml(d.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(d.where)} ↗</a>
        <span class="muted">${rows.length} releases · ${missing.length} missing</span>
        <button type="button" class="button primary" data-labels-get-all ${missing.length && !v.busy ? "" : "disabled"}>⬇ Download all missing (${missing.length})</button></div>
      ${d.others?.length ? `<p class="muted">Not this label? ${d.others.map(o => `<button type="button" class="text-action" data-labels-pick="${escapeHtml(o.id)}">${escapeHtml(o.name)}${o.note ? ` (${escapeHtml(o.note)})` : ""}</button>`).join(" · ")}</p>` : ""}
      ${d.where === "MusicBrainz" ? '<p class="muted">MusicBrainz releases download through YouTube → Galaxy → Bandcamp → Soulseek; anything not found goes on the wishlist.</p>' : ""}
      <ul class="dl-list">${rows.map(r => { const [cls, text] = badge(r); return `<li><span class="dl-badge ${cls}">${text}</span>
        <div><b>${escapeHtml(r.title)}</b> <span class="muted">· ${escapeHtml(r.artist)}${r.year ? ` · ${escapeHtml(r.year)}` : ""}${r.type && r.type !== "album" ? ` · ${escapeHtml(r.type.toUpperCase())}` : ""}</span></div>
        ${r.status === "have" ? "" : `<button type="button" class="button small" data-labels-get="${escapeHtml(r.id)}" ${labelsView.got.has(r.id) || v.busy ? "disabled" : ""}>⬇ Download</button>`}</li>`; }).join("")}</ul>` : ""}`;
}

async function labelsLoad(labelId = "") {
  const v = labelsView;
  v.busy = true; v.error = ""; renderDownloads();
  try { v.data = await api("/api/labels/view", {query: v.query, label_id: labelId, singles: v.singles}); v.got = new Set(); }
  catch (error) { v.error = error.message || String(error); v.data = null; }
  v.busy = false; renderDownloads();
}

async function labelsGet(rows) {
  const v = labelsView;
  rows.forEach(r => v.got.add(r.id)); renderDownloads();
  try {
    const result = await api("/api/labels/get", {rows: rows.map(r => ({title: r.title, artist: r.artist, url: r.url, status: r.status}))});
    const parts = [result.queued ? `${result.queued} songs queued` : "", result.jobs ? `${result.jobs} albums being looked up` : ""].filter(Boolean);
    toast(`${parts.join(", ") || "Nothing queued"}${result.failed?.length ? ` · ${result.failed.length} failed` : ""} — see Downloads › Queue.`);
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { rows.forEach(r => v.got.delete(r.id)); v.error = error.message || String(error); }
  renderDownloads();
}

document.addEventListener("submit", event => {
  if (!event.target.closest?.("[data-labels-form]")) return;
  event.preventDefault();
  labelsView.query = $("#labels-query")?.value.trim() || "";
  if (labelsView.query) labelsLoad();
});

document.addEventListener("change", event => {
  if (event.target.closest?.("[data-labels-singles]")) labelsView.singles = event.target.checked;
});

document.addEventListener("click", event => {
  const v = labelsView, rows = v.data?.rows || [];
  const pick = event.target.closest?.("[data-labels-pick]");
  if (pick) { labelsLoad(pick.dataset.labelsPick); return; }
  const one = event.target.closest?.("[data-labels-get]");
  if (one) { const r = rows.find(x => x.id === one.dataset.labelsGet); if (r) labelsGet([r]); return; }
  if (event.target.closest?.("[data-labels-get-all]")) { const todo = rows.filter(labelsMissing).slice(0, 200); if (todo.length) labelsGet(todo); }
});
