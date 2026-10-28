"""New-release alerts (fork v16).

For the artists you pick, plus (when switched on) every album artist with 3 or more albums in
your iBroadcast library, Tagcast checks Deezer and Apple Music for new releases once every 7 days
(adjustable) while it is running, and on request. A release is new when it came out after that
artist's previous check; the first check of an artist counts the last 90 days. New releases you
don't fully have are listed with their status, for "Get the missing" (Bandcamp → Galaxy →
Soulseek). State: ``releases.json`` in the settings folder.
"""

import logging
import secrets
import threading
import time
from collections import defaultdict

from . import artist_match as match
from .discography import release_key
from .library import LibraryError, number

log = logging.getLogger("tagcast")
STATE_FILE = "releases.json"
FIRST_LOOKBACK = 90 * 86400
MAX_RESULTS = 500


def date_ts(value):
    value = str(value or "")[:10]
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return time.mktime(time.strptime(value[:size], fmt))
        except ValueError:
            continue
    return 0.0


def library_artists(library, minimum=3):
    """Album artists with at least ``minimum`` different albums (discs of one set count once)."""
    albums = defaultdict(set)
    names = {}
    for album_id in library.album_ids():
        album = library.albums[album_id]
        name = library.artist_name(album.get("artist_id"))
        if number(album.get("artist_id")) == 0:
            continue  # Various Artists
        key = match.match_key(name)
        names.setdefault(key, name)
        albums[key].add(release_key(album.get("name")))
    return sorted((names[k] for k, v in albums.items() if len(v) >= minimum), key=str.casefold)


class NewReleases:
    def __init__(self, studio, scheduler=True):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("artists", []), ("auto", True), ("every_days", 7), ("last_check", 0), ("checked", {}),
                           ("results", []), ("unseen", 0)):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.progress = None  # {"done", "total", "artist"} while a check runs
        if scheduler:
            threading.Thread(target=self._schedule, name="new-releases", daemon=True).start()

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    # -- reading ---------------------------------------------------------------------------

    def artists(self):
        picked = list(self.state["artists"])
        if not self.state["auto"]:
            return picked
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return picked
        seen = {match.match_key(a) for a in picked}
        return picked + [a for a in library_artists(library) if match.match_key(a) not in seen]

    def _refresh_statuses(self, results):
        """What you have changes after downloads: compare the listed releases again (no requests)."""
        if not results:
            return
        try:
            checked = self.studio.download_manager.check(
                [{"kind": "album", "artist": r["artist"], "title": r["title"], "tracks": r.get("tracks")} for r in results])["results"]
        except Exception:
            return
        for r, c in zip(results, checked):
            r["status"] = "missing" if c["status"] == "new" else c["status"]

    def view(self):
        with self.lock:
            self._refresh_statuses([r for r in self.state["results"] if not r.get("dismissed")])
            every = self.state["every_days"] * 86400
            return {"artists": list(self.state["artists"]), "auto": self.state["auto"], "every_days": self.state["every_days"],
                    "last_check": self.state["last_check"],
                    "next_check": (self.state["last_check"] + every) if self.state["last_check"] else 0,
                    "results": [r for r in self.state["results"] if not r.get("dismissed")],
                    "unseen": self.state["unseen"], "progress": self.progress}

    def summary(self):
        return {"unseen": self.state["unseen"], "running": bool(self.progress)}

    # -- settings --------------------------------------------------------------------------

    def save_settings(self, body):
        if not isinstance(body, dict):
            raise LibraryError("Choose the new-release settings.")
        with self.lock:
            if "auto" in body:
                self.state["auto"] = body["auto"] is True
            if "every_days" in body:
                try:
                    days = int(body["every_days"])
                except (TypeError, ValueError):
                    raise LibraryError("Check every 1 to 30 days.") from None
                if not 1 <= days <= 30:
                    raise LibraryError("Check every 1 to 30 days.")
                self.state["every_days"] = days
            if "add" in body:
                name = match.clean(body["add"])
                if not name or len(name) > 200:
                    raise LibraryError("Type an artist name.")
                if match.match_key(name) not in {match.match_key(a) for a in self.state["artists"]}:
                    if len(self.state["artists"]) >= 500:
                        raise LibraryError("You follow 500 artists already.")
                    self.state["artists"].append(name)
            if "remove" in body:
                key = match.match_key(body["remove"])
                self.state["artists"] = [a for a in self.state["artists"] if match.match_key(a) != key]
        self._persist()
        return self.view()

    def mark_seen(self):
        with self.lock:
            self.state["unseen"] = 0
        self._persist()
        return self.view()

    def dismiss(self, ids):
        wanted = set(map(str, ids or []))
        with self.lock:
            for r in self.state["results"]:
                if r["id"] in wanted:
                    r["dismissed"] = True
        self._persist()
        return self.view()

    # -- checking --------------------------------------------------------------------------

    def check(self):
        """Start a check in the background (one at a time)."""
        self.studio._require_client()
        with self.lock:
            if self.progress:
                return self.view()
            self.progress = {"done": 0, "total": 0, "artist": ""}
        threading.Thread(target=self._run, name="new-releases-check", daemon=True).start()
        return self.view()

    def _run(self):
        found = 0
        try:
            names = self.artists()
            self.progress["total"] = len(names)
            now = time.time()
            for name in names:
                self.progress["artist"] = name
                try:
                    found += self._check_artist(name, now)
                except Exception as error:  # one artist never stops the others
                    log.info("New releases for %s failed: %s", name, error)
                self.progress["done"] += 1
            with self.lock:
                self.state["last_check"] = now
                self.state["unseen"] += found
        finally:
            with self.lock:
                self.progress = None
            self._persist()

    def _check_artist(self, name, now):
        key = match.match_key(name)
        since = self.state["checked"].get(key) or (now - FIRST_LOOKBACK)
        data = self.studio.discography.view(name, refresh=True)
        known = {(r["artist_key"], r["release_key"]) for r in self.state["results"]}
        new = 0
        with self.lock:
            for r in data["releases"]:
                when = date_ts(r.get("date") or r.get("year"))
                if when <= since or when > now + 86400 or r["status"] == "have":
                    continue
                ident = (key, release_key(r["title"]))
                if ident in known:
                    continue
                known.add(ident)
                self.state["results"].insert(0, {
                    "id": secrets.token_hex(6), "artist": data["name"], "artist_key": key, "release_key": ident[1],
                    "title": r["title"], "date": r.get("date") or r.get("year") or "", "type": r.get("type", "album"),
                    "status": r["status"], "tracks": r.get("tracks"), "cover": r.get("cover", ""),
                    "deezer_id": r.get("deezer_id"), "found": now})
                new += 1
            del self.state["results"][MAX_RESULTS:]
            self.state["checked"][key] = now
        if new:
            self._persist()
        return new

    def _schedule(self):
        time.sleep(30)  # let Tagcast and the library start first
        while True:
            try:
                due = time.time() - self.state["last_check"] >= self.state["every_days"] * 86400
                if due and (self.state["auto"] or self.state["artists"]) and not self.progress and self.studio.client:
                    self.check()
            except Exception as error:
                log.info("Scheduled new-release check skipped: %s", error)
            time.sleep(600)
