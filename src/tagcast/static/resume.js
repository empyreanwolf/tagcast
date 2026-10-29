"use strict";

// Resume the play queue after the page is closed or reloaded (fork v52). The queue is remembered as
// track ids with their album ids, plus the position; resuming loads those albums and starts paused.

const RESUME_KEY = "tagcast-resume-queue";
const RESUME_LIMIT = 3000;
const resume = {timer: 0, busy: false, lastSaved: 0, hadQueue: false};

function readResume() {
  try {
    const saved = JSON.parse(localStorage.getItem(RESUME_KEY) || "null");
    if (saved && Array.isArray(saved.queue) && saved.queue.length && Number.isInteger(saved.index)) return saved;
  } catch { /* private window or damaged value */ }
  return null;
}

function resumeSave() {
  clearTimeout(resume.timer);
  resume.timer = setTimeout(() => {
    resume.lastSaved = Date.now();
    try {
      if (player.queue.length) resume.hadQueue = true;
      if (!player.queue.length || !player.queue[player.index]) { if (!player.queue.length && resume.hadQueue) localStorage.removeItem(RESUME_KEY); return; }
      const start = Math.max(0, Math.min(player.index - 5, player.queue.length - RESUME_LIMIT));
      const queue = player.queue.slice(start, start + RESUME_LIMIT).map(t => ({id: String(t.id), album: String(t.album?.id ?? ""), next: t.upNext ? 1 : 0}));
      localStorage.setItem(RESUME_KEY, JSON.stringify({queue, index: player.index - start, position: Math.floor(Number(audio.currentTime) || 0), repeat: player.repeat, at: Date.now()}));
    } catch { /* storage full or blocked: resume is optional */ }
  }, 800);
}

audio.addEventListener("timeupdate", () => { if (player.queue.length && Date.now() - resume.lastSaved > 5000) resumeSave(); });

function updateResumeControls() {
  const button = $("#resume-queue");
  if (!button) return;
  const saved = readResume();
  button.hidden = !saved || player.queue.length > 0 || !live();
  if (saved) button.textContent = `↺ Resume last queue (${saved.queue.length.toLocaleString("en")} ${saved.queue.length === 1 ? "track" : "tracks"})`;
  if (!button.hidden && !resultsActions.resumeOffered) {
    resultsActions.resumeOffered = true;
    const box = $("#toast");
    box.innerHTML = `Your last play queue is still saved. <button type="button" class="button small" data-resume-queue>Resume</button>`;
    box.hidden = false; clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 20000);
  }
}

async function resumeQueue() {
  const saved = readResume();
  if (!saved || resume.busy || !live() || state.loading || player.queue.length) return;
  resume.busy = true;
  const details = state.details;
  try {
    const albumIds = [...new Set(saved.queue.map(t => t.album).filter(Boolean))];
    await albumDetails(albumIds.flatMap(id => shelfIds(id)));
    if (details !== state.details || !live() || player.queue.length) return;
    const byAlbum = new Map();
    for (const id of albumIds) { try { byAlbum.set(id, new Map(queueTracks(playableAlbum(id)).map(t => [String(t.id), t]))); } catch { /* album gone */ } }
    const queue = [], position = [];
    saved.queue.forEach((entry, i) => {
      const track = byAlbum.get(entry.album)?.get(entry.id);
      if (!track) return;
      if (i === saved.index) position.push(queue.length);
      queue.push(entry.next ? {...track, upNext: true} : {...track});
    });
    if (!queue.length) throw Error("those tracks are no longer in the library.");
    const missing = saved.queue.length - queue.length;
    player.epoch += 1; player.shuffle = false;
    player.queue = queue;
    player.index = position[0] ?? Math.min(saved.index, queue.length - 1);
    if (["none", "queue", "track"].includes(saved.repeat)) player.repeat = saved.repeat;
    playCurrent(false);
    const seek = Number(saved.position) || 0;
    if (seek > 0 && position.length) audio.addEventListener("loadedmetadata", () => { audio.currentTime = Math.min(seek, audio.duration || seek); }, {once: true});
    toast(`Resumed ${queue.length} ${queue.length === 1 ? "track" : "tracks"}, paused.${missing ? ` ${missing} no longer in the library.` : ""}`);
  } catch (error) {
    toast(`Could not resume the last queue: ${error.message}`);
  } finally { resume.busy = false; updateResumeControls(); }
}

document.addEventListener("click", event => {
  if (event.target.closest?.("[data-resume-queue]") || event.target.closest?.("#resume-queue")) resumeQueue();
});
