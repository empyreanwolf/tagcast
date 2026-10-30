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
import time
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


class YouTube(FileWorker):
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
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "socket_timeout": 20}
        url = (f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAw%3D%3D" if playlists
               else f"ytsearch{SEARCH_RESULTS}:{query}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as error:
            raise YouTubeError(f"YouTube search failed ({str(error)[:160]})") from None
        return list(info.get("entries") or [])

    def find_song(self, artist, title, seconds=0):
        entries = self._search(f"{artist} {title}")
        return best_video(entries, artist, title, seconds)

    def find_album(self, artist, album):
        """An auto-generated "Album" playlist of this artist: title equals the album, uploader is the artist
        (or "Artist - Topic"), 2 to 60 songs. Anything less sure is None so the next source is tried."""
        for entry in self._search(f"{artist} {album} album", playlists=True)[:6]:
            channel = str(entry.get("channel") or entry.get("uploader") or "")
            name = re.sub(r"(?i)^album\s*-\s*", "", str(entry.get("title") or ""))
            if sim(name, album) < 0.9:
                continue
            if match.match_key(artist) not in match.match_key(channel):
                continue
            songs = [e for e in self._search_playlist(entry) if e.get("id") and e.get("title")]
            if 2 <= len(songs) <= 60:
                return {"title": album, "artist": artist, "tracks": songs}
        return None

    def _search_playlist(self, entry):
        url = entry.get("url") or (f"https://www.youtube.com/playlist?list={entry['id']}" if entry.get("id") else "")
        return self._search(url) if url else []

    # -- queueing ---------------------------------------------------------------------------

    def _item(self, artist, title, album, video, number=0, total=1, album_artist=None):
        return {"kind": "song", "title": title, "artist": artist, "album": album or title,
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
            raise YouTubeError(f"No official audio found on YouTube for {artist} - {title}.")
        return self._submit([self._item(artist, title, match.clean(album), video)])

    def add_album(self, artist, album):
        found = self.find_album(match.clean(artist), match.clean(album))
        if not found:
            raise YouTubeError(f"No album playlist found on YouTube for {artist} - {album}.")
        total = len(found["tracks"])
        items = [self._item(artist, match.clean(t["title"]), album, t, number=i + 1, total=total)
                 for i, t in enumerate(found["tracks"])]
        return self._submit(items)

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

    # -- downloading (the worker) ------------------------------------------------------------

    def _yt_fetch(self, url, folder, hook):
        """Download the best m4a audio into ``folder``; returns the file path."""
        import yt_dlp
        folder.mkdir(parents=True, exist_ok=True)
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20, "retries": 3,
                "fragment_retries": 3, "format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": str(folder / "audio.%(ext)s"),
                "progress_hooks": [hook], "overwrites": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except (StopRequested, JobCancelled):
            raise
        except Exception as error:
            reason = str(error)
            if isinstance(error, yt_dlp.utils.DownloadError) and error.exc_info and isinstance(error.exc_info[1], (StopRequested, JobCancelled)):
                raise error.exc_info[1]
            raise JobFailed(f"YouTube download failed ({reason[:160]}). Updating yt-dlp often fixes this.") from None
        files = sorted(p for p in folder.glob("audio.*") if p.suffix not in (".part", ".ytdl"))
        if not files:
            raise JobFailed("YouTube gave no audio file")
        return files[0]

    def process(self, job):
        p, jid = job["payload"], job["id"]
        self.manager.update_item(jid, started=time.time(), speed=0, eta=0)
        self._step(jid, 0, "Reading the YouTube video…")
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
        file = self._fetch_audio(p["url"], folder, hook)
        try:
            part = self._part_path(jid)
            part.parent.mkdir(parents=True, exist_ok=True)
            Path(file).replace(part)
            nbytes = part.stat().st_size
            fmt = self.check_audio(part)
            self._step(jid, 1, f"{nbytes / 1048576:.1f} MB · {fmt.upper()} from YouTube")
            self.save(job, part, fmt, nbytes, title=job["title"], artists=p["artists"], album_artist=p["album_artist"],
                      album=p["album"], track_no=p["track_no"], track_total=p["track_total"], year="", cover=None,
                      extras=["from YouTube, lossy"])
        finally:
            try:
                for leftover in folder.glob("*"):
                    leftover.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass
