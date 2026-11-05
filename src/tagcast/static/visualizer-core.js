"use strict";

// Visualizer engine (fork v78): Butterchurn, the Milkdrop-style visualizer (github.com/jberg/butterchurn), loaded
// from the jsDelivr CDN the first time it is switched on, so Tagcast itself stays small. This file has no page
// code of its own, so the Mini Player window loads it too.
const bvCdn = {
  lib: "https://cdn.jsdelivr.net/npm/butterchurn@2.6.7/lib/butterchurn.min.js",
  presets: "https://cdn.jsdelivr.net/npm/butterchurn-presets@2.4.7/lib/butterchurnPresets.min.js",
};
let bvLoading = null;

function bvScript(src) {
  return new Promise((resolve, reject) => {
    const node = document.createElement("script");
    node.src = src; node.async = true;
    node.onload = resolve;
    node.onerror = () => reject(new Error("The visualizer could not be downloaded (no internet?)"));
    document.head.appendChild(node);
  });
}

function bvLoad() {
  bvLoading ||= Promise.all([bvScript(bvCdn.lib), bvScript(bvCdn.presets)]).then(() => {
    const lib = window.butterchurn?.default || window.butterchurn, pack = window.butterchurnPresets;
    const presets = pack?.getPresets ? pack.getPresets() : pack?.default?.getPresets?.();
    if (!lib?.createVisualizer || !presets) throw new Error("The visualizer files did not load correctly");
    return {lib, presets};
  }).catch(error => { bvLoading = null; throw error; });
  return bvLoading;
}

// Pretend sound for windows that cannot hear the music (the Mini Player): a steady beat with a moving wave.
function bvFakeLevels(ms) {
  const t = ms / 1000, beat = Math.pow(Math.max(0, Math.sin(t * Math.PI * 4)), 3), size = 1024;
  const make = shift => Uint8Array.from({length: size}, (_, i) => Math.max(0, Math.min(255, 128 + (20 + 90 * (0.25 + 0.75 * beat)) * (0.6 * Math.sin(i * 0.07 + t * 9 + shift) + 0.4 * Math.sin(i * 0.23 + t * 14)))));
  const wave = make(0);
  return {timeByteArray: wave, timeByteArrayL: wave, timeByteArrayR: make(1.3)};
}

// One visualizer drawing into one canvas. options: {context, node} for real sound, or neither for the pretend one.
function bvEngine(canvas, options = {}) {
  const e = {canvas, viz: null, names: [], presets: {}, current: "", lock: false, running: false, raf: 0, changed: 0, last: 0, fps: 60, fake: !options.node, onPreset: null};
  e.ready = bvLoad().then(({lib, presets}) => {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    e.context = options.context || new Ctx();
    e.presets = presets; e.names = Object.keys(presets);
    e.viz = lib.createVisualizer(e.context, canvas, {width: canvas.width || 320, height: canvas.height || 240, pixelRatio: 1, textureRatio: 1});
    if (options.node) { try { e.viz.connectAudio(options.node); e.fake = false; } catch { e.fake = true; } }
    return e;
  });
  e.size = (w, h) => {
    w = Math.max(32, Math.round(w)); h = Math.max(32, Math.round(h));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; try { e.viz?.setRendererSize(w, h); } catch { /* lost context */ } }
  };
  e.choose = (name, blend = 2.5) => {
    if (!e.viz || !e.presets[name]) return false;
    try { e.viz.loadPreset(e.presets[name], blend); } catch { return false; }
    e.current = name; e.changed = performance.now();
    e.onPreset?.(name);
    return true;
  };
  e.next = () => {
    const others = e.names.filter(n => n !== e.current);
    return e.choose(others[Math.floor(Math.random() * others.length)] || e.names[0]);
  };
  const frame = ms => {
    e.raf = 0;
    if (!e.running) return;
    e.raf = requestAnimationFrame(frame);
    if (ms - e.last < 1000 / e.fps - 2) return;
    e.last = ms;
    if (!e.lock && e.viz && ms - e.changed > 30000) e.next();
    try { e.viz.render(e.fake ? {audioLevels: bvFakeLevels(ms)} : undefined); } catch { e.running = false; }
  };
  e.run = () => { if (!e.viz) return; e.running = true; if (!e.raf) e.raf = requestAnimationFrame(frame); };
  e.stop = () => { e.running = false; if (e.raf) cancelAnimationFrame(e.raf); e.raf = 0; };
  return e;
}
