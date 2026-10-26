"""Galaxy (fork v11): the GD Studio downloader inside Tagcast.

Ported from the user's own app, github.com/empyreanwolf/gd-music-downloader (``app.py``):
search by artist / album / track on netease, joox or bilibili; exact album tracklists only;
one download at a time with a pause between songs; 45 API calls per 5 minutes with backoff
when GD Studio pushes back; quality 320 kbps, then 128, then the same song on another source;
``.part`` files, 30-second-preview rejection; native tags (ID3v2.4 / Vorbis / MP4) with cover;
saved as ``Album Artist\\Album\\## - Title.ext`` in the Downloads folder.

Everything goes through the shared Downloads queue (``downloads.py``): songs you may already
have are held in Needs your decision, finished songs are added to the download history. Nothing
is uploaded to iBroadcast. Music data: music.gdstudio.xyz (personal, non-commercial use).
"""

import base64
import collections
import difflib
import hashlib
import html
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

import requests

from .library import LibraryError

log = logging.getLogger("tagcast")

API_BASE = "https://music-api.gdstudio.xyz/api.php"
API_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tagcast-Galaxy/1.0"
OPEN_SOURCES = ["netease", "joox", "bilibili"]
OTHER_SOURCES = ["tencent", "kuwo", "tidal", "qobuz", "apple", "ytmusic", "spotify"]
FALLBACK_SOURCES = ["netease", "joox"]  # tried when the chosen source has no link
SEARCH_COUNT = 30
ALBUM_COUNT = 99   # the API rejects count > 99 (HTTP 422)
ARTIST_TOP = 50
BITRATE = 320
API_QUOTA = 45     # the API allows 50 per 5 minutes; keep a little headroom
API_PERIOD = 300.0
API_DELAY = 1.5
TRACK_DELAY = 6.0
AUDIO_EXTS = ("mp3", "flac", "m4a")
STEP_NAMES = ["Got link", "Downloading", "Cover", "Tagging", "Saved"]
STATE_FILE = "galaxy.json"


class GalaxyError(LibraryError):
    pass


class TransientHTTPError(Exception):
    def __init__(self, msg, retry_after=0.0):
        super().__init__(msg)
        self.retry_after = retry_after


class StopRequested(Exception):
    pass


class JobCancelled(Exception):
    pass


class JobFailed(Exception):
    pass


# -- small helpers (as in the Galaxy app) ---------------------------------------------

def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
        return float(m.group()) if m else 0.0


def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    return " ".join(s.split())


_BRACKETS = re.compile(r"\s*[\(\[（【][^\)\]）】]*[\)\]）】]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def core(s):
    s = norm(s)
    s = _BRACKETS.sub("", s)
    s = re.sub(r"\s+-\s+.*$", "", s)
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def sim(a, b):
    a1, b1 = norm(a), norm(b)
    if not a1 or not b1:
        return 0.0
    if a1 == b1:
        return 1.0
    r1 = difflib.SequenceMatcher(None, a1, b1).ratio()
    a2, b2 = core(a), core(b)
    r2 = (1.0 if a2 == b2 else difflib.SequenceMatcher(None, a2, b2).ratio()) if a2 and b2 else 0.0
    return max(r1, r2 * 0.97)


def artist_matches(name, wanted):
    return norm(name) == norm(wanted)


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(s, fallback="Unknown", maxlen=120):
    s = _FORBIDDEN.sub("", str(s or ""))
    s = " ".join(s.split()).strip().rstrip(". ").strip()
    if not s:
        s = fallback
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(". ").strip() or fallback
    return s


def build_path(base, album_artist, album, track_no, title, ext="mp3"):
    n = to_int(track_no, 0)
    return (Path(base) / safe_name(album_artist, "Unknown Artist") / safe_name(album, "Unknown Album")
            / f"{n:02d} - {safe_name(title, 'Unknown Title')}.{ext}")


def find_existing_by_title(base, album_artist, album, title):
    """Any '<nn> - <title>.<ext>' already in the album folder (the track number may differ)."""
    folder = Path(base) / safe_name(album_artist, "Unknown Artist") / safe_name(album, "Unknown Album")
    if not folder.is_dir():
        return None
    want = norm(safe_name(title, "Unknown Title"))
    try:
        for p in folder.iterdir():
            m = re.match(r"^\d+ - (.+)\.(mp3|flac|m4a)$", p.name, re.I)
            if m and norm(m.group(1)) == want:
                return p
    except OSError:
        return None
    return None


def api_size_bytes(size):
    """The API documents `size` as KB but returns bytes. A song is never under ~300 KB."""
    v = to_float(size)
    if v <= 0:
        return 0.0
    return v if v > 300_000 else v * 1024


def detect_audio_format(path):
    """'mp3', 'flac' or 'm4a' from the file's first bytes, else ValueError."""
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:3] == b"ID3" and len(head) >= 10:
            size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
            f.seek(10 + size + (10 if head[5] & 0x10 else 0))
            after = f.read(4)
        else:
            after = head[:4]
    if head[4:8] == b"ftyp":
        return "m4a"
    if after[:4] == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (len(after) > 1 and after[0] == 0xFF and (after[1] & 0xE0) == 0xE0):
        return "mp3"
    raise ValueError(f"unsupported audio format (starts with {head[:8]!r})")


def album_artist_for(tracks):
    firsts = [t["artists"][0] for t in tracks if t.get("artists")]
    return Counter(firsts).most_common(1)[0][0] if firsts else "Unknown Artist"


def netease_pic_url(pic_id, size):
    """NetEase cover URL built locally (no API call, so it doesn't use the quota)."""
    pid = str(pic_id or "").strip()
    if not pid.isdigit():
        return ""
    magic = b"3go8&$8*3*3h0k(2)2"
    raw = bytes(b ^ magic[i % len(magic)] for i, b in enumerate(pid.encode()))
    enc = base64.b64encode(hashlib.md5(raw).digest()).decode().replace("/", "_").replace("+", "-")
    return f"https://p3.music.126.net/{enc}/{pid}.jpg?param={size}y{size}"


def image_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return ""


def normalize_track(x, fallback_source):
    artists = x.get("artist")
    if isinstance(artists, str):
        artists = [a.strip() for a in re.split(r"\s*(?:/|,|、)\s*", artists) if a.strip()]
    elif isinstance(artists, list):
        artists = [str(a).strip() for a in artists if str(a).strip()]
    else:
        artists = []
    tid = str(x.get("id", "")).strip()
    return {"id": tid, "title": html.unescape(str(x.get("name") or x.get("title") or "")).strip() or "Unknown Title",
            "artists": [html.unescape(a) for a in artists],
            "album": html.unescape(str(x.get("album") or "")).strip(),
            "pic_id": str(x.get("pic_id") or "").strip(), "lyric_id": str(x.get("lyric_id") or tid).strip(),
            "source": str(x.get("source") or fallback_source).replace("_album", "")}


# -- rate limits ------------------------------------------------------------------------

_tls = threading.local()  # the download thread installs a cancel-aware sleep here


def _sleep(secs):
    (getattr(_tls, "sleep", None) or time.sleep)(secs)


class QuotaLimiter:
    """Minimum spacing plus a rolling quota (45 calls per 300 s) shared by all threads.
    Low-priority calls (covers) leave `reserve` slots free so downloads and searches go first."""

    def __init__(self, interval=API_DELAY, max_calls=API_QUOTA, period=API_PERIOD, reserve=15, clock=time.monotonic):
        self.interval, self.max_calls, self.period, self.reserve = float(interval), int(max_calls), float(period), int(reserve)
        self.clock = clock
        self._lock = threading.Lock()
        self._calls = collections.deque()
        self._next = 0.0
        self.blocked_until = 0.0

    def penalize(self, secs):
        """The server pushed back (503 / reset): hold every API call for `secs`."""
        with self._lock:
            until = self.clock() + secs
            self._next = max(self._next, until)
            self.blocked_until = max(self.blocked_until, until)

    def _prune(self, now):
        while self._calls and self._calls[0] <= now - self.period:
            self._calls.popleft()

    def status(self):
        with self._lock:
            now = self.clock()
            self._prune(now)
            wait = max(0.0, self.blocked_until - now)
            if len(self._calls) >= self.max_calls:
                wait = max(wait, self._calls[0] + self.period - now)
            return {"used": len(self._calls), "quota": self.max_calls, "wait": round(wait)}

    def wait(self, low=False):
        while True:
            with self._lock:
                now = self.clock()
                self._prune(now)
                cap = max(1, self.max_calls - (self.reserve if low else 0))
                if len(self._calls) < cap:
                    slot = max(now, self._next)
                    self._next = slot + self.interval
                    self._calls.append(slot)
                    break
                wait_for = self._calls[len(self._calls) - cap] + self.period - now + 0.05
                self.blocked_until = max(self.blocked_until, now + wait_for)
            hook = getattr(_tls, "on_quota_wait", None)
            if hook:
                hook(wait_for)
            _sleep(min(wait_for, 1.0))
        delay = slot - self.clock()
        if delay > 0:
            _sleep(delay)


def _session():
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = API_UA
        _tls.session = s
    return s


def request_with_backoff(call, retries=3, base_delay=2.0, on_retry=None):
    """call() -> response. Retries 429/5xx/timeouts with exponential backoff."""
    attempt = 0
    while True:
        try:
            resp = call()
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                ra = to_float(resp.headers.get("Retry-After"))
                resp.close()
                raise TransientHTTPError(f"HTTP {resp.status_code}", min(ra, 60.0))
            return resp
        except (TransientHTTPError, requests.Timeout, requests.ConnectionError) as e:
            if attempt >= retries:
                raise GalaxyError(f"GD Studio: {e} (gave up after {retries} retries)") from None
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            if isinstance(e, TransientHTTPError) and e.retry_after > delay:
                delay = e.retry_after
            attempt += 1
            if on_retry:
                on_retry(attempt, delay, e)
            _sleep(delay)


class GDApi:
    def __init__(self, limiter, get=None):
        self.limiter = limiter
        self.get = get or (lambda params, timeout: _session().get(API_BASE, params=params, timeout=timeout))

    def _get(self, params, low=False):
        def call():
            self.limiter.wait(low)
            return self.get(params, 20)

        def on_retry(attempt, delay, err):
            if isinstance(err, (TransientHTTPError, requests.ConnectionError)):
                self.limiter.penalize(min(90.0, 20.0 * attempt))

        r = request_with_backoff(call, on_retry=on_retry)
        if r.status_code != 200:
            body = " ".join(str(r.text).split())[:160]
            src = str(params.get("source", "")).replace("_album", "")
            hint = (f" - source “{src}” is probably closed right now, try netease / joox / bilibili"
                    if r.status_code in (400, 403, 404, 422) and src not in OPEN_SOURCES else "")
            raise GalaxyError(f"GD Studio API HTTP {r.status_code}{hint}" + (f" ({body})" if body and not hint else ""))
        text = str(r.text).strip().lstrip("﻿")
        try:
            return json.loads(text)
        except ValueError:
            raise GalaxyError(f"GD Studio API returned something that isn't JSON: {text[:150]!r}") from None

    def search(self, source, keyword, count=SEARCH_COUNT, page=1):
        data = self._get({"types": "search", "source": source, "name": keyword,
                          "count": max(1, min(int(count), 99)), "pages": page})
        if isinstance(data, dict):
            data = data.get("data") or data.get("result") or []
        if not isinstance(data, list):
            return []
        base = source.replace("_album", "")
        return [normalize_track(x, base) for x in data if isinstance(x, dict) and x.get("id") is not None]

    def url(self, source, track_id, br):
        data = self._get({"types": "url", "source": source, "id": track_id, "br": br})
        if not isinstance(data, dict):
            return {}
        return {"url": str(data.get("url") or "").strip(), "br": to_int(data.get("br"), 0), "size": to_float(data.get("size"))}

    def pic(self, source, pic_id, size=500, low=False):
        data = self._get({"types": "pic", "source": source, "id": pic_id, "size": size}, low=low)
        return str(data.get("url") or "").strip() if isinstance(data, dict) else ""


def fetch_album_exact(api, source, album, artist_hint=None):
    """(tracks, found_name) for an album with exactly this name, else ([], "").
    Another edition ("X Plus" for "X") or loose songs are never offered."""
    base = source.replace("_album", "")
    want = norm(album)

    def by_artist(items):
        return bool(artist_hint) and any(artist_matches(a, artist_hint) for t in items for a in t["artists"])

    for q in [album] + ([f"{album} {artist_hint}"] if artist_hint else []):
        groups = {}
        for t in api.search(f"{base}_album", q, ALBUM_COUNT, 1):
            groups.setdefault(t["album"] or album, []).append(t)
        exact = [items for name, items in groups.items() if norm(name) == want]
        if exact:
            items = max(exact, key=lambda it: (by_artist(it), len(it)))
            return items, items[0]["album"] or album
    return [], ""


def artist_tracks(api, source, artist):
    """Up to 50 songs where the artist is credited, from up to 3 search pages."""
    exact, loose, seen = [], [], set()
    want = norm(artist)
    for page in (1, 2, 3):
        if page == 3 and len(exact) >= ARTIST_TOP:
            break
        res = api.search(source, artist, SEARCH_COUNT, page)
        if not res:
            break
        for t in res:
            key = (norm(t["title"]), norm(t["album"]))
            if key in seen:
                continue
            if any(artist_matches(a, artist) for a in t["artists"]):
                seen.add(key)
                exact.append(t)
            elif any(want and want in norm(a) for a in t["artists"]):
                loose.append(t)
    if not exact:  # nothing matched exactly - fall back to substring matches
        for t in loose:
            key = (norm(t["title"]), norm(t["album"]))
            if key not in seen:
                seen.add(key)
                exact.append(t)
    return exact[:ARTIST_TOP]


# -- tags -------------------------------------------------------------------------------

def read_duration(path, fmt):
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    info = {"flac": FLAC, "m4a": MP4}.get(fmt, MP3)(str(path)).info
    return float(info.length or 0), int((getattr(info, "bitrate", 0) or 0) / 1000)


def write_tags(path, fmt, *, title, artists, album_artist, album, track_no, track_total, year="", genre="", cover=None):
    """Fresh tags in the native format: ID3v2.4 for MP3, Vorbis comments for FLAC, MP4 atoms for M4A."""
    mime = image_mime(cover or b"")
    artists = list(artists) or ["Unknown Artist"]
    if fmt == "flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()
        audio.clear_pictures()
        t = audio.tags
        t["TITLE"], t["ARTIST"], t["ALBUMARTIST"], t["ALBUM"] = [title], artists, [album_artist], [album or "Unknown Album"]
        if track_no:
            t["TRACKNUMBER"] = [str(track_no)]
            if track_total:
                t["TRACKTOTAL"] = t["TOTALTRACKS"] = [str(track_total)]
        t["DISCNUMBER"], t["DISCTOTAL"] = ["1"], ["1"]
        if year:
            t["DATE"] = [str(year)]
        if genre:
            t["GENRE"] = [genre]
        if mime:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", cover
            audio.add_picture(pic)
        audio.save(deleteid3=True)
    elif fmt == "m4a":
        from mutagen.mp4 import MP4, MP4Cover
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags.clear()
        t = audio.tags
        t["\xa9nam"], t["\xa9ART"], t["aART"], t["\xa9alb"] = [title], artists, [album_artist], [album or "Unknown Album"]
        if track_no:
            t["trkn"] = [(int(track_no), int(track_total or 0))]
        t["disk"] = [(1, 1)]
        if year:
            t["\xa9day"] = [str(year)]
        if genre:
            t["\xa9gen"] = [genre]
        if mime:
            t["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG)]
        audio.save()
    else:
        from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
        tags = ID3()  # a fresh tag replaces whatever the source embedded
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TPE1(encoding=3, text=artists))
        tags.add(TPE2(encoding=3, text=[album_artist]))
        tags.add(TALB(encoding=3, text=[album or "Unknown Album"]))
        if track_no:
            tags.add(TRCK(encoding=3, text=[f"{track_no}/{track_total}" if track_total else str(track_no)]))
        tags.add(TPOS(encoding=3, text=["1/1"]))
        if year:
            tags.add(TDRC(encoding=3, text=[str(year)]))
        if genre:
            tags.add(TCON(encoding=3, text=[genre]))
        if mime:
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        tags.save(str(path), v2_version=4, v1=0)


def tag_preview(track, album_artist, track_no, track_total):
    return [["Title", track.get("title", "")], ["Artists", " ; ".join(track.get("artists") or [])],
            ["Album artist", album_artist], ["Album", track.get("album", "")],
            ["Track", f"{track_no}/{track_total}" if track_no and track_total else str(track_no or "—")],
            ["Disc", "1/1"], ["Cover", "front cover, 500px"],
            ["Year / genre", "added in v12 (MusicBrainz)"], ["Format", "MP3 → ID3v2.4 · FLAC → Vorbis · M4A → MP4"]]


# -- the service --------------------------------------------------------------------------

class Galaxy:
    """Search endpoints plus one background download thread over the shared Downloads queue."""

    def __init__(self, studio, api=None, fetch=None, track_delay=TRACK_DELAY):
        self.studio = studio
        self.limiter = QuotaLimiter()
        self.api = api or GDApi(self.limiter)
        self.fetch = fetch or (lambda url, **kw: _session().get(url, **kw))  # file and cover downloads
        self.track_delay = track_delay
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("paused", False)
        self.state.setdefault("source", "netease")
        self.state.setdefault("picks", {})
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.cancel_id = None
        self.thread = None
        self.stopping = False
        self.waiting_until = 0.0
        self.active = None  # id of the song downloading now
        self._t0 = 0.0
        self._cover_gate = threading.Semaphore(2)
        self._cover_memory, self._cover_misses = {}, {}
        manager = studio.download_manager
        manager.on_queued = self.ensure_worker  # "Download anyway" restarts the thread
        with manager.lock:  # a download that was running when Tagcast stopped starts again
            for item in manager.state["queue"]:
                if item.get("state") == "active":
                    item.update(state="queued", step=-1, step_detail="", progress=0)
        if any(i.get("state") == "queued" and i.get("source") == "galaxy" for i in manager.state["queue"]):
            self.ensure_worker()

    def _persist(self):
        self.studio._write(STATE_FILE, self.state)

    @property
    def manager(self):
        return self.studio.download_manager

    def folder(self):
        return self.manager.folder()

    def _source(self, source):
        source = source or self.state["source"]
        if source not in OPEN_SOURCES + OTHER_SOURCES:
            raise GalaxyError("Choose netease, joox or bilibili.")
        return source

    # -- searching -------------------------------------------------------------------

    def _annotate(self, tracks, album_artist=None, numbered=False):
        """Each track with its status against iBroadcast, the history and the download folder."""
        if not tracks:
            return []
        items = [{"kind": "song", "title": t["title"], "artist": (t["artists"] or [""])[0], "album": t["album"]}
                 for t in tracks]
        results = self.manager.check(items)["results"]
        queued = {(i["payload"]["source"], i["payload"]["id"]): i["state"] for i in self.manager.state["queue"]
                  if isinstance(i.get("payload"), dict) and i.get("state") in ("queued", "active")}
        out = []
        for n, (t, r) in enumerate(zip(tracks, results), 1):
            row = {**t, "status": r["status"], "you_have": r["you_have"]}
            artist = album_artist or (t["artists"] or ["Unknown Artist"])[0]
            on_disk = find_existing_by_title(self.folder(), artist, t["album"], t["title"])
            if on_disk:
                row["on_disk"] = str(on_disk)
            if numbered:
                row["track_no"] = n
                row["path"] = str(build_path(self.folder(), artist, t["album"], n, t["title"], "{ext}"))
            if (t["source"], t["id"]) in queued:
                row["queued"] = queued[(t["source"], t["id"])]
            out.append(row)
        return out

    def search(self, mode, query, source=None):
        query = " ".join(str(query or "").split())
        if not query or len(query) > 200:
            raise GalaxyError("Type what to search for (at most 200 characters).")
        source = self._source(source)
        if source != self.state["source"]:
            self.state["source"] = source
            self._persist()
        if mode == "album":
            return self.album(query, None, source)
        if mode == "artist":
            tracks = artist_tracks(self.api, source, query)
            albums = {}
            for t in tracks:
                entry = albums.setdefault(t["album"] or "Unknown Album", {"songs": 0, "source": t["source"], "pic_id": ""})
                entry["songs"] += 1
                entry["pic_id"] = entry["pic_id"] or t["pic_id"]
            return {"mode": mode, "query": query, "source": source, "tracks": self._annotate(tracks),
                    "albums": [{"name": n, **e} for n, e in albums.items()], **self.status()}
        if mode == "track":
            return {"mode": mode, "query": query, "source": source,
                    "tracks": self._annotate(self.api.search(source, query, SEARCH_COUNT, 1)), **self.status()}
        raise GalaxyError("Search by artist, album or track.")

    def album(self, album, artist=None, source=None):
        album = " ".join(str(album or "").split())
        if not album:
            raise GalaxyError("Choose an album.")
        source = self._source(source)
        tracks, found = fetch_album_exact(self.api, source, album, artist or None)
        if not tracks:
            return {"mode": "album", "query": album, "source": source, "found": False, "tracks": [],
                    "note": f"No album named exactly “{album}” on {source}. Other editions and loose songs are skipped.",
                    **self.status()}
        album_artist = artist if artist and any(artist_matches(a, artist) for t in tracks for a in t["artists"]) \
            else album_artist_for(tracks)
        rows = self._annotate(tracks, album_artist, numbered=True)
        for row in rows:
            row["preview"] = tag_preview(row, album_artist, row["track_no"], len(rows))
        cover = next((t for t in tracks if t["pic_id"]), None)
        return {"mode": "album", "query": album, "source": source, "found": True, "album": found,
                "album_artist": album_artist, "tracks": rows,
                "cover": {"source": cover["source"], "pic_id": cover["pic_id"]} if cover else None,
                **self.status()}

    # -- queueing --------------------------------------------------------------------

    def add(self, tracks, album_artist=None, total=None):
        """Queue songs. Songs you may have (iBroadcast, history, already in the folder) are held."""
        if not isinstance(tracks, list) or not tracks or len(tracks) > 500:
            raise GalaxyError("Choose between 1 and 500 songs.")
        items = []
        for t in tracks:
            if not isinstance(t, dict) or not str(t.get("id", "")).strip() or not str(t.get("title", "")).strip():
                raise GalaxyError("A song is missing its id or title. Search again.")
            source = self._source(t.get("source"))
            artists = [str(a) for a in t.get("artists") or [] if str(a).strip()][:20]
            artist = str(album_artist or (artists or ["Unknown Artist"])[0])
            payload = {"id": str(t["id"]), "source": source, "artists": artists, "album": str(t.get("album") or ""),
                       "album_artist": artist, "pic_id": str(t.get("pic_id") or ""),
                       "track_no": to_int(t.get("track_no"), 0) or None, "track_total": to_int(total, 0) or None,
                       "fixed_numbering": bool(t.get("track_no"))}
            items.append({"kind": "song", "title": str(t["title"]), "artist": (artists or [""])[0],
                          "album": payload["album"], "payload": payload})

        def on_disk(item):
            p = item["payload"]
            found = find_existing_by_title(self.folder(), p["album_artist"], p["album"], item["title"])
            return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": p["album"],
                    "path": str(found)} if found else None

        result = self.manager.submit(items, "galaxy", pre_check=on_disk)
        self.ensure_worker()
        return result

    # -- queue controls ----------------------------------------------------------------

    def control(self, action, ids=None):
        ids = [str(i) for i in ids or []]
        m = self.manager
        if action in ("pause", "resume"):
            with self.lock:
                self.state["paused"] = action == "pause"
                self._persist()
            self.wake.set()
            self.ensure_worker()
        elif action == "cancel":
            self.cancel_id = self.active
        elif action == "remove":
            with m.lock:
                if self.active in ids:
                    self.cancel_id = self.active
            m.remove([i for i in ids if i != self.active])
        elif action == "retry":
            m.requeue(ids or None)
            self.ensure_worker()
        elif action == "top":
            m.move_to_top(ids)
        elif action in ("clear_done", "clear_failed", "clear_waiting", "clear_skipped"):
            m.clear_state({"clear_done": "done", "clear_failed": "failed", "clear_waiting": "queued",
                           "clear_skipped": "skipped"}[action], keep=self.active)
        else:
            raise GalaxyError("Unknown queue action.")
        self.wake.set()
        return self.status()

    def status(self):
        api = self.limiter.status()
        return {"paused": self.state["paused"], "api": api, "active": self.active,
                "next_in": max(0, round(self.waiting_until - time.monotonic())),
                "running": bool(self.thread and self.thread.is_alive())}

    # -- the download thread ------------------------------------------------------------

    def ensure_worker(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wake.set()
                return
            self.stopping = False
            self.thread = threading.Thread(target=self._loop, name="GalaxyDownloads", daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stopping = True
        self.wake.set()

    def _sleep(self, secs):
        end = time.monotonic() + secs
        while True:
            if self.stopping:
                raise StopRequested()
            self._check_cancel()
            r = end - time.monotonic()
            if r <= 0:
                return
            time.sleep(min(0.2, r))

    def _check_cancel(self):
        if self.active and self.cancel_id == self.active:
            raise JobCancelled()

    def _step(self, item_id, idx, detail="", **extra):
        self.manager.update_item(item_id, step=idx, step_detail=detail, **extra)

    def _loop(self):
        _tls.sleep = self._sleep
        _tls.on_quota_wait = lambda secs: self.active and self._step(
            self.active, self.manager.item(self.active).get("step", 0) if self.manager.item(self.active) else 0,
            f"API limit reached (45 per 5 min) - waiting {secs:.0f} s")
        while not self.stopping:
            job = None if self.state["paused"] else self.manager.next_queued("galaxy")
            if job is None:
                self.wake.wait(0.5)
                self.wake.clear()
                if not self.manager.has_queued("galaxy") and not self.state["paused"]:
                    return  # nothing left; add() starts a new thread
                continue
            self.active, self.cancel_id = job["id"], None
            try:
                self.process(job)
            except StopRequested:
                self.manager.update_item(job["id"], persist=True, state="queued", step=-1, step_detail="", progress=0)
                break
            except JobCancelled:
                self.manager.remove([job["id"]])
            except (JobFailed, GalaxyError) as e:
                self.manager.update_item(job["id"], persist=True, state="failed", note=str(e), speed=0, eta=0)
            except Exception as e:  # never let one song stop the queue
                log.exception("Galaxy download failed")
                self.manager.update_item(job["id"], persist=True, state="failed", note=f"{type(e).__name__}: {e}")
            finally:
                self._cleanup(job["id"])
                self.active = self.cancel_id = None
            if self.manager.has_queued("galaxy") and not self.state["paused"] and not self.stopping:
                self.waiting_until = time.monotonic() + self.track_delay
                try:
                    self._sleep(self.track_delay)
                except (StopRequested, JobCancelled):
                    pass
                self.waiting_until = 0.0

    def _part_path(self, item_id):
        return Path(self.folder()) / "_incomplete" / f"{item_id}.part"

    def _cleanup(self, item_id):
        part = self._part_path(item_id)
        try:
            part.unlink(missing_ok=True)
            part.parent.rmdir()  # only when empty
        except OSError:
            pass

    def process(self, job):
        p = job["payload"]
        jid = job["id"]
        self.manager.update_item(jid, started=time.time(), speed=0, eta=0)
        self._step(jid, 0, "Requesting link…")
        link = self.get_link(job)
        self._step(jid, 0, f"{link['br'] or '?'} kbps via {link['source']}")
        part = self._part_path(jid)
        part.parent.mkdir(parents=True, exist_ok=True)
        self._step(jid, 1, "Connecting…")
        nbytes = self.download(jid, link["url"], part)
        fmt = self.check_audio(part, link, nbytes)
        self._step(jid, 1, f"{nbytes / 1048576:.1f} MB · {fmt.upper()}")
        self._step(jid, 2, "Fetching cover…")
        cover = self.fetch_cover(p)
        track_no, total = to_int(p.get("track_no"), 0), to_int(p.get("track_total"), 0)
        self._step(jid, 3, {"flac": "Writing FLAC tags…", "m4a": "Writing M4A tags…"}.get(fmt, "Writing ID3v2.4…"))
        write_tags(part, fmt, title=job["title"], artists=p["artists"], album_artist=p["album_artist"],
                   album=p["album"], track_no=track_no, track_total=total, cover=cover)
        final = build_path(self.folder(), p["album_artist"], p["album"], track_no, job["title"], fmt)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, final)
        for other in AUDIO_EXTS:  # a re-download in another format replaces the old copy
            if other != fmt:
                try:
                    final.with_suffix("." + other).unlink(missing_ok=True)
                except OSError:
                    pass
        extras = ["no cover"] if not cover else []
        self.manager.update_item(jid, persist=True, state="done", step=4, step_detail=str(final),
                                 note="Saved" + (f" · {', '.join(extras)}" if extras else ""), path=str(final),
                                 progress=100, finished=time.time(), size_mb=round(nbytes / 1048576, 2),
                                 format=fmt, speed=0, eta=0)
        self.manager.record({"title": job["title"], "artists": p["artists"], "album": p["album"],
                             "source": "galaxy", "path": str(final)})

    def get_link(self, job):
        p = job["payload"]
        rates = [BITRATE, 128]

        def try_rates(source, tid):
            for b in rates:
                try:
                    info = self.api.url(source, tid, b)
                except (StopRequested, JobCancelled):
                    raise
                except Exception as e:
                    log.info("Galaxy url call failed (%s, br=%s): %s", source, b, e)
                    continue
                if info.get("url"):
                    info["source"], info["id"] = source, tid
                    info["br"] = info.get("br") or b
                    return info
            return None

        info = try_rates(p["source"], p["id"])
        if info:
            return info
        query = f"{(p['artists'] or [''])[0]} {job['title']}".strip()
        for alt in [s for s in FALLBACK_SOURCES if s != p["source"]]:
            self._step(job["id"], 0, f"Trying {alt}…")
            try:
                results = self.api.search(alt, query, 10, 1)
            except (StopRequested, JobCancelled):
                raise
            except Exception as e:
                log.info("Galaxy fallback search on %s failed: %s", alt, e)
                continue
            best, best_score = None, 0.0
            for r in results:
                t = sim(r["title"], job["title"])
                a = max((sim(x, y) for x in r["artists"] for y in p["artists"]), default=0.0)
                if t >= 0.8 and a >= 0.7 and t + a > best_score:
                    best, best_score = r, t + a
            if best:
                info = try_rates(alt, best["id"])
                if info:
                    return info
        raise JobFailed("No download link (tried 320 and 128 kbps and other sources)")

    def download(self, jid, url, part):
        attempt = 0
        while True:
            try:
                with self.fetch(url, stream=True, timeout=(15, 60)) as r:
                    if r.status_code == 429 or r.status_code >= 500:
                        raise TransientHTTPError(f"HTTP {r.status_code}")
                    if r.status_code != 200:
                        raise JobFailed(f"Download HTTP {r.status_code}")
                    total = to_int(r.headers.get("Content-Length"), 0)
                    done, last = 0, 0.0
                    self._t0 = time.monotonic()
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(64 * 1024):
                            if self.stopping:
                                raise StopRequested()
                            self._check_cancel()
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if now - last > 0.3:
                                last = now
                                self._progress(jid, done, total)
                    self._progress(jid, done, total)
                    if total and done < total:
                        raise TransientHTTPError(f"incomplete ({done}/{total} bytes)")
                    if done == 0:
                        raise JobFailed("Empty download")
                    return done
            except (TransientHTTPError, requests.Timeout, requests.ConnectionError,
                    requests.exceptions.ChunkedEncodingError) as e:
                if attempt >= 3:
                    raise JobFailed(f"Download failed: {e}") from None
                delay = 2.0 * (2 ** attempt) + random.uniform(0, 0.5)
                attempt += 1
                self._step(jid, 1, f"Retry {attempt}/3 in {delay:.0f}s ({e})")
                self._sleep(delay)

    def _progress(self, jid, done, total):
        pct = int(done * 100 / total) if total else 0
        mb_done, mb_total = done / 1048576, total / 1048576
        elapsed = max(0.001, time.monotonic() - self._t0)
        speed = mb_done / elapsed if elapsed > 0.3 else 0.0
        eta = (mb_total - mb_done) / speed if speed > 0 and total else 0.0
        detail = f"{mb_done:.1f} / {mb_total:.1f} MB" if total else f"{mb_done:.1f} MB"
        if speed:
            detail += f" · {speed:.2f} MB/s"
        self.manager.update_item(jid, step=1, step_detail=detail, progress=pct, speed=round(speed, 2), eta=round(eta))

    def check_audio(self, part, link, nbytes):
        try:
            fmt = detect_audio_format(part)
        except ValueError as e:
            raise JobFailed(str(e)) from None
        try:
            real, real_br = read_duration(part, fmt)
        except Exception as e:
            raise JobFailed(f"Unreadable {fmt.upper()}: {e}") from None
        api_br, size_b = to_int(link.get("br"), 0), api_size_bytes(link.get("size"))
        preview, why = real < 35, ""
        if api_br > 320:  # lossless: "br" is a quality code, compare the sizes instead
            if size_b > 0 and nbytes < 0.6 * size_b:
                preview, why = True, f"got {nbytes / 1048576:.1f} MB of {size_b / 1048576:.1f} MB"
        else:
            br = api_br or real_br
            size_b = size_b or float(nbytes)
            expected = (size_b * 8) / (br * 1000) if br > 0 else 0.0
            if expected > 0 and real < 0.6 * expected:
                preview, why = True, f"expected ~{expected:.0f}s"
        if preview:
            raise JobFailed(f"Preview only ({real:.0f}s" + (f", {why})" if why else ")"))
        return fmt

    def fetch_cover(self, p, size=500):
        if not p.get("pic_id"):
            return None
        urls = []
        if p["source"] == "netease":
            urls.append(netease_pic_url(p["pic_id"], size))
        for url in urls + [None]:
            try:
                if url is None:
                    url = self.api.pic(p["source"], p["pic_id"], size, low=True)
                    if not url:
                        return None
                r = self.fetch(url, timeout=20)
                if r.status_code == 200 and image_mime(r.content):
                    return r.content
            except (StopRequested, JobCancelled):
                raise
            except Exception as e:
                log.info("Galaxy cover fetch failed: %s", e)
        return None

    # -- cover pictures for the page ---------------------------------------------------
    # Like the Galaxy app's cover cache: NetEase covers come straight from NetEase (no API
    # call); other sources use a low-priority API call that never takes the slots downloads
    # need. Pictures are kept on disk (galaxy-covers in the settings folder, at most 3,000);
    # a cover that could not be found is not asked for again within an hour.

    def _cover_dir(self):
        home = getattr(self.studio, "home", None)
        return Path(home) / "galaxy-covers" if home else None

    def thumbnail(self, source, pic_id, size=300):
        source = self._source(source)
        pic_id = str(pic_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,200}", pic_id):
            raise GalaxyError("Unknown cover.")
        size = to_int(size, 300)
        size = 100 if size <= 100 else 300 if size <= 300 else 500
        key = f"{source}_{pic_id}_{size}"
        folder = self._cover_dir()
        path = folder / f"{hashlib.sha1(key.encode()).hexdigest()}.img" if folder else None
        if path and path.is_file():
            return path.read_bytes()
        if key in self._cover_memory:
            return self._cover_memory[key]
        if self._cover_misses.get(key, 0) > time.time() - 3600:
            return None
        with self._cover_gate:  # at most two fetches at once
            data = self.fetch_cover({"source": source, "pic_id": pic_id}, size)
        if not data:
            self._cover_misses[key] = time.time()
            return None
        if path:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                files = list(folder.glob("*.img"))
                if len(files) > 3000:
                    for old in sorted(files, key=lambda f: f.stat().st_mtime)[:500]:
                        old.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            self._cover_memory[key] = data
        return data

    # -- remembered artist choices (used by the artist cards) ----------------------------

    def pick(self, name_key, album_key, card):
        with self.lock:
            picks = self.state["picks"]
            if card:
                picks[f"{name_key}|{album_key}"] = str(card)[:100]
            else:
                picks.pop(f"{name_key}|{album_key}", None)
            if len(picks) > 5000:
                for key in list(picks)[:len(picks) - 5000]:
                    picks.pop(key)
            self._persist()
        return {"ok": True}
