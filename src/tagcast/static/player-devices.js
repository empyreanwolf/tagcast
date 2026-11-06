"use strict";

// Player devices (fork v77): twenty small drawings of how people have listened to music, from the horn
// phonograph to a festival stage (hip hop albums get a rapper). Each one shows the album cover somewhere (a label, a screen, a tape pack) and moves
// while a song plays. This file has no DOM or player code, so the Mini Player window loads it too.
const pdYears = {  // which device an album's year gets, in order: [last year of the range, device]
  ranges: [[1909, "phonograph"], [1929, "gramophone"], [1939, "radio"], [1949, "jukebox"], [1959, "transistor"], [1964, "reel"],
    [1974, "vinyl"], [1979, "carradio"], [1984, "boombox"], [1989, "walkman"], [1993, "cassette"], [1996, "minidisc"],
    [1999, "portablecd"], [2001, "cd"], [2003, "mp3"], [2007, "ipod"], [2012, "headphones"], [2017, "dj"], [9999, "stage"]],
};

function pdEsc(text) { return String(text ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); }

// The cover, drawn inside a shape. Without a cover the shape gets a pink-to-violet gradient.
function pdArtTools(uid, art) {
  let n = 0;
  const image = (x, y, w, h) => art ? `<image href="${pdEsc(art)}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid slice"/>` : "";
  return {
    rect(x, y, w, h, r = 0) {
      const id = `${uid}k${n++}`;
      return `<clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/></clipPath><g clip-path="url(#${id})"><rect x="${x}" y="${y}" width="${w}" height="${h}" fill="url(#${uid}g)"/>${image(x, y, w, h)}</g>`;
    },
    circle(cx, cy, r) {
      const id = `${uid}k${n++}`;
      return `<clipPath id="${id}"><circle cx="${cx}" cy="${cy}" r="${r}"/></clipPath><g clip-path="url(#${id})"><circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#${uid}g)"/>${image(cx - r, cy - r, r * 2, r * 2)}</g>`;
    },
  };
}

const pdEq = (x, y, count, w, h, gap, color) => Array.from({length: count}, (_, i) =>
  `<rect class="a-eq" x="${x + i * (w + gap)}" y="${y}" width="${w}" height="${h}" rx="${w / 2}" fill="${color}" style="animation-delay:${(i * 0.17).toFixed(2)}s"/>`).join("");
const pdSpokes = (cx, cy, r, color, width = 3) => `<circle cx="${cx}" cy="${cy}" r="${(r * 0.5).toFixed(1)}" fill="${color}"/>` + [0, 60, 120, 180, 240, 300].map(a => {
  const rad = (a - 90) * Math.PI / 180;
  return `<line x1="${(cx + r * 0.6 * Math.cos(rad)).toFixed(1)}" y1="${(cy + r * 0.6 * Math.sin(rad)).toFixed(1)}" x2="${(cx + r * Math.cos(rad)).toFixed(1)}" y2="${(cy + r * Math.sin(rad)).toFixed(1)}" stroke="${color}" stroke-width="${width}" stroke-linecap="round"/>`;
}).join("");

const pdDevices = [
  {id: "phonograph", name: "Phonograph", era: "1877", body: (A) => `
    <rect x="70" y="205" width="260" height="56" rx="8" fill="url(#W)" stroke="#2a1d14" stroke-width="2"/>
    <rect x="130" y="150" width="110" height="46" rx="8" fill="#1a1410"/>${A.rect(134, 154, 102, 38, 6)}
    <g class="a-wave" style="transform-origin:185px 173px"><rect x="134" y="154" width="102" height="38" rx="6" fill="#000" opacity="0.18"/></g>
    <line x1="240" y1="175" x2="256" y2="175" stroke="#b9c2c6" stroke-width="5" stroke-linecap="round"/>
    <path d="M254 163 L334 66 Q352 56 370 84 L256 188 Z" fill="url(#B)" stroke="#6b4f18" stroke-width="2"/>
    <ellipse cx="352" cy="74" rx="16" ry="34" transform="rotate(40 352 74)" fill="#5b4210" stroke="#d8b25a" stroke-width="3"/>
    <path class="a-pulse" d="M370 40 q22 14 14 42 M382 28 q30 24 18 62" fill="none" stroke="#f0cf6b" stroke-width="3" stroke-linecap="round"/>
    <path d="M130 172 q-26 0 -34 22" fill="none" stroke="#b9c2c6" stroke-width="5" stroke-linecap="round"/><circle cx="92" cy="198" r="7" fill="#b9c2c6"/>`},
  {id: "gramophone", name: "Gramophone", era: "1887", body: (A) => `
    <rect x="90" y="196" width="200" height="66" rx="8" fill="url(#W)" stroke="#2a1d14" stroke-width="2"/>
    <ellipse cx="190" cy="190" rx="98" ry="20" fill="#0f1417" stroke="#33444c" stroke-width="2"/>
    <ellipse class="a-dash" cx="190" cy="190" rx="70" ry="13" fill="none" stroke="#2c373d" stroke-width="2" stroke-dasharray="14 10"/>
    <ellipse cx="190" cy="190" rx="26" ry="6" fill="url(#g)"/>
    <g style="transform-origin:190px 190px"><circle cx="190" cy="190" r="3" fill="#080c0f"/></g>
    <path d="M186 176 L318 44 Q348 36 366 76 L222 192 Z" fill="url(#B)" stroke="#6b4f18" stroke-width="2"/>
    <ellipse cx="344" cy="58" rx="18" ry="40" transform="rotate(40 344 58)" fill="#5b4210" stroke="#d8b25a" stroke-width="3"/>
    <line x1="120" y1="186" x2="176" y2="190" stroke="#cfd8dc" stroke-width="5" stroke-linecap="round"/>
    <path class="a-pulse" d="M366 30 q22 16 12 46" fill="none" stroke="#f0cf6b" stroke-width="3" stroke-linecap="round"/>
    <rect x="260" y="220" width="22" height="9" rx="3" fill="#2a1d14"/>`},
  {id: "radio", name: "Wooden radio", era: "1930s", body: (A) => `
    <path d="M86 262 V120 Q86 52 200 52 Q314 52 314 120 V262 Z" fill="url(#W)" stroke="#2a1d14" stroke-width="3"/>
    <rect x="106" y="112" width="124" height="130" rx="10" fill="#1a120c"/>${A.rect(110, 116, 116, 122, 8)}
    <rect x="110" y="116" width="116" height="122" rx="8" fill="#1a120c" opacity="0.5"/>
    ${[126, 138, 150, 162, 174, 186, 198, 210, 222].map(y => `<line x1="112" y1="${y}" x2="224" y2="${y}" stroke="#000" stroke-opacity="0.35" stroke-width="2"/>`).join("")}
    <circle cx="268" cy="140" r="36" fill="#f3e3b0" stroke="#2a1d14" stroke-width="3"/>
    <circle class="a-pulse" cx="268" cy="140" r="30" fill="none" stroke="#e6b44c" stroke-width="3"/>
    <line class="a-sweep" x1="268" y1="140" x2="268" y2="114" stroke="#b3261e" stroke-width="3" stroke-linecap="round" style="transform-origin:268px 140px"/>
    <circle cx="256" cy="210" r="13" fill="#2a1d14" stroke="#d8b25a" stroke-width="2"/><circle cx="288" cy="210" r="13" fill="#2a1d14" stroke="#d8b25a" stroke-width="2"/>`},
  {id: "jukebox", name: "Jukebox", era: "1940s", body: (A) => `
    <path d="M110 268 V118 A90 90 0 0 1 290 118 V268 Z" fill="url(#W)" stroke="#2a1d14" stroke-width="3"/>
    <path d="M134 262 V122 A66 66 0 0 1 266 122 V262 Z" fill="#080c0f" stroke="#d8b25a" stroke-width="3"/>
    <g class="a-spin" style="transform-origin:200px 160px"><circle cx="200" cy="160" r="46" fill="#101417" stroke="#2c373d" stroke-width="2"/><circle cx="200" cy="160" r="36" fill="none" stroke="#1f272c"/>${A.circle(200, 160, 16)}<circle cx="200" cy="160" r="3" fill="#080c0f"/></g>
    <path class="a-pulse" d="M122 270 V118 A78 78 0 0 1 278 118 V270" fill="none" stroke="#f0879e" stroke-width="5" stroke-linecap="round"/>
    <path class="a-pulse" style="animation-delay:.6s" d="M100 270 V118 A100 100 0 0 1 300 118 V270" fill="none" stroke="#86d7ee" stroke-width="5" stroke-linecap="round"/>
    <rect x="150" y="226" width="100" height="26" rx="6" fill="#1a1410" stroke="#d8b25a" stroke-width="2"/>
    ${[162, 178, 194, 210, 226].map(x => `<rect x="${x}" y="233" width="10" height="12" rx="2" fill="#e8eef0" opacity="0.8"/>`).join("")}`},
  {id: "transistor", name: "Transistor radio", era: "1950s", body: (A) => `
    <path d="M120 84 Q120 40 200 40 Q280 40 280 84" fill="none" stroke="#8a6a40" stroke-width="9" stroke-linecap="round"/>
    <rect x="80" y="80" width="240" height="170" rx="22" fill="url(#O)" stroke="#6b4f2c" stroke-width="3"/>
    <rect x="100" y="100" width="116" height="130" rx="10" fill="#2b2118"/>${A.rect(104, 104, 108, 122, 8)}
    <rect x="104" y="104" width="108" height="122" rx="8" fill="#000" opacity="0.4"/>
    ${[0, 1, 2, 3, 4, 5].map(i => `<line x1="108" y1="${116 + i * 20}" x2="208" y2="${116 + i * 20}" stroke="#2b2118" stroke-width="5" stroke-linecap="round"/>`).join("")}
    <circle cx="266" cy="140" r="30" fill="#f4ecd6" stroke="#6b4f2c" stroke-width="3"/>
    <line class="a-sweep" x1="266" y1="140" x2="266" y2="116" stroke="#b3261e" stroke-width="3" stroke-linecap="round" style="transform-origin:266px 140px"/>
    <circle cx="266" cy="206" r="14" fill="#6b4f2c"/><circle cx="266" cy="206" r="4" fill="#f4ecd6"/>`},
  {id: "reel", name: "Reel-to-reel", era: "1950s–60s", body: (A) => `
    <rect x="10" y="30" width="380" height="240" rx="20" fill="#151c20" stroke="#2b383f" stroke-width="2"/>
    <path d="M110 188 L140 236 L260 236 L290 170" fill="none" stroke="#6b4c3a" stroke-width="2"/>
    <circle cx="110" cy="132" r="76" fill="#0f1519" stroke="#aeb8bc" stroke-width="5"/>
    <g class="a-spin" style="transform-origin:110px 132px">${A.circle(110, 132, 54)}<circle cx="110" cy="98" r="13" fill="#0f1519"/><circle cx="139.4" cy="149" r="13" fill="#0f1519"/><circle cx="80.6" cy="149" r="13" fill="#0f1519"/><circle cx="110" cy="132" r="13" fill="#aeb8bc"/><circle cx="110" cy="132" r="5" fill="#080c0f"/></g>
    <circle cx="290" cy="132" r="76" fill="#0f1519" stroke="#aeb8bc" stroke-width="5"/><circle cx="290" cy="132" r="38" fill="#3a2a22"/>
    <g class="a-spin" style="transform-origin:290px 132px"><circle cx="290" cy="98" r="13" fill="#0f1519"/><circle cx="319.4" cy="149" r="13" fill="#0f1519"/><circle cx="260.6" cy="149" r="13" fill="#0f1519"/><circle cx="290" cy="132" r="13" fill="#aeb8bc"/><circle cx="290" cy="132" r="5" fill="#080c0f"/></g>
    <circle cx="140" cy="236" r="5" fill="#aeb8bc"/><circle cx="260" cy="236" r="5" fill="#aeb8bc"/><rect x="180" y="228" width="40" height="16" rx="3" fill="#aeb8bc"/>`},
  {id: "vinyl", name: "Vinyl record", era: "1948", body: (A) => `
    <g class="a-spin slow" style="transform-origin:160px 150px"><circle cx="160" cy="150" r="130" fill="#101417"/>
    ${[120, 108, 96, 84, 72, 60].map(r => `<circle cx="160" cy="150" r="${r}" fill="none" stroke="#1f272c"/>`).join("")}
    <path d="M160 150 L160 20 A130 130 0 0 1 217 32 Z" fill="#fff" opacity="0.07"/><path d="M160 150 L160 280 A130 130 0 0 1 103 268 Z" fill="#fff" opacity="0.07"/>
    ${A.circle(160, 150, 46)}<circle cx="160" cy="150" r="30" fill="none" stroke="#fff" stroke-opacity="0.35"/><circle cx="160" cy="150" r="5" fill="#080c0f"/></g>
    <g class="pd-arm" style="transform-origin:335px 45px"><line x1="335" y1="45" x2="350" y2="16" stroke="#b6c0c4" stroke-width="10" stroke-linecap="round"/><line x1="335" y1="45" x2="245" y2="215" stroke="#b6c0c4" stroke-width="6" stroke-linecap="round"/><rect x="232" y="208" width="26" height="14" rx="3" fill="#e8eef0" transform="rotate(-28 245 215)"/></g>
    <circle cx="335" cy="45" r="18" fill="#232c32" stroke="#3a4a52" stroke-width="2"/><circle cx="335" cy="45" r="6" fill="#b6c0c4"/>`},
  {id: "carradio", name: "Car radio", era: "1970s", body: (A) => `
    <rect x="40" y="90" width="320" height="130" rx="12" fill="#161c20" stroke="#aeb8bc" stroke-width="3"/>
    <circle cx="92" cy="155" r="30" fill="#232c32" stroke="#aeb8bc" stroke-width="3"/>${Array.from({length: 12}, (_, i) => { const a = i * 30 * Math.PI / 180; return `<line x1="${(92 + 24 * Math.sin(a)).toFixed(1)}" y1="${(155 - 24 * Math.cos(a)).toFixed(1)}" x2="${(92 + 29 * Math.sin(a)).toFixed(1)}" y2="${(155 - 29 * Math.cos(a)).toFixed(1)}" stroke="#aeb8bc"/>`; }).join("")}
    <circle cx="308" cy="155" r="30" fill="#232c32" stroke="#aeb8bc" stroke-width="3"/><circle cx="308" cy="155" r="5" fill="#aeb8bc"/>
    <rect x="140" y="108" width="120" height="94" rx="8" fill="#080c0f" stroke="#33444c" stroke-width="2"/>${A.rect(144, 112, 112, 60, 5)}<rect x="144" y="112" width="112" height="60" rx="5" fill="#000" opacity="0.3"/>
    <rect x="148" y="182" width="104" height="12" rx="3" fill="#1a2227"/><rect class="a-sweep" x="152" y="183" width="4" height="10" fill="#f5a25d" style="transform-box:fill-box"/>
    ${[0, 1, 2, 3, 4].map(i => `<rect x="${70 + i * 50}" y="232" width="34" height="14" rx="4" fill="#232c32" stroke="#aeb8bc"/>`).join("")}`},
  {id: "boombox", name: "Boombox", era: "1966–80s", body: (A) => `
    <path d="M110 92 V58 H290 V92" fill="none" stroke="#9aa6ab" stroke-width="9" stroke-linecap="round"/>
    <rect x="30" y="86" width="340" height="170" rx="16" fill="#222a2e" stroke="#3a4a52" stroke-width="3"/>
    <g class="a-throb" style="transform-origin:96px 176px"><circle cx="96" cy="176" r="52" fill="#11181c" stroke="#aeb8bc" stroke-width="3"/><circle cx="96" cy="176" r="34" fill="#1a2227" stroke="#33444c" stroke-width="2"/><circle cx="96" cy="176" r="12" fill="#080c0f"/></g>
    <g class="a-throb" style="transform-origin:304px 176px"><circle cx="304" cy="176" r="52" fill="#11181c" stroke="#aeb8bc" stroke-width="3"/><circle cx="304" cy="176" r="34" fill="#1a2227" stroke="#33444c" stroke-width="2"/><circle cx="304" cy="176" r="12" fill="#080c0f"/></g>
    <rect x="158" y="104" width="84" height="56" rx="6" fill="#080c0f" stroke="#33444c" stroke-width="2"/>${A.rect(164, 112, 72, 30, 3)}
    <circle cx="180" cy="152" r="5" fill="none" stroke="#9aa6ab"/><circle cx="220" cy="152" r="5" fill="none" stroke="#9aa6ab"/>
    ${pdEq(166, 170, 7, 6, 22, 4, "#86d7ee")}<rect x="160" y="212" width="80" height="20" rx="4" fill="#161c20"/>`},
  {id: "walkman", name: "Walkman", era: "1979", body: (A) => `
    <rect x="70" y="10" width="260" height="280" rx="24" fill="url(#M)"/><rect x="326" y="60" width="9" height="60" rx="4" fill="#5c6b72"/>
    <rect x="94" y="34" width="212" height="128" rx="10" fill="#080c0f" stroke="#5c6b72" stroke-width="2"/><rect x="106" y="46" width="188" height="104" rx="8" fill="#1a2227"/>
    ${A.rect(118, 56, 164, 40, 4)}
    <circle cx="156" cy="124" r="17" fill="none" stroke="#9aa6ab" stroke-width="3"/><circle cx="244" cy="124" r="17" fill="none" stroke="#9aa6ab" stroke-width="3"/>
    <g class="a-spin fast" style="transform-origin:156px 124px">${pdSpokes(156, 124, 14, "#e8eef0")}</g><g class="a-spin fast" style="transform-origin:244px 124px">${pdSpokes(244, 124, 14, "#e8eef0")}</g>
    ${[94, 153, 212, 271].map(x => `<rect x="${x - 3}" y="190" width="50" height="54" rx="8" fill="#2b383f"/>`).join("")}
    <polygon points="112,207 128,217 112,227" fill="#f0879e"/><rect x="170" y="209" width="16" height="16" rx="2" fill="#e8eef0"/>
    <polygon points="241,207 227,217 241,227" fill="#e8eef0"/><polygon points="250,207 236,217 250,227" fill="#e8eef0"/><polygon points="282,207 296,217 282,227" fill="#e8eef0"/><polygon points="291,207 305,217 291,227" fill="#e8eef0"/>`},
  {id: "cassette", name: "Cassette", era: "1963–90s", body: (A) => `
    <rect x="30" y="40" width="340" height="220" rx="16" fill="#1a2227" stroke="#33444c" stroke-width="2"/>${A.rect(52, 58, 296, 100, 8)}
    <rect x="52" y="138" width="296" height="20" fill="#080c0f" opacity="0.35"/><rect x="66" y="74" width="140" height="8" rx="4" fill="#fff" opacity="0.85"/>
    <rect x="116" y="168" width="168" height="54" rx="27" fill="#080c0f" stroke="#33444c" stroke-width="2"/>
    <circle cx="160" cy="195" r="20" fill="none" stroke="#9aa6ab" stroke-width="3"/><circle cx="240" cy="195" r="20" fill="none" stroke="#9aa6ab" stroke-width="3"/>
    <g class="a-spin fast" style="transform-origin:160px 195px">${pdSpokes(160, 195, 16, "#e8eef0")}</g><g class="a-spin fast" style="transform-origin:240px 195px">${pdSpokes(240, 195, 16, "#e8eef0")}</g>
    <path d="M80 260 L104 226 L296 226 L320 260 Z" fill="#11181c" stroke="#33444c" stroke-width="2"/><circle cx="130" cy="244" r="5" fill="#080c0f"/><circle cx="270" cy="244" r="5" fill="#080c0f"/>`},
  {id: "minidisc", name: "MiniDisc", era: "1992", body: (A) => `
    <rect x="100" y="24" width="200" height="200" rx="12" fill="url(#T)" stroke="#6c8d9a" stroke-width="3"/>
    <g class="a-spin" style="transform-origin:200px 128px"><circle cx="200" cy="128" r="78" fill="#cfd8dc" opacity="0.9"/>${A.circle(200, 128, 74)}<circle cx="200" cy="128" r="16" fill="#cfd8dc"/><circle cx="200" cy="128" r="5" fill="#080c0f"/></g>
    <rect x="160" y="24" width="80" height="56" rx="4" fill="#cfd8dc" stroke="#8a979c" stroke-width="2"/><rect x="190" y="32" width="20" height="36" rx="3" fill="#8a979c"/>
    <rect x="90" y="236" width="220" height="44" rx="8" fill="#232c32" stroke="#3a4a52" stroke-width="2"/><rect x="104" y="246" width="90" height="24" rx="3" fill="#2f4a3d"/>${pdEq(110, 250, 8, 6, 16, 4, "#9bf0b8")}<rect x="214" y="256" width="80" height="6" rx="3" fill="#080c0f"/>`},
  {id: "portablecd", name: "Portable CD player", era: "1984", body: (A) => `
    <rect x="70" y="30" width="260" height="240" rx="64" fill="url(#M)" stroke="#6c7b82" stroke-width="3"/>
    <circle cx="200" cy="146" r="98" fill="#1a2227" stroke="#5c6b72" stroke-width="3"/>
    <g class="a-spin fast" style="transform-origin:200px 146px">${A.circle(200, 146, 92)}<circle cx="170" cy="118" r="38" fill="#fff" opacity="0.12"/><circle cx="200" cy="146" r="22" fill="#cdd6da" opacity="0.92"/><circle cx="200" cy="146" r="8" fill="#080c0f"/></g>
    <rect x="164" y="248" width="72" height="14" rx="4" fill="#2f4a3d"/><circle cx="100" cy="70" r="8" fill="#5c6b72"/><circle cx="300" cy="70" r="8" fill="#5c6b72"/>`},
  {id: "cd", name: "CD", era: "1982", body: (A) => `
    <g class="a-spin" style="transform-origin:200px 150px"><circle cx="200" cy="150" r="140" fill="url(#g)"/>${A.circle(200, 150, 140)}<circle cx="170" cy="110" r="56" fill="#fff" opacity="0.1"/></g>
    <circle cx="200" cy="150" r="140" fill="url(#S)"/><circle cx="200" cy="150" r="140" fill="none" stroke="#e8eef0" stroke-opacity="0.4" stroke-width="2"/>
    <circle cx="200" cy="150" r="58" fill="none" stroke="#fff" stroke-opacity="0.25"/><circle cx="200" cy="150" r="46" fill="#cdd6da" fill-opacity="0.9"/><circle cx="200" cy="150" r="15" fill="#080c0f"/>`},
  {id: "mp3", name: "MP3 player", era: "1998", body: (A) => `
    <rect x="130" y="20" width="140" height="260" rx="18" fill="url(#M)" stroke="#6c7b82" stroke-width="3"/>
    <rect x="148" y="40" width="104" height="84" rx="5" fill="#b9d3b0" stroke="#3a4a52" stroke-width="3"/>${A.rect(154, 46, 38, 38, 2)}<rect x="154" y="46" width="38" height="38" fill="#2f4a3d" opacity="0.35"/>
    <rect x="198" y="52" width="46" height="5" fill="#2f4a3d"/><rect x="198" y="64" width="34" height="5" fill="#2f4a3d"/>${pdEq(156, 92, 10, 6, 24, 4, "#2f4a3d")}
    <circle cx="200" cy="200" r="48" fill="#e8eef0" stroke="#aeb8bc" stroke-width="2"/><circle cx="200" cy="200" r="18" fill="#cfd8dc" stroke="#aeb8bc"/><polygon points="194,191 210,200 194,209" fill="#4a5a62"/>`},
  {id: "ipod", name: "Click-wheel player", era: "2001", body: (A) => `
    <rect x="124" y="14" width="152" height="272" rx="20" fill="url(#M)" stroke="#6c7b82" stroke-width="3"/>
    <rect x="140" y="32" width="120" height="96" rx="5" fill="#0b0f12" stroke="#3a4a52" stroke-width="3"/>${A.rect(144, 36, 112, 88, 3)}
    <rect x="144" y="106" width="112" height="18" fill="#000" opacity="0.45"/><rect x="150" y="113" width="100" height="4" rx="2" fill="#fff" opacity="0.3"/><rect x="150" y="113" width="46" height="4" rx="2" fill="#86d7ee"/>
    <circle cx="200" cy="206" r="62" fill="#e9edef" stroke="#b6c0c4" stroke-width="2"/>
    <g class="a-spin slow" style="transform-origin:200px 206px">${Array.from({length: 12}, (_, i) => `<circle cx="${(200 + 50 * Math.cos(i * Math.PI / 6)).toFixed(1)}" cy="${(206 + 50 * Math.sin(i * Math.PI / 6)).toFixed(1)}" r="2" fill="#aeb8bc"/>`).join("")}</g>
    <circle cx="200" cy="206" r="24" fill="#cfd8dc" stroke="#aeb8bc" stroke-width="2"/>`},
  {id: "headphones", name: "Headphones", era: "2004–2012", body: (A) => `
    <path d="M86 178 V138 A114 114 0 0 1 314 138 V178" fill="none" stroke="url(#M)" stroke-width="18" stroke-linecap="round"/>
    <path d="M86 178 V138 A114 114 0 0 1 314 138 V178" fill="none" stroke="#11181c" stroke-width="5" stroke-linecap="round" opacity="0.5"/>
    <g class="a-throb" style="transform-origin:78px 200px"><rect x="48" y="150" width="62" height="104" rx="28" fill="#232c32" stroke="#aeb8bc" stroke-width="3"/><rect x="62" y="172" width="34" height="60" rx="16" fill="#11181c"/></g>
    <g class="a-throb" style="transform-origin:322px 200px"><rect x="290" y="150" width="62" height="104" rx="28" fill="#232c32" stroke="#aeb8bc" stroke-width="3"/><rect x="304" y="172" width="34" height="60" rx="16" fill="#11181c"/></g>
    <g class="a-bob">${A.rect(140, 96, 120, 120, 14)}<rect x="140" y="96" width="120" height="120" rx="14" fill="none" stroke="#86d7ee" stroke-opacity="0.55" stroke-width="2"/></g>
    <path class="a-pulse" d="M26 170 q-16 30 0 62 M10 152 q-30 48 0 98" fill="none" stroke="#f0879e" stroke-width="4" stroke-linecap="round"/>
    <path class="a-pulse" style="animation-delay:.5s" d="M374 170 q16 30 0 62 M390 152 q30 48 0 98" fill="none" stroke="#86d7ee" stroke-width="4" stroke-linecap="round"/>
    ${pdEq(146, 238, 10, 8, 34, 4, "#86d7ee")}`},
  {id: "dj", name: "DJ decks", era: "2013–2017", body: (A) => `
    <rect x="6" y="40" width="160" height="236" rx="14" fill="#161c20" stroke="#33444c" stroke-width="3"/>
    <rect x="234" y="40" width="160" height="236" rx="14" fill="#161c20" stroke="#33444c" stroke-width="3"/>
    <rect x="170" y="40" width="60" height="236" rx="10" fill="#1d262b" stroke="#33444c" stroke-width="2"/>
    <circle cx="86" cy="150" r="68" fill="#0b0f12" stroke="#5c6b72" stroke-width="3"/>
    <g class="a-spin" style="transform-origin:86px 150px"><circle cx="86" cy="150" r="64" fill="#101417"/>${[56, 48, 40].map(r => `<circle cx="86" cy="150" r="${r}" fill="none" stroke="#1f272c"/>`).join("")}${A.circle(86, 150, 28)}<circle cx="86" cy="150" r="4" fill="#080c0f"/><path d="M86 150 L86 90 A60 60 0 0 1 120 100 Z" fill="#fff" opacity="0.07"/></g>
    <circle cx="314" cy="150" r="68" fill="#0b0f12" stroke="#5c6b72" stroke-width="3"/>
    <g class="a-spin" style="transform-origin:314px 150px"><circle cx="314" cy="150" r="64" fill="#101417"/>${[56, 48, 40].map(r => `<circle cx="314" cy="150" r="${r}" fill="none" stroke="#1f272c"/>`).join("")}<circle cx="314" cy="150" r="28" fill="#e0489a"/><circle cx="314" cy="150" r="4" fill="#080c0f"/><path d="M314 150 L314 90 A60 60 0 0 1 348 100 Z" fill="#fff" opacity="0.07"/></g>
    <line x1="150" y1="62" x2="124" y2="116" stroke="#b6c0c4" stroke-width="5" stroke-linecap="round"/><line x1="250" y1="62" x2="276" y2="116" stroke="#b6c0c4" stroke-width="5" stroke-linecap="round"/>
    ${pdEq(180, 70, 4, 8, 90, 4, "#86d7ee")}
    <circle cx="186" cy="190" r="9" fill="#2b383f" stroke="#aeb8bc"/><circle cx="214" cy="190" r="9" fill="#2b383f" stroke="#aeb8bc"/>
    <rect x="178" y="236" width="44" height="8" rx="4" fill="#080c0f"/><rect class="a-pulse" x="192" y="230" width="16" height="20" rx="3" fill="#f0879e"/>`},
  {id: "stage", name: "Festival stage", era: "2018 on", body: (A) => `
    <polygon class="a-pulse" points="70,6 6,214 130,214" fill="#f0879e" opacity="0.28"/>
    <polygon class="a-pulse" style="animation-delay:.55s" points="330,6 270,214 394,214" fill="#86d7ee" opacity="0.28"/>
    <polygon class="a-pulse" style="animation-delay:1.1s" points="200,4 140,214 260,214" fill="#f5d36b" opacity="0.18"/>
    <rect x="92" y="46" width="216" height="140" rx="8" fill="#0b0f12" stroke="#5c6b72" stroke-width="3"/>${A.rect(98, 52, 204, 128, 4)}
    <rect x="8" y="104" width="64" height="112" rx="6" fill="#161c20" stroke="#33444c" stroke-width="2"/><rect x="328" y="104" width="64" height="112" rx="6" fill="#161c20" stroke="#33444c" stroke-width="2"/>
    <g class="a-throb" style="transform-origin:40px 134px"><circle cx="40" cy="134" r="20" fill="#0b0f12" stroke="#aeb8bc" stroke-width="2"/><circle cx="40" cy="134" r="7" fill="#232c32"/></g>
    <g class="a-throb" style="transform-origin:40px 184px"><circle cx="40" cy="184" r="20" fill="#0b0f12" stroke="#aeb8bc" stroke-width="2"/><circle cx="40" cy="184" r="7" fill="#232c32"/></g>
    <g class="a-throb" style="transform-origin:360px 134px"><circle cx="360" cy="134" r="20" fill="#0b0f12" stroke="#aeb8bc" stroke-width="2"/><circle cx="360" cy="134" r="7" fill="#232c32"/></g>
    <g class="a-throb" style="transform-origin:360px 184px"><circle cx="360" cy="184" r="20" fill="#0b0f12" stroke="#aeb8bc" stroke-width="2"/><circle cx="360" cy="184" r="7" fill="#232c32"/></g>
    <rect x="0" y="214" width="400" height="14" fill="#1d262b"/>
    ${Array.from({length: 11}, (_, i) => `<g class="a-bob" style="animation-delay:${((i * 0.23) % 1.4).toFixed(2)}s"><circle cx="${20 + i * 36}" cy="${262 - (i % 3) * 6}" r="13" fill="#05080a"/><rect x="${12 + i * 36}" y="${272 - (i % 3) * 6}" width="16" height="40" rx="8" fill="#05080a"/>${i % 2 ? `<line x1="${20 + i * 36}" y1="${274 - (i % 3) * 6}" x2="${28 + i * 36}" y2="${244 - (i % 3) * 6}" stroke="#05080a" stroke-width="5" stroke-linecap="round"/>` : ""}</g>`).join("")}`},
  {id: "rapper", name: "Rapper", era: "Hip hop albums", genre: true, body: (A) => `
    <defs><radialGradient id="cdRap" cx="40%" cy="35%" r="65%"><stop offset="0%" stop-color="#e8f0f4"/><stop offset="40%" stop-color="#b0c4cc"/><stop offset="70%" stop-color="#7a9baa"/><stop offset="100%" stop-color="#4a6878"/></radialGradient></defs>
    <g class="a-spin slow" style="transform-origin:112px 165px">
      <circle cx="112" cy="165" r="102" fill="url(#cdRap)"/>
      <circle cx="80" cy="133" r="42" fill="#fff" opacity="0.1"/>
      ${A.circle(112, 165, 86)}
      <circle cx="112" cy="165" r="34" fill="none" stroke="#fff" stroke-opacity="0.3"/>
      <circle cx="112" cy="165" r="22" fill="#cdd6da" fill-opacity="0.9"/>
      <circle cx="112" cy="165" r="7" fill="#080c0f"/>
    </g>
    <g transform="translate(48 45) scale(0.85)">
    <text class="a-bob" x="196" y="96" font-size="30" fill="#f0879e" font-family="sans-serif">♪</text><text class="a-bob" style="animation-delay:.7s" x="178" y="62" font-size="24" fill="#86d7ee" font-family="sans-serif">♫</text>
    <path d="M200 300 V236 Q200 192 270 192 Q340 192 340 236 V300 Z" fill="url(#F)" stroke="#33444c" stroke-width="2"/>
    <path d="M238 196 Q270 246 302 196" fill="none" stroke="#e2bb5e" stroke-width="5" stroke-linecap="round"/><circle cx="270" cy="236" r="9" fill="#e2bb5e" stroke="#8a6a22" stroke-width="2"/>
    <rect x="258" y="168" width="24" height="28" fill="#b97f58"/>
    <g class="a-bob" style="animation-duration:.8s"><path d="M322 220 Q352 206 362 168" fill="none" stroke="#2c3a41" stroke-width="22" stroke-linecap="round"/><circle cx="363" cy="160" r="12" fill="#b97f58"/></g>
    <g class="a-nod" style="transform-origin:270px 182px">
      <ellipse cx="270" cy="140" rx="34" ry="40" fill="#c58f66"/>
      <path d="M230 130 Q230 90 270 90 Q310 90 310 130 Z" fill="#b3261e"/><rect x="268" y="122" width="58" height="9" rx="4" fill="#8f1b16"/>
      <rect x="241" y="130" width="26" height="13" rx="5" fill="#080c0f"/><rect x="273" y="130" width="26" height="13" rx="5" fill="#080c0f"/><line x1="267" y1="136" x2="273" y2="136" stroke="#080c0f" stroke-width="3"/>
      <ellipse class="a-talk" cx="268" cy="160" rx="11" ry="7" fill="#3b1010"/>
    </g>
    <path d="M226 226 Q186 214 200 176 Q208 160 236 160" fill="none" stroke="#2c3a41" stroke-width="22" stroke-linecap="round"/>
    <circle cx="238" cy="160" r="11" fill="#b97f58"/>
    <rect x="228" y="134" width="14" height="30" rx="7" fill="#cfd8dc" stroke="#6c7b82" stroke-width="2" transform="rotate(-18 235 150)"/><circle cx="244" cy="128" r="10" fill="#4a5a62" stroke="#cfd8dc" stroke-width="2"/>
    <path class="a-pulse" d="M214 138 q-16 6 -4 22 M200 130 q-26 12 -8 40" fill="none" stroke="#f0879e" stroke-width="3" stroke-linecap="round"/></g>`},
];

// The device an album gets: your forced choice, else a rapper for hip hop, else by year, else (no year) a steady
// random one per album. genres is a text such as "Hip Hop, Rap".
const pdRapGenre = /hip[\s-]?hop|\brap\b|\brapper\b|\btrap\b|\bdrill\b|grime|boom[\s-]?bap/i;
function pdPick(year, seed = "", choice = "auto", genres = "") {
  if (choice === "off") return "";
  if (pdDevices.some(d => d.id === choice)) return choice;
  if (pdRapGenre.test(String(genres || ""))) return "rapper";
  const y = Number(year);
  if (Number.isFinite(y) && y >= 1500 && y <= 2100) return pdYears.ranges.find(([last]) => y <= last)[1];
  let hash = 0;
  for (const ch of String(seed)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const byYear = pdDevices.filter(d => !d.genre);
  return byYear[hash % byYear.length].id;
}

// The whole SVG for one device. uid keeps gradient and clip ids apart when several are on a page.
function pdSvg(id, art = "", uid = "pd") {
  const device = pdDevices.find(d => d.id === id);
  if (!device) return "";
  const defs = `<defs>
    <linearGradient id="${uid}g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e0489a"/><stop offset="1" stop-color="#6a2a8a"/></linearGradient>
    <linearGradient id="${uid}W" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7a5239"/><stop offset="1" stop-color="#3d2a1d"/></linearGradient>
    <linearGradient id="${uid}B" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e2bb5e"/><stop offset="1" stop-color="#8a6a22"/></linearGradient>
    <linearGradient id="${uid}O" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e0a867"/><stop offset="1" stop-color="#a8693a"/></linearGradient>
    <linearGradient id="${uid}M" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#d4dcdf"/><stop offset="1" stop-color="#7f8d93"/></linearGradient>
    <linearGradient id="${uid}T" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#a9c4cf"/><stop offset="1" stop-color="#5f8493"/></linearGradient>
    <linearGradient id="${uid}F" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2c3a41"/><stop offset="1" stop-color="#151d21"/></linearGradient>
    <linearGradient id="${uid}S" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="0.3" stop-color="#86d7ee" stop-opacity="0.4"/><stop offset="0.5" stop-color="#fff" stop-opacity="0"/><stop offset="0.7" stop-color="#f0879e" stop-opacity="0.35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
  </defs>`;
  // The drawings refer to the gradients by short names (url(#g), url(#W) ...); give them this svg's own ids.
  const body = device.body(pdArtTools(uid, art)).replace(/url\(#([gWBOMTFS])\)/g, `url(#${uid}$1)`);
  return `<svg class="pd pd-${device.id}" viewBox="0 0 400 300" role="img" aria-label="${pdEsc(device.name)} playing this album" xmlns="http://www.w3.org/2000/svg">${defs}${body}</svg>`;
}
