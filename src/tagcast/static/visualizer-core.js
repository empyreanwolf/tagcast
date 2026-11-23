"use strict";

// Visualizer engine (fork v78): Butterchurn, the Milkdrop-style visualizer (github.com/jberg/butterchurn), loaded
// from the jsDelivr CDN the first time it is switched on, so Tagcast itself stays small. This file has no page
// code of its own, so the Mini Player window loads it too.
const bvCdn = {
  lib: "https://cdn.jsdelivr.net/npm/butterchurn@2.6.7/lib/butterchurn.min.js",
  presets: "https://cdn.jsdelivr.net/npm/butterchurn-presets@2.4.7/lib/butterchurnPresets.min.js",
};
let bvLoading = null;

// fork v115, from upstream Tagcast 1.9.8: three more presets, written as functions (Butterchurn accepts them as they are).
// Ember Tunnel and Kaleido Bloom are adapted from Audiofilus (MIT, copyright (c) 2026 Juan Villacorta; see
// vendor/audiofilus/LICENSE). Stereo Vectorscope draws left against right: the sum runs up, the difference sideways.
const bvKeep = (value, low, high) => Math.min(high, Math.max(low, value));
const bvSame = state => state;
const bvOff = {baseVals: {enabled: 0}};

function bvExtraPresets() {
  return {
    "Tagcast · Ember Tunnel": {
      baseVals: {gammaadj: 1.3, decay: .96, echo_zoom: 1.006, echo_alpha: .3, wave_mode: 0, wave_thick: 1, wave_brighten: 1,
        darken_center: 1, wave_a: .7, wave_scale: 1.2, wave_mystery: -.2, zoom: 1.04, rot: .01, warp: .3, wave_r: 1, wave_g: .5, wave_b: .1},
      init_eqs: state => { state.spin = 0; return state; },
      frame_eqs: state => {
        state.spin += .002 + .01 * bvKeep(state.bass_att - 1, 0, 2);
        state.rot = .02 * Math.sin(state.time * .3 + state.spin) + .03 * bvKeep(state.bass_att - 1, -1, 2);
        state.zoom = 1.03 + .04 * bvKeep(state.bass - 1, 0, 2);
        state.wave_r = .9 + .1 * Math.sin(state.time * 1.3);
        state.wave_g = .35 + .2 * Math.sin(state.time * .7 + state.treb_att);
        state.wave_b = .05 + .05 * Math.sin(state.time * .5);
        state.wave_mystery = -.2 + .15 * bvKeep(state.mid_att - 1, -1, 2);
        state.q1 = .5 + .5 * Math.sin(state.time * .4);
        return state;
      },
      pixel_eqs: state => {
        state.rot += .04 * Math.sin(state.rad * 6 - state.time * 2) * state.q1;
        state.zoom += .02 * state.rad;
        return state;
      },
      shapes: [], waves: [], warp: "", comp: "",
    },
    "Tagcast · Kaleido Bloom": {
      baseVals: {gammaadj: 1.2, decay: .95, echo_zoom: 1, echo_alpha: .5, echo_orient: 3, wave_mode: 1, wave_brighten: 1, wave_a: .6,
        wave_scale: .8, zoom: .985, rot: .01, warp: .1, wave_r: 1, wave_g: .8, wave_b: 1},
      init_eqs: state => { state.q3 = 0; return state; },
      frame_eqs: state => {
        state.q1 = .6 + .4 * Math.sin(state.time * .31);
        state.q2 = .4 + .4 * Math.sin(state.time * .47 + 2);
        state.q3 = bvKeep(state.mid_att - 1, -1, 2);
        state.rot = .01 + .02 * Math.sin(state.time * .13);
        state.wave_r = state.q1;
        state.wave_g = state.q2;
        return state;
      },
      pixel_eqs: bvSame,
      shapes: [
        {baseVals: {enabled: 1, sides: 6, thickoutline: 1, num_inst: 6, x: .5, y: .5, rad: .06, r: 1, g: .3, b: .6, a: .6, r2: .2, g2: .1, b2: .8, a2: 0, border_a: .25},
          init_eqs: bvSame,
          frame_eqs: state => {
            const angle = state.instance / 6 * Math.PI * 2 + state.time * .5, radius = .22 + .05 * state.q3;
            state.x = .5 + radius * Math.cos(angle) * state.aspecty;
            state.y = .5 + radius * Math.sin(angle);
            state.rad = .04 + .04 * bvKeep(state.bass_att, 0, 2);
            state.ang = -angle * 2;
            state.r = state.q1; state.g = .25 * state.q2; state.b = 1 - state.q1 * .6;
            return state;
          }},
        {baseVals: {enabled: 1, sides: 24, textured: 1, num_inst: 1, x: .5, y: .5, rad: .2, tex_zoom: 1.6, r: 1, g: 1, b: 1, a: .6, r2: 1, g2: 1, b2: 1, a2: 0, border_a: 0},
          init_eqs: bvSame,
          frame_eqs: state => { state.tex_ang = state.time * .2; state.rad = .18 + .03 * Math.sin(state.time * .9); return state; }},
        bvOff, bvOff,   // Butterchurn visits all four shape slots when custom shapes are present
      ],
      waves: [], warp: "", comp: "",
    },
    "Tagcast · Stereo Vectorscope": {
      baseVals: {gammaadj: 1.1, decay: .82, wave_a: 0, zoom: 1, rot: 0, warp: 0, ob_a: 0, ib_a: 0},
      init_eqs: bvSame,
      frame_eqs: state => state,
      pixel_eqs: bvSame,
      shapes: [bvOff, bvOff, bvOff, bvOff],
      waves: [
        {baseVals: {enabled: 1, samples: 256, sep: 0, scaling: 1, smoothing: .35, r: .35, g: 1, b: .55, a: .6, usedots: 0, spectrum: 0, thick: 0, additive: 1},
          init_eqs: bvSame,
          frame_eqs: state => { state.r = .3 + .2 * Math.sin(state.time * .4); state.b = .5 + .3 * Math.sin(state.time * .3 + 2); return state; },
          point_eqs: state => {
            state.x = .5 + (state.value1 - state.value2) * .45 / (state.aspectx || 1);   // difference runs sideways
            state.y = .5 + (state.value1 + state.value2) * .45;                          // sum runs up
            return state;
          }},
        bvOff, bvOff, bvOff,
      ],
      warp: "", comp: "",
    },
  };
}

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
    return {lib, presets: {...bvExtraPresets(), ...presets}};
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
