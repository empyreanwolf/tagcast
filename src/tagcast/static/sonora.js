"use strict";

// Sonora look (fork v76): a dark, glassy theme with big art and one accent colour, in the Theme menu.
// "Look" switches Classic / Sonora. "Accent" is Auto (taken from the playing album's cover) or a fixed colour.
// Sonora is always dark; the old light/dark choice comes back when you switch to Classic.
const sonoraKeys = {look: "tagcast-look", accent: "tagcast-accent", theme: "tagcast-look-prev-theme"};
const sonoraAccents = [
  {id: "cyan", name: "Cyan", color: "#86d7ee"}, {id: "pink", name: "Pink", color: "#f0879e"},
  {id: "violet", name: "Violet", color: "#a78bfa"}, {id: "green", name: "Green", color: "#6fd6a0"},
  {id: "orange", name: "Orange", color: "#f5a25d"}, {id: "red", name: "Red", color: "#f26d6d"},
  {id: "gold", name: "Gold", color: "#f0cf6b"},
];
const sonoraState = {look: "sonora", accent: "auto", autoColor: sonoraAccents[0].color, cover: ""};

function sonoraStored(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function sonoraStore(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } }

// A cover colour made bright and clear enough to sit on a near-black page.
function sonoraNormalize(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d + (g < b ? 6 : 0)) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return sonoraHsl(h * 60, d < 0.1 ? 0.15 : 0.7, 0.7);
}

function sonoraHsl(h, s, l) {
  const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = n => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
  return "#" + [f(0), f(8), f(4)].map(v => v.toString(16).padStart(2, "0")).join("");
}

// When the cover cannot be read (another site's picture), the album's name picks a steady colour instead.
function sonoraHashColor(text) {
  let hash = 0;
  for (const ch of String(text || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return sonoraHsl(hash % 360, 0.65, 0.7);
}

function sonoraCoverColor(src) {
  return new Promise(resolve => {
    if (!src || typeof Image !== "function" || typeof document.createElement !== "function") return resolve(null);
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 24;
        const ctx = canvas.getContext("2d", {willReadFrequently: true});
        ctx.drawImage(img, 0, 0, 24, 24);
        const data = ctx.getImageData(0, 0, 24, 24).data;
        let r = 0, g = 0, b = 0, w = 0;
        for (let i = 0; i < data.length; i += 4) {
          const max = Math.max(data[i], data[i + 1], data[i + 2]), min = Math.min(data[i], data[i + 1], data[i + 2]);
          const weight = (max - min) ** 2 + 1, bright = max > 30 && min < 235;
          if (!bright) continue;
          r += data[i] * weight; g += data[i + 1] * weight; b += data[i + 2] * weight; w += weight;
        }
        resolve(w ? sonoraNormalize(r / w, g / w, b / w) : null);
      } catch { resolve(null); }   // the picture is from another site: the canvas is locked
    };
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

function sonoraPaint(color) {
  const style = document.documentElement.style;
  style.setProperty("--sn-accent", color);
  style.setProperty("--sn-glow", color);
}

function sonoraRefreshAccent() {
  const fixed = sonoraAccents.find(a => a.id === sonoraState.accent);
  sonoraPaint(fixed ? fixed.color : sonoraState.autoColor);
}

async function sonoraFollowCover() {
  const art = document.querySelector("#player-art");
  const title = document.querySelector("#player-title");
  const src = art && art.getAttribute("src");
  if (!src) return;                                  // nothing playing: keep the last colour
  const key = src + "|" + (title ? title.textContent : "");
  if (key === sonoraState.cover) return;
  sonoraState.cover = key;
  const color = await sonoraCoverColor(src) || sonoraHashColor(title ? title.textContent : src);
  if (key !== sonoraState.cover) return;             // a newer song already took over
  sonoraState.autoColor = color;
  sonoraStore("tagcast-accent-last", color);
  if (sonoraState.accent === "auto") sonoraRefreshAccent();
}

function sonoraApplyLook(look, remember = true) {
  const root = document.documentElement;
  look = look === "classic" ? "classic" : "sonora";
  const was = sonoraState.look;
  sonoraState.look = look;
  if (look === "sonora") {
    if (remember && was !== "sonora") sonoraStore(sonoraKeys.theme, root.dataset.theme || "auto");
    root.dataset.look = "sonora";
    root.dataset.theme = "dark";
  } else {
    delete root.dataset.look;
    const before = sonoraStored(sonoraKeys.theme, "");
    if (before === "auto") delete root.dataset.theme;
    else if (before === "light" || before === "dark") root.dataset.theme = before;
    else { const own = sonoraStored("tagcast-theme", "auto"); if (own === "light" || own === "dark") root.dataset.theme = own; else delete root.dataset.theme; }
  }
  if (remember) sonoraStore(sonoraKeys.look, look);
  for (const value of ["classic", "sonora"]) { const box = document.querySelector("#look-" + value); if (box) box.checked = look === value; }
  if (typeof showTheme === "function") showTheme();
  if (look === "sonora") sonoraFollowCover();
}

function sonoraSetAccent(id, remember = true) {
  sonoraState.accent = id === "auto" || sonoraAccents.some(a => a.id === id) ? id : "auto";
  if (remember) sonoraStore(sonoraKeys.accent, sonoraState.accent);
  for (const a of ["auto", ...sonoraAccents.map(a => a.id)]) { const box = document.querySelector("#accent-" + a); if (box) box.checked = sonoraState.accent === a; }
  sonoraRefreshAccent();
}

function sonoraMenuHtml() {
  const swatches = [`<label title="Auto: follows the playing album"><input type="radio" name="sn-accent" id="accent-auto" value="auto" data-accent-choice><span class="sn-swatch auto"></span> Auto</label>`]
    .concat(sonoraAccents.map(a => `<label title="${a.name}"><input type="radio" name="sn-accent" id="accent-${a.id}" value="${a.id}" aria-label="${a.name}" data-accent-choice><span class="sn-swatch" style="--c:${a.color}"></span></label>`));
  return `<fieldset><legend>Look</legend><label><input type="radio" name="sn-look" id="look-sonora" value="sonora" data-look-choice> Sonora · dark, big art</label><label><input type="radio" name="sn-look" id="look-classic" value="classic" data-look-choice> Classic</label></fieldset>
    <fieldset class="sn-only"><legend>Accent colour</legend><div class="sn-menu-colours">${swatches.join("")}<small>Auto follows the album that is playing.</small></div></fieldset>`;
}

function sonoraInstallMenu() {
  const menu = document.querySelector("#theme-menu");
  if (!menu || typeof menu.insertAdjacentHTML !== "function" || menu.querySelector("#look-sonora")) return;
  menu.insertAdjacentHTML("afterbegin", sonoraMenuHtml());
  menu.addEventListener("change", event => {
    const t = event.target;
    if (t.dataset && t.dataset.lookChoice !== undefined) sonoraApplyLook(t.value);
    else if (t.dataset && t.dataset.accentChoice !== undefined) sonoraSetAccent(t.value);
  });
}

function sonoraStart() {
  sonoraState.accent = sonoraStored(sonoraKeys.accent, "auto");
  const last = sonoraStored("tagcast-accent-last", "");
  if (/^#[0-9a-f]{6}$/i.test(last)) sonoraState.autoColor = last;
  sonoraInstallMenu();
  sonoraApplyLook(sonoraStored(sonoraKeys.look, "sonora"), false);
  sonoraSetAccent(sonoraState.accent, false);
  if (typeof MutationObserver === "function") {
    const art = document.querySelector("#player-art"), title = document.querySelector("#player-title");
    const watch = new MutationObserver(() => sonoraFollowCover());
    if (art) watch.observe(art, {attributes: true, attributeFilter: ["src"]});
    if (title) watch.observe(title, {childList: true, characterData: true, subtree: true});
  }
  sonoraFollowCover();
}
sonoraStart();
