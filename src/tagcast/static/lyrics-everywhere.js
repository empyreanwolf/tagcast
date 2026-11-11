"use strict";

// Fork v85: timed lyrics everywhere while a song plays, not only in the large Now playing view:
// a line in the player bar, a floating box that stays across pages, and the Mini Player.
// Uses the same lyrics, sources and track clock as playback-lyrics.js.
const lyricsEverywhere = {bar: lyricsEverywhereStored("bar", true), float: lyricsEverywhereStored("float", false),
  mini: lyricsEverywhereStored("mini", true), card: lyricsEverywhereStored("card", true),
  autosave: lyricsEverywhereStored("autosave", true), translate: lyricsEverywhereStored("translate", true), autotranslate: lyricsEverywhereStored("autotranslate", true), karaoke: lyricsEverywhereStored("karaoke", true),
  karaokereal: lyricsEverywhereStored("karaokereal", false), cardSize: lyricsEverywhereCardSize(), last: "", box: null,
  cardNode: null, cardKey: "", cardActive: -2, cardFollow: true, cardTimer: 0, saved: new Set(), savedKeys: new Set(), tries: {}, reasons: {}, pending: {}};

function lyricsEverywhereStored(name, fallback) {
  try { const v = localStorage.getItem(`tagcast-lyrics-${name}`); return v === null ? fallback : v === "true"; }
  catch { return fallback; }
}

function lyricsEverywhereSet(name, value) {
  lyricsEverywhere[name] = Boolean(value);
  try { localStorage.setItem(`tagcast-lyrics-${name}`, String(lyricsEverywhere[name])); } catch { /* private window */ }
  lyricsEverywhere.last = "";
  if (name === "karaoke" || name === "karaokereal") lyricsKaraokeApply(playbackLyricsView.data);
  if (["translate", "karaoke", "karaokereal"].includes(name) && playbackLyricsView.data && typeof paintPlaybackLyrics === "function") paintPlaybackLyrics();
  renderPlaybackLyrics(); renderLyricsEverywhere();
}

function lyricsEverywhereWanted() {
  if (!player.queue[player.index] || $("#player").hidden) return false;
  const miniOpen = lyricsEverywhere.mini && typeof miniOwner !== "undefined" && miniOwner.active;
  return ((lyricsEverywhere.bar || lyricsEverywhere.float || lyricsEverywhere.card) && !document.hidden) || Boolean(miniOpen);
}

// Fork v87: a line's translation (GD Studio's translated lyric), when "Show translations" is on.
const lyricsTranslation = line => (lyricsEverywhere.translate && typeof line?.tr === "string" ? line.tr : "");

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
  return {prev: text(index - 1), now: text(index) || "♪", next: text(index + 1), index, tr: lyricsTranslation(lines[index]),
    around: [-2, -1, 0, 1, 2].map(d => ({i: index + d, text: index + d >= 0 ? text(index + d) : "", tr: d === 0 ? lyricsTranslation(lines[index]) : ""}))};
}

function lyricsEverywhereMini() {
  if (!lyricsEverywhere.mini) return null;
  const lines = lyricsEverywhereLines();
  return lines ? {now: lines.now, next: lines.tr || lines.next} : null;
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
      const under = lines?.tr || lines?.next || "";
      const key = `${now}\n${under}`;
      if (key !== lyricsEverywhere.last) {
        lyricsEverywhere.last = key;
        $("#bar-lyric-now").textContent = now;
        $("#bar-lyric-next").textContent = under;
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
        .map((row, n) => `<p class="floating-lyric${n === 2 ? " now" : ""}"${row.i >= 0 ? ` data-line="${row.i}"` : ""}>${(n === 2 && lyricsKaraokeHtml(playbackLyricsView.data?.lines[row.i])) || escapeHtml(row.text || " ")}${row.tr ? `<small class="floating-lyric-tr">${escapeHtml(row.tr)}</small>` : ""}</p>`).join("");
    if (region.dataset.html !== html) { region.innerHTML = html; region.dataset.html = html; }
  } else if (lyricsEverywhere.box) {
    lyricsEverywhere.box.hidden = true;
  }
  renderHeaderLyrics(on, position);
  if (on) lyricsKaraokeSync(position ?? playerTrackPosition());
}  // the Mini Player reads lyricsEverywhereMini() in its own 4x-per-second snapshot

function lyricsEverywhereSettings() {
  const check = (name, label) => `<label class="checkline"><input type="checkbox" data-lyrics-everywhere="${name}" ${lyricsEverywhere[name] ? "checked" : ""}> ${label}</label>`;
  return `<section class="player-display-settings" data-settings-category="playback"><h3>Lyrics while playing <span class="settings-mode">Immediate · this browser</span></h3>
    ${check("bar", "Current line in the player bar (the ♪ button)")}
    ${check("float", "Floating lyrics box (drag it anywhere, stays across pages)")}
    ${check("mini", "Lyrics in the Mini Player")}
    ${check("card", "Lyrics card in page headers (right of the cover and title)")}
    ${check("karaoke", "Karaoke glow: words light up as they are sung (estimated when the lyrics only have line timing)")}
    ${check("karaokereal", "Karaoke glow only with real word timing (no estimates)")}
    ${check("translate", "Show translations under the lyrics")}
    ${check("autotranslate", "Translate Chinese, Japanese and Korean lyrics to English (free web translator, saved on this PC)")}
    ${check("autosave", "Save found timed lyrics to iBroadcast (only songs without lyrics, never overwrites)")}
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


// Fork v86: a lyrics card in the empty space of page headers (album, artist, playlist and other pages),
// like upstream Tagcast 1.5.5's Now playing card: LYRICS + Follow switch, current line bold, click to jump.
function headerLyricsTarget() {
  for (const node of document.querySelectorAll("main .album-detail-header, main .page-intro")) {
    if (node.offsetParent !== null && !node.closest(".settings-page, [hidden]")) return node;
  }
  return null;
}

function headerLyricsNode() {
  if (lyricsEverywhere.cardNode) return lyricsEverywhere.cardNode;
  const card = document.createElement("section");
  card.id = "header-lyrics"; card.className = "header-lyrics";
  card.setAttribute("aria-label", "Lyrics");
  card.innerHTML = `<header class="header-lyrics-head"><span>Lyrics</span>
    <span class="header-lyrics-tools"><button type="button" class="header-lyrics-size" data-step="-1" aria-label="Smaller text" title="Smaller">A−</button>
    <button type="button" class="header-lyrics-size" data-step="1" aria-label="Bigger text" title="Bigger">A+</button>
    <label class="header-lyrics-follow" title="Keep the current line in view"><span>Follow</span><input type="checkbox" role="switch" checked></label></span></header>
    <div class="header-lyrics-lines" tabindex="0" role="region" aria-label="Lyrics; scroll to read other lines"></div>
    <footer class="header-lyrics-source"></footer>`;
  const region = card.querySelector(".header-lyrics-lines"), follow = card.querySelector(".header-lyrics-follow input");
  const paintSize = () => {
    card.dataset.size = String(lyricsEverywhere.cardSize);
    for (const button of card.querySelectorAll(".header-lyrics-size")) {
      const next = lyricsEverywhere.cardSize + Number(button.dataset.step);
      button.disabled = next < 0 || next > 2;
    }
  };
  paintSize();
  card.addEventListener("click", event => {
    const button = event.target.closest(".header-lyrics-size");
    if (!button || button.disabled) return;
    lyricsEverywhere.cardSize = Math.max(0, Math.min(2, lyricsEverywhere.cardSize + Number(button.dataset.step)));
    try { localStorage.setItem("tagcast-lyrics-cardsize", String(lyricsEverywhere.cardSize)); } catch { /* private window */ }
    paintSize(); lyricsEverywhere.cardActive = -2; renderLyricsEverywhere();
  });
  follow.addEventListener("change", () => { lyricsEverywhere.cardFollow = follow.checked; lyricsEverywhere.cardActive = -2; renderLyricsEverywhere(); });
  const stopFollowing = () => { if (playbackLyricsView.data?.timed) { lyricsEverywhere.cardFollow = false; follow.checked = false; } };
  for (const name of ["wheel", "touchstart"]) region.addEventListener(name, stopFollowing, {passive: true});
  region.addEventListener("click", event => {
    const node = event.target.closest("[data-line]");
    if (!node || window.getSelection?.()?.toString()) return;
    lyricsEverywhereSeek(playbackLyricsView.data?.lines[Number(node.dataset.line)]);
    lyricsEverywhere.cardFollow = true; follow.checked = true; lyricsEverywhere.cardActive = -2;
  });
  lyricsEverywhere.cardNode = card;
  return card;
}

function headerLyricsSource(data) {
  const sources = {unison: ["Unison", "https://unison.boidu.dev/"], lrclib: ["LRCLIB", "https://lrclib.net/"], gdstudio: ["GD Studio", "https://music.gdstudio.xyz/"]};
  const source = sources[data?.source];
  if (!source) return data ? "Saved in iBroadcast" : "";
  const link = `<a href="${source[1]}" target="_blank" rel="noopener noreferrer">Lyrics from ${source[0]} ↗</a>`;
  const track = player.queue[player.index];
  if (track && lyricsEverywhere.saved.has(String(track.id))) return `${link} · Saved in iBroadcast`;
  const reason = track ? lyricsEverywhere.reasons[String(track.id)] : "";
  const save = track && lyricsEverywhere.pending[String(track.id)] ? ` <button type="button" class="text-action" data-lyrics-save-now="${escapeHtml(String(track.id))}">Save now</button>` : "";
  return `${link} · Not stored in iBroadcast${reason ? ` (${escapeHtml(reason)})` : ""}${save}`;
}

function renderHeaderLyrics(on, position = playerTrackPosition()) {
  const view = lyricsEverywhere;
  if (!view.card || !on) { if (view.cardNode) view.cardNode.remove(); return; }
  if (!view.cardTimer && typeof setInterval === "function") {
    // A page change while paused rebuilds the header without a timeupdate: place the card again.
    view.cardTimer = setInterval(() => { if (view.card && !document.hidden && player.queue[player.index]) renderLyricsEverywhere(); }, 1000);
  }
  const target = headerLyricsTarget();
  if (!target) { if (view.cardNode) view.cardNode.remove(); return; }
  const card = headerLyricsNode();
  if (card.parentNode !== target) { target.append(card); view.cardActive = -2; }
  const data = playbackLyricsView.data, region = card.querySelector(".header-lyrics-lines");
  const track = player.queue[player.index];
  const key = `${track?.id}|${playbackLyricsView.key}|${data ? data.lines.length : playbackLyricsView.loading ? "loading" : "none"}|${data?.source}|${view.saved.has(String(track?.id))}|${view.translate}|${view.karaoke}|${view.karaokereal}`;
  if (key !== view.cardKey) {
    view.cardKey = key; view.cardActive = -2;
    if (!view.cardFollow && data?.timed) { view.cardFollow = true; card.querySelector(".header-lyrics-follow input").checked = true; }
    region.toggleAttribute("data-timed", Boolean(data?.timed));
    region.innerHTML = data ? data.lines.map((line, i) => `<p class="header-lyric"${data.timed ? ` data-line="${i}"` : ""}>${headerLyricInner(line, data.timed, false)}</p>`).join("")
      : `<p class="header-lyric muted">${playbackLyricsView.loading ? "Checking lyrics…" : "No lyrics found for this song."}</p>`;
    card.querySelector(".header-lyrics-follow").hidden = !data?.timed;
    card.querySelector(".header-lyrics-source").innerHTML = headerLyricsSource(data);
  }
  if (!data?.timed) return;
  const active = playbackLyricIndex(data.lines, position);
  if (active === view.cardActive) return;
  const nodes = region.querySelectorAll("[data-line]");
  const old = nodes[view.cardActive];
  if (old) { old.classList.remove("now"); old.removeAttribute("aria-current"); old.innerHTML = headerLyricInner(data.lines[view.cardActive], true, false); }
  const node = nodes[active];
  view.cardActive = active;
  if (!node) return;
  node.classList.add("now"); node.setAttribute("aria-current", "true");
  node.innerHTML = headerLyricInner(data.lines[active], true, true);
  if (view.cardFollow) region.scrollTop = Math.max(0, node.offsetTop - region.offsetTop - region.clientHeight / 3);
}

// Fork v86: timed lyrics found online while playing are saved once to the song's iBroadcast lyrics,
// only when the song has none (the server checks again and never overwrites). Shown in Change history.
// Fork v92: plain (untimed) lyrics are saved too; a failed save is tried again (3 times, 30 s apart); the card says
// why a song is "Not stored" and offers "Save now" (which also works when auto-save is switched off).
async function lyricsAutosave(selection, entry, result, force = false) {
  const view = lyricsEverywhere;
  if (!selection || !result || !["lrclib", "unison", "gdstudio"].includes(result.source) || !Array.isArray(result.lines)) return;
  const id = String(selection.track_id), key = `${selection.session}|${id}`;
  view.pending[id] = {selection, entry, result};
  const why = text => { if (view.reasons[id] !== text) { view.reasons[id] = text; view.cardKey = ""; if (typeof renderLyricsEverywhere === "function") renderLyricsEverywhere(); lyricsRefreshLargeSource(); } };
  if (view.saved.has(id)) return;
  if (!view.autosave && !force) { why("auto-save is off"); return; }
  if (state.connection?.premium === false) { why("needs iBroadcast Premium"); return; }
  if (entry?.data?.native_text?.trim()) return;
  if (view.savedKeys.has(key) && !force) return;
  const tries = view.tries[key] || {n: 0, at: 0};
  if (!force && (tries.n >= 3 || Date.now() - tries.at < 30000)) return;
  view.tries[key] = {n: tries.n + 1, at: Date.now()};
  const timed = result.timed === true;
  const lines = result.lines.filter(line => !timed || Number.isFinite(line.start)).slice(0, 2000)
    .map(line => timed ? {start: line.start, text: String(line.text || "").slice(0, 500)} : {text: String(line.text || "").slice(0, 500)});
  if (lines.filter(line => line.text.trim()).length < 2) { view.savedKeys.add(key); why("too short to save"); return; }
  try {
    const data = await api("/api/lyrics/autosave", {track_id: id, source: result.source, timed, lines});
    if (data.status === "has") { view.savedKeys.add(key); view.saved.add(id); why(""); return; }
    if (data.status !== "saved") { why(data.note || "iBroadcast did not take it, trying again"); return; }
    view.savedKeys.add(key);
    view.saved.add(id);
    why("");
    if (entry) { entry.data = null; entry.error = ""; }
    const track = player.queue.find(t => String(t.id) === id) || {title: selection.title, artist: selection.artist};
    const album = track.album_id != null && typeof state.albums?.find === "function" ? state.albums.find(a => String(a.id) === String(track.album_id)) : null;
    const label = `${track.artist || selection.artist} – ${track.title || selection.title}`;
    state.history.unshift({created: new Date().toISOString(), status: data.confirmed === false ? "unverified" : "saved", source: "ibroadcast",
      selection: [{name: album?.name || label}],
      changes: [{label, status: data.confirmed === false ? "unverified" : "saved",
        fields: {lyrics: {before: "", after: `${timed ? "Timed" : "Plain"} lyrics from ${({lrclib: "LRCLIB", unison: "Unison", gdstudio: "GD Studio"})[result.source]} (${lines.length} lines)`}}}]});
    rememberHistory();
    if (typeof updateHistoryControls === "function") updateHistoryControls();
    if (typeof refreshAlbumLyricsLabels === "function") refreshAlbumLyricsLabels();
    view.cardKey = ""; renderLyricsEverywhere(); lyricsRefreshLargeSource();
  } catch (error) { why(`save failed (${String(error?.message || "offline").slice(0, 60)}), trying again`); }
}

function lyricsRefreshLargeSource() {
  const node = typeof $ === "function" ? $("#visual-lyrics-source") : null, data = playbackLyricsView?.data;
  if (node && data) node.innerHTML = headerLyricsSource(data);
}

document.addEventListener("click", event => {
  const button = event.target.closest("[data-lyrics-save-now]");
  if (!button) return;
  const found = lyricsEverywhere.pending[button.dataset.lyricsSaveNow];
  if (found) void lyricsAutosave(found.selection, found.entry, found.result, true);
});


// Fork v88: Chinese (also Japanese and Korean) lyrics get an English line under each line, from the free web translator
// behind /api/lyrics/translate (one request per song, remembered on this PC). Lines that already have a translation keep it.
const lyricsCjk = /[぀-ヿ㐀-䶿一-鿿가-힯]/;

async function lyricsTranslateEnglish(data) {
  const view = lyricsEverywhere;
  if (!view.autotranslate || !data?.timed || data.trBusy || data.trDone || data.lines.length > 300) return;
  const lines = data.lines.map(line => String(line.text || "").slice(0, 300));
  if (!lines.some(text => lyricsCjk.test(text))) return;
  data.trBusy = true;
  try {
    const answer = await api("/api/lyrics/translate", {lines});
    if (answer.status !== "ready" || !Array.isArray(answer.tr) || answer.tr.length !== lines.length) return;
    data.trDone = true;
    data.lines.forEach((line, i) => { if (!line.tr && typeof answer.tr[i] === "string" && answer.tr[i]) line.tr = answer.tr[i]; });
    if (playbackLyricsView.data === data) {
      view.cardKey = ""; view.last = "";
      if (typeof paintPlaybackLyrics === "function") paintPlaybackLyrics();
      renderLyricsEverywhere();
    }
  } catch { /* the translator is busy or offline: the lyrics stay as they are and it is tried again next time */ }
  finally { data.trBusy = false; }
}

// Fork v90: the header card comes in three sizes (A−/A+), remembered; 1 (medium) is the default.
function lyricsEverywhereCardSize() {
  try {
    const stored = localStorage.getItem("tagcast-lyrics-cardsize");
    const v = stored === null || stored === "" ? NaN : Number(stored);
    return Number.isInteger(v) && v >= 0 && v <= 2 ? v : 1;
  }
  catch { return 1; }
}

function headerLyricInner(line, timed, current) {
  const tr = lyricsTranslation(line);
  const text = (current && lyricsKaraokeHtml(line)) || escapeHtml(line.text || (timed ? "♪" : " "));
  return `${text}${tr ? `<small class="header-lyric-tr">${escapeHtml(tr)}</small>` : ""}`;
}

// Fork v90: karaoke glow. Words light up as they are sung: with the real word timing when the lyrics have it,
// otherwise estimated by spreading each line's time over its words (every character for Chinese, Japanese, Korean).
// CSS animations do the work (negative delays join a line in the middle), so there is no per-frame script.
const lyricsToken = /[぀-ヿ㐀-䶿一-鿿가-힯]|\s+|[^\s぀-ヿ㐀-䶿一-鿿가-힯]+/g;

function lyricsKaraokeEstimate(line) {
  const text = String(line.text || "");
  if (!text.trim() || !Number.isFinite(line.start) || !Number.isFinite(line.end) || line.end <= line.start) return null;
  const parts = text.match(lyricsToken) || [];
  const weight = part => (/^\s+$/.test(part) ? 0 : lyricsCjk.test(part) ? 1 : Math.min(8, Math.max(1, [...part].length * 0.6)));
  const total = parts.reduce((sum, part) => sum + weight(part), 0);
  if (!total) return null;
  // The sung part: at most 92% of the line, and not slower than about half a second per word or character.
  const span = Math.min((line.end - line.start) * 0.92, total * 0.5);
  let at = line.start;
  const words = parts.map(part => {
    const start = at;
    at += span * weight(part) / total;
    return {text: part, start, end: Math.min(at, line.end)};
  });
  words.est = true;
  return words;
}

function lyricsKaraokeApply(data) {
  if (!data?.timed) return;
  const estimate = lyricsEverywhere.karaoke && !lyricsEverywhere.karaokereal;
  for (const line of data.lines) {
    if (line.words?.est) { if (!estimate) delete line.words; continue; }
    if (estimate && !(Array.isArray(line.words) && line.words.length)) {
      const words = lyricsKaraokeEstimate(line);
      if (words) line.words = words;
    }
  }
}

function lyricsKaraokeHtml(line) {
  if (!lyricsEverywhere.karaoke || !line) return "";
  const words = typeof playbackLyricWords === "function" ? playbackLyricWords(line) : [];
  if (!words.length) return "";
  return `<span class="kar-line">${words.map(word => (/^\s*$/.test(word.text) ? escapeHtml(word.text)
    : `<span class="kar-word" data-s="${word.start}" data-e="${word.end}">${escapeHtml(word.text)}</span>`)).join("")}</span>`;
}

function lyricsKaraokeStart(line, position, paused) {
  line.classList.remove("kar-run");
  for (const word of line.querySelectorAll(".kar-word")) {
    const start = Number(word.dataset.s), end = Number(word.dataset.e);
    word.style.setProperty("--kd", `${(start - position).toFixed(3)}s`);
    word.style.setProperty("--kt", `${Math.max(0.05, end - start).toFixed(3)}s`);
  }
  void line.offsetWidth;  // restart the animations
  line.classList.add("kar-run");
  line.classList.toggle("kar-paused", paused);
  line.karaoke = {position, at: performance.now(), paused};
}

// Called on every lyrics render (about four times a second): starts new lines, and starts a line again
// after a seek, a pause or a resume, so the glow always matches the song.
function lyricsKaraokeSync(position) {
  if (!lyricsEverywhere.karaoke || !Number.isFinite(position)) return;
  const paused = Boolean(audio.paused);
  for (const line of document.querySelectorAll(".floating-lyrics .kar-line, .header-lyrics .kar-line")) {
    const known = line.karaoke;
    const expected = known ? known.position + (known.paused ? 0 : (performance.now() - known.at) / 1000) : NaN;
    if (!known || known.paused !== paused || Math.abs(position - expected) > 0.6) lyricsKaraokeStart(line, position, paused);
  }
}
