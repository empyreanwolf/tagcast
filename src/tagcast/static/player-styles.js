"use strict";

// Player style (fork v77): Now playing, the bottom bar and the Mini Player show a device that fits the album's
// year (a gramophone, a cassette, a Walkman, earbuds ...) with the cover on it. Theme menu > Player style forces
// one or turns it off; "How we listen" shows the whole timeline. The drawings live in player-devices.js.
const psState = {key: "tagcast-player-style", choice: "auto", big: "", mini: "", listen: false};
try { psState.choice = localStorage.getItem(psState.key) || "auto"; } catch { /* private window */ }
if (psState.choice !== "auto" && psState.choice !== "off" && !pdDevices.some(d => d.id === psState.choice)) psState.choice = "auto";

function psTrack() { return typeof player !== "undefined" ? player.queue?.[player.index] : null; }

function psYear(track) {
  const detail = typeof state !== "undefined" ? state.details?.get?.(String(track.album?.id)) : null;
  return Number(track.year || detail?.year || track.album?.year || 0) || 0;
}

function psCurrentDevice() {
  const track = psTrack();
  return track ? pdPick(psYear(track), String(track.album?.id || track.id || track.title), psState.choice) : "";
}

function psPlaying() {
  return Boolean(typeof audio !== "undefined" && audio && !audio.paused && !audio.ended && (audio.currentSrc || audio.getAttribute?.("src")));
}

function psSlot(id, parent, before) {
  let node = document.querySelector("#" + id);
  if (!node && parent) {
    node = document.createElement("div");
    node.id = id;
    parent.insertBefore(node, before || null);
  }
  return node;
}

function psRender() {
  try { psRenderNow(); } catch { /* a missing piece of the page must never stop playback */ }
}

function psRenderNow() {
  if (typeof document === "undefined" || !document.documentElement) return;
  const device = psCurrentDevice(), track = psTrack(), art = track?.album?.artwork || "", playing = psPlaying();
  const root = document.documentElement;
  if (device) root.dataset.ps = "on"; else delete root.dataset.ps;
  // Now playing (the big stage), placed above the song's name.
  const content = document.querySelector("#visual-player .visual-player-content");
  const big = content ? psSlot("ps-big", content, content.firstChild) : null;
  if (big) {
    big.className = "ps-stage" + (playing ? " ps-playing" : "");
    const key = device + "|" + art;
    if (psState.big !== key) {
      psState.big = key;
      big.innerHTML = device ? pdSvg(device, art, "pb") + `<div class="ps-name">${pdEsc(pdDevices.find(d => d.id === device).name)}</div>` : "";
    }
    big.hidden = !device;
  }
  // The small one in the bottom bar.
  const button = document.querySelector("#player-cover-button");
  if (button) {
    let mini = button.querySelector(".ps-mini");
    if (!mini) { mini = document.createElement("span"); mini.className = "ps-mini"; mini.setAttribute("aria-hidden", "true"); button.appendChild(mini); }
    mini.classList?.toggle("ps-playing", playing);
    const key = device + "|" + art;
    if (psState.mini !== key) { psState.mini = key; mini.innerHTML = device ? pdSvg(device, art, "pn") : ""; }
  }
  if (psState.listen) psListenRefresh();
}

function psChoose(choice) {
  psState.choice = choice === "off" || pdDevices.some(d => d.id === choice) ? choice : "auto";
  try { localStorage.setItem(psState.key, psState.choice); } catch { /* private window */ }
  psState.big = psState.mini = "";
  const select = document.querySelector("#ps-choice");
  if (select) select.value = psState.choice;
  psRender();
}

function psYearsText(id) {
  const i = pdYears.ranges.findIndex(r => r[1] === id);
  if (i < 0) return "";
  const from = i ? pdYears.ranges[i - 1][0] + 1 : 0, to = pdYears.ranges[i][0];
  return !from ? `before ${to + 1}` : to > 3000 ? `${from} on` : from === to ? String(from) : `${from}–${to}`;
}

// ---- the timeline page: a winding path of devices, four to a row ----
function psListenHtml() {
  const current = psCurrentDevice(), cards = pdDevices.map(d => `<button type="button" class="listen-card${psState.choice === d.id ? " chosen" : ""}${current === d.id ? " now" : ""}" data-ps-pick="${d.id}" aria-pressed="${psState.choice === d.id}">
    <span class="listen-art">${pdSvg(d.id, "", "pl" + d.id)}</span><strong>${pdEsc(d.name)}</strong><small>${pdEsc(psYearsText(d.id))}</small>
    <em>${psState.choice === d.id ? "Your player ✓" : current === d.id ? "Playing now" : "Use this player"}</em></button>`);
  const rows = [];
  for (let i = 0; i < cards.length; i += 4) rows.push(`<div class="listen-row${(i / 4) % 2 ? " back" : ""}">${cards.slice(i, i + 4).join("")}</div>`);
  return `<div class="dialog-heading"><div><div class="eyebrow">THE PLAYER FOLLOWS THE ALBUM'S YEAR</div><h2>How we listen</h2></div>
    <button type="button" class="close" data-close="listen-dialog" aria-label="Close How we listen">×</button></div>
    <p class="muted">A walk through the ways people have listened, from the horn phonograph to earbuds. Tagcast picks the device that matches an album's year (no year: a random one). Choose a device to always use it.</p>
    <div class="listen-modes"><button type="button" class="button small ${psState.choice === "auto" ? "primary" : ""}" data-ps-pick="auto">Auto · by album year</button>
      <button type="button" class="button small ${psState.choice === "off" ? "primary" : ""}" data-ps-pick="off">Off · plain cover</button></div>
    <div class="listen-path">${rows.join("")}</div>`;
}

function psListenRefresh() {
  const dialog = document.querySelector("#listen-dialog");
  if (dialog?.open) { const top = dialog.scrollTop; dialog.innerHTML = psListenHtml(); dialog.scrollTop = top; }
}

function psOpenTimeline() {
  let dialog = document.querySelector("#listen-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "listen-dialog"; dialog.className = "listen-dialog";
    document.body.appendChild(dialog);
  }
  dialog.innerHTML = psListenHtml();
  psState.listen = true;
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
  dialog.addEventListener?.("close", () => { psState.listen = false; }, {once: true});
}

function psMenuHtml() {
  const options = [["auto", "Auto · by album year"], ["off", "Off · plain cover"], ...pdDevices.map(d => [d.id, `${d.name} (${psYearsText(d.id)})`])]
    .map(([value, label]) => `<option value="${value}"${psState.choice === value ? " selected" : ""}>${pdEsc(label)}</option>`).join("");
  return `<fieldset><legend>Player style</legend><label class="ps-select">Device <select id="ps-choice">${options}</select></label>
    <button type="button" class="button small" id="ps-open-listen">How we listen…</button></fieldset>`;
}

function psInstallMenu() {
  const menu = document.querySelector("#theme-menu");
  if (!menu || typeof menu.insertAdjacentHTML !== "function" || menu.querySelector("#ps-choice")) return;
  menu.insertAdjacentHTML("beforeend", psMenuHtml());
  menu.addEventListener("change", event => { if (event.target.id === "ps-choice") psChoose(event.target.value); });
  menu.addEventListener("click", event => { if (event.target.closest?.("#ps-open-listen")) psOpenTimeline(); });
}

document.addEventListener("click", event => {
  const pick = event.target.closest?.("[data-ps-pick]");
  if (pick) { psChoose(pick.dataset.psPick); return; }
  if (event.target.closest?.("#show-listen")) psOpenTimeline();
  if (event.target.closest?.('[data-close="listen-dialog"]')) document.querySelector("#listen-dialog")?.close();
});

psInstallMenu();
if (typeof audio !== "undefined") for (const name of ["play", "playing", "pause", "ended", "emptied", "loadeddata"]) audio.addEventListener(name, psRender);
if (typeof MutationObserver === "function") {
  const watch = new MutationObserver(psRender);
  for (const [selector, options] of [["#player-art", {attributes: true, attributeFilter: ["src"]}], ["#player-title", {childList: true, characterData: true, subtree: true}]]) {
    const node = document.querySelector(selector);
    if (node) watch.observe(node, options);
  }
}
psRender();
