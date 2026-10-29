"use strict";

// Crossfade (fork v52). Near the end of a track the next one starts on the normal player (fading in)
// while the ending track carries on in a second, temporary player (fading out). Off by default.
// Not used with experimental gapless playback, repeat-one, or on the last track of the queue.

const crossfade = {seconds: 0, active: null, makeAudio: () => new Audio()};
try { crossfade.seconds = Math.max(0, Math.min(12, Number(localStorage.getItem("tagcast-crossfade")) || 0)); } catch { /* default off */ }

function crossfadeEligible() {
  return crossfade.seconds > 0 && !crossfade.active && live()
    && !(typeof gaplessTrial !== "undefined" && gaplessTrial.enabled)
    && player.repeat !== "track" && player.index >= 0 && player.index < player.queue.length - 1
    && (typeof localPlaybackAllowed !== "function" || localPlaybackAllowed());
}

function crossfadeTick() {
  if (!crossfadeEligible() || audio.paused || audio.seeking) return;
  const duration = audio.duration, position = audio.currentTime, remaining = duration - position;
  if (!Number.isFinite(duration) || duration < crossfade.seconds * 3 || remaining > crossfade.seconds || remaining < 0.3) return;
  crossfadeStart(remaining);
}

function crossfadeStart(seconds) {
  let outgoing = null;
  try {
    outgoing = crossfade.makeAudio();
    outgoing.src = audio.currentSrc || audio.getAttribute("src");
    outgoing.currentTime = audio.currentTime;
    outgoing.playbackRate = audio.playbackRate;
  } catch { outgoing = null; }
  if (!outgoing) return;
  const keptUrl = preload.playingUrl;           // the ending track may be playing from memory: keep it until the fade is over
  if (keptUrl) preload.playingUrl = "";
  const volume = audio.volume, fade = {outgoing, keptUrl, volume, started: performance.now(), length: seconds * 1000, target: null, timer: 0};
  crossfade.active = fade;
  outgoing.volume = volume;
  Promise.resolve(outgoing.play()).catch(() => finishCrossfade(fade));
  samplePlayback();
  audio.volume = 0;
  step(1);
  fade.target = player.queue[player.index];
  fade.timer = setInterval(() => crossfadeStep(fade), 50);
}

function crossfadeStep(fade) {
  if (crossfade.active !== fade) return;
  const progress = Math.min(1, (performance.now() - fade.started) / fade.length);
  if (audio.paused || player.queue[player.index] !== fade.target || progress >= 1) return finishCrossfade(fade);
  audio.volume = fade.volume * Math.sin(progress * Math.PI / 2);
  fade.outgoing.volume = fade.volume * Math.cos(progress * Math.PI / 2);
}

function finishCrossfade(fade) {
  if (crossfade.active !== fade) return;
  crossfade.active = null;
  clearInterval(fade.timer);
  try { fade.outgoing.pause(); fade.outgoing.removeAttribute("src"); fade.outgoing.load(); } catch { /* already gone */ }
  if (fade.keptUrl) { try { URL.revokeObjectURL(fade.keptUrl); } catch { /* already gone */ } }
  audio.volume = fade.volume;
}

audio.addEventListener("timeupdate", crossfadeTick);
audio.addEventListener("pause", () => { if (crossfade.active) finishCrossfade(crossfade.active); });

document.addEventListener("change", event => {
  if (event.target.id !== "crossfade-seconds") return;
  crossfade.seconds = Math.max(0, Math.min(12, Number(event.target.value) || 0));
  try { localStorage.setItem("tagcast-crossfade", String(crossfade.seconds)); } catch { /* optional */ }
});

function crossfadeSettings() {
  const options = [[0, "Off"], [2, "2 seconds"], [4, "4 seconds"], [6, "6 seconds"], [8, "8 seconds"], [12, "12 seconds"]];
  return `<section class="settings-section" data-settings-category="playback"><h3>Crossfade</h3><label class="field">Overlap the end of a song with the start of the next<select id="crossfade-seconds">${options.map(([value, label]) => `<option value="${value}" ${crossfade.seconds === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><p class="muted">Off by default. Works best with “Pre-load next songs” on. Not used with gapless playback, repeat-one, or after the last song in the queue. Songs shorter than three times the overlap play normally.</p></section>`;
}
