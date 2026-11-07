"""Find lyrics for every song that has none (fork v81).

A slow, gentle background run over the whole library. For each song the server first
asks iBroadcast whether it already has lyrics (those are never touched), then looks
for them on LRCLIB, then on GD Studio (netease, joox, bilibili; title with artist,
title alone, simplified Chinese). A match must have the same title and the same
artist, and its timing must fit the song's length. A sure match is saved to the
song's iBroadcast lyrics as timed LRC text (the same field "Find lyrics" writes);
a song with only unsure candidates goes to the "Couldn't match" list, where one
click picks the right one.

The run can be paused, resumed or stopped. What each song turned out to be is kept in
``lyrics-job.json`` so a stop, a crash or a restart continues where it left off. GD
Studio calls go through Galaxy's own rate limiter (low priority), so downloads win and
a firewall page pauses everything.
"""

import logging
import re
import threading
import time

import requests

from . import activity, galaxy as galaxy_module
from .artist_match import simplified
from .galaxy import GalaxyBlocked, GalaxyError
from .library import LibraryError, number
from .lyrics import lyric_text
from .playback_lyrics import (MAX_TEXT, _gdstudio_match, _lyrics_artists, _lyrics_loose, fetch_lrclib_display,
                              gdstudio_clean_lrc, lyrics_strip_artist, lyrics_title, parse_lrc)
from .sources import SourceError

log = logging.getLogger("tagcast")

STATE_FILE = "lyrics-job.json"
GD_SOURCES = ("netease", "joox", "bilibili")
SEARCH_COUNT = 10
SONG_PAUSE = 1.0          # seconds between songs
READ_PAUSE = 0.15         # seconds between "does it have lyrics yet" reads
SAVE_EVERY = 10           # songs between writes of the state file
BLOCK_WAIT = 300          # seconds to wait after a GD Studio firewall page
MAX_ERRORS = 5            # other failures in a row before the run pauses itself
MAX_BLOCKS = 3            # blocks in a row before the run pauses itself
MAX_CANDIDATES = 5
STATUS_LABELS = {"saved": "Saved", "has": "Already had lyrics", "instrumental": "Instrumental",
                 "none": "Not found", "review": "Couldn't match", "skipped": "Skipped"}


class _Interrupt(Exception):
    pass


class _Pause(_Interrupt):
    pass


class _Stop(_Interrupt):
    pass


def lrc_from_lines(result):
    """LRC text from parsed lines: [mm:ss.xx] when every line is timed, plain lines otherwise."""
    rows = []
    for line in result["lines"]:
        if result.get("timed") and line.get("start") is not None:
            cs = round(line["start"] * 100)
            rows.append(f"[{cs // 6000:02d}:{cs % 6000 / 100:05.2f}]{line['text']}")
        else:
            rows.append(line["text"])
    return "\n".join(rows).strip("\n")


def lrc_plain_preview(value, lines=8):
    rows = [re.sub(r"^(?:\[[^\]]*\])+", "", row).strip() for row in value.splitlines()]
    return [row for row in rows if row][:lines]


def gd_queries(track):
    """Search words for GD Studio, best first: title with artist, title alone, then the same in simplified Chinese."""
    title = lyrics_title(lyrics_strip_artist(track["title"], track["artist"])) or track["title"]
    primary = re.split(r"\s*[,;&/×·、，]\s*|\s+(?:and|x|feat\.?|ft\.?|featuring|with)\s+", track["artist"],
                       maxsplit=1, flags=re.I)[0].strip()
    queries = []
    for name in (f"{title} {primary}", title, f"{simplified(title)} {simplified(primary)}", simplified(title)):
        name = " ".join(name.split())
        if name and name not in queries:
            queries.append(name)
    return queries


def _hit(item):
    """A GD Studio search row in the shape the playback matcher reads."""
    return {"name": item["title"], "artist": item["artists"]}


class LyricsJob:
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
        self.data = None  # {"account", "checked": {track id: status}, "review": {track id: {...}}}
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
                log.warning("Could not save the lyrics job state: %s", error)

    # -- reading ----------------------------------------------------------------------------

    def _tracks(self, library):
        ids = []
        for track_id, row in library.tracks.items():
            if not row.get("trashed"):
                ids.append(number(track_id))
        return sorted(ids)

    def status(self):
        self.studio._require_client()
        library, user = self.studio._library()
        with self.lock:
            state = self._state(str(user))
            checked = state["checked"]
            counts = {}
            for value in checked.values():
                counts[value] = counts.get(value, 0) + 1
            tracks = self._tracks(library)
            remaining = sum(1 for i in tracks if str(i) not in checked)
            review = [{"track_id": tid, **row} for tid, row in list(state["review"].items())[:100]]
            limiter = self.studio.galaxy.limiter.status()
            return {"running": self.running, "paused": self.paused, "message": self.message,
                    "progress": dict(self.progress) if self.progress else None,
                    "jobs": list(self.jobs[:50]), "counts": counts, "total": len(tracks), "remaining": remaining,
                    "review": review, "review_count": len(state["review"]), "premium": self.studio.premium,
                    "gd": limiter}

    # -- control ------------------------------------------------------------------------------

    def start(self, retry=False):
        client = self.studio._require_client()
        if not self.studio.premium:
            raise LibraryError("Saving lyrics to iBroadcast requires Premium.")
        library, user = self.studio._library()
        with self.lock:
            if self.running:
                raise LibraryError("Already finding lyrics.")
            state = self._state(str(user))
            if retry:
                for tid in [t for t, v in state["checked"].items() if v == "none"]:
                    del state["checked"][tid]
            self.running, self.paused, self.message = True, False, ""
            self.stop_flag.clear()
            self.pause_flag.clear()
            self.progress = {"done": 0, "total": 0, "name": "", "phase": "starting"}
        self.thread = threading.Thread(target=self._run, args=(client, library, str(user)), name="lyrics-job", daemon=True)
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

    # -- interruption points ---------------------------------------------------------------------

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
        """Wait here while paused; Stop still ends the run."""
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
        galaxy_module._tls.sleep = self._sleep  # Galaxy's rate-limit waits end at once on Pause / Stop
        blocks = errors = 0
        try:
            state = self._state(account)
            todo = [i for i in self._tracks(library) if str(i) not in state["checked"]]
            with self.lock:
                self.progress = {"done": 0, "total": len(todo), "name": "", "phase": "starting"}
            for track_id in todo:
                while True:
                    try:
                        self._hold()
                        self._current(client)
                        self._one(client, library, track_id)
                        blocks = errors = 0
                        break
                    except _Pause:
                        continue
                    except _Stop:
                        raise
                    except GalaxyBlocked as error:
                        blocks += 1
                        with self.lock:
                            self.message = "GD Studio blocked a request; waiting before trying again."
                        activity.record("metadata", "warn", f"Lyrics job: {error}", source="lyrics_job")
                        if blocks >= MAX_BLOCKS:
                            with self.lock:
                                self.paused = True
                                self.pause_flag.set()
                                self.message = "Paused: GD Studio keeps blocking. Press Resume to try again later."
                            blocks = 0
                            continue
                        try:
                            self._sleep(BLOCK_WAIT)
                        except _Pause:
                            pass
                    except Exception as error:
                        if type(error).__name__ == "NotConnected":
                            with self.lock:
                                self.message = "Stopped: you are disconnected from iBroadcast."
                            raise _Stop() from None
                        # Not recorded as checked: the song is tried again on the next run.
                        errors += 1
                        log.info("Lyrics job: song %s skipped: %s", track_id, error)
                        with self.lock:
                            self.message = f"A song was skipped: {error}"
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
                    self._sleep(SONG_PAUSE)
                except _Pause:
                    pass
        except _Stop:
            pass
        except Exception as error:  # a crash ends the run, never the app
            log.exception("Lyrics job failed")
            with self.lock:
                self.message = f"Stopped: {error}"
        finally:
            galaxy_module._tls.sleep = None
            self._save_state()
            with self.lock:
                done = (self.progress or {}).get("done", 0)
                self.running = self.paused = False
                self.progress = None
            self.pause_flag.clear()
            self.stop_flag.clear()
            activity.record("metadata", "info", f"Lyrics job ended after {done} songs", source="lyrics_job")

    def _one(self, client, library, track_id):
        try:
            track = library.track_view(track_id)
            album = library.albums.get(number(track["album_id"]), {}).get("name") or ""
        except LibraryError:
            self._record(track_id, {"title": "", "artist": ""}, "skipped", "", "No longer in the library.", store=False)
            return
        track = dict(track)
        album = str(album)
        with self.lock:
            self.progress.update(name=f"{track['artist']} - {track['title']}", phase="checking")
        # 1. Does iBroadcast already have lyrics? (never replaced)
        rows, before = self.studio.lyrics._userdata(client, str(track_id))
        time.sleep(READ_PAUSE)
        if before.strip():
            self._record(track_id, track, "has", "", "")
            return
        if not track["title"].strip() or not track["artist"].strip() or track["title"] == "Untitled track":
            self._record(track_id, track, "none", "", "Title or artist missing.")
            return
        # 2. LRCLIB
        with self.lock:
            self.progress["phase"] = "LRCLIB"
        found = self._lrclib(track, album)
        if found == "instrumental":
            self._record(track_id, track, "instrumental", "lrclib", "LRCLIB says it is instrumental.")
            return
        source = "LRCLIB"
        candidates = []
        # 3. GD Studio
        if not found:
            with self.lock:
                self.progress["phase"] = "GD Studio"
            found, candidates = self._gdstudio(track)
            source = "GD Studio"
        if found:
            status = self._write(client, library, track_id, track, found)
            self._record(track_id, track, status[0], source, status[1])
            return
        if candidates:
            self._record(track_id, track, "review", "gdstudio", f"{len(candidates)} possible, none sure.", candidates=candidates)
        else:
            self._record(track_id, track, "none", "", "Nothing found on LRCLIB or GD Studio.")

    def _lrclib(self, track, album):
        for attempt in range(3):
            try:
                result = fetch_lrclib_display(track, album, self._check)
                break
            except SourceError as error:
                if attempt == 2 or not getattr(error, "temporary", True):
                    return None
                self._sleep(max(10, min(120, getattr(error, "retry_after", 30) or 30)))
            except requests.RequestException:
                return None
        else:
            return None
        if result["status"] == "instrumental":
            return "instrumental"
        if result["status"] != "ready" or not result["lines"]:
            return None
        text = lrc_from_lines(result)
        return text if text.strip() and len(text) <= MAX_TEXT else None

    def _gdstudio(self, track):
        """(lyrics text, near misses). A sure match needs the same title and artist and a timing that fits."""
        api = self.studio.galaxy.api
        seen, near = set(), []
        queries = gd_queries(track)
        for source in GD_SOURCES:
            for query in (queries if source == GD_SOURCES[0] else queries[:2]):
                self._check()
                try:
                    rows = api.search(source, query, SEARCH_COUNT)
                except GalaxyBlocked:
                    raise
                except GalaxyError as error:
                    log.info("Lyrics job: GD Studio %s search failed: %s", source, error)
                    break  # this source is closed or failing right now: next source
                sure = []
                for item in rows:
                    key = (item["source"], item["lyric_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    if _gdstudio_match(_hit(item), track):
                        sure.append(item)
                    elif self._near(item, track):
                        near.append(item)
                for item in sure[:2]:
                    text = self._gd_lyric(api, item, track)
                    if text:
                        return text, []
                if sure:
                    break  # the song is there but its lyrics do not fit: other sources rarely differ
        candidates = [{"source": i["source"], "lyric_id": i["lyric_id"], "title": i["title"],
                       "artist": ", ".join(i["artists"]), "album": i["album"]} for i in near[:MAX_CANDIDATES]]
        return "", candidates

    @staticmethod
    def _near(item, track):
        """Same artist but another title spelling, or the same title by another artist: worth a manual look."""
        wanted = _lyrics_loose(lyrics_title(lyrics_strip_artist(track["title"], track["artist"])))
        found = _lyrics_loose(lyrics_title(lyrics_strip_artist(item["title"], ", ".join(item["artists"]))))
        artists = ", ".join(item["artists"])
        same_artist = _lyrics_artists(simplified(artists), simplified(track["artist"]))
        same_title = bool(found) and found == wanted
        return same_artist != same_title or (same_artist and bool(found) and (found in wanted or wanted in found))

    def _gd_lyric(self, api, item, track, strict=True):
        try:
            raw = api.lyric(item["source"], item["lyric_id"], low=True)
        except GalaxyBlocked:
            raise
        except GalaxyError:
            return ""
        return self._fitting(raw, track, strict)

    @staticmethod
    def _fitting(raw, track, strict=True):
        text = gdstudio_clean_lrc(raw or "").strip()
        if not text or len(text) > MAX_TEXT or "\x00" in text:
            return ""
        try:
            parsed = parse_lrc(text, track["length"], "gdstudio")
        except SourceError:
            return ""
        if parsed["status"] != "ready" or len(parsed["lines"]) < 2:
            return ""
        starts = [line["start"] for line in parsed["lines"] if line["start"] is not None]
        if strict and track["length"] > 0 and starts and max(starts) < track["length"] * .4:
            return ""
        return text

    # -- saving -------------------------------------------------------------------------------------

    def _write(self, client, library, track_id, track, text, interruptible=True):
        """Put the lyrics on the song, only if it still has none. Returns (status, note)."""
        with self.lock:
            if self.progress and interruptible:
                self.progress["phase"] = "saving"
        lyrics = self.studio.lyrics
        try:
            text = lyric_text(text)
            end = time.monotonic() + 120
            while not self.studio.save_lock.acquire(timeout=2):
                if interruptible:
                    self._check()
                if time.monotonic() > end:
                    raise LibraryError("Another save is still running.")
            try:
                rows, before = lyrics._userdata(client, str(track_id))
                if before.strip():
                    return "has", "Got lyrics meanwhile; left alone."
                wanted = [row for row in rows if row.get("type") != "lyrics"] + [{"type": "lyrics", "value": text}]
                client._jsonrequest("update_track", _retry=False,
                                    tracks=[{"file_id": int(track_id), "userdata": wanted}])
                try:
                    _, actual = lyrics._userdata(client, str(track_id))
                    confirmed = actual == text
                except Exception:
                    confirmed = False
            finally:
                self.studio.save_lock.release()
            if confirmed:
                self._remember(track_id, track, text)
                return "saved", "Timed lyrics saved." if re.search(r"(?m)^\[\d", text) else "Lyrics saved."
            return "saved", "Sent; iBroadcast has not confirmed it yet."
        except _Interrupt:
            raise
        except Exception as error:
            log.warning("Lyrics job save failed for %s: %s", track_id, error)
            return "none", f"Could not save: {error}"

    def _remember(self, track_id, track, text):
        """Keep the saved text in the local lyrics cache so Now playing shows it at once."""
        try:
            lyrics = self.studio.lyrics
            body = {"track_id": str(track_id), "title": track["title"], "artist": track["artist"],
                    "session": self.studio.playback_session}
            with self.studio.lock:
                selection = lyrics._selection(body)
            lyrics._native_store(selection, text)
        except Exception:
            pass

    def _record(self, track_id, track, status, source, note, candidates=None, store=True):
        with self.lock:
            state = self._state()
            if store:
                state["checked"][str(track_id)] = status
                self._dirty += 1
            if status == "review":
                state["review"][str(track_id)] = {"title": track["title"], "artist": track["artist"],
                                                  "candidates": candidates or []}
            else:
                state["review"].pop(str(track_id), None)
            job = {"track_id": str(track_id), "title": track["title"], "artist": track["artist"],
                   "status": status, "source": source, "note": note}
            self.jobs.insert(0, job)
            del self.jobs[200:]
            if self.progress:
                self.progress["done"] += 1
        if status in ("saved", "review") or (status == "none" and note.startswith("Could not")):
            activity.record("metadata", "ok" if status == "saved" else "warn",
                            f"{track['artist']} - {track['title']}: {STATUS_LABELS.get(status, status)} {note}".strip(),
                            source="lyrics_job")

    # -- the Couldn't match list --------------------------------------------------------------------

    def _candidate(self, track_id, source, lyric_id):
        state = self._state()
        row = state["review"].get(str(track_id))
        if not row:
            raise LibraryError("This song is no longer in the list.")
        for item in row["candidates"]:
            if item["source"] == source and item["lyric_id"] == lyric_id:
                return row, item
        raise LibraryError("That choice is no longer in the list.")

    def preview(self, track_id, source, lyric_id):
        self.studio._require_client()
        self._candidate(track_id, source, lyric_id)
        raw = self.studio.galaxy.api.lyric(source, lyric_id, low=False)
        text = gdstudio_clean_lrc(raw or "")
        return {"lines": lrc_plain_preview(text)}

    def pick(self, track_id, source, lyric_id):
        client = self.studio._require_client()
        if not self.studio.premium:
            raise LibraryError("Saving lyrics to iBroadcast requires Premium.")
        library, user = self.studio._library()
        with self.lock:
            self._state(str(user))
            self._candidate(track_id, source, lyric_id)
        track = dict(library.track_view(number(track_id)))
        raw = self.studio.galaxy.api.lyric(source, lyric_id, low=False)
        text = self._fitting(raw, track, strict=False)
        if not text:
            raise LibraryError("These lyrics are empty or do not fit this song.")
        status, note = self._write(client, library, number(track_id), track, text, interruptible=False)
        with self.lock:
            state = self._state()
            if status in ("saved", "has"):
                state["checked"][str(track_id)] = status
                state["review"].pop(str(track_id), None)
            self._save_state()
        if status == "none":
            raise LibraryError(note)
        return {"status": status, "note": note}

    def autosave(self, body):
        """Fork v86: timed lyrics found while playing (LRCLIB/Unison), saved once if the song has none."""
        client = self.studio._require_client()
        if not self.studio.premium:
            raise LibraryError("Saving lyrics to iBroadcast requires Premium.")
        track_id = str(body.get("track_id") or "")
        source = body.get("source")
        lines = body.get("lines")
        if not track_id.isdigit() or source not in ("lrclib", "unison") or not isinstance(lines, list) or not 2 <= len(lines) <= 2000:
            raise LibraryError("These lyrics cannot be saved.")
        rows = []
        for line in lines:
            start = line.get("start") if isinstance(line, dict) else None
            text = line.get("text") if isinstance(line, dict) else None
            if isinstance(start, bool) or not isinstance(start, (int, float)) or not isinstance(text, str) or not 0 <= start < 6 * 3600:
                raise LibraryError("These lyrics cannot be saved.")
            rows.append({"start": float(start), "text": " ".join(text.split())[:500]})
        if sum(1 for row in rows if row["text"]) < 2:
            raise LibraryError("These lyrics are empty.")
        library, user = self.studio._library()
        try:
            track = dict(library.track_view(number(track_id)))
        except LibraryError:
            raise LibraryError("This song is no longer in the library.")
        length = float(track.get("length") or 0)
        if length and max(row["start"] for row in rows) > length + 10:
            raise LibraryError("These lyrics are longer than the song.")
        text = lrc_from_lines({"timed": True, "lines": sorted(rows, key=lambda row: row["start"])})
        status, note = self._write(client, library, number(track_id), track, text, interruptible=False)
        label = "LRCLIB" if source == "lrclib" else "Unison"
        with self.lock:
            state = self._state(str(user))
            if status in ("saved", "has"):
                state["checked"][track_id] = status
                state["review"].pop(track_id, None)
            self._save_state()
        if status == "saved":
            activity.record("metadata", "ok", f"{track['artist']} - {track['title']}: lyrics from {label} saved while playing", note,
                            source="lyrics_autosave")
        return {"status": status, "note": note, "confirmed": status == "saved" and not note.startswith("Sent;")}

    def dismiss(self, track_id):
        with self.lock:
            state = self._state()
            state["review"].pop(str(track_id), None)
            state["checked"][str(track_id)] = "skipped"
            self._save_state()
        return {"dismissed": True}
