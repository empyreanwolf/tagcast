"""Track numbers from a real source, for the whole account (fork v91).

A slow background run over every album that has songs without a track number. For each album it asks
MusicBrainz, then Discogs (when a token is saved in Settings), for the release's track list, matches the
songs by title and writes the number the release gives them. Nothing is guessed from file order:

* a song is numbered only when its title matches exactly one place on a release of the same album and artist;
* a release whose track list disagrees with a number you already have is not used at all;
* songs that already have a number are never changed, and a song whose title appears twice is left alone.

The numbers are written through the normal save, so they show in Change history and can be undone.
Pause, Resume and Stop; what was checked is kept in ``track-numbers.json``.
"""

import logging
import re
import threading
import time

from . import activity
from . import artist_match as match
from .downloads import close_title, song_key
from .galaxy import sim
from .library import LibraryError, number, text

log = logging.getLogger("tagcast")
STATE_FILE = "track-numbers.json"
MB = "https://musicbrainz.org/ws/2"
DISCOGS = "https://api.discogs.com"
ALBUM_PAUSE = 1.0
SAVE_EVERY = 10
MAX_ERRORS = 5
STATUS_LABELS = {"numbered": "Numbered", "partly": "Partly numbered", "none": "No source had it"}


class _Interrupt(Exception):
    pass


class _Pause(_Interrupt):
    pass


class _Stop(_Interrupt):
    pass


def positions_of(listing, title):
    """The places on a track list (list of (position, title)) whose title is this song."""
    exact = [pos for pos, other in listing if song_key(other) == song_key(title)]
    return exact or [pos for pos, other in listing if close_title(title, other)]


def plan_numbers(tracks, listings):
    """tracks: [{"id", "title", "number"}]. The best listing and what it gives: (listing, {track id: position},
    unmatched titles), or None when no listing fits. A listing that contradicts a number you have is skipped."""
    best = None
    for listing in listings:
        rows = listing["tracks"]
        wanted, conflicts = {}, 0
        for track in tracks:
            found = positions_of(rows, track["title"])
            if len(found) != 1:
                continue
            if track["number"] and track["number"] != found[0]:
                conflicts += 1
            elif not track["number"]:
                wanted[track["id"]] = found[0]
        if conflicts:
            continue
        taken = {t["number"] for t in tracks if t["number"]}
        count = {}
        for position in wanted.values():
            count[position] = count.get(position, 0) + 1
        wanted = {tid: pos for tid, pos in wanted.items() if count[pos] == 1 and pos not in taken}
        if wanted and (best is None or len(wanted) > len(best[1])):
            best = (listing, wanted)
    if best is None:
        return None
    listing, wanted = best
    missing = [t["title"] for t in tracks if not t["number"] and t["id"] not in wanted]
    return listing, wanted, missing


def discogs_positions(tracklist):
    """Discogs' tracklist -> [(position, title)]: "3", "2-4" and "2.4" give the last number; vinyl sides ("A1")
    or anything else give the running count of real tracks."""
    rows = [t for t in tracklist or [] if (t.get("type_") or "track") == "track" and t.get("title")]
    out = []
    for index, row in enumerate(rows, 1):
        raw = str(row.get("position") or "").strip()
        found = re.fullmatch(r"(?:\d+[-.])?(\d+)", raw)
        if raw and not found and not re.fullmatch(r"[A-Za-z]+\d*", raw):
            found = None
        out.append((int(found.group(1)) if found else None, row["title"], index))
    if all(p is not None for p, _, _ in out):
        return [(p, t) for p, t, _ in out]
    return [(i, t) for _, t, i in out]


class TrackNumberJob:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        self.running = False
        self.paused = False
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.thread = None
        self.progress = None
        self.jobs = []
        self.message = ""
        self.data = None  # {"account", "checked": {album id: status}, "review": {album id: {...}}}
        self._dirty = 0

    # -- stored state ---------------------------------------------------------------------

    def _state(self, account=None):
        with self.lock:
            if self.data is None:
                saved = self.studio._read(STATE_FILE)
                self.data = saved if isinstance(saved, dict) else {}
            if account is not None and self.data.get("account") != account:
                self.data = {"account": account}
            self.data.setdefault("checked", {})
            self.data.setdefault("review", {})
            return self.data

    def _save_state(self):
        with self.lock:
            if self.data is None:
                return
            try:
                self.studio._write(STATE_FILE, self.data)
                self._dirty = 0
            except OSError as error:
                log.warning("Could not save the track number state: %s", error)

    # -- reading ----------------------------------------------------------------------------

    @staticmethod
    def _albums(library):
        """Album ids that have at least one active song without a track number."""
        out = []
        for album_id in library.album_ids():
            ids = library.active_track_ids(album_id)
            if any(not number(library.tracks[i].get("track")) for i in ids):
                out.append(album_id)
        return sorted(out, key=number)

    def status(self):
        self.studio._require_client()
        library, user = self.studio._library()
        with self.lock:
            state = self._state(str(user))
            counts = {}
            for value in state["checked"].values():
                counts[value] = counts.get(value, 0) + 1
            albums = self._albums(library)
            remaining = sum(1 for a in albums if str(a) not in state["checked"])
            review = [{"album_id": aid, **row} for aid, row in list(state["review"].items())[:100]]
            return {"running": self.running, "paused": self.paused, "message": self.message,
                    "progress": dict(self.progress) if self.progress else None, "jobs": list(self.jobs[:50]),
                    "counts": counts, "total": len(albums), "remaining": remaining, "review": review,
                    "review_count": len(state["review"]), "discogs": bool(self._discogs_token())}

    # -- control ------------------------------------------------------------------------------

    def start(self, retry=False):
        client = self.studio._require_client()
        library, user = self.studio._library()
        with self.lock:
            if self.running:
                raise LibraryError("Already numbering tracks.")
            state = self._state(str(user))
            if retry:
                for aid in [a for a, v in state["checked"].items() if v in ("none", "partly")]:
                    del state["checked"][aid]
                    state["review"].pop(aid, None)
            self.running, self.paused, self.message = True, False, ""
            self.stop_flag.clear()
            self.pause_flag.clear()
            self.progress = {"done": 0, "total": 0, "name": "", "phase": "starting"}
        self.thread = threading.Thread(target=self._run, args=(client, library, str(user)), name="track-numbers", daemon=True)
        self.thread.start()
        return {"started": True}

    def pause(self):
        with self.lock:
            if not self.running:
                raise LibraryError("Nothing is running.")
            self.paused = True
            self.pause_flag.set()
        return {"paused": True}

    def resume(self):
        with self.lock:
            if not self.running:
                raise LibraryError("Nothing is running.")
            self.paused = False
            self.pause_flag.clear()
        return {"resumed": True}

    def stop(self):
        with self.lock:
            if not self.running:
                raise LibraryError("Nothing is running.")
        self.stop_flag.set()
        return {"stopping": True}

    def dismiss(self, album_id):
        with self.lock:
            self._state()["review"].pop(str(album_id), None)
            self._dirty += 1
        self._save_state()
        return {"dismissed": True}

    def _check(self):
        if self.stop_flag.is_set():
            raise _Stop()
        if self.pause_flag.is_set():
            raise _Pause()

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while True:
            self._check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(.25, left))

    def _hold(self):
        while self.pause_flag.is_set():
            if self.stop_flag.is_set():
                raise _Stop()
            time.sleep(.3)

    def _current(self, client):
        if self.studio.client is not client:
            self.message = "Stopped: the iBroadcast account changed."
            raise _Stop()

    # -- the run -----------------------------------------------------------------------------------

    def _run(self, client, library, account):
        errors = 0
        try:
            state = self._state(account)
            todo = [a for a in self._albums(library) if str(a) not in state["checked"]]
            with self.lock:
                self.progress = {"done": 0, "total": len(todo), "name": "", "phase": "looking"}
            for album_id in todo:
                while True:
                    try:
                        self._hold()
                        self._current(client)
                        self._one(library, album_id)
                        errors = 0
                        break
                    except _Pause:
                        continue
                    except _Stop:
                        raise
                    except Exception as error:
                        if type(error).__name__ == "NotConnected":
                            with self.lock:
                                self.message = "Stopped: you are disconnected from iBroadcast."
                            raise _Stop() from None
                        errors += 1  # not recorded as checked: tried again on the next run
                        log.info("Track numbers: album %s skipped: %s", album_id, error)
                        with self.lock:
                            self.message = f"An album was skipped: {error}"
                            if self.progress:
                                self.progress["done"] += 1
                        if errors >= MAX_ERRORS:
                            with self.lock:
                                self.paused = True
                                self.pause_flag.set()
                                self.message = f"Paused after {errors} errors in a row ({error}). Press Resume to continue."
                            errors = 0
                            continue
                        break
                if self._dirty >= SAVE_EVERY:
                    self._save_state()
                try:
                    self._sleep(ALBUM_PAUSE)
                except _Pause:
                    pass
        except _Stop:
            pass
        except Exception as error:  # a crash ends the run, never the app
            log.exception("Track number job failed")
            with self.lock:
                self.message = f"Stopped: {error}"
        finally:
            self._save_state()
            with self.lock:
                done = (self.progress or {}).get("done", 0)
                self.running = self.paused = False
                self.progress = None
            self.pause_flag.clear()
            self.stop_flag.clear()
            activity.record("metadata", "info", f"Track number job ended after {done} albums", source="track_numbers")

    def _one(self, library, album_id):
        album = library.albums[album_id]
        album_name = text(album.get("name"))
        artist = library.artist_name(album.get("artist_id")) if album.get("artist_id") else ""
        tracks = [{"id": i, "title": text(library.tracks[i].get("title")), "number": number(library.tracks[i].get("track"))}
                  for i in library.active_track_ids(album_id)]
        with self.lock:
            if self.progress:
                self.progress["name"] = f"{artist} - {album_name}"
                self.progress["phase"] = "looking"
        if not tracks or all(t["number"] for t in tracks) or not artist or not album_name:
            self._record(album_id, artist, album_name, "none", "", "Nothing to number here.", [])
            return
        listings = self.listings(artist, album_name)
        found = plan_numbers(tracks, listings)
        if not found:
            note = "No release had these songs in a matching track list." if listings else "No release found on MusicBrainz or Discogs."
            self._record(album_id, artist, album_name, "none", "", note, [])
            return
        listing, wanted, missing = found
        with self.lock:
            if self.progress:
                self.progress["phase"] = "saving"
        saved = self._write(album_id, tracks, wanted)
        status = "numbered" if saved == len(wanted) and not missing else "partly"
        note = f"{saved} of {len([t for t in tracks if not t['number']])} songs numbered from “{listing['title']}”."
        self._record(album_id, artist, album_name, status, listing["source"], note, missing)

    def _write(self, album_id, tracks, wanted):
        by_id = {t["id"]: t for t in tracks}
        changes = [{"kind": "track", "id": str(tid), "albumId": str(album_id), "label": by_id[tid]["title"],
                    "fields": {"track": {"before": 0, "after": position}}} for tid, position in sorted(wanted.items(), key=lambda x: x[1])]
        end = time.monotonic() + 120
        while True:
            try:
                result = self.studio.save(changes)
                break
            except Exception as error:
                if "still running" in str(error) and time.monotonic() < end:
                    self._sleep(2)
                    continue
                raise
        failed = [r for r in result.get("results", []) if r.get("status") != "sent"]
        return len(changes) - len(failed)

    def _record(self, album_id, artist, album_name, status, source, note, missing):
        with self.lock:
            state = self._state()
            state["checked"][str(album_id)] = status
            self._dirty += 1
            if status == "partly" and missing:
                state["review"][str(album_id)] = {"artist": artist, "album": album_name, "missing": missing[:12], "source": source}
            else:
                state["review"].pop(str(album_id), None)
            self.jobs.insert(0, {"album_id": str(album_id), "artist": artist, "album": album_name, "status": status,
                                 "source": source, "note": note})
            del self.jobs[200:]
            if self.progress:
                self.progress["done"] += 1
        if status in ("numbered", "partly"):
            activity.record("metadata", "ok", f"{artist} - {album_name}: {note}", source="track_numbers")

    # -- sources ------------------------------------------------------------------------------------

    @property
    def http(self):
        return self.studio.discography.http

    def _discogs_token(self):
        try:
            return self.studio.discography._discogs_token()
        except Exception:
            return ""

    def listings(self, artist, album):
        """Track lists of releases of this album by this artist: MusicBrainz first, Discogs after."""
        out = []
        for lookup in (self._musicbrainz, self._discogs):
            try:
                out += lookup(artist, album)
            except Exception as error:
                log.info("Track numbers: %s failed for %s - %s: %s", lookup.__name__.strip("_"), artist, album, error)
            if out:
                break
        return out

    def _artist_ok(self, credits, artist):
        return any(match.match_key(c) == match.match_key(artist) or sim(c, artist) >= 0.9 for c in credits if c)

    def _musicbrainz(self, artist, album):
        rows = (self.http.json(f"{MB}/release", {"query": f'release:"{album}" AND artist:"{artist}"', "limit": 8, "fmt": "json"}) or {}).get("releases") or []
        out = []
        for row in rows:
            if sim(row.get("title") or "", album) < 0.9:
                continue
            credits = [c.get("name") or (c.get("artist") or {}).get("name") or "" for c in row.get("artist-credit") or []]
            if not self._artist_ok(credits, artist):
                continue
            detail = self.http.json(f"{MB}/release/{row['id']}", {"inc": "recordings", "fmt": "json"}) or {}
            for medium in detail.get("media") or []:
                tracks = [(number(t.get("position")), t.get("title") or "") for t in medium.get("tracks") or [] if number(t.get("position"))]
                if tracks:
                    out.append({"source": "MusicBrainz", "title": row.get("title") or album, "tracks": tracks})
            if len(out) >= 6:
                break
        return out

    def _discogs(self, artist, album):
        token = self._discogs_token()
        if not token:
            return []
        found = (self.http.json(f"{DISCOGS}/database/search", {"release_title": album, "artist": artist, "type": "release",
                                                              "per_page": 6, "token": token}) or {}).get("results") or []
        out = []
        for row in found:
            name = str(row.get("title") or "")
            artist_part, _, title_part = name.partition(" - ")
            if sim(title_part or name, album) < 0.9 or (title_part and not self._artist_ok([artist_part], artist)):
                continue
            detail = self.http.json(f"{DISCOGS}/releases/{row['id']}", {"token": token}) or {}
            tracks = discogs_positions(detail.get("tracklist"))
            if tracks:
                out.append({"source": "Discogs", "title": title_part or name, "tracks": tracks})
            if len(out) >= 4:
                break
        return out
