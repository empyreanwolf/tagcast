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

from . import activity
from . import artist_match as match
from .downloads import FEAT_BRACKET, album_key
from .galaxy import FALLBACK_SOURCES, GalaxyBlocked, find_song, sim
from .library import LibraryError
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
NOT_FOUND = "Not found on Galaxy, Bandcamp or Soulseek"
GALAXY_PAUSE = 600.0   # after GD Studio blocks a request, Galaxy is skipped this long (fork v54)
_galaxy_block = {"until": 0.0}
CACHE_FILE = "discography_cache.json"
CACHE_DAYS = 7
DEEZER = "https://api.deezer.com"
ITUNES = "https://itunes.apple.com"
MB = "https://musicbrainz.org/ws/2"
TYPES = {"album": "Album", "ep": "EP", "single": "Single", "compile": "Compilation"}
SUFFIX = re.compile(r"\s+-\s+(single|ep)$", re.I)


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


class Discography:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(CACHE_FILE)
        self.cache = data if isinstance(data, dict) else {}
        self.jobs = {}
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
            out.append({"title": title, "year": str(r.get("year") or "") if r.get("year") else "", "date": "",
                        "type": discogs_kind(r), "tracks": None, "cover": r.get("thumb") or "", "deezer_id": None,
                        "sources": [label]})
        return {"releases": out, "notes": [] if same else ["Discogs has no artist with exactly this name"]}

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
               "total": len(albums) + len(songs), "log": []}
        self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running"} | {job["id"]: job}
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
        return {"jobs": [{k: j.get(k) for k in ("id", "name", "state", "done", "total", "current", "log")} for j in self.jobs.values()]}

    def dismiss(self):
        self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running"}
        return self.active()

    @staticmethod
    def _step(ctx, text):
        job = (ctx or {}).get("job")
        if job is not None:
            job["current"] = text

    def _run(self, job, albums, songs):
        ctx = {"bandcamp": None, "bandcamp_checked": False, "job": job}
        for a in albums:
            title = match.clean((a or {}).get("title"))
            self._step(ctx, f"{title}: searching Galaxy…")
            appearance = a.get("appearance")  # True = artist appears on this album; get their songs, not the whole album
            full_album = a.get("full_album")  # True = override: download the whole album (from album title search)
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
                    job["done"] += 1
                    continue
                # full_album on an appearance: search by album title alone (we don't know the main artist)
                dl_name = "" if full_album and appearance else job["name"]
                source, note = self._album(dl_name, title, a.get("status"), a.get("deezer_id"), ctx, a.get("tracks"))
            except Exception as error:  # one album never stops the rest
                log.exception("Getting %s failed", title)
                source, note = None, str(error)
            if not source:
                dl_name = "" if full_album and appearance else job["name"]
                note = self._wish("album", dl_name, title, status=a.get("status"), note=note)
            job["log"].append({"kind": "album", "title": title, "source": source, "note": note})
            job["done"] += 1
        for s in songs:
            title = match.clean((s or {}).get("title"))
            try:
                source, note = self._song(job["name"], title, match.clean(s.get("album")), ctx)
            except Exception as error:
                log.exception("Getting %s failed", title)
                source, note = None, str(error)
            if not source:
                note = self._wish("song", job["name"], title, album=match.clean(s.get("album")), note=note)
            job["log"].append({"kind": "song", "title": title, "source": source, "note": note})
            job["done"] += 1
        job["state"] = "done"
        job["current"] = ""

    @staticmethod
    def _summary(result):
        parts = [f"{result.get('queued', 0)} queued"]
        if result.get("held"):
            parts.append(f"{result['held']} waiting in Needs your decision")
        return ", ".join(parts)

    def _bandcamp_base(self, name, ctx, albums=()):
        if not ctx["bandcamp_checked"]:
            ctx["bandcamp_checked"] = True
            try:
                ctx["bandcamp"] = self.studio.bandcamp.find_artist(name, albums)
            except Exception as error:
                log.info("Bandcamp search failed: %s", error)
        return ctx["bandcamp"]

    def _bandcamp_album(self, name, title, ctx):
        """("Bandcamp", note) when queued, else a short reason Bandcamp was skipped."""
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

    def _youtube(self):
        youtube = getattr(self.studio, "youtube", None)
        return youtube if youtube is not None and youtube.enabled() else None

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
        if chinese_name(name):
            # v70: a Chinese artist: Galaxy first (NetEase's own album and track list), then YouTube.
            self._step(ctx, f"{title}: searching Galaxy…")
            source, note = self._galaxy_album(name, title)
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
            source, note = self._galaxy_album(name, title)
            if source:
                return source, note
            galaxy_note = note
        # 2. Bandcamp: the artist's page first, then Bandcamp's own album search (v46)
        self._step(ctx, f"{title}: looking on Bandcamp…")
        why = self._bandcamp_album(name, title, ctx)
        if isinstance(why, tuple):
            return why
        tried += [f"Galaxy · query “{name} {title}”: not found", f"Bandcamp · {why}"]
        # 3. Soulseek (whole albums only; a partly owned album would be fetched again)
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured() and status != "partly":
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

    def _galaxy_album(self, name, title):
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
                        return "Galaxy", self._summary(galaxy.add(data["tracks"], artist, len(data["tracks"])))
            # The album search found nothing: the title may be a song (a single, or an album that
            # shares its name with its first song), listed under another album name on Galaxy.
            best = find_song(galaxy.api, self._galaxy_sources(), name, title, deep=True)
            if best:
                own = best.get("album") or ""
                if own and sim(own, title) < 0.9:   # the song's own album may be the whole thing
                    data = galaxy.album(own, name, best["source"])
                    if data.get("found"):
                        return "Galaxy", self._summary(galaxy.add(data["tracks"], data["album_artist"], len(data["tracks"]))) + f" (album “{own}”)"
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
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured():
            result = soulseek.add([{"kind": "song", "artist": name, "title": title, "album": album, "fallback": True}])
            return "Soulseek", self._summary(result) + " (wishlist if Soulseek has nothing)"
        tried.append(f"Galaxy · query “{name} {title}”: " + ("no download link" if skip_galaxy else "not found"))
        return None, self._not_found(galaxy_note, tried)

    def _galaxy_song(self, name, title):
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

    def _wish(self, kind, artist, title, album="", status="missing", note=""):
        """Not found anywhere: put it on the wishlist, which tries again every few days."""
        wishlist = getattr(self.studio, "wishlist", None)
        if wishlist is None or not title:
            return note
        try:
            wishlist.add(kind, artist, title, album, status or "missing", note)
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
        ctx = {"bandcamp": None, "bandcamp_checked": False}
        try:
            source = None
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
            activity.record("downloads", "info", f"YouTube age-restricted {item['title']}: trying {source} instead", source=source.casefold())
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
