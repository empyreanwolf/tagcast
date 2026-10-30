"""More sources (fork v60): SoundCloud search and chart, plus SoundCloud and Audiomack links, through yt-dlp.

- **Search**: one search bar for the sources that can be searched. Today that is SoundCloud
  (yt-dlp's ``scsearch``). Audiomack and the mixtape sites have no public search Tagcast can rely on, so
  paste their links instead (Audiomack song and album links work through yt-dlp).
- **Chart**: SoundCloud's own Top chart for Hip-hop & Rap, from the same undocumented web API that
  soundcloud.com uses (a public client id is read through yt-dlp). It appears on the Charts page.
- **Links (v61)**: YouTube links too. A YouTube playlist downloads every video, tagged as one album
  (album = playlist name, tracks in playlist order).
- Songs are saved like every other download (tagged, tidied, history), as MP3 with the page's cover art
  and tags when ffmpeg is available (v61). Files are lossy; the note says so.
"""

import logging
import re
from urllib.parse import urlparse

import requests

from . import artist_match as match
from .library import LibraryError
from .youtube import YtdlpWorker

log = logging.getLogger("tagcast")
SEARCH_RESULTS = 20
SITES = {"soundcloud.com": "SoundCloud", "audiomack.com": "Audiomack", "youtube.com": "YouTube", "youtu.be": "YouTube"}
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


def _entry_row(entry, site):
    url = entry.get("webpage_url") or entry.get("url") or ""
    artist, title = split_title(entry.get("title"), entry.get("uploader") or entry.get("channel"))
    if not url or not title:
        return None
    return {"url": url, "title": title, "artist": artist, "site": site, "duration": entry.get("duration") or 0}


class MoreSources(YtdlpWorker):
    source = "more"
    track_delay = 2.0
    formats = "bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio"
    lossy_note = "lossy, from YouTube/SoundCloud/Audiomack"
    wait_text = "Reading the page…"

    def __init__(self, studio, search=None, extract=None, chart_fetch=None, fetch_audio=None):
        self._search = search or self._yt_search
        self._extract = extract or self._yt_extract
        self._chart = chart_fetch or self._soundcloud_chart
        self._fetch_audio = fetch_audio or self._yt_fetch
        super().__init__(studio, None)

    # -- yt-dlp -------------------------------------------------------------------------------

    @staticmethod
    def _opts():
        return {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "socket_timeout": 20}

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
            raise MoreError(f"SoundCloud search failed ({str(error)[:160]})") from None
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
            raise MoreError(f"Could not read {url} ({str(error)[:160]})") from None
        return list(info.get("entries") or [info])

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

    # -- queueing ---------------------------------------------------------------------------

    def add(self, rows):
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise MoreError("Choose between 1 and 200 songs.")
        items = []
        for r in rows:
            url = str((r or {}).get("url") or "")
            site = site_of(url)
            title, artist = match.clean(r.get("title")), match.clean(r.get("artist"))
            if not site or not title:
                raise MoreError("Only YouTube, SoundCloud and Audiomack links can be downloaded here.")
            items.append({"kind": "song", "title": title, "artist": artist, "album": title,
                          "payload": {"id": url, "source": "more", "site": site, "url": url, "artists": [artist or "Unknown Artist"],
                                      "album_artist": match.clean(r.get("album_artist")) or artist or "Unknown Artist",
                                      "album": match.clean(r.get("album")) or title, "page_meta": True,
                                      "single": not match.clean(r.get("album")),
                                      "track_no": int(r.get("track_no") or 1), "track_total": int(r.get("track_total") or 1),
                                      "duration": r.get("duration") or 0}})

        def on_disk(item):
            from .galaxy import find_existing_by_title
            p = item["payload"]
            found = find_existing_by_title(self.folder(), p["album_artist"], p["album"], item["title"])
            return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": p["album"],
                    "path": str(found)} if found else None
        result = self.manager.submit(items, "more", pre_check=on_disk)
        self.ensure_worker()
        return result

    def add_links(self, text):
        """YouTube, SoundCloud and Audiomack links, one per line (a song, an album, a set or a playlist).
        A playlist is tagged as one album: album = the playlist's name, tracks in playlist order (v61)."""
        out = []
        for url in [u for u in re.findall(r"https?://\S+", str(text or ""))][:20]:
            site = site_of(url)
            try:
                if not site:
                    raise MoreError("Only youtube.com, youtu.be, soundcloud.com and audiomack.com links work here.")
                entries = [e for e in self._extract(url) if e]
                rows = [r for r in (_entry_row({**e, "url": _entry_url(e, url, site)}, site) for e in entries) if r]
                cut = len(rows) > 200
                rows = rows[:200]
                total = len(rows)
                album = match.clean(entries[0].get("playlist_title") or entries[0].get("playlist") or "") if entries else ""
                artists = {match.match_key(r["artist"]) for r in rows}
                album_artist = rows[0]["artist"] if len(artists) == 1 and rows else "Various Artists"
                for i, r in enumerate(rows):
                    r.update(track_no=i + 1, track_total=total)
                    if total > 1:
                        r["album"] = album or ""
                        r["album_artist"] = album_artist
                if not rows:
                    raise MoreError("No songs found at this link.")
                result = self.add(rows)
                out.append({"line": url, "ok": True, "note": f"{result.get('queued', 0)} queued"
                            + (f" ({album})" if total > 1 and album else "")
                            + (f", {result['held']} waiting in Needs your decision" if result.get("held") else "")
                            + (" · only the first 200 songs" if cut else "")})
            except LibraryError as error:
                out.append({"line": url, "ok": False, "note": str(error)})
        if not out:
            raise MoreError("Paste at least one YouTube, SoundCloud or Audiomack link.")
        return {"results": out}

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
