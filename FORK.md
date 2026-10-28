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

## Using it from your phone, anywhere (v25)

Tagcast only answers `http://127.0.0.1` by default -- on purpose, since it holds your
iBroadcast sign-in and any API keys you've added, and can start downloads. To reach it from
your phone from anywhere (not just your home WiFi), without opening it up to anyone else:

1. Install [Tailscale](https://tailscale.com) on this PC and on your phone, and sign in to
   the same Tailscale account on both (free for personal use).
2. On this PC, run `tailscale serve --https=443 127.0.0.1:8913` (use 8913, this fork's
   port). Run `tailscale status` to see this PC's Tailscale name, something like
   `your-pc.tailxxxxx.ts.net`.
3. Set a Windows environment variable `TAGCAST_EXTRA_HOSTS` to that name (System Properties
   → Environment Variables, or `setx TAGCAST_EXTRA_HOSTS your-pc.tailxxxxx.ts.net` in a new
   Command Prompt), then restart Tagcast so it picks it up.
4. On your phone, with the Tailscale app running, open `https://your-pc.tailxxxxx.ts.net` in
   the browser. "Add to Home Screen" there makes it feel like its own app.

If step 4 gives a 403, the error message on the page names the exact Host it saw and didn't
recognize -- add that one to `TAGCAST_EXTRA_HOSTS` instead (`tailscale serve`'s exact
forwarding behavior wasn't something I could test from here). Downloads you start from your
phone still run, and land in your Music folder, on this PC -- your phone is only the remote
control.

## Versions

The fork version is shown beside the **Artist cleanup** title and in its error messages.
A short alert sound plays when a run stops with an error or an action fails.

- **v40** **Ask Gemini waits up to 2 minutes** for an answer (was 30 seconds) and says plainly when it times out.
- **v39** **Ask Gemini / Tavily keys: pasted spaces are removed.** A key copied with a space, line break
  or invisible character inside is cleaned instead of refused with "That Gemini API key doesn't look right."
- **v38** **Recently uploaded: check your uploads by day.** Downloads → **Clean up** now has two
  tabs; **Recently uploaded** mirrors iBroadcast's Date Uploaded page.
  - **By day**: one row per upload day, newest first, with a cover mosaic, the date, albums,
    tracks and total length. Click a day for its albums and tracks (genre, artist, album artist,
    album, year).
  - Anything missing or suspicious is flagged: no genre, an odd genre (Unknown, Other…), genres
    joined with ";", no artist / album artist / album name / year / cover. **Problems only**
    shows just those.
  - **Edit album** opens the normal editor and Review dialog; the list refreshes after you save.
  - **Most recent**: the flat newest-first list, like the Most Recent Uploads playlist.
  - Tick **I checked this day** to remember a day is done (kept in Tagcast's settings).
- **v37** **Instrumental versions are left out of "download all".** In Downloads → Galaxy, **Queue all**
  and **Download album** (artist, album and whole-album buttons) skip tracks whose title says
  Instrumental, Karaoke, Off Vocal, Backing Track, 伴奏, 纯音乐 / 純音樂, 无人声 / 無人聲 or 消音.
  They stay in the list with an **Instrumental** tag and a note ("N instrumental left out"); tick
  them and use **Download selected** (or the single ⬇ button) when you do want one.
- **v36** **Everything that waits now runs in the background.** Saving tags, changing a cover or
  artist image, ratings, favourites, trash, playlists and lyrics no longer hold the page:
  - The dialog closes at once and the new values show straight away; the save finishes behind
    the scenes. If iBroadcast refuses it, the old values come back and a "Not saved" box (and
    History) lets you review the same changes again.
  - Saves wait in one queue, in the order you made them, on the server. No more "Another save is
    still running". A **Tasks** pill (bottom right) shows what is running, waiting or failed.
  - If the queue is congested (6+ waiting, or one waited 45 s+) Tagcast tells you, with a rough
    time, and asks you to keep it open (closing the tab warns while tasks are active).
  - **Artist pictures**: saved in batches of 10 with one confirmation per batch (before: one
    full library download per artist). Each line says Saving / Saved / Not showing yet / Failed,
    and a picture appears in Tagcast as soon as iBroadcast confirms it.
- **v35** **Activity log: see what's working and what isn't, under the hood.** Overview →
  **Activity log** (or **Problems only**), and an **Activity log** button in the Downloads queue.
  Everything Tagcast does is recorded, newest first:
  - **The download queue, item by item**: queued (and on which source), held for your
    decision (and where Tagcast thinks you already have it), your Skip / Download anyway,
    started, saved (where, how long it took), failed (why, and at which step), retried,
    removed. Galaxy, Bandcamp, Soulseek and Telegram alike.
  - **Tags and metadata**: every save to iBroadcast with each field's new value, which records
    iBroadcast refused, and the read-back afterwards (what iBroadcast actually has); every file
    or track Tag clean-up wrote, or failed to.
  - **Background runs**: how each Clean up metadata / Artist cleanup / Duplicate cleanup run
    ended (with its items counted by outcome), and every Artist pictures artist (picked a face,
    a photo, a cover, or skipped and why).
  - **Every button that reaches Tagcast**, with what was sent, what came back and how long it
    took; any failure with its message, and for a crash the full technical details to send me.
  - **Warnings and errors from inside Tagcast** that used to sit only in `tagcast.log`, or
    nowhere at all (lookups that failed quietly, cover fetches, MusicBrainz misses).
  - **The page's own errors**: a script error or Tagcast being unreachable is reported too.
  The top of the page shows, for the last 24 hours, each area's OK / error / warning counts and
  its last problem; click one to see only that area. Search, filter by outcome, and it refreshes
  itself every 3 seconds while open (untick **Live** to stop). **Download log file** saves the
  whole log as readable text, the thing to send along when something breaks. It's kept in
  `activity.log` in your settings folder (`%USERPROFILE%\.tagcast`), at most about 6 MB, so it
  never grows without limit. Passwords, tokens and API keys are never written to it. Your
  terminal window stays as quiet as before.
- **v34** **Soulseek: SoulseekQt is no longer accepted as "the Sockseek program".** Tagcast
  runs a separate background downloader called Sockseek; pointing that setting at SoulseekQt
  (the normal Soulseek app) made a SoulseekQt window pop up on every job ("already running"),
  and every job failed with "Sockseek did not start". Tagcast now refuses SoulseekQt there,
  both when you choose it and if it was saved before, and says what to pick instead:
  sockseek.exe from github.com/fiso64/sockseek/releases. The same Soulseek login can only be
  online once, so close SoulseekQt while Tagcast downloads, or give Tagcast its own username.
- **v33** Seven fixes and changes from your feedback list:
  - **The artist page works like iBroadcast's.** An album artist's page now has **Albums**
    and **Tracks** tabs. Tracks lists every song on that artist's albums in one table (title,
    album, year, time), with its own search and sort, **Play all**, **Shuffle**, and click any
    song to play the list from there. The tab you last used is remembered.
  - **Merge two albums by the same artist.** On an artist's page, drag one album cover onto
    another, or select exactly two albums and press **Merge 2 albums** (that one also works on
    a phone; the album with more tracks keeps its title). The merged album takes the other's
    title (and year, if different); iBroadcast then joins them, just like dragging in
    iBroadcast. Track numbers, track tags and covers are left exactly as they are. It goes
    through the usual Review dialog, so nothing is written until you press Save, and the
    read-back now counts the joined album as saved rather than "unverified".
  - **200 per page by default**, everywhere there's a page-size choice (albums, artists,
    browse pages, tracks, favourites, recently played, 1001 Albums, which gained a 200
    option). Sizes saved before this version are reset once so you see 200 straight away; any
    size you pick afterwards is remembered as before. Recently played and 1001 Albums now
    remember their size too.
  - **Release year and Decades load faster.** They wait on an index of your whole library
    that has to be rebuilt every time the library reloads, which happens after every edit
    (including Artist cleanup and Artist pictures runs). It is now built in the background as
    soon as a new library arrives, and a general speed-up in how Tagcast looks up library
    records makes the build itself roughly 20–40% faster (measured on a library the size of yours).
  - **Artist pictures moved into Overview**: it's the **Artist pictures…** button in the
    Metadata health card (next to the "Album artists with an image" meter), with a ← Overview
    button to go back. It no longer has its own sidebar entry.
  - **Artist pictures uses song covers too.** Most of the "No album cover to use yet"
    skips were artists whose albums *do* show a cover in iBroadcast: the album record itself
    has no cover, and iBroadcast shows the first track's artwork. That now counts, and when an
    artist has no usable album cover at all, the cover of any song they're on (featured
    appearances included) is used. Press **Run now** again: everyone skipped before is tried
    again.
  - **Bandcamp: clearer failures.** Both your links failed the same way, and even the artist's
    releases page came back empty ("1 releases" for an artist with 28), so Bandcamp was most
    likely sending Tagcast something other than the real pages, such as a "checking your
    browser" page. Tagcast now sends the standard headers every browser sends, says what it
    actually received instead of only "no album on it" (a "check you're a person" page is named
    as such), writes the start of that page to `tagcast.log`, and no longer keeps such a page
    cached for 10 minutes, so the next try really asks again. If it still fails, the message and
    the log line will show what to fix next.
- **v32** **Galaxy finds songs and albums the GD Studio site shows but Tagcast missed.**
  Two reports: "Empire State Of Mind (feat. Alicia Keys)" by JAŸ-Z came back "Not found
  on Bandcamp, Soulseek or Galaxy" although the site lists it, and an Album search for
  "Heavy Lies the Crown" (While You Were Gone) said no such album although the site shows
  its 5 songs. Fixed in three ways:
  - **"Get it" (Discography, Charts, Recommendations, Wishlist) searches the way you
    would.** It used to make one search, "Artist Title" with the "(feat. …)" left in, on one
    source, and look at only 10 results. It now also tries without "(feat. …)" or brackets,
    and the title alone, with 30 results each, stopping at the first real match. The artist
    still has to match, so a cover by someone else isn't picked.
  - **Accents no longer split an artist.** "JAŸ-Z" and "JAY-Z", "Beyoncé" and "Beyonce" now
    count as the same artist when Galaxy checks who a song is by.
  - **Albums: a song search backs up the album search.** When the API's album search has
    nothing, Tagcast now runs a normal song search (the same one as the site's 歌曲搜索)
    and keeps only songs whose album name matches exactly; loose singles never count.
  - "Get it" and "Get the missing" now try your chosen source first, then netease and joox,
    before giving up. A manual Album search stays on the source you picked, and its
    "not found" note now suggests the other sources to try.
- **v31** **Short songs are no longer thrown away as "previews".** Galaxy used to reject
  every file under 35 seconds as a 30-second preview, so a real 32-second intro or skit
  could never be downloaded (reported: "A WORD 4RM MEL" by Kal Banx, "Preview only (32s)").
  Being short is no longer enough on its own. A short file that otherwise arrived whole is
  now checked against the song's length on MusicBrainz (already looked up for every
  download): if MusicBrainz says the song really is that short, it's saved; if it says the
  song is minutes long, it's still rejected, and the note now says so ("Preview only (30s;
  MusicBrainz says 3:12)"). When MusicBrainz doesn't know the song, the item fails as
  "Maybe a preview" with a **Keep it (short track)** button that downloads it again and keeps
  it. The checks that catch a cut-off file (fewer bytes or seconds than the API announced)
  are unchanged and still apply even after Keep it. Bandcamp already compared against each
  track's own listed length, so it was never affected.
- **v30** Three fixes from a report after v29, plus one small feature:
  - **The duplicate check no longer treats a "Various Artists" (or otherwise
    unrecognised-artist) library track as a wildcard.** A compilation track titled
    "Intro" was making every artist's own "Intro" look like a duplicate -- press
    "Skip" enough times on a whole discography and you'd skip songs you never
    actually had. Only a source with *no artist text at all* (an old Telegram file
    with no "Artist - Title" in its name) still falls back to matching by title alone;
    a *named* artist that just doesn't produce a useful key, like "Various Artists",
    no longer does.
  - **Galaxy no longer substitutes a different artist's album when the one you asked
    for shares its exact title.** Downloading an artist's whole discography could
    silently grab songs from someone else entirely (reported: asking for Moka Only's
    discography pulled in Travis Roig and an unrelated "beaming" release) whenever
    another artist happened to have an album with the exact same name and the search
    turned up no exact artist match -- Galaxy used to fall back to "the biggest
    same-named group" regardless of whose it was. It now skips an album like that
    entirely (consistent with "skip albums with no exact edition match") rather than
    guessing.
  - **Shuffle play, a new 🔀 button next to "Play random album"** on any Genres, Tags,
    Track artists, Composers, Release year or Decades detail page: builds one shuffled
    playlist from every song in every album currently shown (not just one album at a
    time), so you can press it on "Chinese Hip Hop" and get a random mix across your
    whole collection in that genre.
  - If you're running artist splitting and a download in **two separate Tagcast
    windows/processes at once**, pointed at the same settings folder: that's very
    likely adding to the stuck-queue problem from v29, not just the disk-write issue
    already fixed there. Each process keeps its own copy of the download queue in
    memory and saves over the other's file periodically, so changes can get lost or
    conflict. Tagcast doesn't need two windows for this -- Artist cleanup and
    Downloads are both tabs in the same app -- so please run one instance only.
- **v29** Download manager resilience, after a report of the Galaxy queue getting stuck
  ("984 waiting", 0 saved, no way to recover but a restart). The most likely cause: the
  download queue is saved to disk after almost every change, and that save was not
  wrapped in any error handling -- a single disk hiccup (antivirus briefly locking the
  file, a cloud-sync conflict, a full disk, etc.) could silently kill a download worker's
  background thread for good, with nothing ever written to the log, and nothing to
  restart it. Fixed on three levels:
  - Saving the queue to disk can no longer kill a worker thread -- any error there is now
    caught, written to `tagcast.log`, and remembered so the Downloads page can show it.
  - Each download worker's entire loop (not just the download itself) is now wrapped in
    its own error handler, so no error, from any cause, can end that thread silently again
    -- it logs the problem and keeps going.
  - A watchdog, run every time the Downloads page refreshes (about every 1.5 seconds while
    something is in progress), notices a source with waiting work and no running thread,
    puts any item it had half-started back in the queue, and restarts that source's
    worker automatically -- no click needed, in almost all cases.
  - For anything the watchdog can't fix alone, the queue view now says plainly when a
    source "Stopped unexpectedly", points at `tagcast.log`, and shows a **Restart** button
    next to **Pause**/**Resume**.
  - This fix can't un-stick a queue that's already stuck from *before* updating -- restart
    Tagcast once to clear that, and the new log entries will catch anything that happens
    next.
- **v28** Three small usability fixes, none of them a new page:
  - **Sidebar cleanup**: Track artists, Composers and Tags now start hidden (the first
    time Tagcast ever runs in a browser only -- if you've already opened Settings and
    changed the sidebar, your choice stays exactly as it was). Turn any of them back on
    in **Settings → Sidebar**. The remaining single artist shortcut is now just called
    **Artist** rather than "Album artists" (it works exactly as before -- it browses by
    album artist; nothing about what it shows changed, only the name).
  - **Genres, Tags, Composers and Favourites load instantly on a repeat visit**: these
    pages were re-asking iBroadcast every single time you opened them, even seconds
    apart with nothing changed. They now only ask again when something about the
    request changed (a different search, sort, or page) or the library itself was
    reloaded -- otherwise the page you already had is shown straight away.
  - **"Uploaded today"**, a new entry in the Albums filter menu: shows just the albums
    with at least one track iBroadcast recorded as uploaded today (your computer's
    local date), the same place you'd check and tag anything you added today. iBroadcast's
    own web player has a similar daily view; this reuses the per-track upload date
    Tagcast already tracked for the "Date uploaded" sort, so no extra request is needed.
- **v27** **Artist pictures**, a new page on the left: a one-time "Run now" job that
  gives every album artist with no image one, in order -- a face found in one of their
  own album covers wins and reuses that cover directly (no re-upload); failing that, a
  real photo from TheAudioDB (free, no key -- the same source already used for
  biographies, looked up by exact artist name only; it has no way to search by genre, so
  genre isn't used); failing that, just one of their own covers anyway, which still looks
  better than the blank default. Only artists with no image yet are touched -- nothing
  already set is replaced. Face detection is OpenCV's bundled frontal-face Haar cascade
  (no extra download, no extra API key) and is best-effort by design: it can miss a
  stylised, side-profile, small, or busy cover, or occasionally flag something that isn't
  a face, and a miss just falls through to the next source rather than erroring. "Search
  Google" wasn't something a free API can actually automate within Google's terms, so
  this uses TheAudioDB instead. The existing **Change artwork** dialog is completely
  unaffected and can always override anything this run picks, any time.
- **v26** **Live sync lyrics**: in the fullscreen Now playing view, a track whose LRCLIB
  lyrics carry line timestamps now scrolls automatically with the music, highlighting the
  current line, and clicking a line seeks there. LRCLIB's timing is community-submitted and
  can be a little off the actual recording. A track with only plain lyrics (most of them,
  or anything saved in iBroadcast, which has no timing field at all) still shows the same
  plain, non-highlighted scrollable text as before; a track with no lyrics at all shows
  nothing. Nothing changes outside the fullscreen view -- the Lyrics dialog and the Now
  playing panel are unaffected.
- **v24** **Daily Digest**, a new page on the left: one read-first page pulling together
  what the other pages already track. New releases (its own undismissed results). Wishlist,
  just found -- the most recent items it dug up, flagged "New" until you hit "Mark seen"
  (same manual acknowledgement New releases already uses). Chart movement -- which songs on
  each of the six fixed Charts tabs are there now that weren't the last time this page
  looked (Charts itself has no history to diff against, so this keeps its own snapshot per
  chart; the very first look at a chart has nothing to compare yet, so it says so rather
  than listing the whole chart as "new"). The top of each Recommendations list. Every
  section fails on its own -- one chart's site changing, or no Gemini key yet, only takes
  out that section, never the page. Nothing downloads from here; each section links back to
  its own page for that.
- **v23** **Recommendations**, a new page on the left, next to Ask Gemini: two separate
  lists seeded from the artists New releases already tracks (followed artists, plus your
  library's own 3+-album artists if that setting is on). **Deezer (related artists)** is
  deterministic -- no AI -- using Deezer's own related-artist data, tallied across your
  seed artists so someone related to several of them ranks first; each kept artist's
  current top track on Deezer is the song offered. **Gemini** asks your own Ask Gemini key
  and model (Settings are shared) for other artists worth exploring, one song each, as a
  one-off question that isn't added to that page's conversation. The two lists are shown
  separately, never merged -- a deterministic signal and a model's opinion are different
  kinds of evidence. "Get it" works the same as Charts: Bandcamp → Soulseek → Galaxy, one
  song at a time, landing on the wishlist if nothing has it.
- **v22** Charts → **Browse**, alongside the existing fixed hip-hop tabs (which are unchanged):
  pick any country and/or genre, as far as each source actually supports it -- nothing here
  is guessed. Apple Music takes any 2-letter country code (its feed has no reliable genre-id
  list beyond Hip-Hop/Rap, so genre stays fixed). Shazam takes both a country and a genre;
  since no official list of valid combinations exists, an unsupported pairing comes back as a
  plain "not available" error rather than wrong data. Deezer takes any genre it lists (pop,
  k-pop, rock, and so on, same by-name lookup as the existing Deezer tab) but no country --
  Deezer's API has no per-country charts, only an undocumented, per-country playlist id that
  could change without notice, so that wasn't built on.
- **v21** Ask Gemini, a new page on the left: a chat backed by your own free Gemini API
  key (aistudio.google.com/apikey -- not the gemini.google.com consumer chat, which has
  no API). Type an artist, a mood or a question; any songs or other artists Gemini names
  are checked against your library/history and shown as a "Get it" list and clickable
  artist chips, same as Charts. Picks go through Bandcamp → Soulseek → Galaxy and land on
  the wishlist if nothing has them. The conversation is saved and persists across restarts
  until you start a new one. It has no live internet access by default, so it says so
  instead of guessing on "what's current" questions -- tick "Search the web" on a message
  to actually look it up first (a free Tavily key, tavily.com, no card, 1,000 searches/mo;
  only spent when you tick the box).
- **v20** Charts, a new page on the left: Chinese Rap (NetEase's own "中文说唱榜") and five
  mainstream hip-hop/rap charts (HotNewHipHop Top 100, Deezer, Apple Music, Shazam US,
  Billboard Hot Rap Songs), each song checked against your library and download history
  first. NetEase songs carry a real track id and queue straight through Galaxy; the rest
  try Bandcamp → Soulseek → Galaxy one at a time and land on the wishlist if nothing has
  them. There's no "underground hip-hop" chart because nothing online actually publishes
  one as a fetchable list -- the page says so instead of faking it.
- **v19** Downloads → Clean up: the Mp3tag fixes before an upload. A combined artist field is
  split on your own separators (feat., &, comma, / and more, editable) into separate artists
  joined by "; ", with the first one set as album artist. For hip-hop and rap, the genre can be
  collapsed into whichever of your own iBroadcast genres already covers "underground" and
  "chinese" hip-hop -- never a genre you don't already have -- deciding Chinese artists from
  MusicBrainz's artist record. Works on a folder of files before upload (tags rewritten in
  place) or on tracks already in your library (genre only there). An artist MusicBrainz can't
  place with confidence is grouped so you can pick a genre once and apply it to everything by
  that artist, the same "select all, set genre" as Mp3tag.
- **v18** Downloads → Missing → Complete my albums: a scan of your library finds albums that are only
  partly there (gaps in track numbers, or fewer songs than Deezer lists), shows which songs are
  missing, and gets them in bulk (Bandcamp → Galaxy; not found → wishlist).
- **v17** Downloads → Missing → Wishlist: what Bandcamp, Soulseek and Galaxy didn't have (also a
  failed Soulseek job whose Galaxy fallback found nothing) is tried again every 3 days while
  Tagcast is open; found items go to the queue, items you meanwhile have are removed. Add wishes by hand too.
- **v16** Downloads → Missing → New releases: for the artists you follow (and, if on, every album
  artist with 3+ albums in your library) Tagcast checks Deezer and Apple Music every 7 days while it
  is open, or on Check now. New releases you don't fully have are listed for Get the missing; a
  message (and Windows notification) says when there are new ones.
- **v15** Discography tab (also "Discography & top songs" on every album artist page): every album,
  EP, single and compilation from Deezer and Apple Music (MusicBrainz on request, cached 7 days),
  each Have / Partly / Downloaded before / Missing; the top 30 songs with 30-second previews.
  "Get the missing" tries Bandcamp, then Soulseek, then Galaxy (a failed Soulseek job falls back
  to Galaxy); anything you may have still waits in Needs your decision.
- **v14** Soulseek tab: "Artist - Album" or "Artist - Title" per line; Tagcast starts Sockseek
  (github.com/fiso64/sockseek) in the background and follows its jobs in the shared queue. Set the
  Sockseek program, your Soulseek login and how much you share under Downloads → Settings.
- **v13** Bandcamp tab: paste an artist or album link; every release shows Have / Partly /
  Downloaded before / Missing. Download the whole discography, an album or single songs (free
  128 kbps streams, never purchases), tagged with cover. One queue and one Pause for all sources.
- **v12** Galaxy part 2: every download is looked up on MusicBrainz for year, genre, track
  number/total (unless it came from a full album) and MusicBrainz IDs; album cards and the album
  page show the year. Downloads → Settings has the pause between songs, the pause between API
  calls and the API quota. (Lyrics and history extras were left out on request.)
- **v11.2** Galaxy shows album covers, as in the Galaxy app: on every song row, album card, the
  album page and the queue. Tagcast fetches them (NetEase without using the API limit; other
  sources with a low-priority call) and keeps them in `galaxy-covers` in the settings folder.
- **v11.1** The Windows launcher repairs a `.venv` that can no longer load Tagcast (moved folder or
  interrupted update): it reinstalls, and rebuilds `.venv` once if needed.
- **v11** Galaxy (your GD downloader) inside Downloads: Artist / Album / Track search on netease,
  joox, bilibili; artists who share a name get separate cards (MusicBrainz, Deezer, your
  library) with "Which artist?" remembered; exact albums only; one queue with pause, cancel,
  retry, move to top, clear, progress, speed and time left; 45 API calls per 5 minutes with
  backoff; 320 → 128 kbps → other source; preview rejection; tags + cover (needs `mutagen`,
  installed automatically). Duplicates wait in Needs your decision.
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
