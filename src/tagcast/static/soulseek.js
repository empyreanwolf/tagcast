"use strict";

// Soulseek (fork v14): albums and songs from Soulseek through Sockseek, which Tagcast starts
// in the background. Type "Artist - Album" (or "Artist - Title" for songs), one per line.
// Everything is checked first; albums or songs you may have wait in Needs your decision.
const soulseekView = {status: null, kind: "album", lines: "", loading: false};

async function soulseekLoad() {
  try { soulseekView.status = await api("/api/soulseek/status"); } catch (error) { soulseekView.status = {error: error.message}; }
  if (downloadsView.tab === "soulseek" && $("#soulseek-status")) $("#soulseek-status").innerHTML = soulseekStatusHtml();
  if (downloadsView.tab === "settings" && $("#soulseek-settings")) $("#soulseek-settings").innerHTML = soulseekSettingsHtml();
}

function soulseekStatusHtml() {
  const s = soulseekView.status;
  if (!s) return '<span class="muted">Checking Sockseek…</span>';
  if (s.error) return `<span class="error-text">${escapeHtml(s.error)}</span>`;
  if (!s.settings.configured) return 'Not set up yet. <button type="button" class="text-action" data-downloads-tab="settings">Set up Soulseek in Settings</button>';
  const login = s.client ? (s.client.isReady ? "logged in to Soulseek" : `Soulseek: ${escapeHtml(s.client.state || "connecting")}`) : "";
  return s.running ? `<span class="dl-badge have">Sockseek ${escapeHtml(s.version)} running</span> ${login}
      ${s.started_here ? '<button type="button" class="button small" data-soulseek="stop">Stop Sockseek</button>' : ""}`
    : `<span class="dl-badge new">Sockseek is not running</span> It starts by itself when a download needs it.
      <button type="button" class="button small" data-soulseek="start">Start now</button>`;
}

function soulseekHtml() {
  const view = soulseekView;
  if (!view.status) soulseekLoad();
  return `<p id="soulseek-status" class="dl-actions">${soulseekStatusHtml()}</p>
    <div class="dl-kind" role="radiogroup" aria-label="Albums or songs">
      <label><input type="radio" name="soulseek-kind" value="album" ${view.kind === "album" ? "checked" : ""}> Albums</label>
      <label><input type="radio" name="soulseek-kind" value="song" ${view.kind === "song" ? "checked" : ""}> Songs</label></div>
    <textarea id="soulseek-lines" class="dl-lines" rows="5" maxlength="20000" aria-label="What to download from Soulseek"
      placeholder="${view.kind === "album" ? "Coldplay - X&Y" : "Coldplay - Clocks"}">${escapeHtml(view.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-soulseek="add" ${view.loading ? "disabled" : ""}>⬇ Download from Soulseek</button>
      <span class="muted">One per line: Artist - ${view.kind === "album" ? "Album" : "Title"}. Sockseek picks a complete, well-shared copy.</span></div>
    <p class="muted">Soulseek is a sharing network: please share some music back (Settings → Soulseek). Sockseek: github.com/fiso64/sockseek (AGPL).</p>`;
}

function soulseekSettingsHtml() {
  const st = soulseekView.status?.settings;
  if (!st) { soulseekLoad(); return '<p class="muted">Loading…</p>'; }
  return `<p class="muted">Download Sockseek for Windows from its GitHub releases page (github.com/fiso64/sockseek/releases), unzip it, and choose <code>sockseek.exe</code> here. A free Soulseek account is enough; the name and password are kept on this computer only.</p>
    <div class="dl-settings">
      <label>Sockseek program <input id="soulseek-exe" class="dl-path" maxlength="400" value="${escapeHtml(st.exe)}" placeholder="C:\\Users\\you\\Downloads\\sockseek\\sockseek.exe"></label>
      <label>Soulseek username <input id="soulseek-username" maxlength="100" value="${escapeHtml(st.username)}" autocomplete="off"></label>
      <label>Soulseek password <input id="soulseek-password" type="password" maxlength="200" placeholder="${st.has_password ? "saved (type to change)" : ""}" autocomplete="new-password"></label>
      <label>Files you share <input id="soulseek-files" type="number" min="0" value="${st.shared_files}"></label>
      <label>Folders you share <input id="soulseek-folders" type="number" min="0" value="${st.shared_folders}"></label>
      <label>Sockseek port <input id="soulseek-port" type="number" min="1024" max="65535" value="${st.port}"></label></div>
    <div class="dl-actions"><button type="button" class="button primary" data-soulseek="settings">Save Soulseek settings</button></div>`;
}

function soulseekItems() {
  const kind = soulseekView.kind;
  return soulseekView.lines.split("\n").map(l => l.trim()).filter(Boolean).map(line => {
    const at = line.indexOf(" - ");
    const artist = at > 0 ? line.slice(0, at).trim() : "", rest = at > 0 ? line.slice(at + 3).trim() : line;
    return kind === "album" ? {kind, artist, album: rest} : {kind, artist, title: rest};
  });
}

document.addEventListener("click", event => {
  const button = event.target.closest?.("[data-soulseek]");
  if (!button || !button.closest("#downloads-page")) return;
  const action = button.dataset.soulseek;
  if (action === "add") {
    const items = soulseekItems();
    if (!items.length) { toast("Type at least one line: Artist - Album."); return; }
    if (items.some(i => !i.artist)) { toast("Each line needs “Artist - …”."); return; }
    downloadsRun(async () => {
      const r = await api("/api/soulseek/add", {items});
      soulseekView.lines = "";
      downloadsNote(`${r.queued} queued${r.held ? `, ${r.held} waiting in Needs your decision` : ""}${r.skipped ? `, ${r.skipped} already listed` : ""}.`);
      await loadDownloads();
    });
  } else if (action === "settings") {
    downloadsRun(async () => {
      soulseekView.status = await api("/api/soulseek/settings", {exe: $("#soulseek-exe").value, username: $("#soulseek-username").value,
        password: $("#soulseek-password").value, shared_files: $("#soulseek-files").value, shared_folders: $("#soulseek-folders").value,
        port: $("#soulseek-port").value});
      $("#soulseek-settings").innerHTML = soulseekSettingsHtml();
      downloadsNote("Soulseek settings saved.");
    });
  } else if (action === "start" || action === "stop") {
    button.disabled = true;
    downloadsRun(async () => {
      soulseekView.status = await api(`/api/soulseek/${action}`, {});
      $("#soulseek-status").innerHTML = soulseekStatusHtml();
    });
  }
});

document.addEventListener("change", event => {
  if (event.target.name !== "soulseek-kind") return;
  soulseekView.kind = event.target.value; renderDownloads();
});
document.addEventListener("input", event => { if (event.target.id === "soulseek-lines") soulseekView.lines = event.target.value; });
