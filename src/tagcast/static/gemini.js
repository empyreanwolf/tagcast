"use strict";

// Ask Gemini (fork v21): a chat page backed by the person's own free Gemini API key
// (aistudio.google.com/apikey) -- not the gemini.google.com consumer chat, which has no
// API a background app can call. Songs Gemini names are checked against the library and
// download history, same as Charts; "Get it" runs them through Bandcamp -> Soulseek ->
// Galaxy one at a time, same as Discography's "Get the missing".
const geminiView = {data: null, picked: new Set(), jobs: null, busy: false, jobTimer: null, settingsOpen: false};

function showGemini() {
  showScreen("gemini");
  renderGemini();
  geminiLoad();
  geminiJobsLoad();
}

async function geminiLoad() {
  try { geminiView.data = await api("/api/gemini"); } catch (error) { setActionStatus("#gemini-status", error.message); }
  renderGemini();
}

async function geminiJobsLoad() {
  try { geminiView.jobs = await api("/api/gemini/jobs"); } catch (error) { /* quietly skip */ }
  if (state.screen === "gemini") renderGemini();
  clearTimeout(geminiView.jobTimer);
  if (geminiView.jobs?.progress && state.screen === "gemini") geminiView.jobTimer = setTimeout(geminiJobsLoad, 1500);
}

function renderGemini() {
  const d = geminiView.data;
  const needsKey = d && !d.has_key;
  $("#gemini-setup").hidden = !(needsKey || geminiView.settingsOpen);
  if (needsKey || geminiView.settingsOpen) { $("#gemini-key-input").value = ""; $("#gemini-tavily-input").value = ""; }
  $("#gemini-chat").innerHTML = d ? geminiChatHtml(d) : `<p class="muted">Loading…</p>`;
  const sending = geminiView.busy;
  $("#gemini-send").disabled = sending || needsKey;
  $("#gemini-input").disabled = sending || needsKey;
  const canSearch = d && d.has_tavily_key;
  $("#gemini-search-toggle").disabled = sending || needsKey || !canSearch;
  if (!canSearch) $("#gemini-search-toggle").checked = false;
  $("#gemini-search-label").title = canSearch ? "Search Tavily for this message before asking Gemini"
    : "Add a free Tavily key in Settings to use this";
  $("#gemini-search-label").classList.toggle("muted", !canSearch);
}

function geminiChatHtml(d) {
  if (!d.history.length) return `<p class="muted">Nothing asked yet. Try an artist name, a mood, or "what should I listen to after Clairo".</p>`;
  return `<div class="dl-list gemini-log">${d.history.map(geminiTurnHtml).join("")}${geminiJobsHtml()}</div>`;
}

function geminiTurnHtml(turn) {
  if (turn.role === "user") return `<div class="gemini-turn gemini-user"><b>You${turn.searched ? ` · 🔎 searched the web` : ""}</b><p>${escapeHtml(turn.text)}</p></div>`;
  const songs = turn.songs || [];
  const picked = songs.filter(s => geminiView.picked.has(s.id)).length;
  const sources = turn.sources || [];
  return `<div class="gemini-turn gemini-model"><b>Gemini</b><p>${escapeHtml(turn.text).replace(/\n/g, "<br>")}</p>
    ${songs.length ? `<div class="dl-actions">
        <label><input type="checkbox" data-gemini-all="${songs.map(s => s.id).join(',')}" ${songs.every(s => geminiView.picked.has(s.id)) ? "checked" : ""}> Select all (${songs.length})</label>
        <button type="button" class="button small primary" data-gemini="get" ${picked ? "" : "disabled"}>⬇ Get it (${picked})</button></div>
      <ul class="dl-list">${songs.map(geminiSongHtml).join("")}</ul>` : ""}
    ${(turn.similar_artists || []).length ? `<div class="gemini-similar">${turn.similar_artists.map(a =>
        `<button type="button" class="pill" data-gemini-similar="${escapeHtml(a)}">${escapeHtml(a)}</button>`).join("")}</div>` : ""}
    ${sources.length ? `<div class="gemini-sources">${sources.map(s =>
        `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title)}</a>`).join("")}</div>` : ""}
    </div>`;
}

function geminiSongHtml(s) {
  const status = s.status && s.status !== "new" ? `<span class="pill ${s.status === "have" ? "" : "missing"}">${escapeHtml(downloadsStatusLabels[s.status] || s.status)}</span>` : "";
  return `<li><input type="checkbox" data-gemini-pick="${s.id}" aria-label="Select ${escapeHtml(s.title)}" ${geminiView.picked.has(s.id) ? "checked" : ""}>
      <div>${escapeHtml(s.title)} · ${escapeHtml(s.artist)}${s.album ? ` · <i>${escapeHtml(s.album)}</i>` : ""} ${status}</div></li>`;
}

function geminiJobsHtml() {
  const j = geminiView.jobs;
  if (!j || !j.jobs.length) return "";
  const p = j.progress;
  return `${p ? `<p class="muted" role="status">Getting songs: ${p.done} of ${p.total}${p.title ? ` — ${escapeHtml(p.title)}` : ""}…</p>` : ""}
    <h3>Recently gotten</h3><ul class="dl-list">${j.jobs.slice(0, 20).map(e =>
      `<li><span class="dl-badge ${e.source ? "have" : "partly"}">${escapeHtml(e.source || "Not found")}</span>
         <div><b>${escapeHtml(e.title)}</b> · ${escapeHtml(e.artist)}<br><span class="muted">${escapeHtml(e.note || "")}</span></div></li>`).join("")}</ul>`;
}

async function geminiSend(message, search) {
  if (geminiView.busy || !message.trim()) return;
  geminiView.busy = true;
  renderGemini();
  try {
    geminiView.data = await api("/api/gemini/ask", {message, search: !!search});
    $("#gemini-input").value = "";
    $("#gemini-search-toggle").checked = false;
    renderGemini();
    geminiJobsLoad();
  } catch (error) {
    setActionStatus("#gemini-status", error.message);
  } finally {
    geminiView.busy = false;
    renderGemini();
  }
}

$("#gemini-form").addEventListener("submit", event => {
  event.preventDefault();
  geminiSend($("#gemini-input").value, $("#gemini-search-toggle").checked);
});

document.addEventListener("click", event => {
  const target = event.target;
  if (target.closest("#show-gemini")) { showGemini(); return; }
  if (!target.closest("#gemini-page")) return;
  const similar = target.closest("[data-gemini-similar]");
  if (similar) { $("#gemini-input").value = `Tell me more about ${similar.dataset.geminiSimilar}`; geminiSend($("#gemini-input").value, false); return; }
  const action = target.closest("[data-gemini]")?.dataset.gemini;
  if (action === "new") {
    (async () => {
      try { geminiView.data = await api("/api/gemini/reset", {}); geminiView.picked.clear(); renderGemini(); }
      catch (error) { setActionStatus("#gemini-status", error.message); }
    })();
    return;
  }
  if (action === "settings") { geminiView.settingsOpen = !geminiView.settingsOpen; renderGemini(); return; }
  if (action === "save-key") {
    (async () => {
      const key = $("#gemini-key-input").value.trim();
      if (!key) return;
      try {
        await api("/api/gemini/settings", {api_key: key});
        geminiView.settingsOpen = false;
        geminiView.data = await api("/api/gemini");
        renderGemini();
      } catch (error) { setActionStatus("#gemini-status", error.message); }
    })();
    return;
  }
  if (action === "save-tavily") {
    (async () => {
      const key = $("#gemini-tavily-input").value.trim();
      if (!key) return;
      try {
        await api("/api/gemini/settings", {tavily_api_key: key});
        geminiView.data = await api("/api/gemini");
        renderGemini();
      } catch (error) { setActionStatus("#gemini-status", error.message); }
    })();
    return;
  }
  if (action === "get") {
    (async () => {
      try {
        await api("/api/gemini/get", {ids: [...geminiView.picked]});
        geminiView.picked.clear();
        renderGemini();
        geminiJobsLoad();
      } catch (error) { setActionStatus("#gemini-status", error.message); }
    })();
    return;
  }
  const all = target.closest("[data-gemini-all]");
  if (all) {
    const ids = all.dataset.geminiAll.split(",").filter(Boolean).map(Number);
    if (ids.every(id => geminiView.picked.has(id))) ids.forEach(id => geminiView.picked.delete(id));
    else ids.forEach(id => geminiView.picked.add(id));
    renderGemini();
  }
});

document.addEventListener("change", event => {
  const pick = event.target.closest?.("[data-gemini-pick]");
  if (!pick) return;
  const id = Number(pick.dataset.geminiPick);
  event.target.checked ? geminiView.picked.add(id) : geminiView.picked.delete(id);
  renderGemini();
});
