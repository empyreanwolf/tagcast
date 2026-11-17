"use strict";

// Fork v100: back up the settings folder (Downloads › Settings) to a Google Drive folder.
const backupView = {data: null, busy: false};

function backupCardHtml() {
  const d = backupView.data;
  if (!d && !backupView.busy) backupLoad();
  const fmt = ts => ts ? new Date(ts * 1000).toLocaleString("en-GB", {day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"}) : "";
  const size = bytes => bytes ? `${(bytes / 1048576).toFixed(1)} MB` : "";
  const status = !d ? "Loading…" : !d.path ? "Set a folder below first."
    : d.running ? "Backing up…"
    : d.last_ok === false ? `Failed: ${d.last_error}`
    : d.last ? `Last backup ${fmt(d.last)}${size(d.last_bytes) ? ` (${size(d.last_bytes)})` : ""}.`
    : "Not backed up yet.";
  return `<section class="dl-card"><h3>Back up to Google Drive</h3>
      <p class="muted">Zips your whole settings folder (sign-in, caches, download/wishlist history, every source's settings) into the folder below, once a week while Tagcast is running and whenever you click "Back up now". Point it at a folder Google Drive for Desktop already syncs - Tagcast just writes the zip there, Drive uploads it. Only the latest copy is kept. Lyrics don't need this: they're saved straight onto the song in your iBroadcast library.</p>
      <div class="dl-actions"><input id="backup-path" class="dl-path" maxlength="400" value="${escapeHtml(d?.path || "")}" placeholder="G:\\My Drive\\Tagcast Backups" aria-label="Google Drive folder">
        <button type="button" class="button primary" data-backup="save">Save</button>
        <button type="button" class="button" data-backup="run" ${backupView.busy || d?.running || !d?.path ? "disabled" : ""}>${d?.running ? "Backing up…" : "Back up now"}</button></div>
      <p class="muted" role="status">${escapeHtml(status)}</p></section>`;
}

async function backupLoad() {
  backupView.busy = true;
  try { backupView.data = await api("/api/backup"); } catch (error) { backupView.data = {error: error.message || String(error)}; }
  backupView.busy = false;
  if (typeof renderDownloads === "function" && downloadsView.tab === "settings") renderDownloads();
}

document.addEventListener("click", async event => {
  const button = event.target.closest?.("[data-backup]");
  if (!button) return;
  const action = button.dataset.backup;
  if (action === "save") {
    backupView.busy = true; renderDownloads();
    try { backupView.data = await api("/api/backup/settings", {path: $("#backup-path")?.value || ""}); }
    catch (error) { backupView.data = {...backupView.data, error: error.message || String(error)}; }
    backupView.busy = false; renderDownloads();
  } else if (action === "run") {
    try { backupView.data = await api("/api/backup/run", {}); } catch (error) { backupView.data = {...backupView.data, error: error.message || String(error)}; }
    renderDownloads();
    backupPoll();
  }
});

async function backupPoll() {
  for (let i = 0; i < 40 && backupView.data?.running; i++) {
    await new Promise(r => setTimeout(r, 1500));
    try { backupView.data = await api("/api/backup"); } catch { break; }
  }
  renderDownloads();
}
