"use strict";

// Fork v73: "same choices on both PCs" (Downloads › Settings) and a Windows notification when the download queue finishes.
const syncView = {data: null, busy: false};
const queueNotify = {key: "tagcast-notify-queue", enabled: false, active: new Set(), timer: null, error: ""};
try { queueNotify.enabled = localStorage.getItem(queueNotify.key) === "1"; } catch { /* private window */ }

function syncCardHtml() {
  const d = syncView.data;
  if (!d && !syncView.busy) syncLoad();
  const merged = Object.entries(d?.merged || {}).map(([k, v]) => `${v} ${({separate: "kept-separate pairs", wishlist: "wishlist items", genre_lookups: "genre look-ups", upload_settings: "upload settings"})[k] || k}`);
  return `<section class="dl-card"><h3>Same choices on both PCs</h3>
      <p class="muted">With a shared folder set above, this PC and the other one share: artists you keep separate, the wishlist, genre look-ups, your genre list and the files already uploaded. It merges every few minutes and only ever adds (something removed on one PC can come back from the other until it is removed there too). Sign-in and keys are never shared.</p>
      <div class="dl-actions"><button type="button" class="button primary" data-sync="now" ${syncView.busy || !d?.folder ? "disabled" : ""}>${syncView.busy ? "Syncing…" : "↻ Sync now"}</button>
        <span class="muted" role="status">${!d ? "Loading…" : !d.folder ? "Set a shared folder above first." : d.error ? escapeHtml(d.error)
          : d.last ? `Last sync ${new Date(d.last * 1000).toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit"})}${merged.length ? `: added ${escapeHtml(merged.join(", "))}` : ": nothing new"}.` : "Not synced yet."}${d?.others?.length ? ` Other PCs: ${escapeHtml(d.others.join(", "))}.` : ""}</span></div></section>
    <section class="dl-card"><h3>Notification when the queue finishes</h3>
      <label class="checkline"><input type="checkbox" data-queue-notify ${queueNotify.enabled ? "checked" : ""}> Show a Windows notification when all downloads are done</label>
      <p class="muted">${escapeHtml(queueNotify.error || "Works while Tagcast is open in your browser, even in another tab or a minimised window.")}</p></section>`;
}

async function syncLoad() {
  try { syncView.data = await api("/api/sync"); } catch (error) { syncView.data = {error: error.message || String(error)}; }
  if (typeof renderDownloads === "function" && downloadsView.tab === "settings") renderDownloads();
}

document.addEventListener("click", async event => {
  if (!event.target.closest?.('[data-sync="now"]')) return;
  syncView.busy = true; renderDownloads();
  try { syncView.data = await api("/api/sync/now", {}); } catch (error) { syncView.data = {...syncView.data, error: error.message || String(error)}; }
  syncView.busy = false; renderDownloads();
});

// -- queue notification ----------------------------------------------------------------------

function queueNotifyCheck(queue) {
  const live = (queue || []).filter(i => ["queued", "active"].includes(i.state));
  for (const i of live) queueNotify.active.add(i.id);  // every download seen waiting or running since the queue was last empty
  if (!queueNotify.active.size || live.length) return null;
  const batch = queueNotify.active;
  queueNotify.active = new Set();
  const finished = (queue || []).filter(i => batch.has(i.id));
  if (!finished.length) return {title: "Tagcast: downloads finished", body: "The queue is empty."};
  const ok = finished.filter(i => i.state === "done").length, failed = finished.filter(i => i.state === "failed").length;
  return {title: failed ? "Tagcast: downloads finished, some failed" : "Tagcast: downloads finished",
    body: `${ok} saved${failed ? `, ${failed} failed` : ""}${finished.length - ok - failed ? `, ${finished.length - ok - failed} skipped` : ""}.`};
}

function queueNotifyShow(message) {
  if (!message || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try { new Notification(message.title, {body: message.body, tag: "tagcast-queue"}); } catch { /* the browser refused */ }
}

async function queueNotifyTick() {
  if (!queueNotify.enabled) return;
  try { queueNotifyShow(queueNotifyCheck((await api("/api/downloads/queue")).queue)); } catch { /* try again next time */ }
}

function queueNotifyStart() {
  clearInterval(queueNotify.timer);
  queueNotify.timer = queueNotify.enabled ? setInterval(queueNotifyTick, 15000) : null;
}

document.addEventListener("change", async event => {
  if (!event.target.matches?.("[data-queue-notify]")) return;
  const on = event.target.checked;
  queueNotify.error = "";
  if (on && typeof Notification === "undefined") { queueNotify.error = "This browser can't show notifications."; event.target.checked = false; renderDownloads(); return; }
  if (on && Notification.permission !== "granted") {
    const answer = await Notification.requestPermission();
    if (answer !== "granted") { queueNotify.error = "Allow notifications for this page in your browser to use this."; event.target.checked = false; renderDownloads(); return; }
  }
  queueNotify.enabled = on;
  try { localStorage.setItem(queueNotify.key, on ? "1" : "0"); } catch { /* private window */ }
  queueNotify.active = new Set();
  queueNotifyStart(); renderDownloads();
});

queueNotifyStart();
