"use strict";

// Notifications only: installing and restarting remain the user's choice.
const updateNotice = {data: null, automatic: true, busy: false, error: "", settingsRevision: 0};

// v66: the fork checks its own GitHub (empyreanwolf/tagcast), never the original Tagcast's releases.
function updateReleaseURL() {
  const data = updateNotice.data;
  if (!data?.available || !data.fork || !/^fork v\d+$/.test(data.latest || "")) return "";
  return "https://github.com/empyreanwolf/tagcast/archive/refs/heads/main.zip";
}

function updateStatusText() {
  if (updateNotice.busy) return "Checking for updates…";
  if (updateNotice.error) return updateNotice.error;
  const data = updateNotice.data;
  if (data?.error) return data.error;
  if (updateReleaseURL()) return `Tagcast ${data.latest} is available. You are running ${data.current}.`;
  if (data?.state === "current") return `Tagcast ${data.current} is the latest build of your fork.`;
  return updateNotice.automatic ? "Updates are checked at startup, at most once a day." : "Automatic update checks are off. You can still check manually.";
}

function updateSettingsPanel() {
  return `<section class="updates-settings" data-settings-category="about"><h3>Tagcast updates</h3>
    <label class="checkline"><input type="checkbox" name="auto_updates" ${updateNotice.automatic ? "checked" : ""}> Automatically check for new fork builds</label>
    <p class="muted">Checks your fork on GitHub (empyreanwolf/tagcast) at startup, at most once a day. The original Tagcast's releases are not offered: installing one would replace this fork. No account or library data is sent.</p>
    <p id="updates-settings-status" class="muted" aria-live="polite">${escapeHtml(updateStatusText())}</p>
    <div class="update-actions"><button type="button" class="button small" id="check-updates" data-check-updates ${updateNotice.busy ? "disabled" : ""}>Check for updates</button>
    <button type="button" class="button small primary" id="settings-update-now" data-update-install ${updateReleaseURL() ? "" : "hidden"}>⬆ Update now &amp; restart</button>
    <button type="button" class="text-action" id="settings-update-details" data-update-details ${updateReleaseURL() ? "" : "hidden"}>View release &amp; downloads →</button></div>
    <h3>Debug report</h3><p class="muted">One zip with the logs, versions and the queue's titles and reasons, for sending when something goes wrong. No sign-in tokens, keys or passwords are included.</p>
    <p><a class="button small" href="/api/debug-report" download>⬇ Debug report</a></p></section>`;
}

function applyUpdateSettings(data) {
  updateNotice.settingsRevision += 1;
  updateNotice.automatic = data.auto_updates !== false;
}

function renderUpdateNotice() {
  const url = updateReleaseURL(), data = updateNotice.data;
  const button = $("#update-button");
  button.hidden = !url;
  button.textContent = url ? `Update available · ${data.latest}` : "";
  const version = $("#downloads-version");
  if (version && data?.current) version.title = `Running ${data.current}`;
  const status = $("#updates-settings-status");
  if (status) status.textContent = updateStatusText();
  const check = $("#check-updates");
  if (check) check.disabled = updateNotice.busy;
  const details = $("#settings-update-details");
  if (details) details.hidden = !url;
  const now = $("#settings-update-now");
  if (now) now.hidden = !url;
}

async function checkForUpdates(manual = false) {
  if (updateNotice.busy) return;
  const revision = updateNotice.settingsRevision;
  updateNotice.busy = true; updateNotice.error = ""; renderUpdateNotice();
  try {
    const data = await api(manual ? "/api/updates/check" : "/api/updates", manual ? {} : undefined);
    updateNotice.data = data;
    if (revision === updateNotice.settingsRevision) updateNotice.automatic = data.auto_updates !== false;
  } catch (error) {
    updateNotice.error = error.status === 404 ? "Restart Tagcast to use update checks: the running server is older than this page."
      : "Could not check for updates. Try again later or view GitHub releases.";
  } finally {
    updateNotice.busy = false; renderUpdateNotice();
  }
}

function openUpdateDetails() {
  const url = updateReleaseURL();
  if (!url) return;
  const data = updateNotice.data;
  $("#updates-content").innerHTML = `<p><strong>Tagcast ${escapeHtml(data.latest)} is available.</strong> You are running ${escapeHtml(data.current)}.</p>
    <p>Download the zip, close Tagcast, extract it over your Tagcast folder (the files are inside <code>tagcast-main</code>), then start <code>start-tagcast-fork.bat</code> again. Your settings and sign-in are kept.</p>
    ${data.error || updateNotice.error ? `<p class="muted">${escapeHtml(data.error || updateNotice.error)} This notification uses the last successful check.</p>` : ""}
    <div class="dialog-footer"><button class="button" data-close="updates">Later</button><a class="button" href="${escapeHtml(data.page || "")}" target="_blank" rel="noopener noreferrer">What changed ↗</a><button class="button primary" data-update-install>⬆ Update now &amp; restart</button><a class="button" href="${url}" target="_blank" rel="noopener noreferrer">⬇ Download fork zip</a></div>`;
  $("#updates").showModal();
}

$("#update-button").addEventListener("click", openUpdateDetails);
document.addEventListener("click", event => {
  if (event.target.closest("[data-check-updates]")) checkForUpdates(true);
  if (event.target.closest("[data-update-details]")) openUpdateDetails();
  if (event.target.closest("[data-update-install]")) installUpdate();
});

// v73: one click downloads the fork from GitHub, replaces the files and restarts Tagcast; the page reloads on the new version.
async function installUpdate() {
  if (updateNotice.busy) return;
  const target = updateNotice.data?.latest;
  updateNotice.busy = true; renderUpdateNotice();
  const say = text => { for (const el of [$("#updates-settings-status"), $("#updates-content")?.querySelector("p")]) if (el) el.textContent = text; };
  say("Downloading the update and installing it…");
  try {
    const result = await api("/api/updates/install", {});
    say(result.message);
    if (!result.restarting) { updateNotice.busy = false; renderUpdateNotice(); return; }
    await updateWaitForRestart(target);
  } catch (error) {
    updateNotice.busy = false; renderUpdateNotice();
    say(error.message || "The update failed. Nothing was changed.");
  }
}

async function updateWaitForRestart(target) {
  await new Promise(resolve => setTimeout(resolve, 5000));
  for (let i = 0; i < 60; i++) {
    try {
      const data = await fetch("/api/updates", {cache: "no-store"}).then(r => r.json());
      if (data && data.current && (!target || data.current === target)) { location.reload(); return; }
    } catch { /* the server is still restarting */ }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  updateNotice.busy = false; renderUpdateNotice();
  const status = $("#updates-settings-status");
  if (status) status.textContent = "Tagcast did not come back by itself. Start start-tagcast-fork.bat again.";
}
