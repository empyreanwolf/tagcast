"""Recommendations (fork v23; on the Charts page since v44).

A "Recommended for you" tab on the Charts page: artists you likely don't have much of yet,
seeded from the artists New Releases already tracks (``new_releases.artists()`` -- the ones
you follow, plus, if that setting is on, every album artist with 3+ albums in your library).

Deterministic, no AI. For each seed artist, finds its Deezer artist id (matched by exact name,
picking the most-followed act when more than one shares that name -- the same tie-break
Discography uses) and calls Deezer's own ``/artist/{id}/related`` endpoint. Related artists are
tallied across all your seeds, so someone related to several of your artists ranks above
someone related to only one, then each kept artist's current Deezer top track is looked up so
there is a real song to get. (The Gemini-based list was removed in v44 along with Ask Gemini.)

Songs you pick go through the same Galaxy -> Bandcamp -> Soulseek order as Charts/Discography,
one at a time; not found anywhere lands on the wishlist. State: in-memory cache only
(``CACHE_SECONDS``), nothing persisted -- the seed list and the API results are cheap enough to
redo on request via "Refresh".
"""

import logging
import threading
import time

import requests

from . import artist_match as match
from .library import LibraryError, text
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
DEEZER = "https://api.deezer.com"
CACHE_SECONDS = 24 * 3600
MAX_SEEDS = 20
MAX_RELATED_PER_SEED = 10
MAX_ROWS = 30
SOURCES = ("deezer",)


class RecommendationsError(LibraryError):
    pass


class Recommendations:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        self.lock = threading.RLock()
        self.cache = {}  # "deezer" -> {"rows", "when", "error", "seeds"}
        self.progress = {}  # source -> {"done", "total", "title"} while "Get it" runs
        self.jobs = []  # recent per-song "Get it" outcomes, most recent first

    # -- reading --------------------------------------------------------------------------

    def _seed_artists(self):
        try:
            self.studio._require_client()
            names = self.studio.new_releases.artists()
        except Exception:
            names = list(self.studio.new_releases.state.get("artists", []))
        return names[:MAX_SEEDS]

    def view(self, source, refresh=False):
        if source not in SOURCES:
            raise RecommendationsError("Unknown source.")
        with self.lock:
            hit = self.cache.get(source)
            fresh = hit and not refresh and hit["when"] > time.time() - CACHE_SECONDS
        if not fresh:
            self._load(source)
        with self.lock:
            hit = self.cache.get(source, {"rows": [], "when": 0, "error": "", "seeds": []})
            return {"source": source, "rows": hit["rows"], "when": hit["when"], "error": hit["error"],
                    "seeds": hit["seeds"], "progress": self.progress.get(source)}

    def _load(self, source):
        seeds = self._seed_artists()
        try:
            if not seeds:
                raise RecommendationsError(
                    "No artists to base this on yet -- follow some on the New releases page, "
                    "or turn on its library-based artists in Settings.")
            rows = self._deezer_related(seeds)
            rows = self._with_status(rows)
            error = ""
        except (LibraryError, SourceError, requests.RequestException, ValueError) as err:
            rows, error = [], str(err)
            log.info("Recommendations %s failed: %s", source, err)
        with self.lock:
            self.cache[source] = {"rows": rows, "when": time.time(), "error": error, "seeds": seeds}

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
            if result["status"] == "downloaded":   # v54: which PC fetched it, so it is not downloaded twice
                row["pc"] = next((h.get("pc") for h in result.get("you_have") or [] if h.get("pc")), "")
        return rows

    # -- Deezer: deterministic, no AI -----------------------------------------------------

    def _deezer_candidate(self, name):
        try:
            rows = (self.http.json(f"{DEEZER}/search/artist", {"q": name, "limit": 10}) or {}).get("data") or []
        except SourceError:
            return None
        matches = [r for r in rows if match.match_key(r.get("name")) == match.match_key(name)]
        return max(matches, key=lambda r: r.get("nb_fan", 0)) if matches else None

    def _deezer_top_track(self, artist_id):
        try:
            rows = (self.http.json(f"{DEEZER}/artist/{artist_id}/top", {"limit": 1}) or {}).get("data") or []
        except SourceError:
            return ""
        return text(rows[0].get("title")) if rows else ""

    def _deezer_related(self, seeds):
        mine = {match.match_key(s) for s in seeds}
        tally = {}  # match_key -> {"name", "count", "artist_id"}
        any_seed_found = False
        for name in seeds:
            seed = self._deezer_candidate(name)
            if not seed:
                continue
            any_seed_found = True
            try:
                related = (self.http.json(f"{DEEZER}/artist/{seed['id']}/related",
                                           {"limit": MAX_RELATED_PER_SEED}) or {}).get("data") or []
            except SourceError:
                continue
            for r in related:
                rname = text(r.get("name"))
                key = match.match_key(rname)
                if not rname or key in mine:
                    continue
                entry = tally.setdefault(key, {"name": rname, "count": 0, "artist_id": r.get("id")})
                entry["count"] += 1
        if not any_seed_found:
            raise RecommendationsError("Deezer doesn't recognize any of your seed artists right now.")
        if not tally:
            raise RecommendationsError("Deezer didn't suggest anyone new beyond who you already have.")
        ranked = sorted(tally.values(), key=lambda e: -e["count"])
        rows = []
        for entry in ranked:
            if len(rows) >= MAX_ROWS:
                break
            title = self._deezer_top_track(entry["artist_id"])
            if title:
                rows.append({"rank": len(rows) + 1, "artist": entry["name"], "title": title, "album": "",
                             "source": "", "source_id": "", "why": f"related to {entry['count']} artist(s) you have"})
        if not rows:
            raise RecommendationsError("Deezer suggested artists, but none with a current top track to try.")
        return rows

    # -- getting songs --------------------------------------------------------------------

    def get(self, source, ranks):
        if source not in SOURCES:
            raise RecommendationsError("Unknown source.")
        with self.lock:
            hit = self.cache.get(source)
        if not hit or not hit["rows"]:
            raise RecommendationsError("Scan this list first.")
        wanted = set(ranks or [])
        rows = [r for r in hit["rows"] if r["rank"] in wanted]
        if not rows:
            raise RecommendationsError("Choose at least one song.")
        with self.lock:
            if self.progress.get(source):
                raise RecommendationsError("Still getting songs from this list.")
            self.progress[source] = {"done": 0, "total": len(rows), "title": ""}
        threading.Thread(target=self._get_rows, args=(source, rows), name=f"recs-get-{source}", daemon=True).start()
        return {"jobs": len(rows)}

    def _get_rows(self, source, rows):
        ctx = {}
        discography = self.studio.discography
        try:
            for row in rows:
                self.progress[source]["title"] = row["title"]
                artist_ctx = ctx.setdefault(match.match_key(row["artist"]), {"bandcamp": None, "bandcamp_checked": False})
                try:
                    found, note = discography._song(row["artist"], row["title"], row.get("album", ""), artist_ctx)
                except Exception as error:
                    found, note = "", str(error)
                if not found:
                    note = discography._wish("song", row["artist"], row["title"], row.get("album", ""), "missing", note)
                with self.lock:
                    self.jobs.insert(0, {"title": row["title"], "artist": row["artist"], "source": found, "note": note})
                    del self.jobs[200:]
                self.progress[source]["done"] += 1
        finally:
            with self.lock:
                self.progress.pop(source, None)

    def jobs_view(self):
        with self.lock:
            return {"jobs": list(self.jobs[:50]), "progress": dict(self.progress)}
