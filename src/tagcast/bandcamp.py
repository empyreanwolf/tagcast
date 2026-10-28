"""Bandcamp (fork v13): discographies, albums and songs from Bandcamp's free 128 kbps streams.

Behaves like otiel/BandcampDownloader (C#), ported to Python: an artist link
(``https://artist.bandcamp.com``) lists every release on its ``/music`` page; an album or track
page carries its tracklist in the ``data-tralbum`` JSON, with an ``mp3-128`` stream per track.
Each album shows Have / Partly / Downloaded before / Missing against your iBroadcast library,
the download history and the download folder; songs you may have wait in Needs your decision.

Files are tagged (title, artists, album artist, album, track n/total, year, cover) and saved as
``Album Artist\\Album\\## - Title.mp3``. Purchases are never made or used. Bandcamp pages are read
at most about once per second and kept for 10 minutes (stream links expire, so a download reads
its album page again when needed).
"""

import html as htmllib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

import requests

from . import artist_match as match
from .downloads import album_key, song_key
from .galaxy import JobFailed, find_existing_by_title, image_mime
from .library import LibraryError
from .source_worker import FileWorker

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tagcast-Bandcamp/1.0"
PAGE_TTL = 600
MAX_RELEASES = 300
ART = "https://f4.bcbits.com/img/a{:010d}_{}.jpg"


class BandcampError(LibraryError):
    pass


log = logging.getLogger(__name__)

# What every browser sends; requests without them are a common trigger for "checking your
# browser" pages (fork v33).
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
_CHALLENGE = re.compile(r"captcha|verify you are human|are you a robot|checking your browser|just a moment|"
                        r"access denied|unusual traffic|cf-chl|challenge-platform", re.I)


def describe_page(page):
    """A short, human description of a page Tagcast couldn't read, for the error and the log."""
    text = str(page or "")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = " ".join(htmllib.unescape(m.group(1)).split())[:80] if m else ""
    if _CHALLENGE.search(text):
        return (f"Bandcamp sent a “check you're a person” page{f' (“{title}”)' if title else ''} instead. Open the link in "
                "your browser once, then try again in a few minutes")
    if not text.strip():
        return "Bandcamp sent an empty page"
    return f"Bandcamp sent a page without the album on it{f' (“{title}”)' if title else ''}"


# -- reading pages ----------------------------------------------------------------------

_FIX_URL = re.compile(r'(?P<root>url: ".+)" \+ "(?P<album>.+",)')


def tralbum(page):
    """The data-tralbum JSON of an album or track page, else None."""
    m = re.search(r'data-tralbum="([^"]*)"', page) or re.search(r"data-tralbum='([^']*)'", page)
    if not m:
        return None
    raw = htmllib.unescape(m.group(1))
    try:
        return json.loads(raw)
    except ValueError:
        try:
            return json.loads(_FIX_URL.sub(r"\g<root>\g<album>", raw))
        except ValueError:
            return None


def _year(*dates):
    for value in dates:
        m = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
        if m:
            return m.group(0)
    return ""


def parse_release(page, url):
    """{url, title, artist, year, art, item_type, tracks: [{num, title, artist, duration, stream}]}."""
    data = tralbum(page)
    if not isinstance(data, dict):
        raise BandcampError("This page has no Bandcamp album or track on it.")
    current = data.get("current") or {}
    artist = match.clean(data.get("artist") or current.get("artist") or "")
    title = match.clean(current.get("title") or "")
    art_id = data.get("art_id")
    tracks = []
    for n, t in enumerate(data.get("trackinfo") or [], 1):
        if not isinstance(t, dict):
            continue
        stream = (t.get("file") or {}).get("mp3-128") if isinstance(t.get("file"), dict) else None
        if stream and stream.startswith("//"):
            stream = "https:" + stream
        tracks.append({"num": int(t.get("track_num") or n), "title": match.clean(t.get("title") or "") or f"Track {n}",
                       "artist": match.clean(t.get("artist") or "") or artist,
                       "duration": float(t.get("duration") or 0), "stream": stream or ""})
    return {"url": url, "title": title or (tracks[0]["title"] if tracks else "Unknown Album"), "artist": artist or "Unknown Artist",
            "year": _year(data.get("album_release_date"), current.get("release_date"), current.get("publish_date")),
            "art": ART.format(int(art_id), 10) if isinstance(art_id, int) else "",
            "thumb": ART.format(int(art_id), 2) if isinstance(art_id, int) else "",
            "item_type": data.get("item_type") or "album", "tracks": tracks}


def music_page_releases(page):
    """Relative /album/… and /track/… links on an artist's /music page."""
    if 'div id="discography"' in page:  # an artist with one album: /music shows that album
        links = list(dict.fromkeys(re.findall(r'href="(/album/[^"?#]+)"', page)))
        return links[:1]
    found = re.findall(r'(?:href="|&quot;|")((?:https?://[^/"&]+)?/(?:album|track)/[^"&?#\s<>]+)', page)
    found = [re.sub(r"^https?://[^/]+", "", f) for f in found]
    return list(dict.fromkeys(found))[:MAX_RELEASES]


def base_url(url):
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise BandcampError("Paste a Bandcamp link, for example https://artist.bandcamp.com or an album link.")
    return f"https://{parts.hostname}", parts.path


def search_results(page):
    """[(url, name)] from a bandcamp.com/search?item_type=b page."""
    out = []
    for m in re.finditer(r'<div class="heading">\s*<a href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>', page):
        url = htmllib.unescape(m.group(1)).split("?")[0].rstrip("/")
        out.append((url, htmllib.unescape(m.group(2)).strip()))
    return out


def album_search_results(page):
    """[(url, title, artist)] from a bandcamp.com/search?item_type=a page (v46)."""
    out = []
    for block in re.split(r'<li class="searchresult', page)[1:]:
        m = re.search(r'<div class="heading">\s*<a href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>', block)
        if not m:
            continue
        sub = re.search(r'<div class="subhead">(.*?)</div>', block, re.S)
        artist = ""
        if sub:
            plain = htmllib.unescape(re.sub(r"<[^>]+>", " ", sub.group(1)))
            by = re.search(r"\bby\s+(.+?)\s*$", " ".join(plain.split()), re.I | re.S)
            artist = by.group(1) if by else ""
            artist = re.split(r"\s+(?:from|released)\b", artist, maxsplit=1)[0].strip()
        out.append((htmllib.unescape(m.group(1)).split("?")[0].rstrip("/"), htmllib.unescape(m.group(2)).strip(), artist))
    return out


# -- the service -------------------------------------------------------------------------

class Bandcamp(FileWorker):
    source = "bandcamp"
    track_delay = 3.0
    pace = 1.0  # seconds between Bandcamp page reads

    def __init__(self, studio, get=None, fetch=None):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self.get = get or (lambda url: self._session.get(url, timeout=20))
        self._pages = OrderedDict()
        self._page_lock = threading.Lock()
        self._last = 0.0
        self._art = {}
        super().__init__(studio, fetch or (lambda url, **kw: self._session.get(url, **kw)))

    def page(self, url, fresh=False):
        with self._page_lock:
            hit = self._pages.get(url)
            if hit and not fresh and hit[0] > time.monotonic():
                return hit[1]
            wait = self._last + self.pace - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            r = self.get(url)
        except requests.RequestException as error:
            raise BandcampError(f"Bandcamp could not be reached ({error.__class__.__name__}).") from None
        if r.status_code == 404:
            raise BandcampError(f"Bandcamp has no page at {url}.")
        if r.status_code != 200:
            raise BandcampError(f"Bandcamp answered HTTP {r.status_code} for {url}.")
        with self._page_lock:
            self._pages[url] = (time.monotonic() + PAGE_TTL, r.text)
            while len(self._pages) > 200:
                self._pages.popitem(last=False)
        return r.text

    def forget(self, url):
        with self._page_lock:
            self._pages.pop(url, None)

    def release(self, url, fresh=False):
        """parse_release on a fetched page. A page with no album on it is described in the
        error and in tagcast.log, and dropped from the cache so the next try asks again (v33)."""
        page = self.page(url, fresh)
        try:
            return parse_release(page, url)
        except BandcampError:
            self.forget(url)
            log.warning("Bandcamp page without album data: %s (%d characters) %r", url, len(page), page[:300])
            raise BandcampError(f"{describe_page(page)}. Details are in tagcast.log.") from None

    def releases_on(self, base):
        """Album and track links on an artist's /music page; an unreadable page is logged."""
        page = self.page(base + "/music")
        found = music_page_releases(page)
        if not found:
            self.forget(base + "/music")
            log.warning("Bandcamp /music page without releases: %s (%d characters) %r", base, len(page), page[:300])
        return found

    # -- discography and albums -------------------------------------------------------------

    def discography(self, url):
        """Every release of the artist of this link (album links give that artist's releases too)."""
        base, path = base_url(url)
        releases = [urljoin(base, p) for p in self.releases_on(base)]
        if not releases and re.match(r"^/(album|track)/", path):
            releases = [base + path]
        if not releases:
            raise BandcampError("No releases found on this Bandcamp page.")
        wanted = base + path if re.match(r"^/(album|track)/", path) else ""
        return {"base": base, "releases": releases, "selected": wanted}

    def album(self, url):
        base, path = base_url(url)
        if not re.match(r"^/(album|track)/", path):
            raise BandcampError("That is not an album or track link.")
        release = self.release(base + path)
        return self._annotate(release)

    def _annotate(self, release):
        tracks = [t for t in release["tracks"] if t["stream"]]
        rows = []
        if tracks:
            items = [{"kind": "song", "title": t["title"], "artist": t["artist"], "album": release["title"]} for t in tracks]
            results = self.manager.check(items)["results"]
        else:
            results = []
        queued = {(i["payload"].get("page_url"), i["payload"].get("num")): i["state"] for i in self.manager.state["queue"]
                  if i.get("source") == "bandcamp" and isinstance(i.get("payload"), dict) and i.get("state") in ("queued", "active")}
        for t, r in zip(tracks, results):
            row = {k: v for k, v in t.items() if k != "stream"}
            row.update(status=r["status"], you_have=r["you_have"])
            on_disk = find_existing_by_title(self.folder(), release["artist"], release["title"], t["title"])
            if on_disk:
                row["on_disk"] = str(on_disk)
            if (release["url"], t["num"]) in queued:
                row["queued"] = queued[(release["url"], t["num"])]
            rows.append(row)
        have = sum(1 for r in rows if r["status"] in ("have",) or r.get("on_disk"))
        before = sum(1 for r in rows if r["status"] in ("downloaded", "partly"))
        if rows and have == len(rows):
            status = "have"
        elif have:
            status = "partly"
        elif before:
            status = "downloaded"
        else:
            status = "new"
        unavailable = len(release["tracks"]) - len(tracks)
        return {**{k: v for k, v in release.items() if k != "tracks"}, "tracks": rows, "status": status,
                "have": have, "unavailable": unavailable}

    # -- queueing ---------------------------------------------------------------------------

    def add(self, url, nums=None):
        """Queue the songs of one album (all, or the chosen track numbers)."""
        base, path = base_url(url)
        release = self.release(base + path)
        wanted = None if nums is None else {int(n) for n in nums}
        total = len(release["tracks"])
        items = []
        for t in release["tracks"]:
            if not t["stream"] or (wanted is not None and t["num"] not in wanted):
                continue
            items.append({"kind": "song", "title": t["title"], "artist": t["artist"], "album": release["title"],
                          "payload": {"id": f"{release['url']}#{t['num']}", "source": "bandcamp", "page_url": release["url"],
                                      "num": t["num"], "artists": [t["artist"]], "album_artist": release["artist"],
                                      "track_total": total, "year": release["year"], "art": release["art"],
                                      "duration": t["duration"]}})
        if not items:
            raise BandcampError("None of these songs can be streamed on Bandcamp (only purchases or pre-orders).")

        def on_disk(item):
            p = item["payload"]
            found = find_existing_by_title(self.folder(), p["album_artist"], item["album"], item["title"])
            return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": item["album"],
                    "path": str(found)} if found else None

        result = self.manager.submit(items, "bandcamp", pre_check=on_disk)
        return {**result, "album": release["title"]}

    # -- finding an artist's albums for "get missing" ------------------------------------

    def find_artist(self, name, albums=()):
        """The Bandcamp base URL of the artist with exactly this name, preferring the one whose
        releases include the wanted albums. None if Bandcamp has no such artist."""
        page = self.page(f"https://bandcamp.com/search?q={requests.utils.quote(name)}&item_type=b")
        candidates = [u for u, n in search_results(page) if match.match_key(n) == match.match_key(name)][:3]
        if len(candidates) <= 1 or not albums:
            return candidates[0] if candidates else None
        wanted = {re.sub(r"[^a-z0-9]+", "-", album_key(a)).strip("-") for a in albums}
        best, score = candidates[0], -1
        for c in candidates:
            try:
                slugs = {p.rsplit("/", 1)[-1] for p in music_page_releases(self.page(c + "/music"))}
            except BandcampError:
                continue
            s = len(slugs & wanted)
            if s > score:
                best, score = c, s
        return best

    def find_album(self, base, title):
        """The release URL on this artist's page whose title is this album, else None."""
        releases = [urljoin(base, p) for p in self.releases_on(base)]
        slug = re.sub(r"[^a-z0-9]+", "-", album_key(title)).strip("-")
        ordered = sorted(releases, key=lambda u: u.rsplit("/", 1)[-1] != slug)  # likely match first
        for url in ordered[:8] if slug else []:
            try:
                release = self.release(url)
            except BandcampError:
                continue
            if album_key(release["title"]) == album_key(title):
                return release
        return None

    def find_album_by_search(self, name, title):
        """Bandcamp's own album search (v46): the release whose title matches and whose artist
        matches the name (either may carry extra words, like "A & B"). None when nothing fits."""
        page = self.page(f"https://bandcamp.com/search?q={requests.utils.quote(f'{name} {title}')}&item_type=a")
        want_artist, want_title = match.match_key(name), album_key(title)
        for url, found_title, artist in album_search_results(page)[:8]:
            key = match.match_key(artist)
            if album_key(found_title) != want_title or not key or not (want_artist in key or key in want_artist):
                continue
            try:
                release = self.release(url)
            except BandcampError:
                continue
            if album_key(release["title"]) == want_title:
                return release
        return None

    def find_track(self, release, title):
        return next((t for t in release["tracks"] if t["stream"] and song_key(t["title"]) == song_key(title)), None)

    # -- downloading (the worker) ------------------------------------------------------------

    def process(self, job):
        p, jid = job["payload"], job["id"]
        self.manager.update_item(jid, started=time.time(), speed=0, eta=0)
        self._step(jid, 0, "Reading the album page…")
        release = self.release(p["page_url"])
        track = next((t for t in release["tracks"] if t["num"] == p["num"] and t["stream"]), None) \
            or self.find_track(release, job["title"])
        if not track:
            raise JobFailed("Bandcamp no longer streams this song")
        self._step(jid, 0, "128 kbps MP3 via Bandcamp")
        try:
            nbytes = self.download(jid, track["stream"], self._part_path(jid))
        except JobFailed as error:
            if "HTTP 4" not in str(error):
                raise
            # The stream link expired: read the page again once.
            release = self.release(p["page_url"], fresh=True)
            track = next((t for t in release["tracks"] if t["num"] == p["num"] and t["stream"]), None)
            if not track:
                raise
            nbytes = self.download(jid, track["stream"], self._part_path(jid))
        part = self._part_path(jid)
        fmt = self.check_audio(part, min_seconds=0.6 * float(p.get("duration") or 0) if p.get("duration") else 0)
        self._step(jid, 1, f"{nbytes / 1048576:.1f} MB · {fmt.upper()}")
        self._step(jid, 3, "Fetching cover…")
        cover = self.cover(p.get("art"))
        self.save(job, part, fmt, nbytes, title=job["title"], artists=p["artists"], album_artist=p["album_artist"],
                  album=job["album"], track_no=p["num"], track_total=p["track_total"], year=p.get("year", ""), cover=cover)

    def cover(self, url):
        if not url:
            return None
        if url in self._art:
            return self._art[url]
        try:
            r = self.fetch(url, timeout=20)
            data = r.content if r.status_code == 200 and image_mime(r.content) else None
        except Exception:
            data = None
        if len(self._art) > 50:
            self._art.clear()
        self._art[url] = data
        return data
