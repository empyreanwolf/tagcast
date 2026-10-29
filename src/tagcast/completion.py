"""Complete my albums (fork v18).

A scan of the whole iBroadcast library (on request, in the background) finds albums that are
only partly there:

- **Track numbers**: gaps in a disc's numbering (you have 1, 2 and 5 of a disc that goes to 5);
- **Deezer**: the album's track count (one search per album, ~7 per second, cached 30 days).
  When Deezer lists the exact title more than once (regional editions), the smallest count that
  is at least what you have is used, so another edition never makes an album look incomplete.

Discs of one set count together; Various Artists albums are left out. The report lists what you
have and what the album should have; the missing titles come from Deezer when you open an album.
"Get the missing" sends albums through Galaxy → Bandcamp (a partly owned album skips Soulseek),
and every song still passes the duplicate check. State: ``completion.json``.
"""

import logging
import threading
import time
from collections import defaultdict

from . import artist_match as match
from .downloads import album_key, song_key
from .library import LibraryError, number, text
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
STATE_FILE = "completion.json"
DEEZER = "https://api.deezer.com"
CACHE_DAYS = 30


def library_sets(library):
    """[{key, artist, title, album_ids, tracks: [{title, number, disc}]}] with discs of one set together."""
    sets = {}
    for album_id in library.album_ids():
        album = library.albums[album_id]
        artist_id = number(album.get("artist_id"))
        if artist_id == 0:
            continue
        title = text(album.get("name"))
        key = (album_key(title), artist_id)
        entry = sets.setdefault(key, {"key": f"{artist_id}|{key[0]}", "artist": library.artist_name(artist_id),
                                      "title": title, "album_ids": [], "tracks": []})
        entry["album_ids"].append(album_id)
        disc = number(album.get("disc")) or 1
        for track_id in library.active_track_ids(album_id):
            t = library.tracks[track_id]
            entry["tracks"].append({"title": text(t.get("title")), "number": number(t.get("track")), "disc": disc})
    return list(sets.values())


def gaps(tracks):
    """Missing positions per disc, from the track numbers you have."""
    by_disc = defaultdict(set)
    for t in tracks:
        if t["number"] and t["number"] > 0:
            by_disc[t["disc"]].add(t["number"])
    out = {}
    for disc, numbers in sorted(by_disc.items()):
        missing = [n for n in range(1, max(numbers) + 1) if n not in numbers]
        if missing:
            out[disc] = missing
    return out


class Completion:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("albums", []), ("scanned", 0), ("counts", {}), ("total", 0)):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.progress = None

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    def view(self):
        with self.lock:
            albums = self.state["albums"]
            self._refresh(albums)
            return {"albums": [a for a in albums if a["have"] < a["expected"]], "scanned": self.state["scanned"],
                    "total": self.state["total"], "progress": self.progress}

    def _refresh(self, albums):
        """After downloads and a library reload, albums can be complete now (no requests)."""
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return
        current = {s["key"]: s for s in library_sets(library)}
        for a in albums:
            s = current.get(a["key"])
            if s:
                a["have"] = len(s["tracks"])
                a["gaps"] = {str(k): v for k, v in gaps(s["tracks"]).items()}

    # -- scanning ----------------------------------------------------------------------------

    def scan(self):
        self.studio._require_client()
        with self.lock:
            if self.progress:
                return self.view()
            self.progress = {"done": 0, "total": 0, "title": ""}
        threading.Thread(target=self._run, name="complete-albums", daemon=True).start()
        return self.view()

    def _run(self):
        try:
            library, _ = self.studio._library()
            sets = library_sets(library)
            self.progress["total"] = len(sets)
            found = []
            for s in sets:
                self.progress["title"] = s["title"]
                try:
                    row = self._check(s)
                except Exception as error:
                    log.info("Album completeness of %s failed: %s", s["title"], error)
                    row = None
                if row:
                    found.append(row)
                self.progress["done"] += 1
                if self.progress["done"] % 50 == 0:
                    self._persist()  # keep the Deezer counts if Tagcast stops
            found.sort(key=lambda r: (match.match_key(r["artist"]), r["title"].casefold()))
            with self.lock:
                self.state.update(albums=found, scanned=time.time(), total=len(sets))
        finally:
            with self.lock:
                self.progress = None
            self._persist()

    def _deezer_count(self, artist, title, have):
        key = f"{match.match_key(artist)}|{album_key(title)}"
        hit = self.state["counts"].get(key)
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400:
            return hit
        try:
            rows = (self.http.json(f"{DEEZER}/search/album", {"q": f'artist:"{artist}" album:"{title}"', "limit": 10}) or {}).get("data") or []
        except SourceError as error:
            log.info("Deezer album search failed: %s", error)
            return {}
        same = [r for r in rows if album_key(r.get("title")) == album_key(title)
                and match.match_key((r.get("artist") or {}).get("name")) == match.match_key(artist) and r.get("nb_tracks")]
        fitting = sorted((r for r in same if int(r["nb_tracks"]) >= have), key=lambda r: int(r["nb_tracks"]))
        best = fitting[0] if fitting else None
        hit = {"nb": int(best["nb_tracks"]) if best else None, "deezer_id": best.get("id") if best else None,
               "cover": best.get("cover_medium", "") if best else "", "when": time.time()}
        with self.lock:
            self.state["counts"][key] = hit
        return hit

    def _check(self, s):
        have = len(s["tracks"])
        holes = gaps(s["tracks"])
        by_numbers = have + sum(len(v) for v in holes.values())
        online = self._deezer_count(s["artist"], s["title"], have)
        expected = max(by_numbers, online.get("nb") or 0)
        if expected <= have:
            return None
        return {"key": s["key"], "artist": s["artist"], "title": s["title"], "have": have, "expected": expected,
                "gaps": {str(k): v for k, v in holes.items()}, "deezer_id": online.get("deezer_id"),
                "cover": online.get("cover", ""), "album_id": str(s["album_ids"][0]),
                "basis": "Deezer" if (online.get("nb") or 0) >= by_numbers and online.get("nb") else "track numbers"}

    # -- the missing titles ------------------------------------------------------------------

    def missing_tracks(self, key):
        with self.lock:
            row = next((a for a in self.state["albums"] if a["key"] == key), None)
        if not row:
            raise LibraryError("Scan again: this album is no longer in the report.")
        self.studio._require_client()
        library, _ = self.studio._library()
        current = next((s for s in library_sets(library) if s["key"] == key), None)
        have = {song_key(t["title"]) for t in (current or {}).get("tracks", [])}
        if not row.get("deezer_id"):
            return {"key": key, "tracks": [], "note": "Deezer doesn't list this album; the gaps in your track numbers are shown."}
        try:
            rows = (self.http.json(f"{DEEZER}/album/{row['deezer_id']}/tracks", {"limit": 200}) or {}).get("data") or []
        except SourceError as error:
            raise LibraryError(f"Deezer {error}") from None
        tracks = [{"number": r.get("track_position"), "disc": r.get("disk_number"), "title": match.clean(r.get("title")),
                   "have": song_key(r.get("title")) in have} for r in rows]
        return {"key": key, "tracks": tracks, "note": ""}
