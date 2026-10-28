"""Hip-hop charts (fork v20), plus Browse: any country/genre (fork v22, see below).

A "Charts" page of outside hip-hop/rap charts, each with a "Get it" button next to every
song. A song is checked against your iBroadcast library and download history first, exactly
like everywhere else in Downloads -- nothing is fetched automatically.

Sources, grouped by what actually exists online:

- **Chinese hip-hop**: NetEase Cloud Music's official "中文说唱榜" (Chinese Rap chart,
  playlist id 991319590) -- the chart you pointed at. Songs carry a real NetEase track id,
  so "Get it" queues them straight through Galaxy, the same as a Galaxy search result.
- **Mainstream hip-hop**: HotNewHipHop's Top 100, Deezer's Hip-Hop/Rap chart (its genre id is
  looked up by name, never hardcoded, so a renumbering on Deezer's side can't silently pick
  the wrong genre), Apple Music/iTunes' Hip-Hop/Rap "Top Songs" feed, and a public Spotify playlist
  (RapCaviar by default; Browse takes any public playlist link). Shazam and Billboard were
  removed in v44: their pages no longer carry readable chart data. These give title + artist only (no platform id), so
  "Get it" goes through the same Bandcamp -> Galaxy -> Soulseek order as Discography's "Get
  the missing", one song at a time; songs not found anywhere land on the wishlist.
- **Underground hip-hop**: no platform publishes this as an actual, fetchable chart --
  everything calling itself one (Spotify/Apple editorial playlists, blog "best of" posts) is
  either locked behind that platform's own login or just prose with no song list. Nothing is
  listed under this group; it stays here as a note rather than a silently-missing feature.

HotNewHipHop and Spotify have no usable public API: their pages are read, and Spotify renders
its list with JavaScript, so this reads whatever structured data blob the
page ships for its own use (a Next.js ``__NEXT_DATA__`` script tag) rather than guessing at
rendered HTML. That is inherently more fragile than a real API -- if a site reshapes its page,
that one chart's fetch fails with a clear "couldn't read this chart" error rather than wrong
data, while the others keep working. State: none persisted beyond an in-memory cache per run.
"""

import json
import logging
import re
import threading
import time

import requests

from . import artist_match as match
from .library import LibraryError, text
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
CACHE_SECONDS = 6 * 3600
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tagcast-Charts/1.0"
NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
TITLE_KEYS = ("title", "trackTitle", "songTitle", "name", "track_title")
ARTIST_KEYS = ("artist", "artists", "artistName", "artist_name", "subtitle")
MAX_ROWS = 200


class ChartsError(LibraryError):
    pass


SPOTIFY_RAPCAVIAR = "37i9dQZF1DX0XUsuxWHRQd"  # Spotify's own hip-hop playlist, public
SPOTIFY_ID_RE = re.compile(r"(?:playlist[/:]|^)([A-Za-z0-9]{22})(?:[?/#].*)?$")

CHARTS = {
    "netease_cn_rap": {"label": "NetEase · Chinese Rap chart", "group": "chinese",
                        "url": "https://music.163.com/discover/toplist?id=991319590"},
    "hotnewhiphop_top100": {"label": "HotNewHipHop · Top 100", "group": "mainstream",
                             "url": "https://www.hotnewhiphop.com/top100"},
    "deezer_hiphop": {"label": "Deezer · Hip-Hop/Rap chart", "group": "mainstream",
                       "url": "https://www.deezer.com/charts"},
    "apple_hiphop": {"label": "Apple Music · Top Hip-Hop/Rap songs", "group": "mainstream",
                      "url": "https://music.apple.com/us/genre/hip-hop-rap/id18"},
    "spotify_rapcaviar": {"label": "Spotify · RapCaviar", "group": "mainstream",
                           "url": f"https://open.spotify.com/playlist/{SPOTIFY_RAPCAVIAR}"},
}

# -- Browse (fork v22): any country, any genre -------------------------------------------
#
# The three fixed tabs above stay exactly as they are. "Browse" sits alongside them and
# lets you pick a country and/or genre, but each source can only offer what it actually
# supports -- none of this is guessed, it follows what was confirmed by hand:
#
# - **Apple Music**: country only. Its RSS feed takes any 2-letter country code in the URL
#   path (confirmed -- this is the same feed the "Apple Music" tab above already uses, just
#   with "us" swapped out), but Apple publishes no list anywhere of which numeric genre ids
#   mean what, and testing shows genre filtering on this exact feed may not even work beyond
#   the Hip-Hop/Rap id (18) already in use -- so genre stays fixed to that rather than risk
#   silently showing the wrong genre under a confident-looking label.
# - **Spotify**: any public playlist link (a hip-hop Top 50, say), read from the playlist's
#   embed page. No login; a private playlist or a page change comes back as a plain error.
# - **Deezer**: genre only (reusing the same by-name genre lookup the "Deezer" tab above
#   uses, so any genre Deezer lists -- pop, k-pop, rock, electro, and so on -- works, not
#   just hip-hop). Deezer's public API has no per-country chart at all -- the only way
#   anyone gets one is an undocumented, per-country Deezer-curated playlist id that could
#   change without notice, which isn't a foundation worth building on.
def _extract_next_data(html, chart_label):
    m = NEXT_DATA_RE.search(html or "")
    if not m:
        raise ChartsError(f"Couldn't read {chart_label} -- the site may have changed its layout.")
    try:
        return json.loads(m.group(1))
    except ValueError:
        raise ChartsError(f"Couldn't read {chart_label} -- the site may have changed its layout.") from None


def _artist_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "; ".join(dict.fromkeys(a.get("name", "") if isinstance(a, dict) else str(a) for a in value
                                        if (a.get("name") if isinstance(a, dict) else str(a))))
    if isinstance(value, dict):
        return text(value.get("name"))
    return ""


def _walk_entries(node, out, seen, limit=MAX_ROWS):
    if len(out) >= limit or id(node) in seen:
        return
    if isinstance(node, dict):
        seen.add(id(node))
        title = next((text(node[k]).strip() for k in TITLE_KEYS if isinstance(node.get(k), str) and node[k].strip()), "")
        artist = _artist_text(next((node[k] for k in ARTIST_KEYS if node.get(k)), None))
        if title and artist:
            out.append({"title": title, "artist": artist})
        for value in node.values():
            _walk_entries(value, out, seen, limit)
    elif isinstance(node, list):
        for item in node:
            _walk_entries(item, out, seen, limit)


def _spotify_rows(data):
    """Songs from a Spotify embed page's data: its trackList (title + "Artist A, Artist B"),
    or, if Spotify renames that, whatever title/artist pairs the page carries."""
    tracks = []

    def find(node, depth=0):
        if depth > 8 or tracks:
            return
        if isinstance(node, dict):
            if isinstance(node.get("trackList"), list):
                tracks.extend(node["trackList"])
                return
            for value in node.values():
                find(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                find(item, depth + 1)

    find(data)
    rows = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        title = text(track.get("title") or track.get("name")).strip()
        artist = _artist_text(track.get("artists") or track.get("subtitle") or "")
        artist = "; ".join(a.strip() for a in re.split(r",\s*|\u00a0,\s*", artist.replace("\u00a0", " ")) if a.strip())
        if title and artist:
            rows.append({"title": title, "artist": artist})
    if not rows:
        found = []
        _walk_entries(data, found, set())
        rows = found
    return _dedup(rows)


def _dedup(rows):
    out, seen = [], set()
    for r in rows:
        key = (match.match_key(r["title"]), match.match_key(r["artist"]))
        if key not in seen and r["title"] and r["artist"]:
            seen.add(key)
            out.append(r)
    return out


class Charts:
    def __init__(self, studio, http=None, session=None):
        self.studio = studio
        self.http = http or Http(PACING)
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.lock = threading.RLock()
        self.cache = {}  # chart_id -> {"rows": [...], "when": ts, "error": ""}
        self.progress = {}  # chart_id -> {"done", "total", "title"} while "Get it" runs on several rows
        self.jobs = []  # recent per-row download results, most recent first

    # -- reading -------------------------------------------------------------------------

    def list(self):
        return {key: {"label": c["label"], "group": c["group"], "url": c["url"]} for key, c in CHARTS.items()}

    def browse_options(self):
        return {
            "apple": {"label": "Apple Music", "needs_country": True, "needs_genre": False,
                      "note": 'Country only -- type any 2-letter code, like "th" or "jp". Genre stays '
                              "Hip-Hop/Rap: Apple publishes no reliable list of other genre ids to pick from."},
            "deezer": {"label": "Deezer", "needs_country": False, "needs_genre": True,
                       "note": 'Genre only -- type any genre Deezer lists, like "pop", "k-pop" or "rock". '
                               "Deezer's API has no per-country charts."},
            "spotify": {"label": "Spotify playlist", "needs_country": False, "needs_genre": True,
                        "genre_label": "Playlist link", "genre_placeholder": "https://open.spotify.com/playlist/…",
                        "note": "Paste the link of any public Spotify playlist, such as a hip-hop Top 50. "
                                "No Spotify login is needed; private playlists cannot be read."},
        }

    def view(self, chart_id, refresh=False):
        if chart_id not in CHARTS:
            raise LibraryError("Unknown chart.")
        with self.lock:
            hit = self.cache.get(chart_id)
            fresh = hit and not refresh and hit["when"] > time.time() - CACHE_SECONDS
        if not fresh:
            self._load(chart_id)
        with self.lock:
            hit = self.cache.get(chart_id, {"rows": [], "when": 0, "error": ""})
            return {"chart": chart_id, "label": CHARTS[chart_id]["label"], "url": CHARTS[chart_id]["url"],
                    "rows": hit["rows"], "when": hit["when"], "error": hit["error"], "progress": self.progress.get(chart_id)}

    def _load(self, chart_id):
        fetchers = {"netease_cn_rap": self._netease, "hotnewhiphop_top100": self._hotnewhiphop,
                    "deezer_hiphop": self._deezer, "apple_hiphop": self._apple,
                    "spotify_rapcaviar": lambda: self._spotify(SPOTIFY_RAPCAVIAR)}
        try:
            rows = fetchers[chart_id]()
            rows = self._with_status(rows)
            error = ""
        except (LibraryError, SourceError, requests.RequestException, ValueError) as err:
            rows, error = [], str(err)
            log.info("Chart %s failed: %s", chart_id, err)
        with self.lock:
            self.cache[chart_id] = {"rows": rows, "when": time.time(), "error": error}

    # -- Browse: any country/genre, where each source actually supports it ---------------

    def browse_view(self, source, country="", genre="", refresh=False):
        chart_id, label, url, fetch = self._browse_spec(source, country, genre)
        with self.lock:
            hit = self.cache.get(chart_id)
            fresh = hit and not refresh and hit["when"] > time.time() - CACHE_SECONDS
        if not fresh:
            self._load_browse(chart_id, fetch)
        with self.lock:
            hit = self.cache.get(chart_id, {"rows": [], "when": 0, "error": ""})
            return {"chart": chart_id, "label": label, "url": url,
                    "rows": hit["rows"], "when": hit["when"], "error": hit["error"], "progress": self.progress.get(chart_id)}

    def browse_get(self, source, country, genre, ranks):
        chart_id, *_ = self._browse_spec(source, country, genre)
        return self.get(chart_id, ranks)

    def _load_browse(self, chart_id, fetch):
        try:
            rows = fetch()
            rows = self._with_status(rows)
            error = ""
        except (LibraryError, SourceError, requests.RequestException, ValueError) as err:
            rows, error = [], str(err)
            log.info("Browse chart %s failed: %s", chart_id, err)
        with self.lock:
            self.cache[chart_id] = {"rows": rows, "when": time.time(), "error": error}

    def _browse_spec(self, source, country, genre):
        source = text(source).strip().casefold()
        country = text(country).strip()
        genre = text(genre).strip()
        if source == "apple":
            code = (country or "us").casefold()
            if not re.fullmatch(r"[a-z]{2}", code):
                raise ChartsError('Use a 2-letter country code, like "us", "gb" or "th".')
            url = f"https://itunes.apple.com/{code}/rss/topsongs/limit=100/genre=18/json"
            return (f"browse:apple:{code}", f"Apple Music · Top Hip-Hop/Rap songs ({code.upper()})", url,
                    lambda: self._apple_browse(code))
        if source == "spotify":
            playlist = self._spotify_id(genre)
            return (f"browse:spotify:{playlist}", f"Spotify · playlist {playlist}",
                    f"https://open.spotify.com/playlist/{playlist}", lambda: self._spotify(playlist))
        if source == "deezer":
            if not genre:
                raise ChartsError('Type a genre, like "pop", "k-pop" or "rock".')
            return (f"browse:deezer:{genre.casefold()}", f"Deezer · {genre} chart", "https://www.deezer.com/charts",
                    lambda: self._deezer_browse(genre))
        raise ChartsError("Unknown source.")

    def _with_status(self, rows):
        if not rows:
            return rows
        manager = self.studio.download_manager
        try:
            self.studio._require_client()
            results = manager.check([{"kind": "song", "artist": r["artist"], "title": r["title"]} for r in rows])["results"]
        except LibraryError:
            results = [{"status": "new"}] * len(rows)
        for row, result in zip(rows, results):
            row["status"] = result["status"]
        return rows

    # -- fetching one chart ------------------------------------------------------------

    def _netease(self):
        url = "https://music.163.com/api/playlist/detail?id=991319590"
        try:
            r = self.session.get(url, headers={"Referer": "https://music.163.com/"}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as error:
            raise ChartsError(f"Couldn't reach NetEase's chart: {error}") from None
        result = data.get("result") or data.get("playlist") or {}
        tracks = result.get("tracks") or []
        if not tracks:
            raise ChartsError("NetEase's chart came back empty -- it may require a different endpoint now.")
        rows = []
        for i, t in enumerate(tracks[:MAX_ROWS], 1):
            if not isinstance(t, dict) or not t.get("id"):
                continue
            artists = t.get("artists") or t.get("ar") or []
            album = t.get("album") or t.get("al") or {}
            name = text(t.get("name") or t.get("title"))
            if not name:
                continue
            rows.append({"rank": i, "title": name, "artist": _artist_text(artists) or "Unknown Artist",
                        "album": text(album.get("name")), "source": "netease", "source_id": str(t["id"]),
                        "pic_id": text(album.get("picId") or album.get("pic") or album.get("id"))})
        return rows

    def _hotnewhiphop(self):
        try:
            r = self.session.get(CHARTS["hotnewhiphop_top100"]["url"], timeout=15)
            r.raise_for_status()
            html = r.text
        except requests.RequestException as error:
            raise ChartsError(f"Couldn't reach HotNewHipHop: {error}") from None
        pattern = re.compile(
            r'href="/\d+-[a-z0-9-]+"[^>]*>(?:<[^>]*>)*\s*([^<]{1,200}?)\s*</a>.{0,400}?'
            r'href="/artist/[a-z0-9-]+"[^>]*>(?:<[^>]*>)*\s*([^<]{1,200}?)\s*</a>', re.S | re.I)
        rows = [{"rank": i + 1, "title": text(title), "artist": text(artist), "album": "", "source": "", "source_id": ""}
                for i, (title, artist) in enumerate(pattern.findall(html)[:MAX_ROWS])]
        if not rows:
            raise ChartsError("Couldn't read HotNewHipHop's Top 100 -- the page layout may have changed.")
        return _dedup(rows)

    def _deezer(self):
        try:
            genres = (self.http.json("https://api.deezer.com/genre") or {}).get("data") or []
        except SourceError as error:
            raise ChartsError(f"Deezer's genre list didn't answer: {error}") from None
        genre = next((g for g in genres if "hip hop" in text(g.get("name")).casefold()
                     or "rap" in text(g.get("name")).casefold()), None)
        if not genre:
            raise ChartsError("Deezer doesn't list a Hip-Hop/Rap genre right now.")
        try:
            chart = self.http.json(f"https://api.deezer.com/chart/{genre['id']}") or {}
        except SourceError as error:
            raise ChartsError(f"Deezer's chart didn't answer: {error}") from None
        tracks = (chart.get("tracks") or {}).get("data") or []
        return [{"rank": i + 1, "title": text(t.get("title")), "artist": text((t.get("artist") or {}).get("name")),
                "album": text((t.get("album") or {}).get("title")), "source": "", "source_id": ""}
                for i, t in enumerate(tracks[:MAX_ROWS]) if t.get("title")]

    def _apple(self):
        try:
            feed = (self.http.json("https://itunes.apple.com/us/rss/topsongs/limit=100/genre=18/json") or {}).get("feed") or {}
        except SourceError as error:
            raise ChartsError(f"Apple Music's feed didn't answer: {error}") from None
        entries = feed.get("entry") or []
        rows = []
        for i, e in enumerate(entries[:MAX_ROWS]):
            title = text((e.get("im:name") or {}).get("label"))
            artist = text((e.get("im:artist") or {}).get("label"))
            if title and artist:
                rows.append({"rank": i + 1, "title": title, "artist": artist, "album": "", "source": "", "source_id": ""})
        if not rows:
            raise ChartsError("Apple Music's Hip-Hop/Rap feed came back empty.")
        return rows

    def _apple_browse(self, code):
        try:
            feed = (self.http.json(f"https://itunes.apple.com/{code}/rss/topsongs/limit=100/genre=18/json") or {}).get("feed") or {}
        except SourceError as error:
            raise ChartsError(f"Apple Music's feed didn't answer: {error}") from None
        entries = feed.get("entry") or []
        rows = []
        for i, e in enumerate(entries[:MAX_ROWS]):
            title = text((e.get("im:name") or {}).get("label"))
            artist = text((e.get("im:artist") or {}).get("label"))
            if title and artist:
                rows.append({"rank": i + 1, "title": title, "artist": artist, "album": "", "source": "", "source_id": ""})
        if not rows:
            raise ChartsError(f'Apple Music has no Hip-Hop/Rap "Top Songs" feed for "{code}", or it came back empty.')
        return rows

    def _deezer_browse(self, genre_name):
        try:
            genres = (self.http.json("https://api.deezer.com/genre") or {}).get("data") or []
        except SourceError as error:
            raise ChartsError(f"Deezer's genre list didn't answer: {error}") from None
        needle = genre_name.casefold()
        genre = next((g for g in genres if needle in text(g.get("name")).casefold()), None)
        if not genre:
            names = ", ".join(sorted({text(g.get("name")) for g in genres if text(g.get("name")) and text(g.get("name")) != "All"}))
            raise ChartsError(f'Deezer doesn\'t list a "{genre_name}" genre. It has: {names}.')
        try:
            chart = self.http.json(f"https://api.deezer.com/chart/{genre['id']}") or {}
        except SourceError as error:
            raise ChartsError(f"Deezer's chart didn't answer: {error}") from None
        tracks = (chart.get("tracks") or {}).get("data") or []
        rows = [{"rank": i + 1, "title": text(t.get("title")), "artist": text((t.get("artist") or {}).get("name")),
                "album": text((t.get("album") or {}).get("title")), "source": "", "source_id": ""}
                for i, t in enumerate(tracks[:MAX_ROWS]) if t.get("title")]
        if not rows:
            raise ChartsError(f"Deezer's {text(genre.get('name'))} chart came back empty.")
        return rows

    @staticmethod
    def _spotify_id(link):
        m = SPOTIFY_ID_RE.search(text(link).strip())
        if not m:
            raise ChartsError("Paste the link of a public Spotify playlist, like https://open.spotify.com/playlist/….")
        return m.group(1)

    def _spotify(self, playlist_id):
        """A public Spotify playlist read from its embed page -- no login, no API key. Spotify
        closed its Web API to Spotify-owned playlists in November 2024, but the embed page that
        open.spotify.com serves for sharing still ships the track list for its own use."""
        url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
        try:
            r = self.session.get(url, timeout=20, headers={"Accept-Language": "en"})
            r.raise_for_status()
            html = r.text
        except requests.RequestException as error:
            raise ChartsError(f"Couldn't reach Spotify: {error}") from None
        data = _extract_next_data(html, "this Spotify playlist")
        rows = _spotify_rows(data)
        if not rows:
            raise ChartsError("Couldn't find any songs in this Spotify playlist -- it may be private, "
                              "empty, or Spotify changed its page.")
        return [{"rank": i + 1, **row, "album": "", "source": "", "source_id": ""} for i, row in enumerate(rows)]

    # -- getting songs -------------------------------------------------------------------

    def get(self, chart_id, ranks):
        """"Get it": queue the chosen rows. NetEase rows go straight through Galaxy (a real
        track id); everything else tries Bandcamp -> Galaxy -> Soulseek, one at a time."""
        with self.lock:
            hit = self.cache.get(chart_id)
        if not hit or not hit["rows"]:
            raise LibraryError("Scan this chart first.")
        wanted = set(ranks or [])
        rows = [r for r in hit["rows"] if r["rank"] in wanted]
        if not rows:
            raise LibraryError("Choose at least one song.")
        netease = [r for r in rows if r.get("source") == "netease"]
        other = [r for r in rows if r.get("source") != "netease"]
        result = {"queued": 0, "jobs": 0}
        if netease:
            tracks = [{"id": r["source_id"], "title": r["title"], "source": "netease",
                      "artists": [a.strip() for a in r["artist"].split(";") if a.strip()],
                      "album": r.get("album", ""), "pic_id": r.get("pic_id", "")} for r in netease]
            sent = self.studio.galaxy.add(tracks)
            result["queued"] += sent.get("queued", 0) + sent.get("held", 0)
        if other:
            with self.lock:
                if self.progress.get(chart_id):
                    raise LibraryError("Still getting songs from this chart.")
                self.progress[chart_id] = {"done": 0, "total": len(other), "title": ""}
            threading.Thread(target=self._get_others, args=(chart_id, other), name="charts-get", daemon=True).start()
            result["jobs"] = len(other)
        return result

    def _get_others(self, chart_id, rows):
        ctx = {}
        discography = self.studio.discography
        try:
            for row in rows:
                self.progress[chart_id]["title"] = row["title"]
                artist_ctx = ctx.setdefault(match.match_key(row["artist"]), {"bandcamp": None, "bandcamp_checked": False})
                try:
                    source, note = discography._song(row["artist"], row["title"], row.get("album", ""), artist_ctx)
                except Exception as error:
                    source, note = "", str(error)
                if not source:
                    note = discography._wish("song", row["artist"], row["title"], row.get("album", ""), "missing", note)
                with self.lock:
                    self.jobs.insert(0, {"title": row["title"], "artist": row["artist"], "source": source, "note": note})
                    del self.jobs[200:]
                self.progress[chart_id]["done"] += 1
        finally:
            with self.lock:
                self.progress.pop(chart_id, None)

    def jobs_view(self):
        with self.lock:
            return {"jobs": list(self.jobs[:50]), "progress": dict(self.progress)}
