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
STEP_NAMES = ["Got link", "Downloading", "MusicBrainz lookup", "Tagging", "Saved"]
MB_BASE = "https://musicbrainz.org/ws/2"
SHORT_SECONDS = 35  # under this, a file is checked against MusicBrainz before it is kept
SETTING_LIMITS = {"track_delay": (0, 600), "api_delay": (0, 60), "api_quota": (5, 50)}
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


def fold(s):
    """norm() without accents: "JAŸ-Z" and "JAY-Z", "Beyoncé" and "Beyonce" compare equal (v32)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return norm("".join(c for c in s if not unicodedata.combining(c)))


_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)


def plain_title(title):
    """The title without "(feat. X)", "[Remastered]" or a trailing "feat. X": what people type into a search."""
    t = _BRACKETS.sub("", str(title or ""))
    t = _FEAT_TAIL.sub("", t)
    return " ".join(t.split()) or " ".join(str(title or "").split())


def artist_matches(name, wanted):
    return fold(name) == fold(wanted)


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
    Another edition ("X Plus" for "X") or loose songs are never offered.

    The API's album search comes first. When it has nothing (it misses some smaller
    albums the site's own song search lists, e.g. "Heavy Lies the Crown" by While You
    Were Gone), a plain song search is grouped by album instead, with the same
    exact-name and artist rules (v32)."""
    base = source.replace("_album", "")
    want = fold(album)
    queries = [album] + ([f"{album} {artist_hint}"] if artist_hint else [])

    def by_artist(items):
        return bool(artist_hint) and any(artist_matches(a, artist_hint) for t in items for a in t["artists"])

    def pick(results, unnamed):
        groups, seen = {}, set()
        for t in results:
            if (t["source"], t["id"]) in seen:
                continue
            seen.add((t["source"], t["id"]))
            groups.setdefault(t["album"] or unnamed, []).append(t)
        exact = [items for name, items in groups.items() if name and fold(name) == want]
        if artist_hint:
            # Another artist's album can share the exact same title ("Intro", a compilation
            # name, ...). Never fall back to "biggest same-named group" in that case -- that
            # silently queues the wrong artist's songs. Only a group actually credited to
            # artist_hint counts; nothing found this way means try the next query, or fail.
            exact = [items for items in exact if by_artist(items)]
        if not exact:
            return None
        items = max(exact, key=len)
        return items, items[0]["album"] or album

    for q in queries:
        found = pick(api.search(f"{base}_album", q, ALBUM_COUNT, 1), album)
        if found:
            return found
    # Song search: an empty album name is a loose song, never "this album".
    results = []
    for q in queries:
        results += api.search(base, q, ALBUM_COUNT, 1)
        found = pick(results, "")
        if found:
            return found
    return [], ""


def find_song(api, sources, artist, title):
    """The best match for one song across these sources, or None (v32).

    Tries "Artist Title" first, then without "(feat. X)" / brackets, then the title alone
    (what a person would type on the site), stopping at the first good match. The
    artist must still match, ignoring accents, so "Empire State Of Mind" alone can't
    turn up somebody's cover."""
    simple = plain_title(title)
    folded = fold(artist)
    queries = []
    for q in (f"{artist} {title}", f"{artist} {simple}", f"{folded} {simple}" if folded != norm(artist) else "", simple):
        if q and q.strip() and q not in queries:
            queries.append(q)
    for source in dict.fromkeys(sources):
        for q in queries:
            best, score = None, 0.0
            for t in api.search(source, q, SEARCH_COUNT, 1):
                ts = max(sim(t["title"], title), sim(plain_title(t["title"]), simple))
                ar = max((1.0 if artist_matches(a, artist) else sim(fold(a), folded) for a in t["artists"]), default=0.0)
                if ts >= 0.8 and ar >= 0.7 and ts + ar > score:
                    best, score = t, ts + ar
            if best:
                return best
    return None


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


def write_tags(path, fmt, *, title, artists, album_artist, album, track_no, track_total, year="", genre="", cover=None,
               mb_track_id="", mb_release_id=""):
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
        if mb_track_id:
            t["MUSICBRAINZ_TRACKID"] = [mb_track_id]
        if mb_release_id:
            t["MUSICBRAINZ_ALBUMID"] = [mb_release_id]
        if mime:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", cover
            audio.add_picture(pic)
        audio.save(deleteid3=True)
    elif fmt == "m4a":
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
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
        if mb_track_id:
            t["----:com.apple.iTunes:MusicBrainz Track Id"] = [MP4FreeForm(mb_track_id.encode("utf-8"))]
        if mb_release_id:
            t["----:com.apple.iTunes:MusicBrainz Album Id"] = [MP4FreeForm(mb_release_id.encode("utf-8"))]
        if mime:
            t["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG)]
        audio.save()
    else:
        from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, UFID
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
        if mb_track_id:
            tags.add(TXXX(encoding=3, desc="MusicBrainz Track Id", text=[mb_track_id]))
            tags.add(UFID(owner="http://musicbrainz.org", data=mb_track_id.encode("ascii", "ignore")))
        if mb_release_id:
            tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mb_release_id]))
        if mime:
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        tags.save(str(path), v2_version=4, v1=0)


def tag_preview(track, album_artist, track_no, track_total):
    return [["Title", track.get("title", "")], ["Artists", " ; ".join(track.get("artists") or [])],
            ["Album artist", album_artist], ["Album", track.get("album", "")],
            ["Track", f"{track_no}/{track_total}" if track_no and track_total else str(track_no or "from MusicBrainz")],
            ["Disc", "1/1"], ["Cover", "front cover, 500px"],
            ["Year", "from MusicBrainz"], ["Genre", "from MusicBrainz"], ["MB IDs", "MusicBrainz Track Id / Album Id"], ["Format", "MP3 → ID3v2.4 · FLAC → Vorbis · M4A → MP4"]]


# -- MusicBrainz (as in the Galaxy app; max ~1 request per second) -------------------------

def _phrase(s):
    return '"' + str(s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _year(date):
    m = re.match(r"(\d{4})", str(date or ""))
    return m.group(1) if m else ""


def _top_genre(obj):
    if not obj:
        return ""
    for key in ("genres", "tags"):
        items = [g for g in (obj.get(key) or []) if g.get("name")]
        if items:
            name = str(max(items, key=lambda g: to_int(g.get("count"), 0))["name"]).strip()
            return " ".join(w[:1].upper() + w[1:] for w in name.split())
    return ""


class MusicBrainz:
    def __init__(self, http=None):
        if http is None:
            from .sources import PACING, Http
            http = Http(PACING)  # musicbrainz.org is paced at 1.1 s per request
        self.http = http

    def _get(self, path, params):
        return self.http.json(f"{MB_BASE}/{path}", {**params, "fmt": "json"}, ttl=86400)

    @staticmethod
    def _pick(recs, artists, title, album):
        best = None
        for rec in recs:
            t_sim = sim(rec.get("title", ""), title)
            if t_sim < 0.75:
                continue
            names = []
            for c in rec.get("artist-credit") or []:
                if isinstance(c, dict):
                    names += [c.get("name") or "", (c.get("artist") or {}).get("name") or ""]
            if artists and not any(sim(n, a) >= 0.8 for n in names if n for a in artists):
                continue
            mb_score = to_int(rec.get("score"), 0) / 100.0
            for rel in rec.get("releases") or []:
                a_sim = sim(rel.get("title", ""), album) if album else 0.5
                if album and a_sim < 0.6:
                    continue
                bonus = 0.05 if rel.get("status") == "Official" else 0.0
                score = t_sim * 0.4 + a_sim * 0.5 + mb_score * 0.1 + bonus
                date_key = rel.get("date") or "9999"
                if best is None or score > best["score"] + 1e-9 or (
                        abs(score - best["score"]) < 1e-9 and date_key < best["date_key"]):
                    best = {"score": score, "date_key": date_key, "rec": rec, "rel": rel, "album_sim": a_sim}
        return best

    def lookup(self, artists, title, album):
        """{year, track_no, track_total, genre, recording_id, release_id, release_title, length_s} or None."""
        base = f"recording:{_phrase(title)}" + (f" AND artist:{_phrase(artists[0])}" if artists else "")
        best = None
        for q in ([base + f" AND release:{_phrase(album)}"] if album else []) + [base]:
            data = self._get("recording", {"query": q, "limit": 15}) or {}
            cand = self._pick(data.get("recordings") or [], artists, title, album)
            if cand and (best is None or cand["score"] > best["score"]):
                best = cand
            if best and best["album_sim"] >= 0.85:
                break
        if not best:
            return None
        rec, rel = best["rec"], best["rel"]
        track_no = track_total = 0
        media = rel.get("media") or []
        if media:
            m = media[0]
            track_total = to_int(m.get("track-count"), 0)
            tracks = m.get("track") or []
            if tracks:
                track_no = to_int(tracks[0].get("number"), 0)
            if not track_no and m.get("track-offset") is not None:
                track_no = to_int(m.get("track-offset"), -1) + 1
        year = _year(rel.get("date")) or _year(rec.get("first-release-date"))
        genre = ""
        try:
            genre = _top_genre(self._get(f"recording/{rec['id']}", {"inc": "genres+tags"}))
            group = (rel.get("release-group") or {}).get("id")
            if not genre and group:
                genre = _top_genre(self._get(f"release-group/{group}", {"inc": "genres+tags"}))
        except Exception as e:
            log.info("MusicBrainz genre lookup failed: %s", e)
        return {"year": year, "track_no": track_no, "track_total": track_total, "genre": genre,
                "recording_id": rec.get("id", ""), "release_id": rel.get("id", ""), "release_title": rel.get("title", ""),
                "length_s": to_int(rec.get("length"), 0) / 1000.0}

    def album_year(self, artist, album):
        q = f"release:{_phrase(album)}" + (f" AND artist:{_phrase(artist)}" if artist else "")
        data = self._get("release", {"query": q, "limit": 10}) or {}
        best_year, best_score = "", 0.0
        for rel in data.get("releases") or []:
            s, y = sim(rel.get("title", ""), album), _year(rel.get("date"))
            if s >= 0.6 and y and (s > best_score + 0.05 or (abs(s - best_score) <= 0.05 and y < best_year)):
                best_year, best_score = y, max(s, best_score)
        return best_year


# -- the service --------------------------------------------------------------------------

class Galaxy:
    """Search endpoints plus one background download thread over the shared Downloads queue."""

    def __init__(self, studio, api=None, fetch=None, track_delay=None, mb=None):
        self.studio = studio
        self.mb = mb or MusicBrainz()
        self.limiter = QuotaLimiter()
        self.api = api or GDApi(self.limiter)
        self.fetch = fetch or (lambda url, **kw: _session().get(url, **kw))  # file and cover downloads
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("settings", {})
        self.apply_settings()
        if track_delay is not None:
            self.track_delay = track_delay
        if self.state.pop("paused", False):  # v11/v12 kept the pause here; it is shared now
            studio.download_manager.state["paused"] = True
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
        manager.register("galaxy", self)
        with manager.lock:  # a download that was running when Tagcast stopped starts again
            for item in manager.state["queue"]:
                if item.get("state") == "active" and item.get("source") == "galaxy":
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
            others = " / ".join(s for s in OPEN_SOURCES if s != source)
            return {"mode": "album", "query": album, "source": source, "found": False, "tracks": [],
                    "note": f"No album named exactly “{album}” on {source}. Other editions and loose songs are skipped."
                            + (f" Try {others}." if others else ""),
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
        self.manager.control(action, ids)
        self.wake.set()
        return self.status()

    def active_ids(self):
        return [self.active] if self.active else []

    def cancel(self, item_id):
        if item_id == self.active:
            self.cancel_id = item_id

    def status(self):
        api = self.limiter.status()
        return {"paused": self.manager.is_paused(), "api": api, "active": self.active, "settings": self.settings(),
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
            # Everything in here used to run unguarded ahead of each song's own try/except
            # below (is_paused/next_queued, both of which can hit the shared queue's _persist);
            # any exception there used to kill this whole thread silently, leaving the rest of
            # the queue stuck with nothing explaining why. The watchdog in downloads.py also
            # restarts a thread that dies despite this, but it should no longer need to.
            try:
                job = None if self.manager.is_paused() else self.manager.next_queued("galaxy")
                if job is None:
                    self.wake.wait(0.5)
                    self.wake.clear()
                    if not self.manager.has_queued("galaxy") and not self.manager.is_paused():
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
                if self.manager.has_queued("galaxy") and not self.manager.is_paused() and not self.stopping:
                    self.waiting_until = time.monotonic() + self.track_delay
                    try:
                        self._sleep(self.track_delay)
                    except (StopRequested, JobCancelled):
                        pass
                    self.waiting_until = 0.0
            except Exception:
                log.exception("Galaxy download thread hit an unexpected error; continuing")
                self.active = self.cancel_id = None
                time.sleep(1)  # avoid a tight loop if something keeps failing the same way

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
        fmt, real = self.check_audio(part, link, nbytes)
        self._step(jid, 1, f"{nbytes / 1048576:.1f} MB · {fmt.upper()}")
        self._step(jid, 2, "Searching recordings…")
        try:
            mbinfo = self.mb.lookup(p["artists"], job["title"], p["album"])
        except (StopRequested, JobCancelled):
            raise
        except Exception as e:  # a lookup failure never fails the download
            log.info("MusicBrainz lookup failed for %s: %s", job["title"], e)
            mbinfo = None
        self._step(jid, 2, f"Matched “{mbinfo['release_title']}” {mbinfo['year']}".strip() if mbinfo
                   else "No match - year/genre missing")
        if real < SHORT_SECONDS and not p.get("allow_short"):
            self.check_short(jid, real, (mbinfo or {}).get("length_s", 0))
        track_no, total = to_int(p.get("track_no"), 0), to_int(p.get("track_total"), 0)
        if mbinfo and not p.get("fixed_numbering"):
            track_no = mbinfo.get("track_no") or track_no
            total = mbinfo.get("track_total") or total
        year, genre = (mbinfo or {}).get("year", ""), (mbinfo or {}).get("genre", "")
        self._step(jid, 3, "Fetching cover…")
        cover = self.fetch_cover(p)
        self._step(jid, 3, {"flac": "Writing FLAC tags…", "m4a": "Writing M4A tags…"}.get(fmt, "Writing ID3v2.4…"))
        write_tags(part, fmt, title=job["title"], artists=p["artists"], album_artist=p["album_artist"],
                   album=p["album"], track_no=track_no, track_total=total, year=year, genre=genre, cover=cover,
                   mb_track_id=(mbinfo or {}).get("recording_id", ""), mb_release_id=(mbinfo or {}).get("release_id", ""))
        final = build_path(self.folder(), p["album_artist"], p["album"], track_no, job["title"], fmt)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, final)
        for other in AUDIO_EXTS:  # a re-download in another format replaces the old copy
            if other != fmt:
                try:
                    final.with_suffix("." + other).unlink(missing_ok=True)
                except OSError:
                    pass
        missing = [name for name, value in (("year", year), ("genre", genre)) if not value]
        extras = ([f"{'/'.join(missing)} missing"] if missing else []) + (["no cover"] if not cover else [])
        self.manager.update_item(jid, persist=True, state="done", step=4, step_detail=str(final),
                                 note="Saved" + (f" · {', '.join(extras)}" if extras else ""), path=str(final),
                                 progress=100, finished=time.time(), size_mb=round(nbytes / 1048576, 2),
                                 format=fmt, speed=0, eta=0, year=year, genre=genre, track_no=track_no)
        self.manager.record({"title": job["title"], "artists": p["artists"], "album": p["album"],
                             "source": "galaxy", "path": str(final)})
        tagclean = getattr(self.studio, "tagclean", None)  # v46: artist "A; B", album artist, genre
        if tagclean is not None:
            try:
                tagclean.tidy_downloaded([final])
            except Exception:
                log.exception("Could not queue the tag clean-up for a download")

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
        preview, why = False, ""
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
        return fmt, real

    def check_short(self, jid, real, mb_length):
        """A file under SHORT_SECONDS that arrived whole: a real short track (an intro, a skit)
        or a ~30 s preview whose announced size was the preview's own. Short alone is not proof
        (v31): MusicBrainz's length for the recording decides when it knows the song; when it
        doesn't, the item fails with a "Keep it (short track)" button rather than guessing."""
        if mb_length > 0:
            if real >= 0.85 * mb_length - 2:
                return  # MusicBrainz agrees the song is this short
            mins, secs = divmod(round(mb_length), 60)
            raise JobFailed(f"Preview only ({real:.0f}s; MusicBrainz says {mins}:{secs:02d})")
        self.manager.update_item(jid, short_seconds=round(real))
        raise JobFailed(f"Maybe a preview ({real:.0f}s) - if it really is a short track, press Keep it")

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

    # -- settings ------------------------------------------------------------------------

    def settings(self):
        saved = self.state["settings"]
        return {"track_delay": float(saved.get("track_delay", TRACK_DELAY)),
                "api_delay": float(saved.get("api_delay", API_DELAY)),
                "api_quota": int(saved.get("api_quota", API_QUOTA))}

    def apply_settings(self):
        s = self.settings()
        self.track_delay = s["track_delay"]
        self.limiter.interval, self.limiter.max_calls = s["api_delay"], s["api_quota"]

    def save_settings(self, body):
        if not isinstance(body, dict):
            raise GalaxyError("Choose the Galaxy settings.")
        new = dict(self.state["settings"])
        for key, (low, high) in SETTING_LIMITS.items():
            if key not in body:
                continue
            try:
                value = float(body[key])
            except (TypeError, ValueError):
                raise GalaxyError("Enter numbers for the Galaxy settings.") from None
            if not low <= value <= high:
                labels = {"track_delay": "The pause between songs", "api_delay": "The pause between API calls",
                          "api_quota": "The API quota"}
                raise GalaxyError(f"{labels[key]} must be between {low} and {high}.")
            new[key] = int(value) if key == "api_quota" else round(value, 1)
        with self.lock:
            self.state["settings"] = new
            self.apply_settings()
            self._persist()
        return self.status()

    def album_years(self, artist, albums):
        """Release years for the album cards (MusicBrainz, about one album per second)."""
        if not isinstance(albums, list):
            raise GalaxyError("Choose albums.")
        years = {}
        for album in [str(a) for a in albums if str(a).strip() and a != "Unknown Album"][:40]:
            try:
                years[album] = self.mb.album_year(str(artist or ""), album)
            except Exception as e:
                log.info("MusicBrainz album year failed for %s: %s", album, e)
                years[album] = ""
        return {"years": years}

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
