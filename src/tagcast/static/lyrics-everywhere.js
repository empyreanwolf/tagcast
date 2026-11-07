"use strict";

// Fork v85: timed lyrics everywhere while a song plays, not only in the large Now playing view:
// a line in the player bar, a floating box that stays across pages, and the Mini Player.
// Uses the same lyrics, sources and track clock as playback-lyrics.js.
const lyricsEverywhere = {bar: lyricsEverywhereStored("bar", true), float: lyricsEverywhereStored("float", false),
  mini: lyricsEverywhereStored("mini", true), last: "", box: null};

function lyricsEverywhereStored(name, fallback) {
  try { const v = localStorage.getItem(`tagcast-lyrics-${name}`); return v === null ? fallback : v === "true"; }
  catch { return fallback; }
}

function lyricsEverywhereSet(name, value) {
  lyricsEverywhere[name] = Boolean(value);
  try { localStorage.setItem(`tagcast-lyrics-${name}`, String(lyricsEverywhere[name])); } catch { /* private window */ }
  lyricsEverywhere.last = "";
  renderPlaybackLyrics(); renderLyricsEverywhere();
}

function lyricsEverywhereWanted() {
  if (!player.queue[player.index] || $("#player").hidden) return false;
  const miniOpen = lyricsEverywhere.mini && typeof miniOwner !== "undefined" && miniOwner.active;
  return ((lyricsEverywhere.bar || lyricsEverywhere.float) && !document.hidden) || Boolean(miniOpen);
}

// The lines around the current position: {prev, now, next, index, timed}.
function lyricsEverywhereLines(position = playerTrackPosition()) {
  const data = playbackLyricsView.data;
  if (!data?.timed || !data.lines.length) return null;
  const lines = data.lines;
  let index = playbackLyricIndex(lines, position);
  if (index < 0) {  // before the first line or in a gap: show what comes next
    const upcoming = lines.findIndex(line => line.start > position);
    return {prev: "", now: "", next: upcoming >= 0 ? lines[upcoming].text : "", index: -1, upcoming};
  }
  const text = i => (lines[i] && lines[i].text) || (lines[i] ? "♪" : "");
  return {prev: text(index - 1), now: text(index) || "♪", next: text(index + 1), index,
    around: [-2, -1, 0, 1, 2].map(d => ({i: index + d, text: index + d >= 0 ? text(index + d) : ""}))};
}

function lyricsEverywhereMini() {
  if (!lyricsEverywhere.mini) return null;
  const lines = lyricsEverywhereLines();
  return lines ? {now: lines.now, next: lines.next} : null;
}

function lyricsEverywhereSeek(line) {
  if (!line || !Number.isFinite(line.start)) return;
  if (typeof seekSystemPlayback === "function") seekSystemPlayback(line.start); else audio.currentTime = line.start;
}

function lyricsEverywhereBox() {
  if (lyricsEverywhere.box) return lyricsEverywhere.box;
  const box = document.createElement("aside");
  box.id = "floating-lyrics"; box.className = "floating-lyrics"; box.hidden = true;
  box.setAttribute("aria-label", "Lyrics");
  box.innerHTML = `<header class="floating-lyrics-head"><span>Lyrics</span>
    <button type="button" class="floating-lyrics-size" aria-label="Bigger or smaller" title="Bigger or smaller">⤢</button>
    <button type="button" class="floating-lyrics-close" aria-label="Close the floating lyrics" title="Close">×</button></header>
    <div class="floating-lyrics-lines" aria-live="off"></div>`;
  document.body.append(box);
  try {
    const pos = JSON.parse(localStorage.getItem("tagcast-lyrics-float-pos") || "null");
    if (pos && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
      box.style.left = `${Math.max(0, Math.min(pos.x, innerWidth - 120))}px`;
      box.style.top = `${Math.max(0, Math.min(pos.y, innerHeight - 60))}px`;
      box.style.right = "auto"; box.style.bottom = "auto";
    }
    if (localStorage.getItem("tagcast-lyrics-float-big") === "true") box.classList.add("big");
  } catch { /* defaults */ }
  box.querySelector(".floating-lyrics-close").addEventListener("click", () => lyricsEverywhereSet("float", false));
  box.querySelector(".floating-lyrics-size").addEventListener("click", () => {
    box.classList.toggle("big");
    try { localStorage.setItem("tagcast-lyrics-float-big", String(box.classList.contains("big"))); } catch { /* private window */ }
  });
  box.querySelector(".floating-lyrics-lines").addEventListener("click", event => {
    const node = event.target.closest("[data-line]");
    if (node) lyricsEverywhereSeek(playbackLyricsView.data?.lines[Number(node.dataset.line)]);
  });
  const head = box.querySelector(".floating-lyrics-head");
  head.addEventListener("pointerdown", event => {
    if (event.target.closest("button")) return;
    const rect = box.getBoundingClientRect(), dx = event.clientX - rect.left, dy = event.clientY - rect.top;
    head.setPointerCapture(event.pointerId);
    const move = e => {
      box.style.left = `${Math.max(0, Math.min(e.clientX - dx, innerWidth - rect.width))}px`;
      box.style.top = `${Math.max(0, Math.min(e.clientY - dy, innerHeight - rect.height))}px`;
      box.style.right = "auto"; box.style.bottom = "auto";
    };
    const up = () => {
      head.removeEventListener("pointermove", move);
      try { localStorage.setItem("tagcast-lyrics-float-pos", JSON.stringify({x: parseFloat(box.style.left), y: parseFloat(box.style.top)})); }
      catch { /* private window */ }
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up, {once: true});
  });
  lyricsEverywhere.box = box;
  return box;
}

function renderLyricsEverywhere(position) {
  const toggle = $("#player-lyrics-toggle");
  if (toggle) toggle.setAttribute("aria-pressed", String(lyricsEverywhere.bar));
  const on = lyricsEverywhereWanted() && !document.hidden;
  const lines = on ? lyricsEverywhereLines(position) : null;
  const loading = on && playbackLyricsView.loading;
  // Bar line
  const bar = $("#bar-lyrics");
  if (bar) {
    const show = on && lyricsEverywhere.bar && Boolean(lines || loading);
    bar.hidden = !show;
    if (show) {
      const now = lines ? lines.now || (lines.next ? "…" : "") : "Checking lyrics…";
      const key = `${now}\n${lines?.next || ""}`;
      if (key !== lyricsEverywhere.last) {
        lyricsEverywhere.last = key;
        $("#bar-lyric-now").textContent = now;
        $("#bar-lyric-next").textContent = lines?.next || "";
      }
      $("#bar-lyric-now").dataset.line = String(lines?.index ?? -1);
    }
  }
  // Floating box
  if (lyricsEverywhere.float && on) {
    const box = lyricsEverywhereBox(), region = box.querySelector(".floating-lyrics-lines");
    box.hidden = false;
    const html = !lines ? `<p class="floating-lyric muted">${loading ? "Checking lyrics…" : playbackLyricsView.data ? "Text lyrics · no timing" : "No timed lyrics for this song"}</p>`
      : (lines.around || [{i: -1, text: ""}, {i: -1, text: ""}, {i: -1, text: "…"}, {i: lines.upcoming ?? -1, text: lines.next}, {i: -1, text: ""}])
        .map((row, n) => `<p class="floating-lyric${n === 2 ? " now" : ""}"${row.i >= 0 ? ` data-line="${row.i}"` : ""}>${escapeHtml(row.text || " ")}</p>`).join("");
    if (region.dataset.html !== html) { region.innerHTML = html; region.dataset.html = html; }
  } else if (lyricsEverywhere.box) {
    lyricsEverywhere.box.hidden = true;
  }
}  // the Mini Player reads lyricsEverywhereMini() in its own 4x-per-second snapshot

function lyricsEverywhereSettings() {
  const check = (name, label) => `<label class="checkline"><input type="checkbox" data-lyrics-everywhere="${name}" ${lyricsEverywhere[name] ? "checked" : ""}> ${label}</label>`;
  return `<section class="player-display-settings" data-settings-category="playback"><h3>Lyrics while playing <span class="settings-mode">Immediate · this browser</span></h3>
    ${check("bar", "Current line in the player bar (the ♪ button)")}
    ${check("float", "Floating lyrics box (drag it anywhere, stays across pages)")}
    ${check("mini", "Lyrics in the Mini Player")}
    <p class="muted">These use the lyrics source chosen under Now playing lyrics. With an online option, the current song is looked up while any of these shows lyrics, not only in the large view.</p></section>`;
}

$("#player-lyrics-toggle").addEventListener("click", () => lyricsEverywhereSet("bar", !lyricsEverywhere.bar));
$("#bar-lyrics-float").addEventListener("click", () => lyricsEverywhereSet("float", !lyricsEverywhere.float));
$("#bar-lyric-now").addEventListener("click", event => {
  if (window.getSelection?.()?.toString()) return;
  lyricsEverywhereSeek(playbackLyricsView.data?.lines[Number(event.currentTarget.dataset.line)]);
});
document.addEventListener("change", event => {
  const name = event.target.dataset?.lyricsEverywhere;
  if (name) lyricsEverywhereSet(name, event.target.checked);
});
// A new song, the end of a pause or a closed large view: load lyrics for the bar, box or Mini Player.
// renderPlaybackLyrics() does nothing when this song's lyrics are already loaded or loading.
for (const name of ["loadedmetadata", "play", "playing", "emptied"]) audio.addEventListener(name, () => Promise.resolve().then(renderPlaybackLyrics));
// Gapless playback can change songs without a new loadedmetadata: check once a second.
let lyricsEverywhereChecked = 0;
audio.addEventListener("timeupdate", () => {
  const now = Date.now();
  if (now - lyricsEverywhereChecked < 1000 || !playbackLyricsWanted()) return;
  lyricsEverywhereChecked = now; renderPlaybackLyrics();
});
