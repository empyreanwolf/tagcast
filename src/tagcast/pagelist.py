"""From a page (fork v45): give Tagcast a link (or pasted text) and it lists every album on it.

"10 top hip hop albums of 2024", a Reddit thread, a Wikipedia list, a public Spotify playlist:
Tagcast reads the page with plain rules (lines like ``1. Artist – Album``, ``Album by Artist``,
list items, headings, table rows, schema.org lists), then checks each candidate online --
MusicBrainz first, then Deezer -- to fix the spelling and fill in the year and cover. A line
neither knows is kept and marked "Not sure"; you can edit it and check it again. Each album is
compared with your library and download history (Have / Partly / New). The albums you tick are
fetched through the same Galaxy -> Bandcamp -> Soulseek chain as Discography; anything no source
has goes on the wishlist. Nothing is written to iBroadcast.

The page text is read, never executed. Only http(s) links are fetched, and never to addresses on
your own network.
"""

import html as htmllib
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
from urllib.parse import urlparse

import requests

from . import artist_match as match
from .library import LibraryError
from .sources import SourceError

log = logging.getLogger("tagcast")

MB = "https://musicbrainz.org/ws/2"
DEEZER = "https://api.deezer.com"
MAX_ITEMS = 300
MAX_TEXT = 2_000_000
MAX_LINE = 160
TIMEOUT = 20
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tagcast", "Accept-Language": "en"}
DASHES = r"(?:\s[-–—―‒]+\s|\s*[–—―]\s*|:\s)"
NUMBERING = re.compile(r"^\s*(?:[#>*•·▪◦■□●○▶►-]+\s*)?(?:\(?\d{1,3}\s*[.)\]:/-]\s*|(?<=#)\d{1,3}\s+|no\.?\s*\d{1,3}\s*[:.-]?\s*)?")
LIST_MARK = re.compile(r"^\s*(?:[*•·▪◦■□●○▶►-]|\d{1,3}\s*[.)\]:]|#+\s|no\.?\s*\d{1,3})\s*", re.I)
YEAR_TAIL = re.compile(r"\s*[(\[]\s*((?:19|20)\d\d)\s*[)\]]\s*$")
YEAR_ONLY = re.compile(r"^(?:19|20)\d\d$")
BY = re.compile(r"^(?P<album>.{2,}?)\s+by\s+(?P<artist>.{2,})$", re.I)
QUOTED = re.compile(r"^(?P<artist>[^\"“”‘’'«»]{2,}?)[,:]?\s+[\"“‘«'](?P<album>[^\"”’»]{1,}?)[\"”’»']$")
HEADER_WORDS = {"artist", "album", "title", "rank", "year", "release", "band", "name", "no", "position", "label", "genre", "date"}
TAG = re.compile(r"<[^>]+>")
NOISE = re.compile(r"^(?:read more|share|reply|comments?|log in|sign up|advertisement|subscribe|menu|home|next|previous|"
                   r"related|click here|continue reading|skip to content)\b", re.I)


class PageError(LibraryError):
    pass


# -- fetching ----------------------------------------------------------------------------

def check_link(link):
    """http(s) only, and never an address on the user's own network."""
    link = (link or "").strip()
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", link, re.I) else link
    parts = urlparse(link)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PageError("Paste a full page link, like https://example.com/best-albums-2024.")
    try:
        for info in socket.getaddrinfo(parts.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise PageError("Tagcast only reads pages on the internet, not on your own network.")
    except socket.gaierror:
        raise PageError(f"Couldn't find the site {parts.hostname}.") from None
    return link


def html_to_lines(source):
    """Page HTML -> plain lines. List items and headings get a bullet so they can be told from
    prose; a table row becomes "A - B" with the rank/year cells dropped."""
    source = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|iframe)\b.*?</\1>", " ", source or "")
    source = re.sub(r"(?s)<!--.*?-->", " ", source)

    def row(m):
        cells = [clean_text(TAG.sub(" ", c)) for c in re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", m.group(1))]
        cells = [c for c in cells if c and not re.fullmatch(r"#?\d{1,3}\.?|(?:19|20)\d\d", c)]
        if len(cells) >= 2 and not {w.casefold() for w in cells[:2]} <= HEADER_WORDS:
            return f"\n• {cells[0]} - {cells[1]}\n"
        return "\n"
    source = re.sub(r"(?is)<tr\b[^>]*>(.*?)</tr>", row, source)
    source = re.sub(r"(?i)<(?:li|h[1-6])\b[^>]*>", "\n• ", source)
    source = re.sub(r"(?i)</?(?:p|div|br|ul|ol|section|article|table|tr|blockquote|figure|figcaption|dt|dd|li|h[1-6])\b[^>]*>", "\n", source)
    return [line for line in (clean_text(TAG.sub("", part)) for part in source.split("\n")) if line]


def clean_text(value):
    value = htmllib.unescape(str(value or ""))
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)          # markdown links
    value = re.sub(r"(\*\*|__|\*|`|~~)", "", value)
    value = value.replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", value).strip()


def ld_candidates(source):
    """schema.org lists (ItemList, MusicPlaylist, MusicAlbum) from <script type="application/ld+json">."""
    out = []

    def artist_of(node):
        by = node.get("byArtist") or node.get("author") or node.get("creator")
        if isinstance(by, list):
            by = by[0] if by else None
        return clean_text(by.get("name") if isinstance(by, dict) else by)

    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, list):
            for n in node:
                walk(n, depth + 1)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kind = " ".join(kind) if isinstance(kind, list) else str(kind or "")
            name = clean_text(node.get("name"))
            if name and "MusicAlbum" in kind:
                out.append({"artist": artist_of(node), "album": name, "raw": name, "song": False})
            elif name and "MusicRecording" in kind and artist_of(node):
                out.append({"artist": artist_of(node), "album": name, "raw": name, "song": True})
            for key in ("itemListElement", "item", "track", "tracks", "@graph", "mainEntity"):
                if key in node:
                    walk(node[key], depth + 1)
    for blob in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', source or ""):
        try:
            walk(json.loads(blob.strip()))
        except ValueError:
            continue
    return out


# -- reading lines -----------------------------------------------------------------------

def split_artist_album(line):
    """One line -> (artist, album, year) or None. Rules, in order: "Album by Artist", a quoted
    album after the artist, then "Artist - Album" (the first dash)."""
    text = clean_text(NUMBERING.sub("", line, count=1))
    year = ""
    m = YEAR_TAIL.search(text)
    if m:
        year, text = m.group(1), text[:m.start()].strip()
    if not text or len(text) > MAX_LINE or NOISE.match(text):
        return None
    m = QUOTED.match(text)
    if m:
        return m["artist"].strip(" ,:-–—"), m["album"].strip(), year
    text = text.strip(" \"“”")
    m = BY.match(text)
    if m and len(m["album"].split()) <= 10 and len(m["artist"].split()) <= 6:
        return m["artist"].strip(), m["album"].strip(" \"“”"), year
    parts = re.split(DASHES, text, maxsplit=1)
    if len(parts) == 2:
        artist, album = (p.strip(" \"“”") for p in parts)
        m = YEAR_TAIL.search(album)
        if m:
            year, album = m.group(1), album[:m.start()].strip()
        if artist and album and len(artist.split()) <= 7 and len(album.split()) <= 12 \
                and not YEAR_ONLY.match(artist) and not YEAR_ONLY.match(album) \
                and not ({artist.casefold(), album.casefold()} & HEADER_WORDS):
            return artist, album, year
    return None


def candidates_from_lines(lines, loose=False):
    """List/heading/table lines first; plain prose lines only when the page has no list at all
    (or ``loose``, for forum comments where each line is one answer)."""
    structured, plain = [], []
    for line in lines:
        short = len(line) <= MAX_LINE
        marked = bool(LIST_MARK.match(line))
        found = split_artist_album(line) if short else None
        if found:
            (structured if marked or loose else plain).append({"artist": found[0], "album": found[1], "year": found[2], "raw": line, "song": False})
    return structured or plain


def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = (match.match_key(item["artist"]), match.match_key(item["album"]))
        if key in seen or not all(key):
            continue
        seen.add(key)
        out.append(item)
    return out[:MAX_ITEMS]


def _q(value):
    return str(value).replace("\\", " ").replace('"', " ")


# -- the class -----------------------------------------------------------------------------

class PageList:
    def __init__(self, studio, session=None):
        self.studio = studio
        self.session = session or requests.Session()
        self.jobs = {}
        self.lock = threading.Lock()

    # fetching ---------------------------------------------------------------------------

    def _get(self, url, **kw):
        try:
            r = self.session.get(url, timeout=TIMEOUT, headers={**BROWSER, **kw.pop("headers", {})}, **kw)
        except requests.RequestException as error:
            raise PageError(f"Couldn't open that page ({error.__class__.__name__}). If the site blocks Tagcast, "
                            "copy the text from the page and use \"Paste the text instead\".") from None
        if r.status_code in (401, 403, 429):
            raise PageError("That site refused Tagcast. Copy the text from the page and use \"Paste the text instead\".")
        if not r.ok:
            raise PageError(f"That page answered HTTP {r.status_code}.")
        return r

    def _reddit(self, url):
        base = url.split("?")[0].rstrip("/")
        r = self._get(base + ".json", params={"limit": 500, "raw_json": 1}, headers={"Accept": "application/json"})
        try:
            data = r.json()
        except ValueError:
            raise PageError("Reddit didn't return the thread. Use \"Paste the text instead\".") from None
        lines = []

        def comments(node, depth=0):
            if depth > 12 or not isinstance(node, dict):
                return
            d = node.get("data") or {}
            for part in (d.get("title"), d.get("selftext"), d.get("body")):
                lines.extend(clean_text(x) for x in str(part or "").split("\n") if clean_text(x))
            kids = d.get("children")
            if isinstance(kids, list):
                for k in kids:
                    comments(k, depth + 1)
            replies = d.get("replies")
            if isinstance(replies, dict):
                comments(replies, depth + 1)
        for listing in data if isinstance(data, list) else [data]:
            comments(listing)
        return lines

    def _wikipedia(self, url):
        parts = urlparse(url)
        title = parts.path.split("/wiki/", 1)[-1]
        r = self._get(f"https://{parts.hostname}/w/api.php", params={
            "action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": 2, "redirects": 1})
        try:
            return (r.json().get("parse") or {})["text"]
        except (ValueError, KeyError):
            raise PageError("Wikipedia has no such page.") from None

    def _spotify(self, url):
        charts = self.studio.charts
        rows = charts._spotify(charts._spotify_id(url))
        return [{"artist": r["artist"].split(";")[0].strip(), "album": r["title"], "year": "", "raw": f"{r['artist']} - {r['title']}", "song": True}
                for r in rows]

    def collect(self, link="", pasted=""):
        """-> list of raw candidates {artist, album, year, raw, song}."""
        pasted = (pasted or "").strip()
        if pasted:
            lines = [clean_text(x) for x in pasted[:MAX_TEXT].split("\n")]
            return candidates_from_lines([x for x in lines if x], loose=len([x for x in lines if x]) <= 60)
        link = check_link(link)
        host = (urlparse(link).hostname or "").lower()
        if host.endswith("spotify.com"):
            return self._spotify(link)
        if host.endswith("reddit.com"):
            return candidates_from_lines(self._reddit(link), loose=True)
        if host.endswith("wikipedia.org") and "/wiki/" in link:
            source = self._wikipedia(link)
        else:
            source = self._get(link).text[:MAX_TEXT]
        found = ld_candidates(source) + candidates_from_lines(html_to_lines(source))
        return found

    # verifying ----------------------------------------------------------------------------

    def _mb(self, artist, album):
        data = self.studio.discography.http.json(f"{MB}/release-group", {
            "query": f'releasegroup:"{_q(album)}" AND artist:"{_q(artist)}"', "limit": 3, "fmt": "json"}) or {}
        for g in data.get("release-groups") or []:
            names = " ".join(c.get("name", "") for c in g.get("artist-credit") or [] if isinstance(c, dict))
            if int(g.get("score") or 0) >= 85 and match.match_key(g.get("title")) == match.match_key(album) \
                    and (match.match_key(artist) in match.match_key(names) or match.match_key(names) in match.match_key(artist)):
                credit = [c for c in g.get("artist-credit") or [] if isinstance(c, dict) and c.get("name")]
                return {"artist": match.clean(credit[0]["name"]) if credit else artist, "album": match.clean(g["title"]),
                        "year": (g.get("first-release-date") or "")[:4]}
        return None

    def _deezer(self, artist, album):
        data = self.studio.discography.http.json(f"{DEEZER}/search/album", {"q": f'artist:"{_q(artist)}" album:"{_q(album)}"', "limit": 5}) or {}
        for a in data.get("data") or []:
            if match.match_key(a.get("title")) == match.match_key(album) \
                    and match.match_key((a.get("artist") or {}).get("name")) == match.match_key(artist):
                return {"artist": match.clean(a["artist"]["name"]), "album": match.clean(a["title"]), "cover": a.get("cover_medium", ""),
                        "deezer_id": a.get("id")}
        return None

    def _album_of_song(self, artist, title):
        data = self.studio.discography.http.json(f"{DEEZER}/search", {"q": f'artist:"{_q(artist)}" track:"{_q(title)}"', "limit": 5}) or {}
        for t in data.get("data") or []:
            if match.match_key((t.get("artist") or {}).get("name")) == match.match_key(artist) and (t.get("album") or {}).get("title"):
                return match.clean(t["album"]["title"])
        return ""

    def resolve(self, item):
        """Fix spelling / fill year and cover. Sets item["state"] to "ok" or "unsure"."""
        item.update(state="unsure", cover=item.get("cover", ""))
        artist, album = item["artist"], item["album"]
        if item.get("song"):
            found = self._album_of_song(artist, album)
            if not found:
                item["album"] = ""
                return item
            album, item["song"] = found, False
            item["album"] = album
        for a, b in ((artist, album), (album, artist)):  # the second try fixes "Album - Artist"
            try:
                hit = self._mb(a, b)
                deezer = self._deezer(hit["artist"], hit["album"]) if hit else self._deezer(a, b)
            except SourceError as error:
                log.info("Checking %s - %s failed: %s", a, b, error)
                hit, deezer = None, None
            if hit or deezer:
                best = {**(deezer or {}), **(hit or {})}
                item.update(artist=best["artist"], album=best["album"], state="ok",
                            year=best.get("year") or item.get("year", ""), cover=(deezer or {}).get("cover", ""),
                            deezer_id=(deezer or {}).get("deezer_id"))
                break
        return item

    def _status(self, items):
        rows = [{"kind": "album", "artist": i["artist"], "title": i["album"]} for i in items if i["album"]]
        results = iter(self.studio.download_manager.check(rows)["results"]) if rows else iter(())
        for i in items:
            r = next(results) if i["album"] else {"status": "new", "you_have": []}
            i["status"], i["you_have"] = r["status"], r["you_have"]

    # jobs ----------------------------------------------------------------------------------

    def read(self, link="", pasted=""):
        if not (link or "").strip() and not (pasted or "").strip():
            raise PageError("Paste a page link, or the text of the page.")
        found = dedupe(self.collect(link, pasted))
        if not found:
            raise PageError("Couldn't find any \"Artist - Album\" lines on that page. "
                            "If the site hides its list from Tagcast, copy the text and use \"Paste the text instead\".")
        job = {"id": secrets.token_hex(6), "state": "running", "done": 0, "total": len(found), "items": [], "source": (link or "pasted text")[:200]}
        for i, f in enumerate(found):
            job["items"].append({"id": str(i), "artist": f["artist"], "album": f["album"], "year": f.get("year", ""), "raw": f["raw"],
                                 "song": bool(f.get("song")), "state": "checking", "cover": "", "status": "", "you_have": []})
        with self.lock:
            self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running"} | {job["id"]: job}
        threading.Thread(target=self._verify, args=(job,), daemon=True, name="page-list").start()
        return self.view(job["id"])

    def _verify(self, job):
        try:
            for item in job["items"]:
                try:
                    self.resolve(item)
                except Exception:
                    log.exception("Checking %s failed", item["raw"])
                    item["state"] = "unsure"
                job["done"] += 1
            self._finish(job)
        finally:
            job["state"] = "done"

    def _finish(self, job):
        usable = [i for i in job["items"] if i["album"]]
        self._status(usable)
        for i in job["items"]:
            if not i["album"]:
                i.update(status="new", you_have=[], state="unsure")

    def view(self, job_id):
        job = self.jobs.get(str(job_id))
        if not job:
            raise PageError("That page is no longer loaded. Read it again.")
        return {k: job[k] for k in ("id", "state", "done", "total", "items", "source")}

    def recheck(self, job_id, item_id, artist, album):
        job = self.jobs.get(str(job_id))
        item = next((i for i in (job or {}).get("items", []) if i["id"] == str(item_id)), None)
        if not item:
            raise PageError("That page is no longer loaded. Read it again.")
        artist, album = match.clean(artist), match.clean(album)
        if not artist or not album:
            raise PageError("Both the artist and the album are needed.")
        item.update(artist=artist, album=album, song=False)
        self.resolve(item)
        self._status([item])
        return item

    def get(self, job_id, ids):
        """Fetch the ticked albums: grouped per artist, through Discography's chain."""
        job = self.jobs.get(str(job_id))
        if not job:
            raise PageError("That page is no longer loaded. Read it again.")
        wanted = set(map(str, ids or []))
        chosen = [i for i in job["items"] if i["id"] in wanted and i["album"]]
        if not chosen:
            raise PageError("Tick at least one album.")
        by_artist = {}
        for i in chosen:
            by_artist.setdefault(i["artist"], []).append({"title": i["album"], "status": "partly" if i["status"] == "partly" else "missing",
                                                          "deezer_id": i.get("deezer_id")})
        jobs = [self.studio.discography.get(artist, albums)["id"] for artist, albums in by_artist.items()]
        return {"queued": len(chosen), "jobs": jobs}

    def get_status(self, job_ids):
        out = []
        for jid in job_ids or []:
            j = self.studio.discography.jobs.get(str(jid))
            if j:
                out.append({"id": j["id"], "name": j["name"], "state": j["state"], "done": j["done"], "total": j["total"], "log": j["log"]})
        return {"jobs": out}
