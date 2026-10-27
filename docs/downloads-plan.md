# Downloads plan (agreed 26 Oct 2026)

Goal: one app. Tagcast gets a **Downloads** section that downloads music and checks
every song/album against the user's iBroadcast library and download history
**before** downloading, so duplicates are never fetched silently.

Design mockups (6 screens): https://claude.ai/artifact/48RUd5CmZUQqde3ihyFqNW
(Bandcamp, Galaxy, Telegram check, Needs your decision, Queue & history, artist Discography).

## Decisions

- **Sources, in this order** for "get missing": **Bandcamp → Soulseek → Galaxy**.
  Quality does not matter (MP3 is fine; no FLAC preference, no quality filters).
- **Not built:** downloading from streaming services (Spotify, Deezer, Apple Music,
  Tidal, Qobuz, YouTube Music; e.g. OnTheSpot). Deezer/Apple are used for
  information only (discography, top songs, 30 s previews). No Bandcamp purchases.
  No auto-upload to iBroadcast yet (tags get cleaned first, later "part 3").
- **Bandcamp:** port the behaviour of otiel/BandcampDownloader (C#) to Python:
  artist or album link → discography with Have/Partly/Downloaded before/Missing →
  download whole discography, album or single songs (128 kbps stream MP3), tagged
  with cover, saved as `Album Artist\Album\## - Title`.
- **Soulseek:** don't build a client. Run **Sockseek** (fiso64, formerly
  slsk-batchdl; AGPL; Windows binaries; HTTP API; album mode by default) in the
  background and drive it from Tagcast. Needs a free Soulseek login and a shared
  folder setting.
- **Galaxy:** port the user's own GD downloader
  (github.com/empyreanwolf/gd-music-downloader, `app.py`): GD Studio API search
  (netease/joox/bilibili), album view with exact-edition matching, queue, rate limit
  50 req / 5 min, tagging (mutagen), MusicBrainz lookup. Results **grouped by artist**
  with an artist card: the user's iBroadcast tracks + genre, Apple Music genre,
  MusicBrainz description, Deezer album count; other same-name artists listed apart.
- **Telegram:** the user requests songs manually in Telegram (@deezload2bot); the
  listener only receives files. Port github.com/empyreanwolf/telegram-auto-downloader
  (Telethon, reuse `safe_session_v2.session`, catch-up, watchdog, hash history) into
  Tagcast as a background service. Check each file's Telegram performer/title
  **before** downloading; duplicates are held. A **"Check before you ask the bot"**
  box answers "do I have this?" for typed text or a Deezer link.
- **Duplicates:** never auto-download a duplicate; hold it in **Needs your decision**
  (shared by all sources), decide in bulk or one by one (Skip / Download anyway),
  showing "You have" vs "This one".
- **Matching:** reuse `artist_match.py` (bilingual, simplified/traditional, case,
  spacing). Titles ignore "(feat. …)", case and spacing but keep version words
  (Live, Remix). Albums: artist + title + track count; another edition = partly owned.
- **Discography page** (artist page tab): Deezer + Apple Music in one request each,
  MusicBrainz only on request; cached 7 days. Have / Partly / Downloaded before /
  Missing; download whole discography, album, or song via the source order above.
  **Top 30 songs:** Deezer ranking, Galaxy search order as fallback; Have/Missing
  badges, 30 s Deezer previews, "Get the missing".
- **One queue, one history** (import Galaxy `history.json` and the Telegram history
  in `%APPDATA%\TelegramAutoDownloader\`), one download folder setting.
- Every update raises the fork version (shown in the UI) and an error plays the
  alert sound (see `artistCleanupAlarm` in `static/artist-cleanup.js`).

## Build order (one version per chat)

| Version | Scope |
|---|---|
| v9 ✔ | Downloads page + tabs, duplicate engine, Needs your decision, Queue & history (+ history import), download folder setting |
| v10 ✔ | Artist cleanup in the background (not a download step) |
| v11 ✔ | Galaxy part 1: search, same-name artist cards, exact albums, queue, limits, tags + cover |
| v12 ✔ | Galaxy part 2: MusicBrainz lookup (year, genre, track numbers, IDs, album years) and Galaxy settings (pauses, API quota). Lyrics shelved; no history extras |
| v13 ✔ | Bandcamp |
| v14 ✔ | Soulseek via Sockseek |
| v15 ✔ | Discography + Top 30 + "get missing" source order |
| v16 ✔ | New-release alerts (weekly, followed artists + artists with 3+ albums) |
| v17 ✔ | Wishlist with retry every few days |
| v18 ✔ | "Complete my albums" report |
| v19 ✔ | Clean up: Mp3tag-style artist-field split + album artist, hip-hop/rap genre collapse into an existing genre (MusicBrainz for Chinese vs not), needs-decision mass-tag by artist |
| v20 ✔ | Charts: a new top-level page (NetEase Chinese Rap, HotNewHipHop, Deezer, Apple Music, Shazam, Billboard hip-hop/rap charts), checked against library/history, "Get it" per song |
| v21 ✔ | Ask Gemini: a chat page backed by the user's own free Gemini API key; songs/artists it names are checked against the library/history and queued the same way as Charts |
| v22 ✔ | Charts → Browse: any country/genre, per source's real capability (Apple: country; Shazam: both; Deezer: genre) |
| v23 ✔ | Recommendations: Deezer related-artist list + a Gemini-based list, shown separately |
| v24 ✔ | Daily Digest: new page aggregating new releases, wishlist finds, chart movement, recommendations |
| v25 ✔ | Remote access: `TAGCAST_EXTRA_HOSTS` opt-in Host-header allowlist for reaching the same loopback socket through `tailscale serve` (phone access, no new attack surface) |
| v26 ✔ | Live sync lyrics: LRCLIB's line timestamps drive an auto-scrolling, click-to-seek lyrics panel in the fullscreen Now playing view |
| v27 ✔ | Artist pictures: one-time "Run now" job filling in every album artist with no image -- a face in one of their own covers, else a TheAudioDB photo, else just a cover |
| v28 | Telegram check (listener inside Tagcast) |

Testing: unit tests with `tests/mock_ibroadcast.py` / LocalStudio and one quick real
check per version. Avoid full-library stress runs unless something needs it.
