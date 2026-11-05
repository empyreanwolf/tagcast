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

## This fork (v79): everything it adds

This is a personal fork of Tagcast 0.85.1 / 1.0 with extra features, run on Windows with
`start-tagcast-fork.bat` (port 8913). The version shows in the UI as "fork vN". The full
change list per version is in [FORK.md](FORK.md).

### Downloads (sidebar → Downloads)
- **Download manager at the top** of the page: Pause, Cancel current, Retry failed, Clear done / waiting / failed / skipped,
  Activity log, API-limit line, overall progress and every item. The tab menu and a "songs need a genre" banner sit above it.
  Every table and list on the page folds away, and each remembers if it was open. Failed items have Copy artist / album / title.
- **Check first:** songs and albums are compared with your iBroadcast library and your download history. Anything you
  may already have waits in **Needs your decision** and is never downloaded automatically. Imports the Galaxy and Telegram histories.
- **Order for every download: YouTube, then Galaxy, then Bandcamp, then Soulseek.**
- **YouTube** (yt-dlp): official audio found by `Artist - Title`, saved as AAC without re-encoding (lossy, about 128 kbps); can be switched off in Settings.
- **More sources**: a Downloads tab with one SoundCloud search bar (one-click download) and a box for SoundCloud/Audiomack links; SoundCloud Top Hip-hop & Rap on the Charts page. Lossy files.
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

### Newer download sources and tools (v59 to v75)
- **YouTube (yt-dlp), tried first:** an artist search shows Albums and Singles with tracks, one-click Download per release or
  Download all; YouTube Music first, then plain YouTube, and a Galaxy fallback if neither has it. Links with a list wait for a
  click (this song, or the whole album / playlist).
- **More sources:** one SoundCloud search bar, SoundCloud and Audiomack links, Spotify links (spotDL), and SoundCloud Top Hip-hop & Rap on Charts.
- **Labels:** type a record label (MusicBrainz) or paste its Bandcamp link; missing releases are marked and can be downloaded in one click.
- **Discography** also uses Discogs (with a token) and NetEase for Chinese artist names; genre decisions ask Discogs, Apple,
  QQ Music, then MusicBrainz.
- **Split albums:** finds one album that was split into many by different album artists and merges it in one click.
  **Swap artist/album** saves, and **Shift-click or drag** to select many albums at once.
- **Upload page:** scan a folder into an Mp3tag-style grid (title, artist, album, year, genre, cover, created date) and upload.
- **Update now & restart:** one button downloads the fork's newest version from GitHub and installs it over the folder; both PCs
  can share the same choices. A debug report and a queue notification help when something breaks.

### Look and players (v76 to v78)
- **Sonora look** (Theme menu › Look, default): dark, glassy, Inter font, big rounded album art that glows on hover, a glass
  player bar. **Classic** brings the old look back. Seven **accent colours** plus Auto, which follows the playing cover.
- **Players that follow the music's age:** Now playing, the bottom bar and the Mini Player draw a device for the album's year,
  with the cover on it, moving while a song plays: phonograph, gramophone, radio, jukebox, transistor, reel-to-reel, vinyl, car
  radio, boombox, Walkman, cassette, MiniDisc, portable CD, CD, MP3 player, iPod, headphones, DJ decks and a festival stage.
  **Hip hop albums get an animated rapper.** Theme menu › Player style forces one or turns it off; **How we listen** is a
  timeline of every device. The device keeps to the left column beside the lyrics.
- **Visualizer (Butterchurn / Milkdrop):** Theme menu › Visualizer. Show it instead of the player picture (also beside the
  lyrics), as a full-page Now playing background, or behind the whole site; Now playing has Visualizer, Lock and Next look
  buttons, the Mini Player has its own button, and the look changes every 30 seconds unless locked. Loaded from a CDN on first use.

### Lyrics (v69, v78, v79)
- **Now playing › Lyrics:** iBroadcast lyrics first, then Unison and LRCLIB online, with timed lines that follow the music.
- **Better matching:** ignores a leading "Artist - ", Dirty / Clean / Explicit, "Da / The" slang and traditional vs simplified
  Chinese, and allows a few seconds of length difference.
- **Search lyrics…** lets you type another title and artist when nothing (or the wrong song) is found.
- **Chinese lyrics:** GD Studio (NetEase) is the fallback after LRCLIB, checked against title, artist and song length.
- **Galaxy downloads carry their lyrics:** timed lyrics are embedded in the file and saved as a `.lrc` file next to it.

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
- **Find lyrics** (Overview): a slow background job that finds timed lyrics for every song without any (LRCLIB, then GD Studio), saves them to iBroadcast, and lists the unsure ones for you to pick. Pause, resume or stop any time.
- **200 per page** by default everywhere; Release year and Decades load faster; Sidebar entries can be shown or hidden in Settings.

### Listening
- **Quick add:** track rows show Queue, Next and Playlist buttons (the ♫＋ one-click button has been hidden since v76).
- **Favourites:** Play and Shuffle buttons play every favourite song as one queue.
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
- **Changed:** the download order is YouTube, Galaxy, Bandcamp, then Soulseek (it was Bandcamp, Galaxy, Soulseek in v45 to v53).
- **Changed:** the Duplicated tag no longer counts matching track numbers alone.

Not tried in every real-world case: see the notes under each version in [FORK.md](FORK.md).



## Media, credits and license

The [media kit](media/README.md) contains logos, icons, wordmarks, screenshots and
the GitHub social preview. [Download the media ZIP](https://github.com/cyberdeliaAI/tagcast/raw/refs/heads/main/media/tagcast-media-kit.zip).

Developed by Cyberdelia, with AI-assisted development using Claude (Anthropic)
and ChatGPT / Codex (OpenAI).

[MIT](LICENSE) for Tagcast code. The original Powered by iBroadcast logos belong
to iBroadcast and are displayed under its
[branding guidelines](https://help.ibroadcast.com/en/developer/branding).
