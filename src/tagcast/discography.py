"""Discography, Top 30 and "get the missing" (fork v15).

For an artist, Tagcast combines Deezer (one search + the album list) and Apple Music / iTunes
(one search + one lookup) into one discography: albums, EPs, singles and compilations with year,
track count and cover. MusicBrainz is added only when asked. Results are cached for 7 days in
``discography_cache.json``. Every release shows Have / Partly / Downloaded before / Missing against
the iBroadcast library and the download history.

Top 30 songs come from Deezer's ranking (with 30-second previews); when Deezer is unavailable,
Galaxy's search order is used. Each song shows Have / Missing.

"Get the missing" runs in the background and tries the sources in the agreed order:
**Bandcamp → Galaxy → Soulseek** (Galaxy before Soulseek since v45: it is the steadier of the two).
Bandcamp is used when the artist has the album there; then Galaxy; if Galaxy has nothing, Soulseek
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

from . import artist_match as match
from .downloads import album_key
from .galaxy import FALLBACK_SOURCES, find_song
from .library import LibraryError
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
NOT_FOUND = "Not found on Bandcamp, Galaxy or Soulseek"
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
    return album_key(SUFFIX.sub("", match.clean(title)))


class Discography:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(CACHE_FILE)
        self.cache = data if isinstance(data, dict) else {}
        self.jobs = {}
        soulseek = getattr(studio, "soulseek", None)
        if soulseek is not None:
            soulseek.on_failed = self.fallback

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
        return {"name": name, "artist": chosen, "candidates": candidates[:6], "releases": list(releases.values()), "notes": notes}

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
        if musicbrainz:
            self._add_musicbrainz(data)
        self._statuses(data)
        data["releases"].sort(key=lambda r: (r.get("year") or "0000", r["title"]), reverse=True)
        data["counts"] = dict(Counter(r["status"] for r in data["releases"]))
        return data

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

    def status(self, job_id):
        job = self.jobs.get(str(job_id))
        if not job:
            raise DiscographyError("That request is no longer running.")
        return job

    def _run(self, job, albums, songs):
        ctx = {"bandcamp": None, "bandcamp_checked": False}
        for a in albums:
            title = match.clean((a or {}).get("title"))
            try:
                source, note = self._album(job["name"], title, a.get("status"), a.get("deezer_id"), ctx)
            except Exception as error:  # one album never stops the rest
                log.exception("Getting %s failed", title)
                source, note = None, str(error)
            if not source:
                note = self._wish("album", job["name"], title, status=a.get("status"), note=note)
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

    def _album(self, name, title, status, deezer_id, ctx):
        # 1. Bandcamp: the artist's page first, then Bandcamp's own album search (v46)
        why = self._bandcamp_album(name, title, ctx)
        if isinstance(why, tuple):
            return why
        # 2. Galaxy
        source, note = self._galaxy_album(name, title)
        if source:
            return source, f"{note} (Bandcamp: {why})"
        # 3. Soulseek (whole albums only; a partly owned album would be fetched again)
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured() and status != "partly":
            result = soulseek.add([{"kind": "album", "artist": name, "album": title, "fallback": True}])
            return "Soulseek", self._summary(result) + " (wishlist if Soulseek has nothing)"
        return None, NOT_FOUND

    def _galaxy_sources(self):
        """The chosen source first, then the other open ones (v32): one source missing a song
        no longer means Galaxy hasn't got it."""
        chosen = self.studio.galaxy.state.get("source") or "netease"
        return list(dict.fromkeys([chosen, *FALLBACK_SOURCES]))

    def _galaxy_album(self, name, title):
        galaxy = self.studio.galaxy
        for source in self._galaxy_sources():
            data = galaxy.album(title, name, source)
            if data.get("found"):
                return "Galaxy", self._summary(galaxy.add(data["tracks"], data["album_artist"], len(data["tracks"])))
        return None, NOT_FOUND

    def _song(self, name, title, album, ctx):
        base = self._bandcamp_base(name, ctx, [album] if album else [])
        if base and album:
            try:
                release = self.studio.bandcamp.find_album(base, album)
                track = release and self.studio.bandcamp.find_track(release, title)
                if track:
                    return "Bandcamp", self._summary(self.studio.bandcamp.add(release["url"], [track["num"]]))
            except LibraryError as error:
                log.info("Bandcamp song failed: %s", error)
        source, note = self._galaxy_song(name, title)
        if source:
            return source, note
        soulseek = getattr(self.studio, "soulseek", None)
        if soulseek is not None and soulseek.configured():
            result = soulseek.add([{"kind": "song", "artist": name, "title": title, "album": album, "fallback": True}])
            return "Soulseek", self._summary(result) + " (wishlist if Soulseek has nothing)"
        return None, NOT_FOUND

    def _galaxy_song(self, name, title):
        galaxy = self.studio.galaxy
        best = find_song(galaxy.api, self._galaxy_sources(), name, title)
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

    def fallback(self, item):
        """A Soulseek job of "get the missing" failed. Galaxy was already tried before Soulseek
        (v45), so what is left is the wishlist, which tries everything again later."""
        if not item:
            return
        note = self._wish(item["kind"], item["artist"], item["title"], item.get("album", ""), note="Not found on Soulseek")
        self.manager.update_item(item["id"], persist=True,
                                 note=f"{item.get('note', '')} · {note}".strip(" ·"))
