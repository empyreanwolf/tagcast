"""More sources (fork v60): SoundCloud search and chart, plus SoundCloud and Audiomack links, through yt-dlp.

- **Search**: one search bar for the sources that can be searched. Today that is SoundCloud
  (yt-dlp's ``scsearch``). Audiomack and the mixtape sites have no public search Tagcast can rely on, so
  paste their links instead (Audiomack song and album links work through yt-dlp).
- **Chart**: SoundCloud's own Top chart for Hip-hop & Rap, from the same undocumented web API that
  soundcloud.com uses (a public client id is read through yt-dlp). It appears on the Charts page.
- **Links (v61)**: YouTube links too. A YouTube playlist downloads every video, tagged as one album
  (album = playlist name, tracks in playlist order).
- **Split a full album video (v105)**: a single video that is a whole album in one file - its
  YouTube chapter markers, or a tracklist parsed out of the description (the same line formats
  github.com/crisbal/album-splitter's ``tracks.txt`` takes), are offered as "Split into N tracks".
  The video is downloaded once; ffmpeg (already bundled for MP3 conversion) cuts it at each
  track's start time (stream copy, no re-encode) and each piece is tagged and saved like any
  other song. No tracklist found -> no split option, just the usual single song.
- Songs are saved like every other download (tagged, tidied, history), as MP3 with the page's cover art
  and tags when ffmpeg is available (v61). Files are lossy; the note says so.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from . import artist_match as match
from .galaxy import AUDIO_EXTS, JobFailed, StopRequested, build_path, fold, plain_title, sim, write_tags
from .library import LibraryError
from .youtube import AgeRestricted, DownloadBlocked, FetchTimeout, YtdlpWorker, ffmpeg_path, plain_error

log = logging.getLogger("tagcast")
# fork v105: a track line in a "full album" video's description - "[hh:]mm:ss - Title",
# "Title - [hh:]mm:ss" or "Title [hh:]mm:ss", the same formats album-splitter's tracks.txt takes.
_TRACK_TS = r"(\d{1,2}(?::\d{2}){1,2})"
_TS_FIRST = re.compile(rf"^{_TRACK_TS}\s*[-–—:.]?\s*(.+)$")
_TS_LAST = re.compile(rf"^(.+?)\s*[-–—:]\s*{_TRACK_TS}$")
_TS_TRAIL = re.compile(rf"^(.+?)\s+{_TRACK_TS}$")
SEARCH_RESULTS = 20
MIN_SECONDS, MAX_SECONDS = 20, 1800  # fork v96: a SoundCloud "song" under 20s or over 30min is not really one
SITES = {"soundcloud.com": "SoundCloud", "audiomack.com": "Audiomack", "youtube.com": "YouTube", "youtu.be": "YouTube",
         "spotify.com": "Spotify"}
# Decorations in YouTube video titles that are not part of the song title (v61).
DECOR = re.compile(r"(?i)\s*[\(\[]\s*(official\s*)?(music\s*video|video|audio|lyric\s*video|lyrics?|visuali[sz]er|hd|4k|mv|m/v)\s*[\)\]]")
CHART_URL = "https://api-v2.soundcloud.com/charts"


class MoreError(LibraryError):
    pass


def site_of(url):
    host = (urlparse(url).hostname or "").lower()
    for domain, name in SITES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return ""


def split_title(title, uploader):
    """("Artist", "Title"): "Artist - Title" uploads are split, otherwise the uploader is the artist."""
    title = match.clean(DECOR.sub("", str(title or "")))
    uploader = re.sub(r"(?i)\s*-\s*topic$", "", str(uploader or ""))
    title = re.sub(r"\s+[–—]\s+", " - ", title)  # v63: en and em dashes split like "-"
    if " - " in title:
        artist, _, song = title.partition(" - ")
        if artist.strip() and song.strip():
            return artist.strip(), song.strip()
    return match.clean(uploader), title


def _entry_url(entry, url, site):
    """A playlist entry's own page: YouTube's flat entries give a bare video id."""
    own = entry.get("webpage_url") or entry.get("url") or ""
    if site == "YouTube" and own and not own.startswith("http"):
        own = f"https://www.youtube.com/watch?v={own}"
    if site == "YouTube" and not own and entry.get("id"):
        own = f"https://www.youtube.com/watch?v={entry['id']}"
    return own or url


def youtube_parts(url):
    """(video id, list id) of a YouTube link (v63)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    video = (query.get("v") or [""])[0]
    if (parsed.hostname or "").lower().endswith("youtu.be"):
        video = parsed.path.strip("/").split("/")[0]
    return video, (query.get("list") or [""])[0]


def list_kind(list_id):
    """"album" (YouTube Music's own album playlists), "mix" (the endless radio) or "playlist"."""
    return "album" if list_id.startswith("OLAK5uy_") else "mix" if list_id.startswith("RD") else "playlist"


def _parse_timestamp(text):
    """"mm:ss" or "hh:mm:ss" -> seconds, or None if it isn't one."""
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return h * 3600 + m * 60 + s


def _strip_dash(text):
    return re.sub(r"^[\s\-–—:.]+|[\s\-–—:.]+$", "", text)


def chapters_from_description(description):
    """fork v105: when a "full album in one video" upload has no YouTube chapter markers, its
    description often lists the tracklist as plain text instead - try to read that. Returns
    [] when nothing in it looks like a timestamped tracklist."""
    found = []
    for raw in str(description or "").splitlines():
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        m = _TS_FIRST.match(line)
        if m:
            ts, title = m.group(1), m.group(2)
        else:
            m = _TS_LAST.match(line) or _TS_TRAIL.match(line)
            if not m:
                continue
            title, ts = m.group(1), m.group(2)
        seconds = _parse_timestamp(ts)
        title = match.clean(_strip_dash(title))
        if seconds is None or not title:
            continue
        found.append((seconds, title))
    found.sort(key=lambda t: t[0])
    seen, rows = set(), []
    for seconds, title in found:
        if seconds in seen:  # fork v105: the same mm:ss line matched twice (e.g. a "Tracklist:" header)
            continue
        seen.add(seconds)
        rows.append({"title": title, "start_time": float(seconds)})
    for i, row in enumerate(rows):
        row["end_time"] = rows[i + 1]["start_time"] if i + 1 < len(rows) else None
    return rows


def _entry_row(entry, site):
    url = entry.get("webpage_url") or entry.get("url") or ""
    artist, title = split_title(entry.get("title"), entry.get("uploader") or entry.get("channel") or entry.get("playlist_uploader"))
    if not url or not title:
        return None
    return {"url": url, "title": title, "artist": artist, "site": site, "duration": entry.get("duration") or 0}


class MoreSources(YtdlpWorker):
    source = "more"
    track_delay = 2.0
    formats = "bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio"
    lossy_note = "lossy, from YouTube/SoundCloud/Audiomack"
    wait_text = "Reading the page…"

    def __init__(self, studio, search=None, extract=None, chart_fetch=None, fetch_audio=None, info=None):
        self._search = search or self._yt_search
        self._extract = extract or self._yt_extract
        self._chart = chart_fetch or self._soundcloud_chart
        self._fetch_audio = fetch_audio or self._yt_fetch
        self._info = info or self._yt_full_info  # fork v105: a single video's full info (chapters, description)
        self.on_failed = None  # fork v96: set by Discography: a Spotify song yt-dlp cannot serve tries other sources
        super().__init__(studio, None)

    def process(self, job):
        if (job.get("payload") or {}).get("chapters"):  # fork v105: a full-album video to split
            self._process_split(job)
            return
        try:
            super().process(job)
        except (AgeRestricted, DownloadBlocked, FetchTimeout) as error:
            if (job.get("payload") or {}).get("spotify"):
                self._failover(job, error)
            else:
                raise

    # -- splitting a full-album video (v105) -------------------------------------------------

    def _yt_full_info(self, url):
        """The video's full info (not the flat form ``_extract`` uses for playlists) - has
        ``chapters`` and ``description``, which a flat/playlist extraction does not carry."""
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 20}
        opts.update(self._cookie_opts())
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False) or {}
        except Exception as error:
            raise MoreError(f"Couldn't read this video ({plain_error(str(error))[:120]})") from None

    def _chapters(self, url):
        """YouTube's own chapters for this video, else a tracklist parsed from its description.
        Returns (chapters, guessed artist, guessed album) - chapters is [] when neither is there."""
        info = self._info(url)
        raw = info.get("chapters") or []
        if len(raw) >= 2:
            chapters = [{"title": match.clean(c.get("title") or f"Track {i + 1}"),
                        "start_time": c.get("start_time") or 0, "end_time": c.get("end_time")}
                       for i, c in enumerate(raw)]
        else:
            chapters = chapters_from_description(info.get("description"))
        artist, album = split_title(info.get("title"), info.get("uploader") or info.get("channel"))
        return chapters, artist, album

    def add_split(self, url, chapters, album_artist, album, link=""):
        """fork v105: queue "this video is a full album" as one job. Submitted as kind "album" so
        the usual album-ownership check applies first (an album you already have in full is held
        for your decision, same as any other album download, instead of being split again)."""
        if not isinstance(chapters, list) or len(chapters) < 2:
            raise MoreError("No tracklist found to split this video by.")
        if len(chapters) > 100:
            raise MoreError("That looks like more than 100 tracks - too many to split at once.")
        album_artist, album = match.clean(album_artist) or "Unknown Artist", match.clean(album) or "Album"
        cleaned = []
        for c in chapters[:100]:
            title = match.clean((c or {}).get("title"))
            start = (c or {}).get("start_time")
            if not title or not isinstance(start, (int, float)):
                raise MoreError("Each track needs a title and a start time.")
            cleaned.append({"title": title, "start_time": float(start), "end_time": (c or {}).get("end_time")})
        payload = {"id": url, "source": "more", "site": "YouTube", "url": url, "chapters": cleaned,
                   "album_artist": album_artist, "album": album}
        item = {"kind": "album", "title": album, "artist": album_artist, "album": album,
                "tracks": len(cleaned), "link": link, "payload": payload}
        result = self.manager.submit([item], "more")
        self.ensure_worker()
        return result

    def _ffmpeg_cut(self, src, dst, start, end):
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise JobFailed("ffmpeg is needed to split a full-album video into tracks.")
        cmd = [ffmpeg, "-y", "-i", str(src), "-ss", str(max(0.0, start))]
        if end:
            cmd += ["-to", str(end)]
        cmd += ["-c", "copy", "-map_metadata", "-1", str(dst)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not Path(dst).exists():
            raise JobFailed(f"ffmpeg could not cut this track ({result.stderr.strip()[-200:]})")

    def _process_split(self, job):
        """Download the full video once, then cut it into one file per chapter with ffmpeg
        (stream copy, no re-encode) and tag/save/record each like any other finished download."""
        p, jid = job["payload"], job["id"]
        chapters = p.get("chapters") or []
        if len(chapters) < 2:
            raise JobFailed("No tracklist to split this video by.")
        self.manager.update_item(jid, started=time.time(), speed=0, eta=0)
        self._step(jid, 0, self.wait_text)
        self._t0 = time.monotonic()

        def hook(d):
            if self.stopping:
                raise StopRequested()
            self._check_cancel()
            if d.get("status") == "downloading":
                done = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                self._progress(jid, done, total)

        folder = self._part_path(jid).parent / f"split-{jid}"
        got = self._fetch_watched(p["url"], folder, hook)
        file, info = got if isinstance(got, tuple) else (got, {})
        whole = self._part_path(jid)
        whole.parent.mkdir(parents=True, exist_ok=True)
        Path(file).replace(whole)
        try:
            fmt = self.check_audio(whole)
            cover = (self._cover or self._yt_cover)(info) if info else None
            album_artist, album = p["album_artist"], p["album"]
            saved = []
            for i, ch in enumerate(chapters):
                self._step(jid, 2, f"Splitting track {i + 1}/{len(chapters)}: {ch['title']}")
                piece = folder / f"track{i + 1:03d}.{fmt}"
                self._ffmpeg_cut(whole, piece, ch.get("start_time") or 0, ch.get("end_time"))
                write_tags(piece, fmt, title=ch["title"], artists=[album_artist], album_artist=album_artist,
                           album=album, track_no=i + 1, track_total=len(chapters), cover=cover)
                final = build_path(self.folder(), album_artist, album, i + 1, ch["title"], fmt)
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(piece, final)
                for other in AUDIO_EXTS:
                    if other != fmt:
                        try:
                            final.with_suffix("." + other).unlink(missing_ok=True)
                        except OSError:
                            pass
                self.manager.record({"title": ch["title"], "artists": [album_artist], "album": album,
                                     "source": self.source, "path": str(final), "site": "YouTube",
                                     "query": job.get("query", ""), "link": job.get("link", "")})
                saved.append(final)
            self._tidy(saved)
            self.manager.update_item(jid, persist=True, state="done", step=4,
                                     step_detail=str(saved[-1]) if saved else "",
                                     note=f"Split into {len(saved)} tracks", path=str(saved[0]) if saved else "",
                                     progress=100, finished=time.time(), format=fmt, speed=0, eta=0)
        finally:
            try:
                whole.unlink(missing_ok=True)
                for leftover in folder.glob("*"):
                    leftover.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass

    # -- yt-dlp -------------------------------------------------------------------------------

    @staticmethod
    def _opts():
        # "in_playlist" (v63): redirects (an OLAK5uy_ album link) are followed, a playlist's songs stay a flat list.
        return {"quiet": True, "no_warnings": True, "no_color": True, "extract_flat": "in_playlist", "skip_download": True, "socket_timeout": 20}

    @staticmethod
    def _yt_search(query):
        try:
            import yt_dlp
        except ImportError:
            raise MoreError("yt-dlp is not installed. Restart Tagcast so it installs it, or run: pip install yt-dlp") from None
        try:
            with yt_dlp.YoutubeDL(MoreSources._opts()) as ydl:
                info = ydl.extract_info(f"scsearch{SEARCH_RESULTS}:{query}", download=False) or {}
        except Exception as error:
            raise MoreError(f"SoundCloud search failed ({plain_error(error)[:160]})") from None
        return list(info.get("entries") or [])

    @staticmethod
    def _yt_extract(url):
        """The songs behind one link: a single track, or every track of an album or set."""
        try:
            import yt_dlp
        except ImportError:
            raise MoreError("yt-dlp is not installed. Restart Tagcast so it installs it, or run: pip install yt-dlp") from None
        try:
            with yt_dlp.YoutubeDL(MoreSources._opts()) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as error:
            raise MoreError(f"Could not read {url} ({plain_error(error)[:160]})") from None
        entries = info.get("entries")
        if entries is None:
            return [info]
        owner = info.get("uploader") or info.get("channel") or ""
        return [{**e, "playlist_title": e.get("playlist_title") or info.get("title") or "", "playlist_uploader": owner}
                for e in entries if e]

    # -- searching ---------------------------------------------------------------------------

    def search(self, query):
        query = match.clean(query)
        if not query:
            raise MoreError("Type what to search for.")
        rows = [r for r in (_entry_row(e, "SoundCloud") for e in self._search(query)) if r]
        return {"results": self._annotate(rows)}

    def _annotate(self, rows):
        if not rows:
            return rows
        try:
            checked = self.manager.check([{"kind": "song", "title": r["title"], "artist": r["artist"]} for r in rows])["results"]
        except LibraryError:
            checked = [{"status": "new", "you_have": []}] * len(rows)
        return [{**r, "status": c["status"], "you_have": c["you_have"]} for r, c in zip(rows, checked)]

    # -- "Get the missing": SoundCloud as a fallback source (fork v96) ----------------------

    def find_song(self, artist, title, seconds=0):
        """A SoundCloud search, taken only when the result's own account is this artist (no reposts, no
        fan uploads); the search drops "(feat. ...)" so a long guest list doesn't bury the main artist."""
        artist = match.main_artist(artist) or artist
        query_title = match.strip_feat_group(title)
        try:
            results = self._search(f"{artist} {query_title}")
        except MoreError as error:
            log.info("SoundCloud song search failed for %s: %s", title, error)
            return None
        wanted = plain_title(query_title)
        for entry in results[:SEARCH_RESULTS]:
            name = str(entry.get("uploader") or entry.get("channel") or "")
            if match.match_key(name) != match.match_key(artist):
                continue  # not the artist's own account
            found_title = str(entry.get("title") or "")
            if sim(plain_title(found_title), wanted) < 0.82 and sim(found_title, title) < 0.82:
                continue
            duration = entry.get("duration") or 0
            if duration and not MIN_SECONDS <= duration <= MAX_SECONDS:
                continue
            if seconds and duration and abs(duration - seconds) > max(20, seconds * 0.2):
                continue
            row = _entry_row(entry, "SoundCloud")
            if row:
                return row
        return None

    def _account_base(self, artist):
        """The artist's own SoundCloud profile page, from a search result whose account is exactly them."""
        try:
            results = self._search(artist)
        except MoreError:
            return ""
        for entry in results[:SEARCH_RESULTS]:
            name = str(entry.get("uploader") or entry.get("channel") or "")
            if match.match_key(name) != match.match_key(artist):
                continue
            base = str(entry.get("uploader_url") or entry.get("channel_url") or "")
            if base:
                return base.rstrip("/")
        return ""

    def find_album(self, artist, album, expected=0):
        """The artist's own SoundCloud set (playlist) matching this album; its track count is checked
        against ``expected`` (the album's own track count) when known, so a wrong set isn't taken whole."""
        artist = match.main_artist(artist) or artist
        base = self._account_base(artist)
        if not base:
            return None
        try:
            sets = [e for e in self._extract(f"{base}/sets") if e]
        except MoreError as error:
            log.info("SoundCloud sets lookup failed for %s: %s", artist, error)
            return None
        wanted = plain_title(album)
        target_url = ""
        for entry in sets:
            title = str(entry.get("title") or entry.get("playlist_title") or "")
            if sim(plain_title(title), wanted) >= 0.82:
                target_url = str(entry.get("webpage_url") or entry.get("url") or "")
                break
        if not target_url:
            return None
        try:
            tracks = [e for e in self._extract(target_url) if e]
        except MoreError as error:
            log.info("SoundCloud set read failed for %s: %s", album, error)
            return None
        if not tracks or (expected and not (expected - 1 <= len(tracks) <= expected + 1)):
            return None
        rows = [r for r in (_entry_row({**t, "url": _entry_url(t, target_url, "SoundCloud")}, "SoundCloud") for t in tracks) if r]
        return rows or None

    # -- queueing ---------------------------------------------------------------------------

    def add(self, rows, link="", query="", album=False):
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise MoreError("Choose between 1 and 200 songs.")
        items = []
        for r in rows:
            url = str((r or {}).get("url") or "")
            site = site_of(url)
            title, artist = match.clean(r.get("title")), match.clean(r.get("artist"))
            if not site or not title:
                raise MoreError("Only YouTube, Spotify, SoundCloud and Audiomack links can be downloaded here.")
            spotify = r.get("spotify") if isinstance(r.get("spotify"), dict) else None
            payload = {"id": url, "source": "more", "site": "Spotify" if spotify else site, "url": url,
                       "artists": (spotify or {}).get("artists") or [artist or "Unknown Artist"],
                       "album_artist": match.clean(r.get("album_artist")) or artist or "Unknown Artist",
                       "album": match.clean(r.get("album")) or title, "page_meta": not spotify,
                       "single": not match.clean(r.get("album")),
                       "track_no": int(r.get("track_no") or 1), "track_total": int(r.get("track_total") or 1),
                       "duration": r.get("duration") or 0}
            if spotify:  # v66: Spotify's own tags and cover, from spotDL
                payload["spotify"] = {k: spotify.get(k, "") for k in ("year", "genre", "disc", "cover_url", "isrc", "spotify_url")}
            items.append({"kind": "song", "title": title, "artist": artist, "album": match.clean(r.get("album")) or title,
                          "link": str(r.get("link") or link), "query": str(r.get("query") or query), "payload": payload})

        def on_disk(item):
            from .galaxy import find_existing_by_title
            p = item["payload"]
            found = find_existing_by_title(self.folder(), p["album_artist"], p["album"], item["title"])
            return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": p["album"],
                    "path": str(found)} if found else None
        result = self.manager.submit(items, "more", pre_check=on_disk, album=album)
        self.ensure_worker()
        return result

    def add_links(self, text, mode=""):
        """YouTube, SoundCloud and Audiomack links, one per line (a song, an album, a set or a playlist).

        v63: a YouTube link with a list (``watch?v=…&list=…`` or ``playlist?list=…``) is not queued until
        you choose: ``mode="song"`` takes only that video, ``mode="list"`` the whole list. An album
        (``OLAK5uy_``) or playlist is tagged as one album (its name, tracks in order); a Mix (``RD…``,
        YouTube's endless radio) as separate singles."""
        out = []
        for url in [u for u in re.findall(r"https?://\S+", str(text or ""))][:20]:
            site = site_of(url)
            try:
                if not site:
                    raise MoreError("Only youtube.com, youtu.be, open.spotify.com, soundcloud.com and audiomack.com links work here.")
                out.append(self._add_link(url, site, mode))
            except LibraryError as error:
                out.append({"line": url, "ok": False, "note": str(error)})
        if not out:
            raise MoreError("Paste at least one YouTube, Spotify, SoundCloud or Audiomack link.")
        return {"results": out}

    def spotify_search(self, query):
        """v99: search Spotify by name instead of pasting a link - spotDL finds the closest
        match itself and we queue every song behind it, same as a pasted link."""
        query = str(query or "").strip()
        if not query:
            raise MoreError("Type something to search for.")
        rows = self.studio.spotify.songs(query)
        result = self.add(rows, query=query)
        return {"note": self._spotify_note(rows, result), **result}

    @staticmethod
    def _spotify_note(rows, result):
        album = rows[0]["album"] if len({r["album"] for r in rows}) == 1 else ""
        return (f"{result.get('queued', 0)} queued with Spotify's tags"
                + (f" ({album})" if album and len(rows) > 1 else "")
                + (f", {result['held']} waiting in Needs your decision" if result.get("held") else ""))

    def _add_link(self, url, site, mode):
        if site == "Spotify":  # v66: spotDL reads Spotify's tags and finds each song on YouTube Music
            rows = self.studio.spotify.songs(url)
            result = self.add(rows, link=url)
            return {"line": url, "ok": True, "note": self._spotify_note(rows, result)}
        kind = ""
        read = url
        if site == "YouTube":
            video, list_id = youtube_parts(url)
            if list_id and mode != "song":
                kind = list_kind(list_id)
                read = f"https://www.youtube.com/playlist?list={list_id}" if kind != "mix" or not video \
                    else f"https://www.youtube.com/watch?v={video}&list={list_id}"
            elif video:
                read = f"https://www.youtube.com/watch?v={video}"
            if list_id and not mode:  # ask first: this song, or the whole list?
                entries = [e for e in self._extract(read) if e]
                title = match.clean(entries[0].get("playlist_title") or "") if entries else ""
                return {"line": url, "ok": False, "choose": True, "kind": kind, "has_song": bool(video),
                        "count": len(entries), "title": re.sub(r"(?i)^album\s*-\s*", "", title),
                        "note": f"{len(entries)} songs in this {kind}"}
            if not list_id and video and mode == "split":  # fork v105: queue the split, re-reading the tracklist
                chapters, artist, album = self._chapters(read)
                result = self.add_split(read, chapters, artist, album, link=url)
                return {"line": url, "ok": True, "note": f"Split into {len(chapters)} tracks queued"
                        + (f", {result['held']} waiting in Needs your decision" if result.get("held") else "")}
            if not list_id and video and not mode:  # fork v105: offer a split when this looks like a full album
                chapters, artist, album = self._chapters(read)
                if len(chapters) >= 2:
                    return {"line": url, "ok": False, "choose": True, "kind": "split", "has_song": True,
                            "count": len(chapters), "title": f"{artist} - {album}".strip(" -"),
                            "note": f"{len(chapters)} tracks found - looks like a full album in one video"}
        entries = [e for e in self._extract(read) if e]
        rows = [r for r in (_entry_row({**e, "url": _entry_url(e, read, site)}, site) for e in entries) if r]
        if site == "YouTube" and len(rows) == 1 and (not rows[0]["artist"] or "list=" in rows[0]["url"]):
            raise MoreError("Couldn't read this link (no song or artist found). Try the plain song or playlist link.")
        cut = len(rows) > 200
        rows = rows[:200]
        total = len(rows)
        album = re.sub(r"(?i)^album\s*-\s*", "", match.clean(entries[0].get("playlist_title") or entries[0].get("playlist") or "")) if entries else ""
        artists = {match.match_key(r["artist"]) for r in rows}
        album_artist = rows[0]["artist"] if len(artists) == 1 and rows else "Various Artists"
        for i, r in enumerate(rows):
            if kind == "mix":
                continue  # a Mix is not an album: each song is its own single
            r.update(track_no=i + 1, track_total=total)
            if total > 1:
                r["album"] = album or ""
                r["album_artist"] = album_artist
        if not rows:
            raise MoreError("No songs found at this link.")
        result = self.add(rows, link=url)
        return {"line": url, "ok": True, "note": f"{result.get('queued', 0)} queued"
                + (f" ({album})" if total > 1 and album and kind != "mix" else "")
                + (f", {result['held']} waiting in Needs your decision" if result.get("held") else "")
                + (" · only the first 200 songs" if cut else "")}

    # -- the SoundCloud chart ---------------------------------------------------------------

    @staticmethod
    def _soundcloud_chart(genre="hiphoprap", region="US", kind="top", limit=50):
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                ie = ydl.get_info_extractor("Soundcloud")
                ie._update_client_id()
                client_id = ie._CLIENT_ID
        except Exception as error:
            raise MoreError(f"Could not get SoundCloud access through yt-dlp ({str(error)[:120]})") from None
        try:
            r = requests.get(CHART_URL, timeout=20, params={
                "kind": kind, "genre": f"soundcloud:genres:{genre}", "region": f"soundcloud:regions:{region}",
                "high_tier_only": "false", "limit": limit, "client_id": client_id})
            r.raise_for_status()
            return r.json().get("collection") or []
        except (requests.RequestException, ValueError) as error:
            raise MoreError(f"SoundCloud's chart did not answer ({str(error)[:120]})") from None

    def soundcloud_rows(self):
        """Chart rows for the Charts page: rank, title, artist, with the link to download from."""
        rows = []
        for item in self._chart():
            track = item.get("track") or item
            url, title = track.get("permalink_url"), track.get("title")
            if not url or not title:
                continue
            artist, song = split_title(title, (track.get("user") or {}).get("username"))
            rows.append({"rank": len(rows) + 1, "title": song, "artist": artist, "album": "", "source": "soundcloud",
                         "source_id": url, "pic_id": ""})
        if not rows:
            raise MoreError("SoundCloud's chart came back empty.")
        return rows
