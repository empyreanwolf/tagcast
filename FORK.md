# This fork: Artist cleanup

Based on Tagcast 0.85.1 (cyberdeliaAI/tagcast, MIT). Adds **Artist cleanup**:
merge duplicate artist entries, split combined names such as `Coldplay/BTS`, and
link guests named in titles such as `(feat. X)`. See `docs/artist-cleanup.md`.

## Downloads (from v9)

**Downloads** in the sidebar. **Check** compares songs/albums (`Artist - Title`, albums
may end with `| 12` for the track count) with your iBroadcast library and download
history. Anything you may already have waits in **Needs your decision**; it is never
downloaded automatically. **Folder & imports** sets the download folder (default: your
Music folder) and imports the Galaxy `history.json` (use **Find it**, or paste the path:
it sits next to the Galaxy app's `app.py`) and the Telegram history from
`%APPDATA%\TelegramAutoDownloader\`. Importing again adds only new songs. Nothing is
uploaded to iBroadcast.

## Run it on Windows

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH"), or uv.
2. Extract this folder anywhere, for example `Documents\tagcast-fork`.
3. Double-click **`start-tagcast-fork.bat`**. The first start installs Tagcast into
   a `.venv` folder next to it; later starts are quick. Your browser opens
   `http://127.0.0.1:8913`.
4. Sign in to iBroadcast as usual. The fork uses the same settings folder
   (`%USERPROFILE%\.tagcast`) as the official app, so an existing sign-in is reused.

The fork runs on port 8913 so it never reuses an official Tagcast already running
on 8912. Close the black window to stop it.

## Suggested order

1. Turn off **Combine Multi-Disc Album Sets** in iBroadcast.
2. **Search & merge** one tiny test first (for example `Lotus ` → `Lotus`), then check in
   the iBroadcast app that the old entry disappeared.
3. **Duplicates** → **Combined names** → **Featured in titles** → **Duplicates** again.
4. Export each report before preparing the next preview if you want to keep it.

## Versions

The fork version is shown beside the **Artist cleanup** title and in its error messages.
A short alert sound plays when a run stops with an error or an action fails.

- **v10** Artist cleanup runs in the background: close its window and keep browsing, playing
  and using Downloads. A bar at the bottom shows the progress (Open / Stop). When it ends: a
  chime (error sound on failure), a message and a Windows notification. Album edits saved
  during a run wait and are sent when it finishes.
- **v9** Downloads page: duplicate check against iBroadcast and the download history,
  Needs your decision (Skip / Download anyway, in bulk or one by one), queue & history,
  Galaxy and Telegram history import, download folder setting. No sources yet (v10+).
- **v8** Version label and error sound.
- **v7** The approval tick stays ticked when the review list redraws.
- **v6** Additional artists stored in another order count as saved; unconfirmed writes
  name the record, what was expected and what iBroadcast has.
- **v5** Records changed during a run are replanned from their current artists.
- **v4** Albums that iBroadcast joins during a merge are followed, not conflicts.
- **v3** A duplicates card can never lock itself.
- **v2** Faster previews, bracket-aware feat. reading, port 8913 launcher.
- **v1** Artist cleanup: duplicates, combined names, featured artists, search & merge.
