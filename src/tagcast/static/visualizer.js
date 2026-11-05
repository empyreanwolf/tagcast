"use strict";

// Visualizer (fork v78): Butterchurn on Now playing, behind the whole site, or beside the lyrics. Theme menu >
// Visualizer picks where; the Now playing header has Visualizer / Lock / Next buttons. The engine is in
// visualizer-core.js. The sound comes from the real player (same-origin stream), so it follows the music.
const bvState = {where: "off", preset: "auto", quality: "normal", last: "stage", engine: null, canvas: null, graph: null, parent: null, resizer: null, error: ""};
const bvKeys = {where: "tagcast-viz", preset: "tagcast-viz-preset", quality: "tagcast-viz-quality", last: "tagcast-viz-last"};
const bvPlaces = [["off", "Off"], ["stage", "Now playing · replaces the player picture (beside lyrics too)"], ["page", "Now playing · full-page background"], ["site", "Whole site background"]];
try {
  for (const key of ["where", "preset", "quality", "last"]) bvState[key] = localStorage.getItem(bvKeys[key]) || bvState[key];
} catch { /* private window */ }
if (!bvPlaces.some(([id]) => id === bvState.where)) bvState.where = "off";
if (!["stage", "page", "site"].includes(bvState.last)) bvState.last = "stage";
if (!["low", "normal", "high"].includes(bvState.quality)) bvState.quality = "normal";

function bvSave(key, value) { bvState[key] = value; try { localStorage.setItem(bvKeys[key], value); } catch { /* private window */ } }
function bvPlaying() { return Boolean(typeof audio !== "undefined" && audio && !audio.paused && !audio.ended); }
function bvOpen() { return Boolean(document.body?.classList.contains("visual-player-open")); }

// The player's sound goes through one AudioContext so the visualizer can listen. Done at the click that switches it on.
function bvGraph() {
  if (bvState.graph !== null) return bvState.graph;
  bvState.graph = false;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx || typeof audio === "undefined" || typeof audio.addEventListener !== "function") return false;
  try {
    const context = new Ctx(), node = context.createMediaElementSource(audio);
    node.connect(context.destination);
    try { if (audio.sinkId && context.setSinkId) context.setSinkId(audio.sinkId); } catch { /* default speakers */ }
    const wake = () => { if (context.state === "suspended") context.resume?.(); };
    for (const name of ["play", "playing", "loadeddata"]) audio.addEventListener(name, wake);
    document.addEventListener("pointerdown", wake, true); document.addEventListener("keydown", wake, true);
    wake();
    bvState.graph = {context, node};
  } catch { bvState.graph = false; }
  return bvState.graph;
}

function bvCanvas() {
  if (!bvState.canvas) {
    bvState.canvas = document.createElement("canvas");
    bvState.canvas.id = "bv-canvas"; bvState.canvas.className = "bv-canvas";
    bvState.canvas.setAttribute("aria-hidden", "true");
  }
  return bvState.canvas;
}

function bvEnsureEngine() {
  if (bvState.engine) return bvState.engine;
  const graph = bvGraph(), engine = bvEngine(bvCanvas(), graph ? {context: graph.context, node: graph.node} : {});
  bvState.engine = engine;
  engine.onPreset = name => { bvFillPresetMenu(); if (engine.lock && bvState.preset !== name) bvSave("preset", name); };
  engine.ready.then(() => {
    engine.fps = bvState.quality === "low" ? 30 : 60;
    bvFillPresetMenu();
    bvApplyPreset(true);
    bvLayout();
    bvSync();
  }).catch(error => { bvState.engine = null; bvState.error = error.message; if (typeof toast === "function") toast(error.message); bvSave("where", "off"); bvRefresh(); });
  return engine;
}

function bvApplyPreset(first) {
  const engine = bvState.engine;
  if (!engine?.viz) return;
  engine.lock = bvState.preset !== "auto" && Boolean(engine.presets[bvState.preset]);
  if (engine.lock) { if (engine.current !== bvState.preset) engine.choose(bvState.preset, first ? 0 : 2.5); }
  else if (first || !engine.current) engine.choose(engine.names[Math.floor(Math.random() * engine.names.length)], 0);
}

// Put the one canvas where the chosen place wants it.
function bvPlaceCanvas() {
  const canvas = bvCanvas(), where = bvState.where, root = document.documentElement;
  const stage = document.querySelector("#bv-stage");
  let parent = null, kind = "";
  if (where === "site") { parent = document.body; kind = "site"; }
  else if (bvOpen() && where === "page") { parent = document.querySelector("#visual-player"); kind = "page"; }
  else if (bvOpen() && where === "stage") {
    const content = document.querySelector("#visual-player .visual-player-content");
    if (content) {
      let slot = stage;
      if (!slot) { slot = document.createElement("div"); slot.id = "bv-stage"; slot.className = "bv-stage"; content.insertBefore(slot, content.firstChild); }
      parent = slot; kind = "stage";
    }
  }
  if (stage && stage !== parent) stage.remove();
  canvas.className = "bv-canvas" + (kind ? " bv-" + kind : "");
  if (parent && canvas.parentNode !== parent) parent.insertBefore(canvas, parent.firstChild);
  if (!parent && canvas.parentNode) canvas.remove();
  bvState.parent = parent;
  if (where !== "off") root.dataset.bv = where; else delete root.dataset.bv;
  if (root.dataset) root.dataset.bvStage = kind === "stage" ? "on" : "";
  if (bvState.resizer && typeof bvState.resizer.disconnect === "function") bvState.resizer.disconnect();
  if (parent && typeof ResizeObserver === "function") { bvState.resizer = new ResizeObserver(bvLayout); bvState.resizer.observe(parent); }
  return parent;
}

function bvLayout() {
  const engine = bvState.engine, parent = bvState.parent;
  if (!engine?.viz || !parent) return;
  const factor = bvState.quality === "low" ? 0.5 : bvState.quality === "high" ? Math.min(2, window.devicePixelRatio || 1) : 0.8;
  const box = parent === document.body || parent.id === "visual-player" ? {width: window.innerWidth, height: window.innerHeight} : parent.getBoundingClientRect();
  engine.size(box.width * factor, box.height * factor);
  engine.drawn = false; bvSync();  // a resized canvas is blank: draw one still frame even while paused
}

// Draw only while it can be seen and something is playing.
function bvSync() {
  const engine = bvState.engine, canvas = bvState.canvas;
  if (!engine?.viz) return;
  const visible = bvState.where !== "off" && canvas?.isConnected && !document.hidden && (typeof canvas.getClientRects !== "function" || canvas.getClientRects().length > 0);
  if (visible && bvPlaying()) engine.run(); else engine.stop();
  if (visible && !bvPlaying() && !engine.drawn) { engine.drawn = true; engine.run(); setTimeout(() => { if (!bvPlaying()) engine.stop(); }, 400); }
}

function bvRefresh() {
  bvPlaceCanvas(); bvLayout(); bvSync();
  const header = document.querySelector("#bv-toggle");
  const on = bvState.where !== "off";
  if (header) { header.setAttribute("aria-pressed", String(on)); header.textContent = on ? "Visualizer on" : "Visualizer"; }
  for (const id of ["bv-lock", "bv-skip"]) { const node = document.querySelector("#" + id); if (node) node.hidden = !on; }
  const lock = document.querySelector("#bv-lock");
  if (lock) { lock.setAttribute("aria-pressed", String(bvState.preset !== "auto")); lock.textContent = bvState.preset !== "auto" ? "🔒 Locked" : "🔓 Lock"; lock.title = bvState.preset !== "auto" ? "Locked to this look · click to change automatically again" : "Keep this look instead of changing every 30 seconds"; }
  const where = document.querySelector("#bv-where"); if (where) where.value = bvState.where;
}

function bvSetWhere(where) {
  if (!bvPlaces.some(([id]) => id === where)) where = "off";
  bvSave("where", where);
  if (where !== "off") { bvSave("last", where); bvEnsureEngine(); }
  bvRefresh();
}

function bvToggle() { bvSetWhere(bvState.where === "off" ? bvState.last : "off"); }

function bvLockToggle() {
  const engine = bvState.engine;
  if (bvState.preset !== "auto") bvSave("preset", "auto");
  else if (engine?.current) bvSave("preset", engine.current);
  bvApplyPreset(false); bvFillPresetMenu(); bvRefresh();
}

function bvSkip() {
  const engine = bvState.engine;
  if (!engine?.viz) return;
  engine.next();
  if (bvState.preset !== "auto") bvSave("preset", engine.current);
  bvRefresh();
}

function bvFillPresetMenu() {
  const select = document.querySelector("#bv-preset"), engine = bvState.engine;
  if (!select) return;
  const names = engine?.names?.length ? engine.names : bvState.preset !== "auto" ? [bvState.preset] : [];
  select.innerHTML = `<option value="auto">Auto · changes every 30 s</option>` + names.map(n => `<option value="${pdEsc(n)}">${pdEsc(n)}</option>`).join("");
  select.value = names.includes(bvState.preset) ? bvState.preset : "auto";
}

function bvMenuHtml() {
  return `<fieldset><legend>Visualizer</legend>
    <label class="ps-select">Show <select id="bv-where">${bvPlaces.map(([id, label]) => `<option value="${id}"${bvState.where === id ? " selected" : ""}>${pdEsc(label)}</option>`).join("")}</select></label>
    <label class="ps-select">Look <select id="bv-preset"></select></label>
    <label class="ps-select">Quality <select id="bv-quality"><option value="low">Low · saves battery</option><option value="normal">Normal</option><option value="high">High</option></select></label>
    <button type="button" class="button small" id="bv-next-menu">Next look</button></fieldset>`;
}

function bvInstall() {
  const menu = document.querySelector("#theme-menu");
  if (menu && typeof menu.insertAdjacentHTML === "function" && !menu.querySelector("#bv-where")) {
    menu.insertAdjacentHTML("beforeend", bvMenuHtml());
    const quality = menu.querySelector("#bv-quality"); if (quality) quality.value = bvState.quality;
    bvFillPresetMenu();
    menu.addEventListener("change", event => {
      const id = event.target.id;
      if (id === "bv-where") bvSetWhere(event.target.value);
      if (id === "bv-preset") { bvSave("preset", event.target.value); bvApplyPreset(false); bvRefresh(); }
      if (id === "bv-quality") { bvSave("quality", event.target.value); if (bvState.engine) bvState.engine.fps = event.target.value === "low" ? 30 : 60; bvLayout(); }
    });
    menu.addEventListener("click", event => { if (event.target.closest?.("#bv-next-menu")) { if (bvState.where === "off") bvSetWhere(bvState.last); else bvSkip(); } });
  }
  const bar = document.querySelector(".visual-player-header > div");
  if (bar && typeof document.createElement === "function" && !document.querySelector("#bv-toggle")) {
    const make = (id, text, hidden) => { const b = document.createElement("button"); b.type = "button"; b.className = "button small"; b.id = id; b.textContent = text; b.hidden = hidden; return b; };
    bar.insertBefore(make("bv-skip", "Next look ⏭", true), bar.firstChild);
    bar.insertBefore(make("bv-lock", "🔓 Lock", true), bar.firstChild);
    bar.insertBefore(make("bv-toggle", "Visualizer", false), bar.firstChild);
  }
}

document.addEventListener("click", event => {
  if (event.target.closest?.("#bv-toggle")) bvToggle();
  else if (event.target.closest?.("#bv-lock")) bvLockToggle();
  else if (event.target.closest?.("#bv-skip")) bvSkip();
});

function bvStart() {
  try { bvInstall(); } catch { /* a missing piece of the page must never stop playback */ }
  if (typeof audio !== "undefined" && typeof audio.addEventListener === "function") for (const name of ["play", "playing", "pause", "ended", "emptied"]) audio.addEventListener(name, bvSync);
  document.addEventListener("visibilitychange", bvSync);
  if (typeof MutationObserver === "function" && document.body) new MutationObserver(() => { try { bvRefresh(); } catch { /* ignore */ } }).observe(document.body, {attributes: true, attributeFilter: ["class"]});
  window.addEventListener("resize", bvLayout);
  if (bvState.where !== "off") {
    // A saved choice can only start the sound graph after the first click (browsers need a gesture).
    const resume = () => { document.removeEventListener("pointerdown", resume, true); document.removeEventListener("keydown", resume, true); bvEnsureEngine(); bvRefresh(); };
    document.addEventListener("pointerdown", resume, true); document.addEventListener("keydown", resume, true);
    bvRefresh();
  }
}
bvStart();
