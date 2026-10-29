"use strict";

// Pre-load the next tracks (fork v50). While a track plays, the next PRELOAD_AHEAD queue tracks are
// downloaded one at a time into memory; when one becomes the current track it plays from memory, so
// skipping or the end of a track starts instantly. Nothing is written to disk. If a download fails,
// is too large, or the setting is off, playback streams exactly as before.

const PRELOAD_AHEAD = 2;
const PRELOAD_MAX_BYTES = 150 * 1024 * 1024;
const preload = {enabled: true, items: new Map(), running: false, playingUrl: "", bad: new Set()};
try { preload.enabled = localStorage.getItem("tagcast-preload") !== "off"; } catch { /* default on */ }

const preloadKey = track => `${track.id}:${player.quality}`;

function preloadWanted() {
  if (!preload.enabled || typeof player === "undefined" || player.repeat === "track") return [];
  if (typeof gaplessTrial !== "undefined" && gaplessTrial.enabled) return [];
  if (typeof localPlaybackAllowed === "function" && !localPlaybackAllowed()) return [];
  const wanted = [];
  for (let i = player.index + 1; i < player.queue.length && wanted.length < PRELOAD_AHEAD; i += 1) {
    const track = player.queue[i];
    if (track && !wanted.some(t => t.id === track.id)) wanted.push(track);
  }
  return wanted;
}

function preloadRelease(item) {
  item.controller?.abort();
  if (item.url) { try { URL.revokeObjectURL(item.url); } catch { /* already gone */ } }
}

// Called when a track starts: returns a memory URL when this track was pre-loaded, else "".
function preloadTake(track) {
  if (preload.playingUrl) { try { URL.revokeObjectURL(preload.playingUrl); } catch { /* already gone */ } preload.playingUrl = ""; }
  const key = preloadKey(track), item = preload.items.get(key);
  if (!item?.url) return "";
  preload.items.delete(key);
  preload.playingUrl = item.url;
  return item.url;
}

function preloadSchedule() {
  const wanted = preloadWanted(), keep = new Set(wanted.map(preloadKey));
  for (const [key, item] of preload.items) if (!keep.has(key)) { preloadRelease(item); preload.items.delete(key); }
  if (!preload.running) preloadNext();
}

async function preloadNext() {
  const wanted = preloadWanted(), track = wanted.find(t => !preload.items.has(preloadKey(t)) && !preload.bad.has(preloadKey(t)));
  if (!track || !live()) return;
  const key = preloadKey(track), quality = player.quality, item = {controller: new AbortController(), url: ""};
  preload.items.set(key, item);
  preload.running = true;
  try {
    const response = await fetch(`/api/stream/${encodeURIComponent(track.id)}?quality=${quality}`, {signal: item.controller.signal});
    const size = Number(response.headers.get("Content-Length")) || 0;
    if (!response.ok || size > PRELOAD_MAX_BYTES) throw Error("skipped");
    const blob = await response.blob();
    if (item.controller.signal.aborted || preload.items.get(key) !== item) return;
    item.url = URL.createObjectURL(blob);
  } catch {
    if (preload.items.get(key) === item) preload.items.delete(key);
    preload.bad.add(key);   // not retried until the queue changes
  } finally { preload.running = false; }
  preloadSchedule();
}

function preloadStop() {
  for (const item of preload.items.values()) preloadRelease(item);
  preload.items.clear(); preload.bad.clear();
}

document.addEventListener("change", event => {
  if (event.target.id !== "preload-enabled") return;
  preload.enabled = event.target.checked;
  try { localStorage.setItem("tagcast-preload", preload.enabled ? "on" : "off"); } catch { /* optional */ }
  if (preload.enabled) preloadSchedule(); else preloadStop();
});

function preloadSettings() {
  return `<section class="settings-section" data-settings-category="playback"><h3>Pre-load next songs</h3><label class="checkline"><input type="checkbox" id="preload-enabled" ${preload.enabled ? "checked" : ""}> Download the next ${PRELOAD_AHEAD} songs in the background so changing track starts instantly</label><p class="muted">Kept in memory only, never saved to disk, and dropped when the queue changes. Not used with gapless playback. Uses extra data while a track plays.</p></section>`;
}
