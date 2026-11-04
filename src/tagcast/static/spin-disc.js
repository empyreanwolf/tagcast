"use strict";

// Spinning disc (fork v72): while a song plays, the album cover turns like a CD in Now playing and in the bottom
// player. Pausing stops it where it is. Settings > Playback turns it off; "reduce motion" turns it off too.
const spinDisc = {key: "tagcast-spin-disc", enabled: true};
try { spinDisc.enabled = localStorage.getItem(spinDisc.key) !== "0"; } catch { /* private window: keep the default */ }

function spinDiscPlaying() {
  return Boolean(audio && !audio.paused && !audio.ended && (audio.currentSrc || audio.getAttribute("src")));
}

function spinDiscApply() {
  const playing = spinDiscPlaying();
  for (const el of [document.querySelector("#visual-player .visual-player-cover"), $("#player-cover-button")]) {
    if (!el) continue;
    el.classList.toggle("disc-on", spinDisc.enabled);
    el.classList.toggle("disc-playing", spinDisc.enabled && playing);
  }
}

function spinDiscSettings() {
  return `<section class="player-display-settings" data-settings-category="playback"><h3>Spinning disc <span class="settings-mode">Immediate · this browser</span></h3>
    <label class="checkline"><input type="checkbox" id="spin-disc-enabled" ${spinDisc.enabled ? "checked" : ""}> Album cover spins like a CD while a song plays</label>
    <p class="muted">In Now playing and the bottom player. Pause stops it. It stays still when your computer is set to reduce motion.</p></section>`;
}

for (const name of ["play", "playing", "pause", "ended", "emptied", "loadeddata"]) audio.addEventListener(name, spinDiscApply);
document.addEventListener("change", event => {
  if (event.target.id !== "spin-disc-enabled") return;
  spinDisc.enabled = event.target.checked;
  try { localStorage.setItem(spinDisc.key, spinDisc.enabled ? "1" : "0"); } catch { /* private window */ }
  spinDiscApply();
});
spinDiscApply();
