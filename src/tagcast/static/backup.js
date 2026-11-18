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

// Fork v109: your settings as one file - download it, and restore it later (here or on another PC).
// Restoring shows what would change first; nothing is written until you click Restore.
const settingsFileView = {preview: null, data: "", busy: false, message: ""};

function settingsFileCardHtml() {
  const v = settingsFileView, p = v.preview;
  const label = {new: "new", changed: "changes", same: "same"};
  const rows = p ? p.files.map(f => `<li><span class="dl-badge ${f.status === "same" ? "have" : "partly"}">${label[f.status] || f.status}</span>
      <div><b>${escapeHtml(f.file)}</b>${f.backup || f.now ? `<div class="muted">${escapeHtml([f.now && `now: ${f.now}`, f.backup && `backup: ${f.backup}`].filter(Boolean).join(" · "))}</div>` : ""}</div></li>`).join("") : "";
  return `<section class="dl-card"><h3>Back up and restore (one file)</h3>
      <p class="muted">Your settings, wishlist, download history and every source's settings in one file. Sign-in tokens and passwords are left out, so after restoring on another PC you sign in again. Restoring shows what would change first; Tagcast restarts to use the restored settings.</p>
      <div class="dl-actions"><a class="button" href="/api/backup/export" download>⬇ Download settings file</a>
        <label class="button">Restore from file…<input id="settings-file" type="file" accept=".zip" hidden></label></div>
      ${p ? `<p>Backup from ${escapeHtml(p.made || "?")}${p.fork ? ` (fork ${escapeHtml(p.fork)})` : ""}: ${p.changes ? `${p.changes} ${p.changes === 1 ? "file changes" : "files change"}` : "nothing would change"}.</p>
        <ul class="dl-list dl-nofold">${rows}</ul>
        <div class="dl-actions">${p.changes ? `<button type="button" class="button primary" data-settings-file="restore" ${v.busy ? "disabled" : ""}>Restore and restart</button>` : ""}
          <button type="button" class="button" data-settings-file="cancel">Cancel</button></div>` : ""}
      ${v.message ? `<p class="muted" role="status">${escapeHtml(v.message)}</p>` : ""}</section>`;
}

document.addEventListener("change", async event => {
  if (event.target?.id !== "settings-file") return;
  const file = event.target.files?.[0];
  if (!file) return;
  const v = settingsFileView;
  v.busy = true; v.message = "Reading the backup…"; renderDownloads();
  try {
    v.data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Could not read that file."));
      reader.readAsDataURL(file);
    });
    v.preview = await api("/api/backup/restore/preview", {data: v.data});
    v.message = "";
  } catch (error) { v.preview = null; v.data = ""; v.message = error.message || String(error); }
  v.busy = false; renderDownloads();
});

document.addEventListener("click", async event => {
  const button = event.target.closest?.("[data-settings-file]");
  if (!button) return;
  const v = settingsFileView;
  if (button.dataset.settingsFile === "cancel") { v.preview = null; v.data = ""; v.message = ""; renderDownloads(); return; }
  if (!v.data || v.busy) return;
  v.busy = true; renderDownloads();
  try {
    const result = await api("/api/backup/restore", {data: v.data});
    v.message = result.message || "Restored."; v.preview = null; v.data = "";
  } catch (error) { v.message = error.message || String(error); }
  v.busy = false; renderDownloads();
});

// Fork v109: same song, different name. Fingerprint the library once (streams the start of every
// song, in the background); every new download is then compared by its sound, and the Upload page's
// duplicate check flags "same audio" too.
const fingerprintView = {data: null, busy: false, timer: null, error: ""};

function fingerprintCardHtml() {
  const v = fingerprintView, d = v.data;
  if (!d && !v.busy && !v.error) fingerprintLoad();
  const p = d?.progress;
  const pct = p && p.total ? Math.round(p.done * 100 / p.total) : 0;
  const status = !d ? (v.error || "Loading…")
    : d.installing ? "Downloading fpcalc…"
    : !d.installed ? "fpcalc (Chromaprint's free fingerprint tool, about 3 MB) isn't installed yet."
    : d.running ? `Fingerprinting your library: ${p ? `${p.done} of ${p.total} (${pct}%)${p.current ? ` · ${p.current}` : ""}` : "starting…"}`
    : `${d.done} of ${d.library_total} library songs fingerprinted${d.failed ? `, ${d.failed} couldn't be read` : ""}.`;
  const buttons = !d ? "" : !d.installed
    ? `<button type="button" class="button primary" data-fingerprint="install" ${d.installing ? "disabled" : ""}>Install fpcalc</button>`
    : d.running ? `<button type="button" class="button" data-fingerprint="stop">Stop</button>`
    : `<button type="button" class="button primary" data-fingerprint="start" ${d.library_total && d.done + d.failed >= d.library_total ? "disabled" : ""}>${d.done ? "Fingerprint new songs" : "Fingerprint my library"}</button>
       ${d.failed ? `<button type="button" class="button" data-fingerprint="retry">Retry the ${d.failed} that failed</button>` : ""}`;
  return `<section class="dl-card"><h3>Same song, different name (audio fingerprints)</h3>
      <p class="muted">Finds a download you already have under another title ("Song (Official Audio)" vs "Song - Remastered") by its sound. Your library is fingerprinted once: Tagcast streams the first 30 seconds of every song from iBroadcast, one at a time in the background (hours for a big library, and you can stop and carry on later). After that every new download is checked, and the Upload page flags "same audio" so you can skip it. Nothing is sent anywhere.</p>
      <div class="dl-actions">${buttons}</div>
      <p class="muted" role="status">${escapeHtml(status)}</p>
      ${d?.error ? `<p class="error-text" role="status">${escapeHtml(d.error)}</p>` : ""}</section>`;
}

async function fingerprintLoad() {
  const v = fingerprintView;
  v.busy = true;
  try { v.data = await api("/api/fingerprints"); v.error = ""; } catch (error) { v.error = error.message || String(error); }
  v.busy = false;
  if (typeof renderDownloads === "function" && downloadsView.tab === "settings") renderDownloads();
  clearTimeout(v.timer);
  if ((v.data?.running || v.data?.installing) && state.screen === "downloads" && downloadsView.tab === "settings") v.timer = setTimeout(fingerprintLoad, 2000);
}

document.addEventListener("click", async event => {
  const button = event.target.closest?.("[data-fingerprint]");
  if (!button) return;
  const v = fingerprintView;
  try { v.data = await api(`/api/fingerprints/${button.dataset.fingerprint}`, {}); v.error = ""; }
  catch (error) { v.data = {...(v.data || {}), error: error.message || String(error)}; }
  renderDownloads();
  fingerprintLoad();
});
