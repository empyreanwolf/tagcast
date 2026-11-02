"""YouTube (fork v59): songs and albums from YouTube, through yt-dlp.

Tagcast does not talk to YouTube itself. It uses yt-dlp (github.com/yt-dlp/yt-dlp, Unlicense), the
open-source downloader, as a Python library: a search finds the best official audio, then the AAC (m4a)
audio stream is saved without re-encoding. Like every source, a song you may already have waits in
Needs your decision, and the finished file is tagged, moved to ``Album Artist\\Album\\## - Title.m4a``,
added to the history and tidied (artist "A; B", genre) like all other downloads.

It is the first source tried (YouTube, then Galaxy, Bandcamp, Soulseek). The files are lossy
(about 128 kbps AAC), which is noted on every item. Switch it off under Downloads > Settings.
Downloading copyrighted music from YouTube can break YouTube's terms and the law where you live.
"""

import logging
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from . import artist_match as match
from .galaxy import JobCancelled, JobFailed, StopRequested, fold, plain_title, sim
from .library import LibraryError
from .source_worker import FileWorker

log = logging.getLogger("tagcast")
STATE_FILE = "youtube.json"
SEARCH_RESULTS = 8
MIN_SECONDS, MAX_SECONDS = 45, 15 * 60
# A video whose title says one of these is a different recording, unless the wanted title says it too.
OTHER_VERSIONS = ("live", "remix", "cover", "karaoke", "instrumental", "sped up", "slowed", "nightcore", "reverb",
                  "8d", "reaction", "mashup", "acoustic", "loop", "1 hour", "type beat", "tutorial", "bass boosted")


class YouTubeError(LibraryError):
    pass


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain_error(error):
    """yt-dlp's message without its terminal colour codes or the "ERROR:" prefix (v65)."""
    return re.sub(r"^\s*ERROR:\s*", "", ANSI.sub("", str(error))).strip()


def ffmpeg_path():
    """ffmpeg for the MP3 conversion (v61): the one on PATH, else the copy bundled by imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def page_meta(info):
    """Tags yt-dlp read from the page (v61): YouTube Music uploads carry track, artist, album and year."""
    info = info or {}
    artists = info.get("artists") or [a.strip() for a in re.split(r",\s*", str(info.get("artist") or info.get("creator") or "")) if a.strip()]
    year = str(info.get("release_year") or "") or str(info.get("release_date") or info.get("upload_date") or "")[:4]
    genre = info.get("genre") or ((info.get("genres") or [""])[0])
    return {"title": match.clean(info.get("track") or ""), "artists": [match.clean(a) for a in artists if match.clean(a)],
            "album": match.clean(info.get("album") or ""), "year": year if year.isdigit() else "", "genre": str(genre or "")}


def pick_thumbnail(info):
    """The best cover candidate: the biggest square-ish thumbnail, else the biggest one."""
    thumbs = [t for t in (info or {}).get("thumbnails") or [] if t.get("url")]
    if not thumbs and (info or {}).get("thumbnail"):
        return info["thumbnail"]
    square = [t for t in thumbs if t.get("width") and t.get("height") and abs(t["width"] - t["height"]) <= t["width"] * 0.05]
    pool = square or thumbs
    pool.sort(key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0), t.get("preference") or 0))
    return pool[-1]["url"] if pool else ""


def square_jpeg(data):
    """Center-crop an image to a square JPEG (video thumbnails are 16:9 with the art in the middle)."""
    try:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return data
        h, w = image.shape[:2]
        side = min(h, w)
        top, left = (h - side) // 2, (w - side) // 2
        ok, out = cv2.imencode(".jpg", image[top:top + side, left:left + side], [cv2.IMWRITE_JPEG_QUALITY, 92])
        return out.tobytes() if ok else data
    except Exception:
        return data


def _words(text):
    return re.sub(r"[^\w]+", " ", fold(text)).split()


def has_word(text, phrase):
    return f" {phrase} " in f" {' '.join(_words(text))} "


def score_video(entry, artist, title, want_seconds=0):
    """How well one search result is the official audio of this song: 0 means not usable."""
    video = str(entry.get("title") or "")
    channel = str(entry.get("channel") or entry.get("uploader") or "")
    seconds = entry.get("duration") or 0
    if not video or (seconds and not MIN_SECONDS <= seconds <= MAX_SECONDS):
        return 0.0
    if want_seconds and seconds and abs(seconds - want_seconds) > max(20, want_seconds * 0.2):
        return 0.0
    for word in OTHER_VERSIONS:
        if has_word(video, word) and not has_word(title, word):
            return 0.0
    topic = fold(channel).endswith(" topic") and match.match_key(channel[: -len(" - Topic")]) == match.match_key(artist)
    named = bool(match.match_key(artist)) and (match.match_key(artist) in match.match_key(channel) or
                                                match.match_key(artist) in match.match_key(video))
    if not (topic or named):
        return 0.0
    # "Artist - Title (Official Audio)": compare the title part only.
    cleaned = re.sub(r"(?i)\(?\[?\s*(official\s*)?(audio|video|music video|lyric video|lyrics|visualizer|hd|4k)\s*\)?\]?", " ", video)
    for part in (artist, ):
        cleaned = re.sub(re.escape(part), " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("-", " ").replace("–", " ").replace("|", " ")
    wanted = plain_title(title)
    ratio = max(sim(plain_title(cleaned), wanted), sim(cleaned, title), 1.0 if has_word(video, " ".join(_words(wanted))) and wanted else 0.0)
    if ratio < 0.8:
        return 0.0
    return ratio + (1.0 if topic else 0.0) + (0.5 if match.match_key(channel) == match.match_key(artist) else 0.0)


def best_video(entries, artist, title, want_seconds=0):
    best, top = None, 0.0
    for entry in entries or []:
        s = score_video(entry, artist, title, want_seconds)
        if s > top:
            best, top = entry, s
    return best


class YtdlpWorker(FileWorker):
    """Downloads a page's best audio with yt-dlp and saves it like every other source (v59)."""

    formats = "bestaudio[ext=m4a]/bestaudio"
    lossy_note = "from YouTube, lossy"
    wait_text = "Reading the YouTube video…"
    _fetch_audio = None

    # -- downloading (the worker) ------------------------------------------------------------

    def _yt_fetch(self, url, folder, hook):
        """Download the audio into ``folder`` as MP3 (v61, when ffmpeg is there; else the m4a as it is).
        Returns (file path, page info) so the cover and tags can be read from the page."""
        import yt_dlp
        folder.mkdir(parents=True, exist_ok=True)
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20, "retries": 3,
                "fragment_retries": 3, "format": self.formats, "outtmpl": str(folder / "audio.%(ext)s"),
                "progress_hooks": [hook], "overwrites": True}
        ffmpeg = ffmpeg_path()
        if ffmpeg:
            opts.update(format="bestaudio/best", ffmpeg_location=ffmpeg,
                        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}])
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True) or {}
        except (StopRequested, JobCancelled):
            raise
        except Exception as error:
            reason = str(error)
            if isinstance(error, yt_dlp.utils.DownloadError) and error.exc_info and isinstance(error.exc_info[1], (StopRequested, JobCancelled)):
                raise error.exc_info[1]
            raise JobFailed(f"YouTube download failed ({plain_error(reason)[:160]}). Updating yt-dlp often fixes this.") from None
        files = sorted((p for p in folder.glob("audio.*") if p.suffix not in (".part", ".ytdl", ".webp", ".jpg", ".png")),
                       key=lambda p: p.suffix != ".mp3")
        if not files:
            raise JobFailed("YouTube gave no audio file")
        return files[0], info

    @staticmethod
    def _yt_cover(info):
        """Square JPEG cover from the page's thumbnail, or None."""
        url = pick_thumbnail(info)
        if not url:
            return None
        try:
            import requests
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            return square_jpeg(r.content) or None
        except Exception:
            return None

    _cover = None

    def process(self, job):
        p, jid = job["payload"], job["id"]
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

        folder = self._part_path(jid).parent / f"yt-{jid}"
        got = self._fetch_audio(p["url"], folder, hook)
        file, info = got if isinstance(got, tuple) else (got, {})
        try:
            part = self._part_path(jid)
            part.parent.mkdir(parents=True, exist_ok=True)
            Path(file).replace(part)
            nbytes = part.stat().st_size
            fmt = self.check_audio(part)
            self._step(jid, 1, f"{nbytes / 1048576:.1f} MB · {fmt.upper()} from yt-dlp")
            tags = self.tags_for(job, info)
            cover_url = ((job["payload"].get("spotify") or {}).get("cover_url") or "")
            cover = (self._cover or self._yt_cover)({"thumbnails": [{"url": cover_url, "width": 1, "height": 1}]} if cover_url else info) \
                if (info or cover_url) else None
            self.save(job, part, fmt, nbytes, cover=cover, extras=[self.lossy_note], **tags)
        finally:
            try:
                for leftover in folder.glob("*"):
                    leftover.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass


    @staticmethod
    def tags_for(job, info):
        """The tags to write (v61): what was queued, with the page's own track, artist, year and genre filled
        in. Rows marked ``page_meta`` (links, playlists, artist releases) take the page's title and artist."""
        p, meta = job["payload"], page_meta(info)
        spotify = p.get("spotify")
        if isinstance(spotify, dict):  # v66: Spotify's own tags (spotDL), not the video page's
            return {"title": job["title"], "artists": p["artists"], "album_artist": p["album_artist"], "album": p["album"],
                    "track_no": p["track_no"], "track_total": p["track_total"],
                    "year": spotify.get("year") or meta["year"], "genre": spotify.get("genre") or ""}
        from_page = bool(p.get("page_meta"))
        title = meta["title"] if from_page and meta["title"] else job["title"]
        artists = meta["artists"] if from_page and meta["artists"] else p["artists"]
        album, album_artist = p["album"], p["album_artist"]
        if from_page and p.get("single") and meta["album"]:
            album = meta["album"]
            album_artist = artists[0] if artists else album_artist
        return {"title": title, "artists": artists, "album_artist": album_artist, "album": album,
                "track_no": p["track_no"], "track_total": p["track_total"], "year": meta["year"], "genre": meta["genre"]}


class YouTube(YtdlpWorker):
    source = "youtube"
    track_delay = 2.0

    def __init__(self, studio, search=None, fetch_audio=None):
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("enabled", True)
        self._search = search or self._yt_search
        self._fetch_audio = fetch_audio or self._yt_fetch
        super().__init__(studio, None)

    # -- availability and settings --------------------------------------------------------------

    @staticmethod
    def installed():
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def version():
        try:
            from yt_dlp.version import __version__
            return __version__
        except ImportError:
            return ""

    def enabled(self):
        return bool(self.state.get("enabled")) and (self.installed() or self._search is not self._yt_search)

    def settings_view(self):
        return {"enabled": bool(self.state.get("enabled")), "installed": self.installed(), "version": self.version()}

    def save_settings(self, body):
        if not isinstance(body, dict) or "enabled" not in body:
            raise YouTubeError("Choose whether to use YouTube.")
        self.state["enabled"] = body["enabled"] is True
        self.studio._write(STATE_FILE, self.state)
        return self.settings_view()

    # -- searching ---------------------------------------------------------------------------

    @staticmethod
    def _yt_search(query, playlists=False):
        """Flat search results: a list of {id, title, channel, duration, url}."""
        try:
            import yt_dlp
        except ImportError:
            raise YouTubeError("yt-dlp is not installed. Restart Tagcast so it installs it, or run: pip install yt-dlp") from None
        # "in_playlist" (v63): a link that redirects (an OLAK5uy_ album, a YouTube Music album page) is followed,
        # while a playlist's songs stay a fast flat list.
        opts = {"quiet": True, "no_warnings": True, "no_color": True, "extract_flat": "in_playlist", "skip_download": True, "socket_timeout": 20}
        if str(query).startswith("http"):
            url = query  # a page (playlist, channel tab, YouTube Music search) listed as it is
        else:
            url = (f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAw%3D%3D" if playlists
                   else f"ytsearch{SEARCH_RESULTS}:{query}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as error:
            raise YouTubeError(f"YouTube search failed ({plain_error(error)[:160]})") from None
        return list(info.get("entries") or [])

    def find_song(self, artist, title, seconds=0):
        entries = self._search(f"{artist} {title}")
        return best_video(entries, artist, title, seconds)

    @staticmethod
    def _who(entry):
        return " ".join(str(x) for x in [entry.get("channel"), entry.get("uploader"), entry.get("artist"),
                                          *(entry.get("artists") or [])] if x)

    def _album_from(self, entry, artist, album, need_artist):
        """The songs of one album result when its name and artist fit, else None."""
        name = re.sub(r"(?i)^(album|ep|single)\s*[-·•]\s*", "", str(entry.get("title") or ""))
        if sim(name, album) < 0.9:
            return None
        key = match.match_key(artist)
        named = key and key in match.match_key(self._who(entry))
        if need_artist and not named and self._who(entry).strip():
            return None
        songs = [e for e in self._search_playlist(entry) if e.get("id") and e.get("title")]
        if not 2 <= len(songs) <= 60:
            return None
        if not named:  # no artist on the result: most songs must come from the artist (or "Artist - Topic")
            by_artist = sum(1 for e in songs if key and key in match.match_key(self._who(e)))
            if by_artist * 2 < len(songs):
                return None
        return songs

    def find_album(self, artist, album):
        """This artist's album: YouTube Music's album search first (v63, it lists "Artist - Topic" albums with
        the artist), then plain YouTube's playlist search. Anything less sure is None so the next source is tried."""
        try:
            music = self._search(f"https://music.youtube.com/search?q={quote(f'{artist} {album}')}#albums")
        except LibraryError:
            music = []
        for entry in music[:6]:
            songs = self._album_from(entry, artist, album, need_artist=False)
            if songs:
                return {"title": album, "artist": artist, "tracks": songs}
        for entry in self._try(f"{artist} {album} album", playlists=True)[:6]:
            songs = self._album_from(entry, artist, album, need_artist=True)
            if songs:
                return {"title": album, "artist": artist, "tracks": songs}
        return None

    def _search_playlist(self, entry):
        url = entry.get("url") or entry.get("webpage_url") or (f"https://www.youtube.com/playlist?list={entry['id']}" if entry.get("id") else "")
        return self._try(url) if url else []

    # -- queueing ---------------------------------------------------------------------------

    def _item(self, artist, title, album, video, number=0, total=1, album_artist=None, query=""):
        return {"kind": "song", "title": title, "artist": artist, "album": album or title, "query": query,
                "payload": {"id": video["id"], "source": "youtube", "url": f"https://www.youtube.com/watch?v={video['id']}",
                            "artists": [artist], "album_artist": album_artist or artist, "album": album or title,
                            "track_no": number or 1, "track_total": total, "duration": video.get("duration") or 0,
                            "video_title": video.get("title", "")}}

    def _submit(self, items):
        def on_disk(item):
            from .galaxy import find_existing_by_title
            p = item["payload"]
            found = find_existing_by_title(self.folder(), p["album_artist"], p["album"], item["title"])
            return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": p["album"],
                    "path": str(found)} if found else None
        result = self.manager.submit(items, "youtube", pre_check=on_disk)
        self.ensure_worker()
        return result

    def add_song(self, artist, title, album=""):
        artist, title = match.clean(artist), match.clean(title)
        if not artist or not title:
            raise YouTubeError("Each song needs an artist and a title.")
        video = self.find_song(artist, title)
        if not video:
            raise YouTubeError(f"query “{artist} {title}”: no official audio")
        return self._submit([self._item(artist, title, match.clean(album), video, query=f"{artist} {title}")])

    def add_album(self, artist, album):
        found = self.find_album(match.clean(artist), match.clean(album))
        query = f"{match.clean(artist)} {match.clean(album)}"
        if not found:
            raise YouTubeError(f"query “{query}”: no matching album")
        total = len(found["tracks"])
        items = [self._item(artist, match.clean(t["title"]), album, t, number=i + 1, total=total, query=query)
                 for i, t in enumerate(found["tracks"])]
        return self._submit(items)

    def try_item(self, item_id):
        """v65: "⬇ Try YouTube" on a failed download: look the song up on YouTube; the failed row goes once queued."""
        item = self.manager.item(str(item_id or ""))
        if not item or item.get("kind") != "song":
            raise YouTubeError("That download is no longer in the queue.")
        artist = re.split(r"\s*;\s*", item.get("artist") or "")[0].strip()
        result = self.add_song(artist, re.sub(r"(?i)\s*\((explicit|clean)\)", "", item["title"]), item.get("album", ""))
        self.manager.remove([item["id"]])
        return result

    def add_lines(self, lines):
        """Songs typed as "Artist - Title", one per line: each is looked up and queued or reported."""
        out = []
        for line in [l.strip() for l in str(lines or "").splitlines() if l.strip()][:100]:
            artist, _, title = line.partition(" - ")
            try:
                if not title.strip():
                    raise YouTubeError("Write it as Artist - Title.")
                result = self.add_song(artist, title)
                out.append({"line": line, "ok": True, "note": f"{result.get('queued', 0)} queued"
                            + (f", {result['held']} waiting in Needs your decision" if result.get("held") else "")})
            except LibraryError as error:
                out.append({"line": line, "ok": False, "note": str(error)})
        return {"results": out}

    # -- an artist's albums and singles (v61) ----------------------------------------------------

    def _try(self, query, playlists=False):
        """A search step that may fail without stopping the others (v65)."""
        try:
            return self._search(query, playlists) if playlists else self._search(query)
        except LibraryError as error:
            log.info("YouTube step skipped (%s): %s", query, error)
            return []

    def _music_releases(self, artist):
        """YouTube Music's album results for this artist (singles and EPs are listed there too). A result with
        no artist name on it is kept here and checked on its tracks (see _tracks_by)."""
        out = []
        for e in self._try(f"https://music.youtube.com/search?q={quote(artist)}#albums"):
            url = e.get("url") or e.get("webpage_url") or ""
            title = match.clean(re.sub(r"(?i)^(album|single|ep)\s*[-·•]\s*", "", str(e.get("title") or "")))
            who = " ".join(str(e.get(k) or "") for k in ("channel", "uploader", "artist", "description"))
            if url and title and (not who.strip() or match.match_key(artist) in match.match_key(who)):
                out.append({"title": title, "url": url, "check": not who.strip()})
        return out

    def _channel_releases(self, artist):
        """Plain YouTube: the artist's channel (or "Artist - Topic"), then its Releases tab, then its Playlists
        tab, then a playlist search. Each step may fail on its own (a channel without a Releases tab)."""
        key = match.match_key(artist)
        channels = self._try(f"https://www.youtube.com/results?search_query={quote(artist)}&sp=EgIQAg%3D%3D")
        picked = []
        for e in channels:
            name = str(e.get("title") or e.get("channel") or e.get("uploader") or "")
            if match.match_key(re.sub(r"(?i)\s*-\s*topic$", "", name)) == key:
                picked.append((not fold(name).endswith(" topic"), e))
        out = []
        for _, best in sorted(picked, key=lambda x: x[0])[:2]:
            base = (best.get("url") or best.get("channel_url") or "").rstrip("/")
            if not base.startswith("http"):
                continue
            for tab in ("/releases", "/playlists"):
                for e in self._try(base + tab):
                    url = e.get("url") or (f"https://www.youtube.com/playlist?list={e['id']}" if e.get("id") else "")
                    title = match.clean(re.sub(r"(?i)^(album|single|ep)\s*-\s*", "", str(e.get("title") or "")))
                    if url and title:
                        out.append({"title": title, "url": url, "check": tab == "/playlists"})
                if out:
                    break
            if out:
                break
        if not out:
            for e in self._try(f"{artist} album", playlists=True)[:15]:
                channel = str(e.get("channel") or e.get("uploader") or "")
                url = e.get("url") or (f"https://www.youtube.com/playlist?list={e['id']}" if e.get("id") else "")
                title = match.clean(re.sub(r"(?i)^album\s*-\s*", "", str(e.get("title") or "")))
                if url and title and (key in match.match_key(channel) or not channel.strip()):
                    out.append({"title": title, "url": url, "check": not channel.strip()})
        return out

    def artist_releases(self, artist, where="music"):
        """{albums, singles}: each release with its tracks; 1 to 3 tracks is a single. YouTube Music first
        (where="music"), falling back to plain YouTube when it finds nothing."""
        artist = match.clean(artist)
        if not artist:
            raise YouTubeError("Type an artist name.")
        used, fell_back = ("youtube" if where == "youtube" else "music"), False
        found = self._music_releases(artist) if used == "music" else self._channel_releases(artist)
        if not found and used == "music":
            used, fell_back, found = "youtube", True, self._channel_releases(artist)
        if not found:
            raise YouTubeError(f"No albums or singles found on YouTube for {artist}.")
        seen, unique = set(), []
        for r in found:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)

        key = match.match_key(artist)

        def tracks(release):
            entries = [e for e in self._try(release["url"]) if e.get("id") and e.get("title")]
            if release.get("check") and entries:  # no artist on the result: most songs must be by the artist
                if sum(1 for e in entries if key in match.match_key(self._who(e))) * 2 < len(entries):
                    entries = []
            rows = [{"id": e["id"], "title": match.clean(e["title"]), "duration": e.get("duration") or 0} for e in entries]
            return {**{k: v for k, v in release.items() if k != "check"}, "tracks": rows[:100]}

        with ThreadPoolExecutor(6) as pool:
            releases = [r for r in pool.map(tracks, unique[:40]) if r["tracks"]]
        try:
            checked = self.manager.check([{"kind": "album", "title": r["title"], "artist": artist, "tracks": len(r["tracks"])}
                                          for r in releases])["results"] if releases else []
        except LibraryError:
            checked = [{"status": "new"}] * len(releases)
        for r, c in zip(releases, checked):
            r["status"] = c.get("status", "new")
            r["kind"] = "single" if len(r["tracks"]) <= 3 else "album"
        return {"artist": artist, "where": used, "fell_back": fell_back,
                "albums": [r for r in releases if r["kind"] == "album"],
                "singles": [r for r in releases if r["kind"] == "single"]}

    def add_releases(self, artist, releases):
        """Queue whole releases from artist_releases: album = release title, tracks in order."""
        artist = match.clean(artist)
        if not artist or not isinstance(releases, list) or not releases:
            raise YouTubeError("Choose at least one album or single.")
        items = []
        for rel in releases[:60]:
            title = match.clean((rel or {}).get("title"))
            songs = [t for t in rel.get("tracks") or [] if re.fullmatch(r"[\w-]{6,20}", str(t.get("id") or "")) and t.get("title")]
            if not title or not songs:
                continue
            for i, t in enumerate(songs):
                item = self._item(artist, match.clean(t["title"]), title, t, number=i + 1, total=len(songs),
                                  query=f"artist search: {artist}")
                item["payload"]["page_meta"] = True
                items.append(item)
        if not items:
            raise YouTubeError("Nothing to download in that choice.")
        return self._submit(items[:1000])
