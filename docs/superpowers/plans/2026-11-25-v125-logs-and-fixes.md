# v125 Claude-readable logs + fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude diagnose Tagcast problems from a small sanitized digest (copied or auto-pushed), and ship the open fixes as one version, v125.

**Architecture:** New `claude_digest.py` builds the digest from `activity` entries, `downloads.json`, `search_line.json` and yt-dlp context. New `logsync.py` pushes it to a separate private GitHub repo with the Contents API (stdlib `urllib`), throttled and off until configured. UI buttons live in `static/activity.js`.

**Tech Stack:** Python 3 stdlib, pytest (`python -m pytest tests/<file> -q`), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-25-v125-logs-and-fixes-design.md`

## Global Constraints

- Replies/UI text in English; replies short; one version for the whole batch: `artistCleanupVersion = "fork v125"` in `src/tagcast/static/artist-cleanup.js`.
- Digest never contains tokens/keys/cookies: pass everything through `debug_report.scrub`; digest cap 8000 bytes.
- Auto-push: at most once per 10 min, only after an `error` activity entry, off until repo + token set; target is a separate private repo, never the fork.
- Logging/pushing must never raise into the caller (same rule as `activity.record`).
- Actions in the UI take 1 click.
- Release: FORK.md line, push to `main`, zip via the `git archive` command in CLAUDE.md.

## Review Focus

- Token absent/invalid: no push, no crash, Settings shows why.
- Network down or GitHub 401/404/409/422: app keeps working; 409 (stale sha) retried once.
- Digest with a token-looking value in settings/queue: value is `<hidden>`.
- Two errors within 10 min: exactly one push.
- Clear saved search while a job is running: running job is not resumed after restart.

---

### Task 1: Digest builder

**Files:** Create `src/tagcast/claude_digest.py`; Test `tests/test_claude_digest.py`

**Interfaces:**
- Produces: `build_digest(studio, max_bytes: int = 8000) -> str` (markdown: version/tools, last 15 error/warn activity entries with detail, failed downloads (last 10), search-line summary, last yt-dlp context from `youtube_context()`); `note_ytdlp(url: str, tail: str) -> None` and `youtube_context() -> dict` (module-level store of the last timeout's URL and last 20 lines of yt-dlp output).

- [ ] **Step 1: Failing tests** `test_digest_lists_recent_errors` (start `activity` on tmp dir, record an error, assert title in digest), `test_digest_hides_secrets` (put `{"api_token": "abc123"}` in a studio `_read` stub for `config.json`; assert `abc123` not in digest), `test_digest_is_capped` (200 errors → `len(digest.encode()) <= 8000`).
- [ ] **Step 2:** Run `python -m pytest tests/test_claude_digest.py -q`; expect FAIL (module missing).
- [ ] **Step 3:** Implement `build_digest`, reusing `debug_report._tools`, `debug_report.scrub`, `activity.current().entries`; truncate oldest sections first to meet the cap.
- [ ] **Step 4:** Run tests; expect PASS. Commit `feat: Claude digest builder`.

### Task 2: yt-dlp timeout context

**Files:** Modify `src/tagcast/youtube.py` (the `FetchTimeout` raise near line 458; the progress/stderr hook near line 433); Test `tests/test_youtube.py` (add)

**Interfaces:** Consumes `claude_digest.note_ytdlp(url, tail)`.

- [ ] **Step 1:** Failing test `test_fetch_timeout_records_context`: force `fetch_idle = 0.1` with a worker that sleeps, assert `claude_digest.youtube_context()["url"]` equals the URL and an `error` activity entry titled "yt-dlp did not answer" exists.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Keep the last 20 yt-dlp log/progress lines in the existing fetch thread's hook; on timeout call `note_ytdlp` and `activity.record("downloads", "error", "yt-dlp did not answer", detail=<url + tail + yt-dlp version>)`.
- [ ] **Step 4:** Run; expect PASS. Commit.

### Task 3: Log sync to GitHub

**Files:** Create `src/tagcast/logsync.py`; Test `tests/test_logsync.py`; Modify `src/tagcast/activity.py` (call `logsync.notify_error()` from `ActivityLog.record` when status is `error`, inside try/except)

**Interfaces:**
- Produces: `class LogSync(studio, http=urllib_put)` with `configure(repo: str, token: str) -> dict`, `status() -> dict` (`{"on": bool, "last": str, "error": str}`), `notify_error() -> None` (throttled, starts a thread), `send_now() -> dict`. Settings saved to `logsync.json` via `studio._write` (never added to `debug_report`'s file list). `http(method, url, headers, body) -> (status:int, json)` is injectable for tests.
- Push: `PUT https://api.github.com/repos/{repo}/contents/latest.md` with `message`, base64 `content`, and `sha` from a prior GET; on 409 re-GET and retry once.

- [ ] **Step 1:** Failing tests with a fake `http`: `test_no_token_no_push`, `test_error_pushes_once_in_ten_minutes` (two `notify_error` calls → one PUT; third after faking +11 min → second PUT), `test_409_retried_once`, `test_network_error_is_swallowed`, `test_status_never_shows_token`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; throttle uses a monotonic clock that tests can patch.
- [ ] **Step 4:** Run; expect PASS. Commit.

### Task 4: Routes and UI

**Files:** Modify `src/tagcast/app.py` (GET `/api/claude-digest` returns text; POST `/api/logsync` with `{action: "save"|"send"|"status", repo, token}`; both with `self._activity = None`), `src/tagcast/static/activity.js` (buttons "Copy for Claude", "Send now", and repo/token fields with a status line); Test `tests/test_app.py` (add), `tests/frontend.test.cjs` (button exists)

- [ ] **Step 1:** Failing tests: `test_digest_route_returns_text`, `test_logsync_save_hides_token_in_response`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Add routes and UI; copy uses `navigator.clipboard.writeText` and shows "Copied" for 2 s.
- [ ] **Step 4:** Run `python -m pytest tests/test_app.py -q` and `node tests/frontend.test.cjs`; expect PASS. Commit.

### Task 5: Clear saved search + remove wrong albums

**Files:** Modify `src/tagcast/discography.py` (`search_control`: new action `"clear"`: empty `self.jobs` running entries, bump `_stop_gen`, `_save_line()`), `src/tagcast/static/discography.js` (button "Clear saved search"), `src/tagcast/static/wishlist` UI file (confirm that multi-select remove via `/api/wishlist/remove` exists; add it if missing); Test `tests/test_discography.py` (add)

- [ ] **Step 1:** Failing test `test_clear_empties_line_file`: queue 2 jobs, `search_control("clear")`, assert `search_line.json` has `"jobs": []` and a fresh `_load_line()` restores nothing.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; then in the wishlist UI check select-all + remove works for the wrong albums named in the handoff; fix if not.
- [ ] **Step 4:** Run; expect PASS. Commit.

### Task 6: Static review of untested code

**Files:** Read `src/tagcast/bandcamp.py`, `src/tagcast/spotify.py`, the v117–v124 changes (`git log`/FORK.md lines); Modify only where a real bug is found; Test: one failing test per bug.

- [ ] **Step 1:** Review for crashes (None handling, bad JSON, missing keys), infinite retries, and Windows path/encoding problems. Write findings to `docs/superpowers/plans/v125-review-findings.md` (file, line, bug, fixed y/n).
- [ ] **Step 2:** For each real bug: failing test, fix, pass, commit. Unproven suspicions go in the findings file only.

### Task 7: Deferred ideas (small ones only)

**Files:** `src/tagcast/anylinks.py`/`pagelist.py` (persist "From a page" lists via `studio._write("pagelists.json", …)`), `src/tagcast/galaxy.py` (after a 403 back off lyrics for 10 min and slow pacing); Tests alongside.

- [ ] **Step 1:** Failing tests: `test_page_lists_survive_restart`, `test_galaxy_403_backs_off_lyrics`.
- [ ] **Step 2:** Implement, run, commit. **Not in v125 (too large, tell the user):** guest-artist album wishes, albums with different titles.

### Task 8: Release

**Files:** `src/tagcast/static/artist-cleanup.js` (version → `fork v125`), `FORK.md` (new top Versions line in the same format as v124), zip.

- [ ] **Step 1:** Run `python -m pytest -q` and `node tests/frontend.test.cjs`; expect all pass.
- [ ] **Step 2:** Bump version, add FORK.md line, commit, `git push origin HEAD:main`.
- [ ] **Step 3:** Build `tagcast-fork-v125.zip` with the CLAUDE.md `git archive` command and send it with SendUserFile.
