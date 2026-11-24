"""Discography, Top 30 and "get the missing" (fork v15).

For an artist, Tagcast combines Deezer (one search + the album list) and Apple Music / iTunes
(one search + one lookup) into one discography: albums, EPs, singles and compilations with year,
track count and cover. MusicBrainz is added only when asked. Results are cached for 7 days in
``discography_cache.json``. Every release shows Have / Partly / Downloaded before / Missing against
the iBroadcast library and the download history.

Top 30 songs come from Deezer's ranking (with 30-second previews); when Deezer is unavailable,
Galaxy's search order is used. Each song shows Have / Missing.

Chinese names (v70) also get NetEase's album list (QQ Music when NetEase has nothing) and NetEase's hot
songs, and "get the missing" tries Galaxy before YouTube for them.

"Get the missing" runs in the background and tries the sources in the agreed order:
**Galaxy → Bandcamp → Soulseek** (fork v54; v45-v53 were Galaxy → Bandcamp → Soulseek).
Galaxy tries every way (album, song title, the artist's songs, traditional and simplified Chinese); then
Bandcamp when the artist has the album there; if both have nothing, Soulseek
when Sockseek is set up (an album that is only partly owned skips Soulseek, which would fetch the
whole album again); a Soulseek job that fails goes on the wishlist. Every song still passes the
duplicate check, so anything you may have waits in Needs your decision.
"""

import logging
import re
import secrets
import threading
import time
from collections import Counter

from . import activity, featured
from . import artist_match as match
from .downloads import FEAT_BRACKET, album_key
from .galaxy import FALLBACK_SOURCES, GalaxyBlocked, find_song, sim
from .library import LibraryError
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
NOT_FOUND = "Not found on Galaxy, Bandcamp, SoundCloud or Soulseek"
LINE_FILE = "search_line.json"   # fork v114: the searches still waiting, kept across a restart
SEARCH_SOURCES = ("galaxy", "bandcamp", "soulseek", "youtube", "soundcloud", "spotify")
JOB_GAP = 2.5            # fork v113: seconds between two queued "get" jobs (the page list starts one per artist)
GALAXY_PAUSE = 600.0   # after GD Studio blocks a request, Galaxy is skipped this long (fork v54)

# fork v103: same word list as static/galaxy.js's galaxyInstrumentalWords - a bonus instrumental,
# backing-track or karaoke version bundled into an otherwise ordinary album/single. The manual
# "Download album" button in the browser already leaves these out (galaxyWanted() in galaxy.js);
# this is the same thing for the automatic "Get missing album" path, which had no such check at all.
INSTRUMENTAL_WORDS = re.compile(
    r"strumental|karaoke|off[\s-]?vocal|backing[\s-]?track|伴奏|纯音乐|純音樂|无人声|無人聲|消音|卡拉\s?ok", re.I)


def _looks_like_a_song(title):
    """fork v121: a release name with a "feat." part or a stray quote mark (lists copied from articles:
    “Bad and Boujee” (feat. Lil Uzi Vert)) names a song, not an album."""
    title = str(title or "")
    return bool(re.search(r"(?i)[(\[]\s*(feat\.?|ft\.?|featuring|with)\s", title) or re.search(r"[“”\"]", title))


def _song_title(title):
    """fork v122: the song's own name from an article-style title: quote marks and "(feat. …)"/"ft."/"with" parts go,
    brackets that are part of the name stay: “B.M.F. (Blowin’ Money Fast)” (feat. Styles P) → B.M.F. (Blowin’ Money Fast)."""
    title = re.sub(r"(?i)\s*[(\[]\s*(feat\.?|ft\.?|featuring|with)\s[^)\]]*[)\]]", "", str(title or ""))
    title = re.sub(r"(?i)\s+(feat\.?|ft\.?|featuring)\s.*$", "", title)
    title = re.sub(r"[“”\"]", "", title)
    return " ".join(title.split()).strip(" -–")


def _only_theirs(tracks, name, album_artist):
    """fork v109: another artist's album (B's) that the artist you asked for (A) is only featured on:
    keep just A's songs, not the whole of B's album. Returns (tracks, note for the log)."""
    if not name or featured._same(album_artist, name):
        return tracks, ""
    mine = [t for t in tracks if featured.credits(name, t.get("title", ""), t.get("artists") or [])]
    if not mine or len(mine) == len(tracks):
        return tracks, ""
    skipped = len(tracks) - len(mine)
    return mine, f" (only {name}'s {len(mine)} of {len(tracks)} songs on {album_artist}'s album; {skipped} skipped)"


def _not_instrumental(tracks):
    kept = [t for t in tracks if not INSTRUMENTAL_WORDS.search(str(t.get("title") or ""))]
    return kept or tracks  # never leave nothing to download because every title happened to match
_galaxy_block = {"until": 0.0}
CACHE_FILE = "discography_cache.json"
CACHE_DAYS = 7
DEEZER = "https://api.deezer.com"
ITUNES = "https://itunes.apple.com"
MB = "https://musicbrainz.org/ws/2"
TYPES = {"album": "Album", "ep": "EP", "single": "Single", "compile": "Compilation"}
SUFFIX = re.compile(r"\s+-\s+(single|ep)$", re.I)
GUEST_CHECK_MAX = 25  # fork v99: at most this many fresh Deezer tracklist fetches per load
GUEST_MIN_TRACKS = 6  # fewer tracks than this isn't worth a various-artists check


class DiscographyError(LibraryError):
    pass


def _year(value):
    m = re.match(r"(\d{4})", str(value or ""))
    return m.group(1) if m else ""


def release_key(title):
    """v67: "(feat. X)" does not make another release ("The Rise in Fall (feat. Neroche)" = "The Rise in Fall")."""
    return album_key(FEAT_BRACKET.sub("", SUFFIX.sub("", match.clean(title))))


NETEASE = "https://music.163.com"
QQ = "https://c.y.qq.com"
# v70: NetEase answers a browser, not a script: a browser's name and the site as referrer.
NETEASE_HEADERS = {"Referer": "https://music.163.com/",
                   "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
NETEASE_TYPES = {"专辑": "album", "EP": "ep", "EP/Single": "ep", "Single": "single", "精选集": "compile", "合集": "compile"}
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def chinese_name(name):
    """An artist name written with Chinese characters (no Japanese kana or Korean hangul): NetEase first (v70)."""
    return bool(HAS_CJK.search(str(name or ""))) and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", str(name or ""))


def name_parts(name):
    """({Chinese parts}, {Latin parts}) as match keys: "派克特Pact" and "派克特（PACT）" both give ({派克特}, {pact})."""
    value = re.sub(r"[（）()【】\[\]]", " ", str(name or ""))
    parts = match.chunks(value)
    cjk = {match.match_key(p) for p in parts if HAS_CJK.search(p) and match.match_key(p)}
    latin = {match.match_key(p) for p in parts if not HAS_CJK.search(p) and match.match_key(p)}
    return cjk, latin


def same_chinese_artist(mine, names):
    """v70: the same artist across spellings: the Chinese parts agree, and the Latin parts too when both have one."""
    my_cjk, my_latin = name_parts(mine)
    for other in names:
        if not other:
            continue
        if match.match_key(other) == match.match_key(mine):
            return True
        cjk, latin = name_parts(other)
        if my_cjk and my_cjk & cjk and (not my_latin or not latin or my_latin & latin):
            return True
    return False


DISCOGS = "https://api.discogs.com"
DISCOGS_NUMBER = re.compile(r"\s*\(\d+\)$")  # "Neroche (2)": Discogs' mark for a second artist of that name


def discogs_name(value):
    return DISCOGS_NUMBER.sub("", str(value or "").replace("*", "")).strip()


def discogs_kind(row):
    if row.get("role") and row["role"] != "Main":
        return "compile"  # appearances and features: shown with Compilations, off by default
    fmt = str(row.get("format") or "")
    if re.search(r"\bComp\b|Compilation", fmt):
        return "compile"
    if re.search(r"\bEP\b", fmt) or re.search(r"\bE\.?P\.?$", str(row.get("title") or ""), re.I):
        return "ep"
    if re.search(r"\bSingle\b", fmt):
        return "single"
    return "album"


class SearchStopped(BaseException):
    """fork v114: "Stop search" was pressed; unwinds the running search without it counting as a failed item."""


class Discography:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(CACHE_FILE)
        self.cache = data if isinstance(data, dict) else {}
        self.jobs = {}
        self.search_paused = False    # fork v114: "Pause search" holds every job before its next item
        self.skipped = set()          # fork v114: sources switched off for searching
        self.restored = 0             # fork v114: searches loaded back after a restart
        self._stop_gen = 0
        self._line_lock = threading.Lock()
        self._turns = []              # fork v113: "get" jobs wait here and run one at a time, in order
        self._turns_lock = threading.Condition()
        self._chinese_artists = {}  # name -> NetEase artist ({} = not there), this session only (v70)
        soulseek = getattr(studio, "soulseek", None)
        if soulseek is not None:
            soulseek.on_failed = self.fallback
        galaxy = getattr(studio, "galaxy", None)
        if galaxy is not None:
            galaxy.on_failed = self.galaxy_fallback
        youtube = getattr(studio, "youtube", None)
        if youtube is not None:
            youtube.on_failed = self.youtube_fallback
        more = getattr(studio, "moresources", None)
        if more is not None:
            more.on_failed = self.youtube_fallback  # fork v96: a Spotify song that yt-dlp cannot serve
        self._load_line()

    @property
    def manager(self):
        return self.studio.download_manager

    def _save_cache(self):
        if len(self.cache) > 300:
            for key in sorted(self.cache, key=lambda k: self.cache[k].get("when", 0))[:len(self.cache) - 300]:
                self.cache.pop(key)
        self.studio._write(CACHE_FILE, self.cache)

    def _cached(self, key, build):
        hit = self.cache.get(key)
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400:
            return hit["data"]
        data = build()
        if not data.get("notes"):  # only complete answers are kept
            self.cache[key] = {"when": time.time(), "data": data}
            self._save_cache()
        return data

    # -- collecting ---------------------------------------------------------------------------

    def _deezer_artists(self, name):
        data = self.http.json(f"{DEEZER}/search/artist", {"q": name, "limit": 15}) or {}
        return [{"id": a["id"], "name": a.get("name", ""), "picture": a.get("picture_medium", ""),
                 "fans": a.get("nb_fan", 0), "albums": a.get("nb_album", 0)}
                for a in data.get("data") or [] if match.match_key(a.get("name")) == match.match_key(name)]

    def _library_titles(self, name):
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return set()
        key, out = match.match_key(name), set()
        for album_id in library.album_ids():
            album = library.albums[album_id]
            if any(match.match_key(library.artist_name(i)) == key for i in library.album_artist_ids(album)):
                out.add(release_key(album.get("name")))
        return out

    def _collect(self, name, deezer_id=None):
        notes, candidates, chosen = [], [], None
        releases = {}
        try:
            candidates = self._deezer_artists(name)
        except SourceError as error:
            notes.append(f"Deezer {error}")
        deezer_albums = {}
        for c in candidates[:4] if not deezer_id else [c for c in candidates if str(c["id"]) == str(deezer_id)][:1]:
            try:
                rows = (self.http.json(f"{DEEZER}/artist/{c['id']}/albums", {"limit": 200}) or {}).get("data") or []
            except SourceError as error:
                notes.append(f"Deezer {error}")
                rows = []
            deezer_albums[c["id"]] = rows
        mine = self._library_titles(name)
        if deezer_albums:
            # Same-name artists: the one with most of your albums, else the most fans.
            best = max(deezer_albums, key=lambda i: (len({release_key(a.get("title")) for a in deezer_albums[i]} & mine),
                                                    next(c["fans"] for c in candidates if c["id"] == i)))
            chosen = next(c for c in candidates if c["id"] == best)
            for a in deezer_albums[best]:
                key = release_key(a.get("title"))
                if not key:
                    continue
                releases.setdefault(key, {"title": match.clean(a.get("title")), "year": _year(a.get("release_date")),
                                          "date": str(a.get("release_date") or "")[:10],
                                          "type": a.get("record_type") or "album", "tracks": None,
                                          "cover": a.get("cover_medium", ""), "deezer_id": a.get("id"), "sources": []})
                releases[key]["sources"].append("Deezer")
        try:
            apple = self._apple(name, set(releases))
        except SourceError as error:
            notes.append(f"Apple Music {error}")
            apple = []
        for a in apple:
            raw = match.clean(a.get("collectionName"))
            key = release_key(raw)
            if not key:
                continue
            kind = "single" if raw.lower().endswith(" - single") else "ep" if raw.lower().endswith(" - ep") else \
                "compile" if a.get("collectionType") == "Compilation" else "album"
            entry = releases.setdefault(key, {"title": SUFFIX.sub("", raw), "year": _year(a.get("releaseDate")), "type": kind,
                                              "tracks": None, "cover": "", "deezer_id": None, "sources": []})
            entry["tracks"] = entry["tracks"] or a.get("trackCount")
            entry["year"] = entry["year"] or _year(a.get("releaseDate"))
            entry["date"] = entry.get("date") or str(a.get("releaseDate") or "")[:10]
            entry["cover"] = entry["cover"] or str(a.get("artworkUrl100") or "").replace("100x100", "250x250")
            entry["genre"] = a.get("primaryGenreName", "")
            if "Apple Music" not in entry["sources"]:
                entry["sources"].append("Apple Music")
        if chinese_name(name):
            self._add_chinese(name, releases, notes)
        return {"name": name, "artist": chosen, "candidates": candidates[:6], "releases": list(releases.values()), "notes": notes}

    # -- Chinese artists: NetEase, then QQ Music (v70) --------------------------------------------

    def _netease_artist(self, name):
        """NetEase's artist for this name ({"id", "name"}), matched on the Chinese and Latin parts; None if not there."""
        cached = self._chinese_artists.get(match.match_key(name))
        if cached is not None:
            return cached or None
        found = []
        cjk, _ = name_parts(name)
        for query in dict.fromkeys([name, *sorted(cjk)]):
            data = self.http.json(f"{NETEASE}/api/cloudsearch/pc", {"s": query, "type": 100, "limit": 10}, headers=NETEASE_HEADERS) or {}
            for a in (data.get("result") or {}).get("artists") or []:
                names = [a.get("name"), *(a.get("alias") or []), a.get("trans"), *(a.get("transNames") or [])]
                if a.get("id") and same_chinese_artist(name, names) and all(f["id"] != a["id"] for f in found):
                    found.append({"id": a["id"], "name": match.clean(a.get("name")), "albums": a.get("albumSize") or 0})
            if found:
                break
        best = max(found, key=lambda a: a["albums"]) if found else None
        self._chinese_artists[match.match_key(name)] = best or {}
        return best

    def _netease_releases(self, artist):
        rows, offset = [], 0
        for _ in range(3):
            data = self.http.json(f"{NETEASE}/api/artist/albums/{artist['id']}", {"offset": offset, "limit": 100},
                                  headers=NETEASE_HEADERS) or {}
            rows += data.get("hotAlbums") or []
            if not data.get("more"):
                break
            offset += 100
        out = []
        for a in rows:
            own = (a.get("artist") or {}).get("id") == artist["id"]
            kind = NETEASE_TYPES.get(str(a.get("type") or ""), "album") if own else "compile"
            date = time.strftime("%Y-%m-%d", time.gmtime((a.get("publishTime") or 0) / 1000)) if a.get("publishTime") else ""
            out.append({"title": match.clean(a.get("name")), "year": date[:4], "date": date, "type": kind,
                        "tracks": a.get("size") or None, "cover": f"{a['picUrl']}?param=250y250" if a.get("picUrl") else "",
                        "netease_id": a.get("id")})
        return out

    def _qq_releases(self, name):
        data = self.http.json(f"{QQ}/soso/fcgi-bin/client_search_cp", {"w": name, "format": "json", "t": 8, "n": 60},
                              headers={"Referer": "https://y.qq.com/"}) or {}
        out = []
        for a in ((data.get("data") or {}).get("album") or {}).get("list") or []:
            singers = [s.get("name") for s in a.get("singer_list") or []] or [a.get("singerName")]
            if not same_chinese_artist(name, singers):
                continue
            count = a.get("song_count") or 0
            kind = "single" if 0 < count <= 3 else "ep" if 0 < count <= 6 else "album"
            out.append({"title": match.clean(a.get("albumName")), "year": str(a.get("publicTime") or "")[:4],
                        "date": str(a.get("publicTime") or "")[:10], "type": kind, "tracks": count or None,
                        "cover": str(a.get("albumPic") or "").replace("http://", "https://")})
        return out

    def _add_chinese(self, name, releases, notes):
        """NetEase's album list for a Chinese name (QQ Music when NetEase has nothing), merged like the others."""
        rows, label = [], ""
        try:
            artist = self._netease_artist(name)
            if artist:
                rows = self._netease_releases(artist)
                label = "NetEase" if match.match_key(artist["name"]) == match.match_key(name) else f"NetEase (as {artist['name']})"
        except SourceError as error:
            notes.append(f"NetEase {error}")
        if not rows:
            try:
                rows, label = self._qq_releases(name), "QQ Music"
            except SourceError as error:
                notes.append(f"QQ Music {error}")
        for row in rows:
            key = release_key(row["title"])
            if not key:
                continue
            entry = releases.get(key)
            if entry is None:
                releases[key] = {**row, "deezer_id": None, "sources": [label]}
                continue
            for field in ("year", "date", "tracks", "cover", "netease_id"):
                entry[field] = entry.get(field) or row.get(field)
            if label not in entry["sources"]:
                entry["sources"].append(label)

    def _apple(self, name, known):
        data = self.http.json(f"{ITUNES}/search", {"term": name, "entity": "musicArtist", "limit": 10}) or {}
        artists = [a for a in data.get("results") or [] if match.match_key(a.get("artistName")) == match.match_key(name)]
        best, score = None, -1
        for a in artists[:3]:
            rows = [r for r in (self.http.json(f"{ITUNES}/lookup", {"id": a["artistId"], "entity": "album", "limit": 200}) or {}).get("results") or []
                    if r.get("wrapperType") == "collection"]
            s = len({release_key(r.get("collectionName")) for r in rows} & known) if known else len(rows)
            if s > score:
                best, score = rows, s
            if not known or len(artists) == 1:
                break
        return best or []

    def view(self, name, deezer_id=None, musicbrainz=False, refresh=False):
        name = match.clean(name)
        if not name or len(name) > 200:
            raise DiscographyError("Type an artist name.")
        key = f"{match.match_key(name)}|{deezer_id or ''}"
        if refresh:
            self.cache.pop(key, None)
        data = self._cached(key, lambda: self._collect(name, deezer_id))
        data = {**data, "releases": [dict(r) for r in data["releases"]], "notes": list(data.get("notes") or [])}
        self._add_discogs(data, refresh)
        if musicbrainz:
            self._add_musicbrainz(data)
        self._check_feat_title(data)
        self._check_guest_only(data)
        self._statuses(data)
        data["releases"].sort(key=lambda r: (r.get("year") or "0000", r["title"]), reverse=True)
        data["counts"] = dict(Counter(r["status"] for r in data["releases"]))
        data["chinese"] = chinese_name(name)
        return data

    def _discogs_token(self):
        try:
            return (self.studio.source_keys() or {}).get("discogs_token", "")
        except Exception:
            return ""

    def _add_discogs(self, data, refresh=False):
        """v67: Discogs, much fuller for underground artists, when a Discogs token is saved in Settings. One row per
        release (Discogs' "master"), the artist's own releases; appearances are listed as Compilations."""
        token = self._discogs_token()
        if not token:
            data["notes"].append("Tip: add a Discogs token in Settings › Sources for fuller discographies")
            return
        key = f"discogs|{match.match_key(data['name'])}"
        if refresh:
            self.cache.pop(key, None)
        found = self._cached(key, lambda: self._discogs(data["name"], token, {release_key(r["title"]) for r in data["releases"]}))
        if found.get("notes"):
            data["notes"].extend(found["notes"])
        releases = {release_key(r["title"]): r for r in data["releases"]}
        for row in found.get("releases") or []:
            k = release_key(row["title"])
            if not k:
                continue
            entry = releases.get(k)
            if entry is None:
                entry = releases[k] = {**row, "sources": list(row["sources"])}
                data["releases"].append(entry)
            else:
                entry["year"] = entry.get("year") or row["year"]
                entry["cover"] = entry.get("cover") or row["cover"]
                if row["sources"][0] not in entry["sources"]:
                    entry["sources"].append(row["sources"][0])
                # fork v102: Discogs' role is more accurate than Deezer's plain "album" type - an
                # entry Deezer already added (so it has no "appearance" of its own yet) still needs
                # Discogs' Appearance role applied, not silently kept as a normal album.
                if row.get("appearance"):
                    entry["appearance"] = True
                    entry["type"] = "compile"

    def _discogs(self, name, token, known):
        headers = {"Authorization": f"Discogs token={token}"}
        try:
            hits = (self.http.json(f"{DISCOGS}/database/search", {"q": name, "type": "artist", "per_page": 10}, headers=headers) or {}).get("results") or []
            same = [h for h in hits if match.match_key(discogs_name(h.get("title"))) == match.match_key(name)][:2]
            best, score = [], -1
            for artist in same:
                rows = []
                for page in range(1, 4):
                    data = self.http.json(f"{DISCOGS}/artists/{artist['id']}/releases",
                                          {"sort": "year", "sort_order": "desc", "per_page": 100, "page": page}, headers=headers) or {}
                    rows += data.get("releases") or []
                    if page >= int((data.get("pagination") or {}).get("pages") or 1):
                        break
                overlap = len({release_key(r.get("title")) for r in rows} & known)
                if (overlap, len(rows)) > (score, len(best)):
                    best, score = rows, overlap
        except SourceError as error:
            return {"releases": [], "notes": [f"Discogs {error}"]}
        out, seen = [], set()
        for r in best:
            if r.get("role") not in (None, "Main", "Appearance") or r.get("role") == "UnofficialRelease":
                continue
            title = match.clean(r.get("title"))
            k = release_key(title)
            if not title or not k or k in seen:
                continue
            seen.add(k)
            alias = discogs_name(r.get("artist"))
            label = "Discogs" + (f" (as {alias})" if alias and match.match_key(alias) != match.match_key(name) and r.get("role") == "Main" else "")
            # fork v102: carry the role forward - discogs_kind() already uses it for "type" (shown with
            # Compilations), but a "Get" download also needs to know to search for this artist's own
            # songs on the release, not the whole thing under their name.
            out.append({"title": title, "year": str(r.get("year") or "") if r.get("year") else "", "date": "",
                        "type": discogs_kind(r), "tracks": None, "cover": r.get("thumb") or "", "deezer_id": None,
                        "appearance": r.get("role") == "Appearance", "sources": [label]})
        return {"releases": out, "notes": [] if same else ["Discogs has no artist with exactly this name"]}

    def _guest_only(self, deezer_id, artist_name):
        """v99: Deezer's own "album" type doesn't distinguish a various-artists compilation that merely
        credits us as a guest on a track or two from one of our own albums. Fetch the tracklist once
        (cached 7 days) and call it a guest appearance when we're not the credited artist on at least
        half the tracks.
        fork v110: also look at who Deezer credits the whole release to - a release credited to someone
        else is a guest appearance however few tracks it has (Jus' Allah on Madecipha & 5:am's album),
        and that credited artist is the real main artist."""
        key = f"guestonly2|{deezer_id}"
        hit = self.cache.get(key)
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400:
            return hit["data"]
        want = match.match_key(artist_name)
        try:
            rows = (self.http.json(f"{DEEZER}/album/{deezer_id}/tracks", {"limit": 100}) or {}).get("data") or []
        except SourceError:
            return None
        try:
            meta = self.http.json(f"{DEEZER}/album/{deezer_id}") or {}
        except SourceError:
            meta = {}
        total = len(rows)
        main = match.clean((meta.get("artist") or {}).get("name"))
        mains = [main] if main else []
        mains += [match.clean(c.get("name")) for c in meta.get("contributors") or [] if c.get("role") in (None, "Main")]
        credited = any(want == match.match_key(m) or want in {match.match_key(x) for x in match.split_parts(m)[1]}
                       for m in mains if m)
        by_credit = bool(main) and not credited
        by_tracks = False
        if total >= GUEST_MIN_TRACKS:
            mine = sum(1 for r in rows if match.match_key((r.get("artist") or {}).get("name")) == want)
            by_tracks = bool(mine) and mine < total / 2
        data = {"guest": by_credit or by_tracks, "tracks": total or meta.get("nb_tracks") or None,
                "main": main if by_credit else ""}
        self.cache[key] = {"when": time.time(), "data": data}
        self._save_cache()
        return data

    def _check_feat_title(self, data):
        """v102: a release titled "Title (feat. <this artist>)" has them as a guest on someone
        else's song, whatever Discogs or Deezer say about it - the title alone already says so,
        for free, with no API call. Runs regardless of a Discogs token, unlike ``_check_guest_only``,
        since it's the one signal that doesn't depend on either source actually covering the release."""
        want = match.match_key(data["name"])
        for r in data["releases"]:
            if r.get("appearance"):
                continue
            names, _ = match.featured(r["title"])
            if any(match.match_key(n) == want for n in names):
                r["appearance"] = True
                r["feat_title"] = True  # fork v110: already known from the title - no Deezer look-up needed
                r["type"] = "compile"

    def _check_guest_only(self, data):
        """fork v110: runs with or without a Discogs token (a token's "Appearance" role misses releases)."""
        budget = GUEST_CHECK_MAX
        for r in data["releases"]:
            if not r.get("deezer_id") or r.get("type") not in ("album", "ep", "single", "compile"):
                continue
            if r.get("feat_title") or (r.get("appearance") and r.get("main_artist")):
                continue  # already known (the title says so, or the real main artist is there)
            if r.get("type") == "compile" and not r.get("appearance"):
                continue  # a real compilation, not a guest spot
            fresh = self.cache.get(f"guestonly2|{r['deezer_id']}", {}).get("when", 0) > time.time() - CACHE_DAYS * 86400
            if not fresh:
                if budget <= 0:
                    continue
                budget -= 1
            found = self._guest_only(r["deezer_id"], data["name"])
            if not found:
                continue
            r["tracks"] = r["tracks"] or found["tracks"]
            if found["guest"]:
                r["type"] = "compile"
                r["appearance"] = True  # fork v102: so "Get" downloads their songs, not the whole thing
                if found.get("main"):
                    r["main_artist"] = found["main"]  # fork v110: the artist the release really belongs to

    def _add_musicbrainz(self, data):
        try:
            found = (self.http.json(f"{MB}/artist", {"query": f'artist:"{data["name"]}"', "limit": 5, "fmt": "json"}) or {}).get("artists") or []
            artist = next((a for a in found if match.match_key(a.get("name")) == match.match_key(data["name"])), None)
            if not artist:
                data["notes"].append("MusicBrainz has no artist with exactly this name")
                return
            groups = (self.http.json(f"{MB}/release-group", {"artist": artist["id"], "limit": 100, "fmt": "json"}) or {}).get("release-groups") or []
        except SourceError as error:
            data["notes"].append(f"MusicBrainz {error}")
            return
        known = {release_key(r["title"]) for r in data["releases"]}
        for g in groups:
            key = release_key(g.get("title"))
            if key and key not in known:
                known.add(key)
                data["releases"].append({"title": match.clean(g.get("title")), "year": _year(g.get("first-release-date")),
                                         "type": {"Album": "album", "EP": "ep", "Single": "single"}.get(g.get("primary-type"), "album"),
                                         "tracks": None, "cover": "", "deezer_id": None, "sources": ["MusicBrainz"]})

    def _statuses(self, data):
        if not data["releases"]:
            return
        items = [{"kind": "album", "artist": data["name"], "title": r["title"], "tracks": r.get("tracks")} for r in data["releases"]]
        results = self.manager.check(items)["results"]
        for r, res in zip(data["releases"], results):
            r["status"] = "missing" if res["status"] == "new" else res["status"]
            r["you_have"] = res["you_have"]

    def top(self, name, deezer_id=None):
        """Top 30 songs: Deezer's ranking, else Galaxy's search order."""
        name = match.clean(name)
        songs, source = [], "Deezer"
        try:
            artist_id = deezer_id or (self._deezer_artists(name) or [{}])[0].get("id")
            if artist_id:
                rows = (self.http.json(f"{DEEZER}/artist/{artist_id}/top", {"limit": 30}) or {}).get("data") or []
                songs = [{"title": match.clean(r.get("title")), "album": match.clean((r.get("album") or {}).get("title")),
                          "preview": r.get("preview", ""), "duration": r.get("duration", 0),
                          "cover": (r.get("album") or {}).get("cover_small", "")} for r in rows]
        except SourceError as error:
            log.info("Deezer top songs failed: %s", error)
        if not songs and chinese_name(name):  # v70: NetEase's hot songs for a Chinese name
            try:
                artist = self._netease_artist(name)
                if artist:
                    rows = (self.http.json(f"{NETEASE}/api/artist/top/song", {"id": artist["id"]}, headers=NETEASE_HEADERS) or {}).get("songs") or []
                    songs = [{"title": match.clean(r.get("name")), "album": match.clean((r.get("al") or {}).get("name")),
                              "preview": "", "duration": int((r.get("dt") or 0) / 1000),
                              "cover": f"{(r.get('al') or {}).get('picUrl')}?param=60y60" if (r.get("al") or {}).get("picUrl") else ""}
                             for r in rows[:30]]
                    source = "NetEase"
            except SourceError as error:
                log.info("NetEase top songs failed: %s", error)
        if not songs:
            source = "Galaxy"
            from .galaxy import artist_tracks
            tracks = artist_tracks(self.studio.galaxy.api, "netease", name)[:30]
            songs = [{"title": t["title"], "album": t["album"], "preview": "", "duration": 0, "cover": ""} for t in tracks]
        if songs:
            results = self.manager.check([{"kind": "song", "artist": name, "title": s["title"], "album": s["album"]} for s in songs])["results"]
            for s, r in zip(songs, results):
                s["status"] = "missing" if r["status"] == "new" else r["status"]
                s["you_have"] = r["you_have"]
        return {"name": name, "source": source, "songs": songs}

    # -- getting the missing ones ----------------------------------------------------------------

    def get(self, name, albums=None, songs=None):
        name = match.clean(name)
        albums, songs = list(albums or [])[:200], list(songs or [])[:100]
        if not name or not (albums or songs):
            raise DiscographyError("Choose albums or songs to get.")
        job = {"id": secrets.token_hex(6), "name": name, "state": "running", "done": 0,
               "total": len(albums) + len(songs), "log": [], "albums": albums, "songs": songs, "gen": self._stop_gen}
        self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running"} | {job["id"]: job}
        self._save_line()
        threading.Thread(target=self._run, args=(job, albums, songs), daemon=True, name="get-missing").start()
        return job

    def get_all(self, name, singles=False):
        """v64: one click from Recommended / Labels: every album and EP of this artist you don't fully have."""
        data = self.view(name)
        kinds = {"album", "ep"} | ({"single"} if singles else set())
        albums = [{"title": r["title"], "status": r["status"], "deezer_id": r.get("deezer_id")}
                  for r in data["releases"] if r["status"] != "have" and (r.get("type") or "album") in kinds]
        if not albums:
            raise DiscographyError(f"You already have every album of {data.get('name') or name}, or none were found.")
        return self.get(data.get("name") or name, albums, [])

    def status(self, job_id):
        job = self.jobs.get(str(job_id))
        if not job:
            raise DiscographyError("That request is no longer running.")
        return job

    def active(self):
        """Every "get" in flight or just finished, for the Downloads queue page (v46)."""
        return {"jobs": [{k: j.get(k) for k in ("id", "name", "state", "done", "total", "current", "log")} for j in self.jobs.values()],
                "search": {"paused": self.search_paused, "skipped": sorted(self.skipped), "restored": self.restored,
                           "sources": list(SEARCH_SOURCES)}}

    def dismiss(self):
        self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running"}
        return self.active()

    def _step(self, ctx, text):
        job = (ctx or {}).get("job")
        if job is not None:
            self._hold(job)
            job["current"] = text

    # -- fork v114: pause, stop and skip for the search step; the line survives a restart ---------

    def _stopped(self, job):
        return job.get("gen", 0) < self._stop_gen

    def _hold(self, job):
        """Wait here while the search is paused; unwind when it was stopped."""
        while True:
            if self._stopped(job):
                raise SearchStopped()
            if not self.search_paused:
                return
            job["current"] = f"{job['name']}: search paused"
            time.sleep(0.3)

    def pause_point(self, gen, waiting=None):
        """fork v117: the wishlist's retries call this before each item. They used to search straight away, beside
        a running "From a page" job and even while the search was paused or stopped, which is the flood that got
        Galaxy blocked. Wait while the search is paused or another search job has the turn; raise SearchStopped when
        Stop was pressed after ``gen`` (``self._stop_gen`` when the run began)."""
        while True:
            if self._stop_gen != gen:
                raise SearchStopped()
            with self._turns_lock:
                busy = bool(self._turns)
            if not self.search_paused and not busy:
                return
            if waiting:
                waiting("search paused" if self.search_paused else "waiting for the search line")
            time.sleep(0.5)

    def _end_stopped(self, job):
        left = max(0, job["total"] - job["done"])
        job["log"].append({"kind": "stop", "title": "Search stopped", "source": "Stopped",
                           "note": f"{left} not searched. Pick them again to search them."})
        job["state"] = "done"
        job["current"] = ""
        self._save_line()

    def search_control(self, action, source=None, off=None):
        if action == "pause":
            self.search_paused = True
        elif action == "resume":
            self.search_paused, self.restored = False, 0
        elif action == "stop":
            self._stop_gen += 1
            self.search_paused, self.restored = False, 0
        elif action == "skip":
            if source not in SEARCH_SOURCES:
                raise DiscographyError("Unknown source.")
            (self.skipped.add if off else self.skipped.discard)(source)
        else:
            raise DiscographyError("Unknown search action.")
        self._save_line()
        return self.active()

    def _save_line(self):
        """The searches that are still waiting are written down, so a restart does not lose them."""
        write = getattr(self.studio, "_write", None)
        if write is None:
            return
        try:
            with self._line_lock:
                rows = []
                for j in list(self.jobs.values()):
                    if j.get("state") != "running" or "albums" not in j:
                        continue
                    done, albums, songs = j["done"], j["albums"], j["songs"]
                    rest_albums, rest_songs = albums[done:], songs[max(0, done - len(albums)):]
                    if rest_albums or rest_songs:
                        rows.append({"id": j["id"], "name": j["name"], "albums": rest_albums, "songs": rest_songs})
                write(LINE_FILE, {"jobs": rows, "skipped": sorted(self.skipped)})
        except Exception:
            log.exception("Could not save the search line")

    def _load_line(self):
        """Searches that were waiting when Tagcast closed come back paused: press Resume to carry on."""
        read = getattr(self.studio, "_read", None)
        try:
            data = read(LINE_FILE) if read else None
        except Exception:
            data = None
        if not isinstance(data, dict):
            return
        self.skipped = {x for x in data.get("skipped") or [] if x in SEARCH_SOURCES}
        rows = [r for r in data.get("jobs") or [] if isinstance(r, dict) and match.clean(r.get("name"))
                and (r.get("albums") or r.get("songs"))]
        if not rows:
            return
        self.search_paused = True
        for r in rows:
            albums, songs = list(r.get("albums") or [])[:200], list(r.get("songs") or [])[:100]
            job = {"id": str(r.get("id") or secrets.token_hex(6)), "name": match.clean(r["name"]), "state": "running", "done": 0,
                   "total": len(albums) + len(songs), "log": [], "albums": albums, "songs": songs, "gen": self._stop_gen,
                   "current": f"{match.clean(r['name'])}: restored, search paused"}
            self.restored += job["total"]
            self.jobs[job["id"]] = job
            threading.Thread(target=self._run, args=(job, albums, songs), daemon=True, name="get-missing").start()
    def _run(self, job, albums, songs):
        # fork v113: one job at a time, first come first served. "From a page" starts one job per artist;
        # all at once they flooded the sources with searches and got Galaxy blocked.
        with self._turns_lock:
            self._turns.append(job)
            if self._turns[0] is not job:
                job["current"] = f"{job['name']}: waiting for its turn…"
            while self._turns[0] is not job and not self._stopped(job):
                self._turns_lock.wait(0.5)
            if self._turns[0] is not job:   # fork v114: stopped while waiting
                self._turns.remove(job)
                self._turns_lock.notify_all()
                stopped = True
            else:
                stopped = False
        if stopped:
            self._end_stopped(job)
            return
        try:
            self._hold(job)   # paused (or restored after a restart): wait here for Resume
            with featured.for_artist(job["name"]):
                self._run_all(job, albums, songs)
        except SearchStopped:
            self._end_stopped(job)
        finally:
            with self._turns_lock:
                self._turns.remove(job)
                more = bool(self._turns)
            if more:
                time.sleep(JOB_GAP)   # a breather between artists
            with self._turns_lock:
                self._turns_lock.notify_all()

    def _run_all(self, job, albums, songs):
        ctx = {"bandcamp": None, "bandcamp_checked": False, "job": job}
        for a in albums:
            a = a if isinstance(a, dict) else {}
            self._hold(job)
            title = match.clean(a.get("title"))
            self._step(ctx, f"{title}: searching Galaxy…")
            appearance = a.get("appearance")  # True = artist appears on this album; get their songs, not the whole album
            full_album = a.get("full_album")  # True = override: download the whole album (from album title search)
            dl_name = job["name"]
            try:
                if appearance and not full_album:
                    # Featured-artist case: search for the artist's own songs by using the album title as a song query.
                    # Galaxy song search for "artist albumTitle" reliably finds their tracks on that album.
                    source, note = self._song(job["name"], title, "", ctx)
                    log_note = f"appearance on '{title}' — searched for {job['name']}'s songs"
                    if not source:
                        note = self._wish("song", job["name"], title, note=note)
                    job["log"].append({"kind": "song", "title": title, "source": source,
                                       "note": f"{log_note}: {note}" if note else log_note})
                    self._trail("song", job["name"], title, a.get("origin"), source, note)   # fork v117
                    job["done"] += 1
                    self._save_line()
                    continue
                # full_album on an appearance: the real main artist (fork v110), else search by album title alone
                main = match.clean(a.get("main_artist"))
                dl_name = (main if full_album and appearance else job["name"])
                with featured.for_artist(dl_name):  # the whole album, asked for on purpose: tagged as it comes
                    source, note = self._album(dl_name, title, a.get("status"), a.get("deezer_id"), ctx, a.get("tracks"))
            except Exception as error:  # one album never stops the rest
                log.exception("Getting %s failed", title)
                source, note = None, str(error)
            if not source:
                release = a.get("release") or {"compile": "album"}.get(a.get("type"), a.get("type") or "")
                note = self._wish("album", dl_name, title, status=a.get("status"), note=note,
                                  release=release, featured=job["name"] if dl_name and dl_name != job["name"] else "")
            job["log"].append({"kind": "album", "title": title, "source": source, "note": note})
            self._trail("album", dl_name, title, a.get("origin"), source, note)
            job["done"] += 1
            self._save_line()
        for s in songs:
            s = s if isinstance(s, dict) else {}
            self._hold(job)
            title = match.clean(s.get("title"))
            try:
                source, note = self._song(job["name"], title, match.clean(s.get("album")), ctx)
            except Exception as error:
                log.exception("Getting %s failed", title)
                source, note = None, str(error)
            if not source:
                note = self._wish("song", job["name"], title, album=match.clean(s.get("album")), note=note)
            job["log"].append({"kind": "song", "title": title, "source": source, "note": note})
            self._trail("song", job["name"], title, s.get("origin"), source, note)
            job["done"] += 1
            self._save_line()
        job["state"] = "done"
        job["current"] = ""
        self._save_line()

    @staticmethod
    def _trail(kind, artist, title, origin, source, note):
        """fork v117: one log line per searched item that says what was searched for, where the request came from
        (the page and row, or the artist's discography) and where it ended up - the log used to show only the result."""
        try:
            activity.record("downloads", "ok" if source else "info",
                            f"Searched {kind} “{artist} {title}” → {source or 'not found'}",
                            detail=f"Search term: {artist} {title}\nAsked from: {origin or 'Discography / top songs / wishlist'}\n{note or ''}".strip(),
                            source=(source or "tagcast").casefold())
        except Exception:
            log.debug("Could not log the search trail", exc_info=True)

    @staticmethod
    def _summary(result):
        parts = [f"{result.get('queued', 0)} queued"]
        if result.get("held"):
            parts.append(f"{result['held']} waiting in Needs your decision")
        if result.get("already_had"):
            parts.append(f"{result['already_had']} already had")
        return ", ".join(parts)

    def _bandcamp_base(self, name, ctx, albums=()):
        if "bandcamp" in self.skipped:
            return None
        if not ctx["bandcamp_checked"]:
            ctx["bandcamp_checked"] = True
            try:
                ctx["bandcamp"] = self.studio.bandcamp.find_artist(name, albums)
            except Exception as error:
                log.info("Bandcamp search failed: %s", error)
        return ctx["bandcamp"]

    def _bandcamp_album(self, name, title, ctx):
        """("Bandcamp", note) when queued, else a short reason Bandcamp was skipped."""
        if "bandcamp" in self.skipped:
            return "Bandcamp is switched off"
        bandcamp = self.studio.bandcamp
        reason = "no artist with this exact name"
        try:
            base = self._bandcamp_base(name, ctx, [title])
            release = None
            if base:
                reason = "artist found, album not on their page"
                release = bandcamp.find_album(base, title)
            if not release:
                release = bandcamp.find_album_by_search(name, title)
                if not release and not base:
                    reason = "not found by artist or album search"
            if release:
                return "Bandcamp", self._summary(bandcamp.add(release["url"]))
        except Exception as error:
            reason = f"search failed ({error})"
        log.info("Bandcamp skipped %s - %s: %s", name, title, reason)
        return reason

    def _soundcloud_album(self, name, title, expected=None):
        """("SoundCloud", note) when found as a set on the artist's own account, else None (v96)."""
        moresources = getattr(self.studio, "moresources", None)
        if moresources is None or "soundcloud" in self.skipped:
            return None
        try:
            rows = moresources.find_album(name, title, expected or 0)
            if not rows:
                return None
            total = len(rows)
            for i, r in enumerate(rows):
                r.update(track_no=i + 1, track_total=total, album=title, album_artist=name)
            return "SoundCloud", self._summary(moresources.add(rows, query=f"{name} {title}", album=True))
        except Exception as error:
            log.info("SoundCloud album failed for %s - %s: %s", name, title, error)
            return None

    def _soundcloud_song(self, name, title, album="", seconds=0):
        """("SoundCloud", note) when found on the artist's own account, else None (v96)."""
        moresources = getattr(self.studio, "moresources", None)
        if moresources is None or "soundcloud" in self.skipped:
            return None
        try:
            row = moresources.find_song(name, title, seconds)
            if not row:
                return None
            if album:
                row["album"] = album
            return "SoundCloud", self._summary(moresources.add([row], query=f"{name} {title}"))
        except Exception as error:
            log.info("SoundCloud song failed for %s - %s: %s", name, title, error)
            return None

    def _spotify_song(self, name, title, album=""):
        """fork v105: spotDL's own Spotify search (v99) tried automatically for a wishlist miss -
        not only when you paste a link or type a search for it by hand. Spotify's own catalog search
        is reliable, but spotDL still returns its "closest match" rather than nothing, so that match
        is checked against the title asked for before it is trusted."""
        spotify = getattr(self.studio, "spotify", None)
        moresources = getattr(self.studio, "moresources", None)
        if spotify is None or moresources is None or "spotify" in self.skipped:
            return None
        try:
            rows = spotify.songs(f"{name} - {title}")
        except LibraryError as error:
            log.info("Spotify search for %s - %s found nothing: %s", name, title, error)
            return None
        if not rows:  # fork v108: an empty answer is a miss, not a crash in max()
            return None
        row = max(rows, key=lambda r: sim(r.get("title") or "", title))
        if sim(row.get("title") or "", title) < 0.8:
            return None
        try:
            result = moresources.add([row], query=f"{name} {title}")
        except LibraryError as error:
            log.info("Spotify match for %s - %s could not be queued: %s", name, title, error)
            return None
        return "Spotify", moresources._spotify_note([row], result)

    def _youtube(self):
        youtube = getattr(self.studio, "youtube", None)
        return youtube if youtube is not None and youtube.enabled() and "youtube" not in self.skipped else None

    def _youtube_album(self, name, title, why=None, expected=None):
        youtube = self._youtube()
        if youtube is None:
            return None
        try:
            return "YouTube", self._summary(youtube.add_album(name, title, expected)) + " (lossy, from YouTube)"
        except LibraryError as error:
            log.info("YouTube album skipped: %s", error)
            reason = str(error)
        except Exception as error:
            log.info("YouTube album failed: %s", error)
            reason = f"search failed ({str(error)[:120]})"
        if why is not None:
            why.append(f"YouTube · {reason}")
        return None

    def _youtube_song(self, name, title, album="", why=None, exclude=()):
        youtube = self._youtube()
        if youtube is None:
            return None
        try:
            return "YouTube", self._summary(youtube.add_song(name, title, album, exclude=exclude)) + " (lossy, from YouTube)"
        except LibraryError as error:
            log.info("YouTube song skipped: %s", error)
            reason = str(error)
        except Exception as error:
            log.info("YouTube song failed: %s", error)
            reason = f"search failed ({str(error)[:120]})"
        if why is not None:
            why.append(f"YouTube · {reason}")
        return None

    @staticmethod
    def _not_found(galaxy_note, why):
        """v63: what every source searched and why it gave up."""
        if "blocked" in str(galaxy_note):
            return galaxy_note
        return NOT_FOUND + (f" ({'; '.join(why)})" if why else "")

    def _album(self, name, title, status, deezer_id, ctx, expected=None):
        tried = []
        if not expected and _looks_like_a_song(title):
            expected = 1   # fork v121: "GASS” (feat. Travis Scott)" is a song title, never a whole album to download
            title = _song_title(title) or title   # fork v122: searched as "GASS", not "GASS” (feat. Travis Scott)"
        if chinese_name(name):
            # v70: a Chinese artist: Galaxy first (NetEase's own album and track list), then YouTube.
            self._step(ctx, f"{title}: searching Galaxy…")
            source, note = self._galaxy_album(name, title, expected)
            if source:
                return source, note
            galaxy_note = note
            self._step(ctx, f"{title}: searching YouTube…")
            found = self._youtube_album(name, title, tried, expected)
            if found:
                return found
        else:
            # 0. YouTube (v59) when it is switched on: an album playlist of this artist
            self._step(ctx, f"{title}: searching YouTube…")
            found = self._youtube_album(name, title, tried, expected)
            if found:
                return found
            if expected == 1:  # fork v94: a one-song release (a feature, a single) has no album playlist: search the song
                from .youtube import strip_edition
                found = self._youtube_song(name, strip_edition(title), "", tried)
                if found:
                    return found
            # 1. Galaxy: every way (album, then the title as a song, then the artist's songs)
            self._step(ctx, f"{title}: searching Galaxy…")
            source, note = self._galaxy_album(name, title, expected)
            if source:
                return source, note
            galaxy_note = note
        # 2. Bandcamp: the artist's page first, then Bandcamp's own album search (v46)
        self._step(ctx, f"{title}: looking on Bandcamp…")
        why = self._bandcamp_album(name, title, ctx)
        if isinstance(why, tuple):
            return why
        tried += [f"Galaxy · {galaxy_note}" if galaxy_note and galaxy_note != NOT_FOUND else f"Galaxy · query “{name} {title}”: not found",
                  f"Bandcamp · {why}"]  # fork v117: say "switched off" / "paused" when that is what happened
        # 2b. SoundCloud: the artist's own account only (v96)
        self._step(ctx, f"{title}: looking on SoundCloud…")
        found = self._soundcloud_album(name, title, expected)
        if found:
            return found
        tried.append("SoundCloud · not on the artist's own account")
        # 3. Soulseek (whole albums only; a partly owned album would be fetched again)
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured() and status != "partly" and "soulseek" not in self.skipped:
            self._step(ctx, f"{title}: asking Soulseek…")
            result = soulseek.add([{"kind": "album", "artist": name, "album": title, "fallback": True}])
            return "Soulseek", self._summary(result) + " (wishlist if Soulseek has nothing)"
        return None, self._not_found(galaxy_note, tried)

    def aliases(self, name):
        """Fork v91: other names MusicBrainz lists for exactly this artist (an alias is the same person), at most 4."""
        try:
            found = (self.http.json(f"{MB}/artist", {"query": f'artist:"{name}"', "limit": 5, "fmt": "json"}) or {}).get("artists") or []
            artist = next((a for a in found if match.match_key(a.get("name")) == match.match_key(name)), None)
            if not artist:
                return []
            detail = self.http.json(f"{MB}/artist/{artist['id']}", {"inc": "aliases", "fmt": "json"}) or {}
            out = []
            for alias in detail.get("aliases") or []:
                value = match.clean(alias.get("name") or "")
                if value and match.match_key(value) != match.match_key(name) and value not in out:
                    out.append(value)
            return out[:4]
        except Exception as error:
            log.info("MusicBrainz aliases for %s failed: %s", name, error)
            return []

    def alias_has_album(self, name, title):
        """fork v105: before trying a MusicBrainz alias for an album search, make sure the alias
        artist's own Deezer catalog actually lists an album with this title - a stage name is a
        fine alias to search under, but MusicBrainz linking a side-project or a collaborator as
        if they were "the same person" is not, and sends the search chasing the wrong name
        (the bug report: Braille & Symbolyc One's "Cloud Nineteen" searched only under "Symbolyc
        One"). True when it can't check (no Deezer artist, or Deezer is unreachable) - this is a
        second opinion that can rule an alias OUT, never one that should block a search on its own."""
        try:
            candidates = self._deezer_artists(name)
        except SourceError:
            return True
        if not candidates:
            return True
        want = release_key(title)
        for c in candidates[:4]:
            try:
                rows = (self.http.json(f"{DEEZER}/artist/{c['id']}/albums", {"limit": 200}) or {}).get("data") or []
            except SourceError:
                continue
            if any(release_key(r.get("title")) == want for r in rows):
                return True
        return False

    def album_credit(self, name, title):
        """fork v110: the one artist Deezer credits releases with this exact title to, when that is not ``name``
        and ``name`` is not one of the credited artists ("Completely Disrespectful": Madecipha & 5:am, not Jus'
        Allah, who is only a guest on it). "" when unsure. Cached 30 days."""
        key = f"credit|{match.match_key(name)}|{release_key(title)}"
        hit = self.cache.get(key)
        if hit and hit.get("when", 0) > time.time() - 30 * 86400:
            return hit["data"]
        try:
            rows = (self.http.json(f"{DEEZER}/search/album", {"q": f'album:"{title}"', "limit": 25}) or {}).get("data") or []
        except SourceError:
            return ""
        want, wanted = release_key(title), match.match_key(name)
        credited = {}
        for row in rows:
            if release_key(row.get("title")) == want:
                artist = match.clean((row.get("artist") or {}).get("name"))
                if artist:
                    credited[match.match_key(artist)] = artist
        answer = ""
        if len(credited) == 1 and wanted not in credited:
            only = next(iter(credited.values()))
            if wanted not in {match.match_key(x) for x in match.split_parts(only)[1]}:
                answer = only
        self.cache[key] = {"when": time.time(), "data": answer}
        self._save_cache()
        return answer

    def tracklist(self, name, title):
        """fork v110: ({song titles}, release type) of one release from Discogs; ([], "") without a token or a match.
        Cached 30 days."""
        token = self._discogs_token()
        if not token:
            return [], ""
        key = f"tracklist|{match.match_key(name)}|{release_key(title)}"
        hit = self.cache.get(key)
        if hit and hit.get("when", 0) > time.time() - 30 * 86400:
            return hit["data"]["titles"], hit["data"]["release"]
        headers = {"Authorization": f"Discogs token={token}"}
        titles = []
        try:
            for kind in ("master", "release"):
                found = (self.http.json(f"{DISCOGS}/database/search", {"type": kind, "artist": name, "release_title": title,
                                                                      "per_page": 5}, headers=headers) or {}).get("results") or []
                best = next((f for f in found if release_key(discogs_name(f.get("title", "")).split(" - ", 1)[-1]) == release_key(title)
                             or release_key(str(f.get("title", "")).split(" - ", 1)[-1]) == release_key(title)), None)
                if not best:
                    continue
                path = "masters" if kind == "master" else "releases"
                detail = self.http.json(f"{DISCOGS}/{path}/{best['id']}", headers=headers) or {}
                titles = [match.clean(t.get("title")) for t in detail.get("tracklist") or []
                          if t.get("type_", "track") == "track" and match.clean(t.get("title"))]
                if titles:
                    break
        except SourceError as error:
            log.info("Discogs tracklist for %s - %s failed: %s", name, title, error)
            return [], ""
        count = len(titles)
        release = "" if not count else "single" if count <= 3 else "ep" if count <= 6 else "album"
        self.cache[key] = {"when": time.time(), "data": {"titles": titles, "release": release}}
        self._save_cache()
        return titles, release

    def credited_as(self, name, title):
        """Fork v91: a different artist name MusicBrainz credits an album with this exact title to, when there is
        exactly one ("Jus Allah" / "Shakespeare Science" -> "Poetic Death"). Only ever a suggestion."""
        try:
            groups = (self.http.json(f"{MB}/release-group", {"query": f'releasegroup:"{title}" AND primarytype:album', "limit": 8, "fmt": "json"}) or {}).get("release-groups") or []
            names = set()
            for group in groups:
                if sim(group.get("title") or "", title) < 0.95:
                    continue
                for credit in group.get("artist-credit") or []:
                    credited = match.clean((credit.get("artist") or {}).get("name") or credit.get("name") or "")
                    if credited and match.match_key(credited) != match.match_key(name):
                        names.add(credited)
            return names.pop() if len(names) == 1 else ""
        except Exception as error:
            log.info("MusicBrainz credit lookup for %s failed: %s", title, error)
            return ""

    def _spellings(self, name):
        """Your spelling first, then NetEase's for a Chinese name ("派克特Pact", "派克特（PACT）") (v70)."""
        out = [name]
        if chinese_name(name):
            try:
                artist = self._netease_artist(name)
            except SourceError:
                artist = None
            if artist and artist["name"] not in out:
                out.append(artist["name"])
        return out

    def _galaxy_sources(self):
        """The chosen source first, then the other open ones (v32): one source missing a song
        no longer means Galaxy hasn't got it."""
        chosen = self.studio.galaxy.state.get("source") or "netease"
        return list(dict.fromkeys([chosen, *FALLBACK_SOURCES]))

    @staticmethod
    def _galaxy_paused():
        return _galaxy_block["until"] > time.time()

    def _galaxy_album(self, name, title, expected=None):
        if "galaxy" in self.skipped:
            return None, "Galaxy is switched off"
        if self._galaxy_paused():
            return None, "Galaxy was blocked by GD Studio a moment ago and is paused"
        galaxy = self.studio.galaxy
        try:
            for source in self._galaxy_sources():
                for spelling in self._spellings(name):
                    data = galaxy.album(title, spelling, source)
                    if data.get("found"):
                        # Saved under your own spelling of the artist, so it lands next to what you have (v70).
                        artist = name if spelling != name else data["album_artist"]
                        tracks, others = _only_theirs(_not_instrumental(data["tracks"]), name, data["album_artist"])
                        if tracks:
                            return "Galaxy", self._summary(galaxy.add(tracks, artist, len(tracks), album=True)) + others
            # The album search found nothing: the title may be a song (a single, or an album that
            # shares its name with its first song), listed under another album name on Galaxy.
            best = find_song(galaxy.api, self._galaxy_sources(), name, title, deep=True)
            if best:
                own = best.get("album") or ""
                # fork v117: never widen a one-song release (a single, a feature) into the album the song is on
                # ("Bills Paid" is on "GOD DID"); an album of a very different size is not "the whole thing" either
                if own and sim(own, title) < 0.9 and expected and expected > 1:   # the song's own album may be the whole thing
                    data = galaxy.album(own, name, best["source"])
                    if data.get("found"):
                        tracks, others = _only_theirs(_not_instrumental(data["tracks"]), name, data["album_artist"])
                        if tracks and expected and abs(len(tracks) - expected) > 2:
                            tracks = []
                        if tracks:
                            return "Galaxy", self._summary(galaxy.add(tracks, data["album_artist"], len(tracks), album=True)) + f" (album “{own}”)" + others
                return "Galaxy", self._summary(galaxy.add([best])) + " (found by song title)"
        except GalaxyBlocked as error:
            _galaxy_block["until"] = time.time() + GALAXY_PAUSE
            log.warning("Galaxy blocked: %s", error)
            return None, str(error)
        return None, NOT_FOUND

    def _song(self, name, title, album, ctx, skip_galaxy=False, skip_youtube=False, exclude=()):
        tried = []
        if chinese_name(name) and not skip_galaxy:
            # v70: a Chinese artist: Galaxy first, then YouTube.
            source, note = self._galaxy_song(name, title)
            if source:
                return source, note
            galaxy_note = note
            found = None if skip_youtube else self._youtube_song(name, title, album, tried, exclude)
            if found:
                return found
        else:
            found = None if skip_youtube else self._youtube_song(name, title, album, tried, exclude)
            if found:
                return found
            if skip_galaxy:
                galaxy_note = "Galaxy had no download link"
            else:
                source, note = self._galaxy_song(name, title)
                if source:
                    return source, note
                galaxy_note = note
        base = self._bandcamp_base(name, ctx, [album] if album else [])
        if base and album:
            try:
                release = self.studio.bandcamp.find_album(base, album)
                track = release and self.studio.bandcamp.find_track(release, title)
                if track:
                    return "Bandcamp", self._summary(self.studio.bandcamp.add(release["url"], [track["num"]]))
            except LibraryError as error:
                log.info("Bandcamp song failed: %s", error)
        found = self._soundcloud_song(name, title, album)
        if found:
            return found
        found = self._spotify_song(name, title, album)
        if found:
            return found
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured() and "soulseek" not in self.skipped:
            result = soulseek.add([{"kind": "song", "artist": name, "title": title, "album": album, "fallback": True}])
            return "Soulseek", self._summary(result) + " (wishlist if Soulseek has nothing)"
        tried.append(f"Galaxy · query “{name} {title}”: " + ("no download link" if skip_galaxy else "not found"))
        tried.append("SoundCloud · not on the artist's own account")
        return None, self._not_found(galaxy_note, tried)

    def _galaxy_song(self, name, title):
        if "galaxy" in self.skipped:
            return None, "Galaxy is switched off"
        if self._galaxy_paused():
            return None, "Galaxy was blocked by GD Studio a moment ago and is paused"
        galaxy = self.studio.galaxy
        try:
            best = None
            for spelling in self._spellings(name):
                best = find_song(galaxy.api, self._galaxy_sources(), spelling, title, deep=True)
                if best:
                    break
        except GalaxyBlocked as error:
            _galaxy_block["until"] = time.time() + GALAXY_PAUSE
            log.warning("Galaxy blocked: %s", error)
            return None, str(error)
        if not best:
            return None, NOT_FOUND
        return "Galaxy", self._summary(galaxy.add([best]))

    def _wish(self, kind, artist, title, album="", status="missing", note="", release="", featured=""):
        """Not found anywhere: put it on the wishlist, which tries again every few days."""
        wishlist = getattr(self.studio, "wishlist", None)
        if wishlist is None or not title:
            return note
        try:
            extra = {k: v for k, v in (("release", release), ("featured", featured)) if v}  # fork v110
            wishlist.add(kind, artist, title, album, status or "missing", note, **extra)
            return f"{note} · on your wishlist"
        except LibraryError:
            return note

    def galaxy_fallback(self, item):
        """v65: Galaxy found the song but had no download link: try YouTube, Bandcamp and Soulseek; the failed
        row goes once another source has it, else the song goes on the wishlist."""
        if not item or item.get("kind") != "song":
            return
        artist = re.split(r"\s*;\s*", item.get("artist") or "")[0].strip()
        if not artist or not item.get("title"):
            return
        try:
            with featured.for_artist(item.get("for_artist", "")):  # v109: keep crediting the artist it was asked for
                source, note = self._song(artist, item["title"], item.get("album", ""), {"bandcamp": None, "bandcamp_checked": False},
                                          skip_galaxy=True)
        except Exception as error:
            log.info("Fallback for %s failed: %s", item["title"], error)
            source, note = None, str(error)
        if source:
            self.manager.remove([item["id"]])
            activity.record("downloads", "info", f"Galaxy couldn't give {item['title']} ({item.get('note') or 'no link'}): trying {source} instead", source=source.casefold())
            return
        note = self._wish("song", artist, item["title"], item.get("album", ""), note=note)
        self.manager.update_item(item["id"], persist=True, note=f"{item.get('note', '')} · {note}".strip(" ·"))

    def youtube_fallback(self, item):
        """Fork v91: YouTube would not serve a video (age-restricted): try another upload of the song, then Galaxy,
        Bandcamp and Soulseek; the failed row goes once another source has it, else the song goes on the wishlist."""
        if not item or item.get("kind") != "song":
            return
        artist = re.split(r"\s*;\s*", item.get("artist") or "")[0].strip()
        if not artist or not item.get("title"):
            return
        bad = str((item.get("payload") or {}).get("id") or "")
        if bad.startswith("http"):  # a More-sources row carries the link, not the video id (v96)
            bad = (re.search(r"[?&]v=([\w-]{11})", bad) or re.search(r"youtu\.be/([\w-]{11})", bad) or [None, ""])[1]
        ctx = {"bandcamp": None, "bandcamp_checked": False}
        try:
            source = None
            with featured.for_artist(item.get("for_artist", "")):  # v109: keep crediting the artist it was asked for
                found = self._youtube_song(artist, item["title"], item.get("album", ""), None, exclude={bad} if bad else ())
                if found:
                    source, note = found
                else:
                    source, note = self._song(artist, item["title"], item.get("album", ""), ctx, skip_youtube=True)
        except Exception as error:
            log.info("Fallback for %s failed: %s", item["title"], error)
            source, note = None, str(error)
        if source:
            self.manager.remove([item["id"]])
            activity.record("downloads", "info", f"YouTube could not serve {item['title']}: trying {source} instead", source=source.casefold())
            return
        note = self._wish("song", artist, item["title"], item.get("album", ""), note=note)
        self.manager.update_item(item["id"], persist=True, note=f"{item.get('note', '')} · {note}".strip(" ·"))

    def fallback(self, item):
        """A Soulseek job of "get the missing" failed. Galaxy was already tried before Soulseek
        (v45), so what is left is the wishlist, which tries everything again later."""
        if not item:
            return
        note = self._wish(item["kind"], item["artist"], item["title"], item.get("album", ""), note="Not found on Soulseek")
        self.manager.update_item(item["id"], persist=True,
                                 note=f"{item.get('note', '')} · {note}".strip(" ·"))
