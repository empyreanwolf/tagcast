<p align="center"><img src="assets/tagcast-icon.svg" width="96" alt=""></p>

<h1 align="center">Tagcast</h1>
<p align="center"><b>Metadata editor &amp; player for iBroadcast.</b></p>
<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="src/tagcast/static/ibroadcast-dark-compact-powered.svg"><img src="src/tagcast/static/ibroadcast-light-compact-powered.svg" width="220" alt="Powered by iBroadcast"></picture><br><small>Independent app by Cyberdelia</small></p>
<p align="center"><a href="https://github.com/cyberdeliaAI/tagcast/actions/workflows/tests.yml"><img src="https://github.com/cyberdeliaAI/tagcast/actions/workflows/tests.yml/badge.svg" alt="Tests"></a> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2e7d4f" alt="MIT license"></a></p>

Browse, discover and play your [iBroadcast](https://www.ibroadcast.com/) collection.
Fix metadata, find covers and artist images, review your changes, then save them
to iBroadcast. Use the **Electron desktop app** on macOS, Windows or Linux, or
run Tagcast in your browser with **Python**. Both share the same editor and player.

**Why Electron?**

Electron gives Tagcast a consistent desktop environment across macOS, Windows and
Linux. This helps us focus on reliable playback and safe metadata editing, while
keeping the interface shared with the browser version.

**Your local music files are never changed.** Tagcast's server listens on
`127.0.0.1` only. It is an independent project, not made or endorsed by iBroadcast.

**Latest update: [0.85.1](https://github.com/cyberdeliaAI/tagcast/releases/tag/v0.85.1)** restores the official iBroadcast service status in the standalone app.

**Introduced in [0.85.0](https://github.com/cyberdeliaAI/tagcast/releases/tag/v0.85.0):**
Optional experimental gapless playback for ordered albums, a dedicated Settings
page and official iBroadcast service health on Overview. Also tag selected albums
together, shuffle an album and browse albums in a stable random order.
[Gapless setup and limitations](docs/gapless-playback.md).

[Website](https://tagcast.app/) · [Install](#install) · [Wiki and user guide](https://github.com/cyberdeliaAI/tagcast/wiki) · [Community](#community) · [Screenshots](#screenshots) · [Updates](#updating) · [Media kit](media/README.md)

![Tagcast 0.80.0 album page showing Ananda Shankar, multiple genres, ratings, album information and the compact player](docs/screenshots/2026-10-23-album/album-details-ananda-shankar.png)

*Tagcast 0.80.0 — album details and compact player, captured on 23 October 2026.*

## What you can do

- **Browse and discover:** artists, albums, tracks, genres, tags, years, decades
  and the optional 1001 Albums guide.
- **Listen:** album playback, a play queue, playlists, fullscreen or in-window Now playing,
  volume and available audio outputs, supported system media controls and optional
  experimental gapless playback for compatible FLAC albums in order.
- **Edit metadata:** album/track details, separate artists and credit roles, genres,
  composers and artwork, assisted
  by online sources and a before/after review.
- **Organise:** native iBroadcast tags, independent local album and artist favourites,
  native star ratings, private playlists, folders, covers, CSV and public playlist URL import.
- **Clean up:** find missing metadata, review duplicate suggestions or select
  complete albums for iBroadcast's trash. Nothing is permanently deleted.
- **Export:** full loaded-library metadata as JSON or linked CSVs in a ZIP.
- **Keep track:** read-back checks distinguish confirmed saves from uncertain
  results; Change history records metadata outcomes and available artwork Undo.

## This fork (v58): everything it adds

This is a personal fork of Tagcast 0.85.1 / 1.0 with extra features, run on Windows with
`start-tagcast-fork.bat` (port 8913). The version shows in the UI as "fork vN". The full
change list per version is in [FORK.md](FORK.md).

### Downloads (sidebar → Downloads)
- **Download manager at the top** of the page: Pause, Cancel current, Retry failed, Clear done / waiting / failed / skipped,
  Activity log, API-limit line, overall progress and every item. The tab menu and a "songs need a genre" banner sit above it.
  Every table and list on the page folds away, and each remembers if it was open. Failed items have Copy artist / album / title.
- **Check first:** songs and albums are compared with your iBroadcast library and your download history. Anything you
  may already have waits in **Needs your decision** and is never downloaded automatically. Imports the Galaxy and Telegram histories.
- **Order for every download: Galaxy, then Bandcamp, then Soulseek.**
- **Galaxy** (GD Studio): search by artist, album or track on netease, joox and bilibili, with covers. It tries every way
  (album, song title, artist, traditional and simplified Chinese, deep results), keeps short songs, and treats a 403
  firewall page as "blocked, retry later". Songs are tagged from MusicBrainz (year, genre, track numbers, IDs).
- **Bandcamp:** paste an artist or album link; releases show Have / Partly / Downloaded before / Missing.
- **Soulseek** (via Sockseek): "Artist - Album" or "Artist - Title" per line, live numbers, and automatic hand-over to the next source.
- **Discography:** every album, EP, single and compilation of an artist plus top songs, each marked Have / Partly / Missing.
- **Missing:** *New releases* for artists you follow (hip-hop genres by default, others selectable), *Complete my albums*,
  and the **Wishlist** of things no source had yet, retried every few days.
- **From a page:** paste a page or playlist and check every album on it.
- **Clean up:** tags the way Mp3tag does (one artist field "A; B", album artist = first artist), genres placed into *your own*
  list from MusicBrainz, Chinese-named artists default to Chinese Hip Hop, and anything unsure is asked about, with a count and banner.
  *Recently uploaded* checks your uploads by day.
- **Every download is tagged right after it is saved.**
- **Which PC downloaded it:** each download records the PC name. With a shared folder (OneDrive / Dropbox) both PCs see each
  other's downloads: "downloaded at Office, not uploaded yet".
- Everything that waits runs in the background; the **Activity log** shows what worked and why something failed.
- **Activity log** (Overview, and a button in the queue): every queue step, save, background run and error, with why it failed;
  filter by area, live refresh, and a downloadable log file to send when something breaks.
- **Reliable queue:** a watchdog restarts a stopped source, a failed disk save cannot kill a worker, and a Restart button appears if needed.
- **Short songs** (intros, skits) are kept when MusicBrainz agrees; a "Keep it" button covers unknown ones.
- **Instrumental, karaoke and backing tracks** are left out of "download all" (still tickable by hand).
- **Finding sources** card shows what Tagcast is searching right now for every album it is looking for.
- **Complete my albums:** finds partly owned albums (gaps in track numbers, fewer songs than Deezer lists) and gets the rest.
- **Soulseek safety:** SoulseekQt is refused in place of Sockseek; stalled jobs go yellow, then red, then hand over after 30 minutes.

### Discover
- **Charts:** Chinese Rap (NetEase) and hip-hop charts, a Browse tab, and public Spotify playlists.
- **Recommendations** (Deezer, no AI) and a **Daily Digest** pulling together new releases, wishlist finds, chart movement and
  recommendations; its sections load one by one and warm up quietly after login.
- **Artist pictures:** a one-time job that fills in missing artist images.
- **Live synced lyrics** in the fullscreen Now playing view.
- **Genre page** lists each album artist once (picture, total albums and songs); a button shows every album instead.
- **Charts → Browse** by country and genre where each source supports it, and any public Spotify playlist link.
- **Shuffle** button on Genres, Tags, Track artists, Composers, Release year and Decades pages: one shuffled mix of everything shown.

### Library and metadata
- **Artist cleanup:** merge duplicate artists, split combined names such as `Coldplay/BTS`, link `(feat. X)` guests.
- **Merge albums** (reviewed), Quick merge of two albums, Select all on this page.
- **Saves are never sent twice;** unconfirmed saves show "Not confirmed".
- **Year:** changing an album's year sets every track's year (like genre).
- **Duplicated tag** means the same title twice on one album, not just the same track number.
- Artists created in a credit are offered in the artist lists right after saving.
- **Sortable headers** on every table (A to Z, reverse, original order).
- **Artist page** has Albums and Tracks tabs (Play all, Shuffle, search and sort); **Merge two albums** by dragging one onto another.
- **Recently uploaded** (Downloads → Clean up): your uploads by day with a cover mosaic, problem flags (no genre, odd genre, missing
  artist, album, year or cover), Edit album, and "I checked this day".
- **Uploaded today** filter on the Albums page.
- **Saves run in the background:** the dialog closes at once, values show immediately, a Tasks pill shows progress, and a refused save can be reviewed again.
- **Artist pictures** (Overview): fills missing album-artist images from a face in their covers, TheAudioDB, or a song cover.
- **200 per page** by default everywhere; Release year and Decades load faster; Sidebar entries can be shown or hidden in Settings.

### Listening
- **One-click add:** ♫＋ on any track, album card or album page adds to your quick playlist; track rows show Queue, Next and Playlist buttons.
- **Heart also adds** the song to a playlist you choose.
- **Pre-loads the next 2 songs**, optional **crossfade**, **resume last queue** after a restart.
- **Queue:** drag or Alt+arrow to reorder, Clear with Undo, smart shuffle that keeps the same artist apart.
- **Play / Shuffle / Add results** for any search or filter, **add selected albums** to a playlist, duplicates skipped, recent playlists first.
- **Smart playlists:** save a search + filter + sort under a name.

### Comfort and speed
- **Text size** A- / A+, clearer album / artist / track roles, better contrast.
- **Faster loading:** compressed and cached scripts, library list built once.
- **Phone access from anywhere** over Tailscale (see FORK.md).

### Removed or changed from the original
- **Removed:** Ask Gemini (page and key settings), the separate Recommendations page (now the **Recommended for you** tab on Charts),
  the Gemini section of the Daily Digest, and the Shazam and Billboard Hot Rap charts.
- **Changed:** the sidebar shortcut "Album artists" is now **Artist**; Track artists, Composers and Tags start hidden (turn them on in Settings → Sidebar).
- **Changed:** the download order is Galaxy, then Bandcamp, then Soulseek (it was Bandcamp, Galaxy, Soulseek in v45 to v53).
- **Changed:** the Duplicated tag no longer counts matching track numbers alone.

Not tried in every real-world case: see the notes under each version in [FORK.md](FORK.md).

## Install

### Desktop app — no Python required

Download the archive for your computer from
[Tagcast 0.85.1](https://github.com/cyberdeliaAI/tagcast/releases/tag/v0.85.1):

| System | Download | Start |
|---|---|---|
| macOS, Apple Silicon | [ZIP](https://github.com/cyberdeliaAI/tagcast/releases/download/v0.85.1/tagcast-0.85.1-macos-arm64.zip) | Move `Tagcast.app` to Applications and open it. |
| macOS, Intel | [ZIP](https://github.com/cyberdeliaAI/tagcast/releases/download/v0.85.1/tagcast-0.85.1-macos-x64.zip) | Move `Tagcast.app` to Applications and open it. |
| Windows, x64 | [ZIP](https://github.com/cyberdeliaAI/tagcast/releases/download/v0.85.1/tagcast-0.85.1-windows-x64.zip) | Extract the whole archive, then open `Tagcast.exe`. |
| Linux, x64 | [TAR.GZ](https://github.com/cyberdeliaAI/tagcast/releases/download/v0.85.1/tagcast-0.85.1-linux-x64.tar.gz) | Extract with permissions intact; run `./Tagcast` in the `Tagcast` folder. |

macOS requires **13 Ventura or newer**. Linux needs a graphical desktop and
glibc 2.35 or newer. Keep the complete package together. No separate browser,
Python or Node.js installation is required. Each archive has a SHA-256 companion.

The apps are not Developer ID notarized or Authenticode signed. If your system
blocks opening, use **System Settings → Privacy & Security → Open Anyway** on
macOS, or **More info → Run anyway** on Windows. See the
[installation guide](https://github.com/cyberdeliaAI/tagcast/wiki/Getting-started)
for details and the bundled browser CLI.

### Homebrew on macOS

```sh
brew install --cask cyberdeliaAI/apps/tagcast
```

Homebrew installs the matching desktop app and links `tagcast-cli` for terminal use.
[Homebrew guide](https://github.com/cyberdeliaAI/homebrew-apps#readme).

### Python/browser version

Extract the release's **Source code** archive or clone this repository. With
**Python 3.11+** or **uv**, use the script for your computer:

| macOS | Windows | Linux |
|---|---|---|
| `start-tagcast.command` | `start-tagcast.bat` | `./start-tagcast.sh` |

The scripts prepare a local environment and open your browser. Keep the terminal
open; Ctrl+C stops its server. From the source folder, uv users can also run:

```sh
uv run --frozen tagcast --open
```

[Source setup and port options](https://github.com/cyberdeliaAI/tagcast/wiki/Getting-started#run-from-source-with-python-or-uv).
No Node.js or Tk is required for normal Python/browser use.

## Connect iBroadcast

Choose **Connect iBroadcast → Sign in with a code**, approve Tagcast on the
iBroadcast website, then return. No client ID setup or client secret is needed;
your password stays on iBroadcast. Without an account, a demo library lets you
preview browsing and edits without saving to iBroadcast.

Settings, tokens, cache and local album favourites stay in `~/.tagcast/`, or
`TAGCAST_HOME`. Electron has its own Chromium profile; browser-local preferences,
Change history and queues are separate from your other browsers.
[Connection guide](https://github.com/cyberdeliaAI/tagcast/wiki/Connecting-to-iBroadcast).

## User guide

The **[Wiki](https://github.com/cyberdeliaAI/tagcast/wiki)** contains the full guide,
organised into focused pages:

| Topic | Guide |
|---|---|
| Install, connect and update | [Getting started](https://github.com/cyberdeliaAI/tagcast/wiki/Getting-started) · [Sign-in](https://github.com/cyberdeliaAI/tagcast/wiki/Connecting-to-iBroadcast) |
| Artists, albums, filters and appearance | [Browsing and discovery](https://github.com/cyberdeliaAI/tagcast/wiki/Browsing-and-discovery) |
| Sound, Now playing, queues and remote control | [Playback and queue](https://github.com/cyberdeliaAI/tagcast/wiki/Playback-and-queue) |
| Stars and hearts | [Ratings and favourites](https://github.com/cyberdeliaAI/tagcast/wiki/Ratings-and-favourites) |
| Playlists, folders and imports | [Playlists](https://github.com/cyberdeliaAI/tagcast/wiki/Playlists) · [CSV import](https://github.com/cyberdeliaAI/tagcast/wiki/Importing-playlists) · [Public playlist URLs](https://github.com/cyberdeliaAI/tagcast/wiki/Importing-public-playlists) |
| Native iBroadcast labels | [Tags](https://github.com/cyberdeliaAI/tagcast/wiki/Tags) |
| Editing metadata and compilation albums | [Metadata editor](https://github.com/cyberdeliaAI/tagcast/wiki/Editing-metadata) · [Various Artists walkthrough](https://github.com/cyberdeliaAI/tagcast/wiki/Various-Artists) · [Artist credits](https://github.com/cyberdeliaAI/tagcast/wiki/Artists-and-credits) |
| Suggestions, images and lyrics | [Online sources](https://github.com/cyberdeliaAI/tagcast/wiki/Online-sources) · [Artwork](https://github.com/cyberdeliaAI/tagcast/wiki/Artwork-and-artist-images) · [Lyrics](https://github.com/cyberdeliaAI/tagcast/wiki/Lyrics) · [LRC/TXT import](https://github.com/cyberdeliaAI/tagcast/wiki/Importing-lyrics) |
| Reviewed cleanup and trash | [Cleanup and duplicates](https://github.com/cyberdeliaAI/tagcast/wiki/Cleanup-and-duplicates) |
| Listening-list matches and background texts | [1001 Albums](https://github.com/cyberdeliaAI/tagcast/wiki/1001-Albums) |
| Settings, Help, saves, multi-disc sets and large libraries | [Settings and help](https://github.com/cyberdeliaAI/tagcast/wiki/Settings-and-help) · [Saving and history](https://github.com/cyberdeliaAI/tagcast/wiki/Saving-and-history) |

## Screenshots

The [full gallery](https://github.com/cyberdeliaAI/tagcast/wiki/Screenshots) shows
browsing, tags, editing, cleanup, playback, Settings and mobile views. The latest
captures show 0.80.0 with a separate sample library, including the
metadata editor, narrow-window player and lyrics import. Earlier-interface examples
stay labelled. The [capture inventory](docs/screenshot-checklist.md) records the originals.

<details>
<summary>Current editor and lyrics import, playlists and retained cleanup/fullscreen examples</summary>

![Edit album tab with metadata fields and online source suggestions](docs/screenshots/2026-10-23/editor-album.jpg)

![Edit tracks tab with an independently scrolling track list and selected track details](docs/screenshots/2026-10-23/editor-tracks.jpg)

![Album lyrics import with album tracks and matched LRC files](docs/screenshots/2026-10-23/lyrics-import.jpg)

![Playlist cards with track counts and total duration](docs/screenshots/2026-10-22/playlists.png)

![Playlist order editor with search and separate Save order and Cancel controls](docs/screenshots/2026-10-22/playlist-order.png)

**Album artist cleanup:** reviewed semicolon splitting into a primary
artist and separate additional artists. This capture shows an approved run in progress.

![Album artist cleanup in progress with checked disc counts and primary/additional artist names](docs/screenshots/cleanup-album-artists-running.png)

![Fullscreen Now playing with cover and playback controls](docs/screenshots/fullscreen.png)

</details>

## Updating

**Quit the older Tagcast first**, or stop its terminal server with Ctrl+C. Replace
it with the new download, or update your source checkout. Your server settings
and local account favourites remain in place. Electron rejects an older server
on its port rather than loading the old interface.

For Homebrew: `brew update`, then `brew upgrade --cask cyberdeliaAI/apps/tagcast`.
Tagcast's update check only notifies; it never installs updates automatically.
[Update instructions](https://github.com/cyberdeliaAI/tagcast/wiki/Getting-started#updating-tagcast).

## Community

Join **[r/Tagcast on Reddit](https://www.reddit.com/r/Tagcast/)** to ask questions,
share ideas and discuss Tagcast with other listeners. For bug reports, use
[GitHub Issues](https://github.com/cyberdeliaAI/tagcast/issues).

## Development

A Python server and plain JavaScript frontend are shared by both routes.
[Development and API notes](https://github.com/cyberdeliaAI/tagcast/wiki/Development),
[Electron design](docs/electron.md) and [native builds](docs/native-builds.md)
describe the architecture, test commands and packaging. See
[documentation maintenance](docs/documentation.md) for Wiki updates.

## Media, credits and license

The [media kit](media/README.md) contains logos, icons, wordmarks, screenshots and
the GitHub social preview. [Download the media ZIP](https://github.com/cyberdeliaAI/tagcast/raw/refs/heads/main/media/tagcast-media-kit.zip).

Developed by Cyberdelia, with AI-assisted development using Claude (Anthropic)
and ChatGPT / Codex (OpenAI).

[MIT](LICENSE) for Tagcast code. The original Powered by iBroadcast logos belong
to iBroadcast and are displayed under its
[branding guidelines](https://help.ibroadcast.com/en/developer/branding).
