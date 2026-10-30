"use strict";

// YouTube (fork v59): songs found with yt-dlp, first in the source order. Lossy (about 128 kbps AAC).
const youtubeView = {status: null, lines: "", results: null, busy: false};

async function youtubeLoad() {
  try { youtubeView.status = await api("/api/youtube/status"); } catch { youtubeView.status = {enabled: false, installed: false, version: ""}; }
  if (state.screen === "downloads" && ["youtube", "settings"].includes(downloadsView.tab)) renderDownloads();
}

function youtubeHtml() {
  const view = youtubeView;
  if (!view.status) youtubeLoad();
  const st = view.status;
  return `<p class="muted">${st && !st.installed ? "yt-dlp is not installed yet: restart Tagcast once (it installs it), or run <code>pip install yt-dlp</code>. " : ""}
    One song per line: <code>Artist - Title</code>. Tagcast looks for the official audio on YouTube and queues it. Files are lossy (about 128 kbps AAC).</p>
    <textarea id="youtube-lines" class="dl-lines" rows="5" maxlength="20000" aria-label="Songs to download from YouTube" placeholder="Coldplay - Yellow">${escapeHtml(view.lines)}</textarea>
    <div class="dl-actions"><button type="button" class="button primary" data-youtube="add" ${view.busy ? "disabled" : ""}>${view.busy ? "Searching…" : "⬇ Download from YouTube"}</button></div>
    ${view.results ? `<ul class="dl-list">${view.results.map(r => `<li><span class="dl-badge ${r.ok ? "have" : "partly"}">${r.ok ? "Queued" : "Not found"}</span>
      <div><b>${escapeHtml(r.line)}</b><div class="dl-have">${escapeHtml(r.note)}</div></div></li>`).join("")}</ul>` : ""}
    <p class="muted">yt-dlp: github.com/yt-dlp/yt-dlp. Downloading copyrighted music from YouTube can break YouTube's terms and the law where you live.</p>`;
}

function youtubeSettingsHtml() {
  const st = youtubeView.status;
  if (!st) { youtubeLoad(); return '<p class="muted">Loading…</p>'; }
  return `<label class="checkline"><input type="checkbox" data-youtube-enabled ${st.enabled ? "checked" : ""}> Try YouTube first for every download (then Galaxy, Bandcamp, Soulseek)</label>
    <p class="muted">${st.installed ? `yt-dlp ${escapeHtml(st.version)} is installed. If YouTube stops working, update it: <code>pip install -U yt-dlp</code>.` : "yt-dlp is not installed yet. Restart Tagcast once, or run <code>pip install yt-dlp</code>."}</p>`;
}

document.addEventListener("click", async event => {
  const add = event.target.closest?.('[data-youtube="add"]');
  if (!add) return;
  const view = youtubeView;
  view.lines = $("#youtube-lines")?.value || "";
  view.busy = true; renderDownloads();
  try {
    view.results = (await api("/api/youtube/add", {lines: view.lines})).results;
    downloadsView.queue = (await api("/api/downloads/queue")).queue;
  } catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  view.busy = false; renderDownloads();
});

document.addEventListener("change", async event => {
  const box = event.target.closest?.("[data-youtube-enabled]");
  if (!box) return;
  try { youtubeView.status = await api("/api/youtube/settings", {enabled: box.checked}); }
  catch (error) { setActionStatus("#downloads-status", error.message || String(error)); }
  renderDownloads();
});
