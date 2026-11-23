"""A download thread for a source that fetches files itself (Bandcamp from fork v13).

The same rules as Galaxy's thread: one song at a time from the shared Downloads queue, a pause
between songs, ``_incomplete\\<id>.part`` files, three retries with backoff, cancel and pause,
progress with speed and time left, native tags with cover, then the file moves to
``Album Artist\\Album\\## - Title.ext`` and the song is added to the download history.
"""

import logging
import os
import random
import threading
import time
from pathlib import Path

import requests

from . import featured
from .galaxy import (
    AUDIO_EXTS,
    Abandoned,
    JobCancelled,
    JobFailed,
    StopRequested,
    TransientHTTPError,
    build_path,
    detect_audio_format,
    read_duration,
    to_int,
    write_tags,
)

log = logging.getLogger("tagcast")


def find_lyrics(path, fmt, title, artists, album, fetch=None):
    """Fork v109: the song's lyrics from LRCLIB - timed when it has them, else plain - or "".
    Free and outside every source's limits; never fails a download."""
    if not title or not artists:
        return ""
    try:
        seconds, _ = read_duration(path, fmt)
    except Exception:
        seconds = 0.0
    try:
        from .galaxy import lrclib_synced
        from .playback_lyrics import MAX_TEXT
        text, instrumental = lrclib_synced(fetch or requests.get, artists[0], title, album, seconds, plain=True)
    except (StopRequested, JobCancelled):
        raise
    except Exception as error:
        log.info("Lyrics for %s - %s failed: %s", artists[0], title, error)
        return ""
    text = (text or "").strip()
    if instrumental or not text or len(text) > MAX_TEXT or "\x00" in text:
        return ""
    return text + "\n"


class FileWorker:
    source = ""
    track_delay = 3.0

    def __init__(self, studio, fetch):
        self.studio = studio
        self.fetch = fetch
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.thread = None
        self.stopping = False
        self.active = None
        self.cancel_id = None
        self._orphans = set()   # fork v114: thread ids given up on by a Reset
        self._t0 = 0.0
        manager = studio.download_manager
        manager.register(self.source, self)
        with manager.lock:  # a download that was running when Tagcast stopped starts again
            for item in manager.state["queue"]:
                if item.get("source") == self.source and item.get("state") == "active":
                    item.update(state="queued", step=-1, step_detail="", progress=0)
        if manager.has_queued(self.source):
            self.ensure_worker()

    @property
    def manager(self):
        return self.studio.download_manager

    def folder(self):
        return self.manager.folder()

    # -- the shared queue's worker interface --------------------------------------------

    def active_ids(self):
        return [self.active] if self.active else []

    def cancel(self, item_id):
        if item_id == self.active:
            self.cancel_id = item_id

    def abandon(self, item_id):
        """fork v114: a Reset could not stop this song (its thread is jammed): give the thread up, start a new one."""
        with self.lock:
            if item_id == self.active and self.thread:
                self._orphans.add(self.thread.ident)
                self.thread = None
                self.active = self.cancel_id = None
            self.ensure_worker()

    def ensure_worker(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wake.set()
                return
            self.stopping = False
            self.thread = threading.Thread(target=self._loop, name=f"{self.source}-downloads", daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stopping = True
        self.wake.set()

    # -- the loop -------------------------------------------------------------------------

    def _sleep(self, secs):
        end = time.monotonic() + secs
        while True:
            if self.stopping:
                raise StopRequested()
            self._check_cancel()
            r = end - time.monotonic()
            if r <= 0:
                return
            time.sleep(min(0.2, r))

    def _check_cancel(self):
        if threading.get_ident() in self._orphans:
            raise Abandoned()
        if self.active and self.cancel_id == self.active:
            raise JobCancelled()

    def _tidy(self, paths):
        """v46: artist "A; B", album artist = the first one and a genre from your list, right after the save."""
        tagclean = getattr(self.studio, "tagclean", None)
        if tagclean is not None:
            try:
                tagclean.tidy_downloaded(paths)
            except Exception:
                log.exception("Could not queue the tag clean-up for a download")

    def _step(self, item_id, idx, detail="", **extra):
        self.manager.update_item(item_id, step=idx, step_detail=detail, **extra)

    def _loop(self):
        while not self.stopping:
            # See galaxy.py's _loop for why this outer try/except exists: is_paused/next_queued
            # run ahead of each song's own error handling below, and either can hit the shared
            # queue's _persist -- an unguarded exception there used to kill this thread for good.
            try:
                job = None if self.manager.is_paused() else self.manager.next_queued(self.source)
                if job is None:
                    self.wake.wait(0.5)
                    self.wake.clear()
                    if not self.manager.has_queued(self.source) and not self.manager.is_paused():
                        with self.lock:   # fork v117: a song added right now still finds this thread alive and would sit
                            if not self.manager.has_queued(self.source):   # until the next poll; decide under the same lock
                                self.thread = None
                                return
                    continue
                self.active, self.cancel_id = job["id"], None
                try:
                    self.process(job)
                except StopRequested:
                    self.manager.update_item(job["id"], persist=True, state="queued", step=-1, step_detail="", progress=0)
                    break
                except JobCancelled:
                    self.manager.cancelled(job["id"])
                except (JobFailed, ValueError) as e:  # LibraryError is a ValueError
                    self.manager.update_item(job["id"], persist=True, state="failed", note=str(e), speed=0, eta=0)
                except Exception as e:
                    log.exception("%s download failed", self.source)
                    self.manager.update_item(job["id"], persist=True, state="failed", note=f"{type(e).__name__}: {e}")
                finally:
                    if threading.get_ident() not in self._orphans:
                        self._cleanup(job["id"])
                        self.active = self.cancel_id = None
                if threading.get_ident() in self._orphans:
                    return
                if self.manager.has_queued(self.source) and not self.manager.is_paused() and not self.stopping:
                    try:
                        self._sleep(self.track_delay)
                    except (StopRequested, JobCancelled):
                        pass
            except Abandoned:
                return
            except Exception:
                log.exception("%s download thread hit an unexpected error; continuing", self.source)
                self.active = self.cancel_id = None
                time.sleep(1)

    def process(self, job):
        raise NotImplementedError

    # -- files ----------------------------------------------------------------------------

    def _part_path(self, item_id):
        return Path(self.folder()) / "_incomplete" / f"{item_id}.part"

    def _cleanup(self, item_id):
        part = self._part_path(item_id)
        try:
            part.unlink(missing_ok=True)
            part.parent.rmdir()
        except OSError:
            pass

    def download(self, jid, url, part):
        part.parent.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            try:
                with self.fetch(url, stream=True, timeout=(15, 60)) as r:
                    if r.status_code == 429 or r.status_code >= 500:
                        raise TransientHTTPError(f"HTTP {r.status_code}")
                    if r.status_code != 200:
                        raise JobFailed(f"Download HTTP {r.status_code}")
                    total = to_int(r.headers.get("Content-Length"), 0)
                    done, last = 0, 0.0
                    self._t0 = time.monotonic()
                    # v67: another source's clean-up may have removed the shared, empty _incomplete folder.
                    Path(part).parent.mkdir(parents=True, exist_ok=True)
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(64 * 1024):
                            if self.stopping:
                                raise StopRequested()
                            self._check_cancel()
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if now - last > 0.3:
                                last = now
                                self._progress(jid, done, total)
                    self._progress(jid, done, total)
                    if total and done < total:
                        raise TransientHTTPError(f"incomplete ({done}/{total} bytes)")
                    if done == 0:
                        raise JobFailed("Empty download")
                    return done
            except (TransientHTTPError, requests.Timeout, requests.ConnectionError,
                    requests.exceptions.ChunkedEncodingError) as e:
                if attempt >= 3:
                    raise JobFailed(f"Download failed: {e}") from None
                delay = 2.0 * (2 ** attempt) + random.uniform(0, 0.5)
                attempt += 1
                self._step(jid, 1, f"Retry {attempt}/3 in {delay:.0f}s ({e})")
                self._sleep(delay)

    def _progress(self, jid, done, total):
        pct = int(done * 100 / total) if total else 0
        mb_done, mb_total = done / 1048576, total / 1048576
        elapsed = max(0.001, time.monotonic() - self._t0)
        speed = mb_done / elapsed if elapsed > 0.3 else 0.0
        eta = (mb_total - mb_done) / speed if speed > 0 and total else 0.0
        detail = f"{mb_done:.1f} / {mb_total:.1f} MB" if total else f"{mb_done:.1f} MB"
        if speed:
            detail += f" · {speed:.2f} MB/s"
        self.manager.update_item(jid, step=1, step_detail=detail, progress=pct, speed=round(speed, 2), eta=round(eta))

    def check_audio(self, part, min_seconds=0):
        try:
            fmt = detect_audio_format(part)
        except ValueError as e:
            raise JobFailed(str(e)) from None
        try:
            real, _ = read_duration(part, fmt)
        except Exception as e:
            raise JobFailed(f"Unreadable {fmt.upper()}: {e}") from None
        if min_seconds and real < min_seconds:
            raise JobFailed(f"Preview only ({real:.0f}s of about {min_seconds / 0.6:.0f}s)")
        return fmt

    embed_lyrics = True  # fork v109: lyrics go into every download, not only Galaxy's
    lyrics_fetch = staticmethod(requests.get)

    def find_lyrics(self, path, fmt, title, artists, album):
        if not self.embed_lyrics:
            return ""
        return find_lyrics(path, fmt, title, artists, album, self.lyrics_fetch)

    @staticmethod
    def write_lrc(final, lyrics):
        """Timed lyrics also go next to the song as a .lrc file, as Galaxy's do (v79)."""
        if lyrics and lyrics.lstrip().startswith("["):
            try:
                final.with_suffix(".lrc").write_text(lyrics, encoding="utf-8")
            except OSError as error:
                log.info("Could not save the .lrc file for %s: %s", final, error)

    def save(self, job, part, fmt, nbytes, *, title, artists, album_artist, album, track_no, track_total,
             year="", genre="", cover=None, extras=(), lyrics=None):
        """Tag the finished .part file, move it into place and add it to the history."""
        jid = job["id"]
        # fork v109: asked for as a song by this artist: Artist A first, Album Artist A, no "feat." in the title
        title, artists, album_artist = featured.credit_for(job.get("for_artist", ""), title, artists, album_artist)
        if lyrics is None:
            self._step(jid, 3, "Looking for lyrics…")
            lyrics = self.find_lyrics(part, fmt, title, artists, album)
        self._step(jid, 3, {"flac": "Writing FLAC tags…", "m4a": "Writing M4A tags…"}.get(fmt, "Writing ID3v2.4…"))
        write_tags(part, fmt, title=title, artists=artists, album_artist=album_artist, album=album,
                   track_no=track_no, track_total=track_total, year=year, genre=genre, cover=cover, lyrics=lyrics)
        final = build_path(self.folder(), album_artist, album, track_no, title, fmt)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, final)
        self.write_lrc(final, lyrics)
        for other in AUDIO_EXTS:
            if other != fmt:
                try:
                    final.with_suffix("." + other).unlink(missing_ok=True)
                except OSError:
                    pass
        notes = list(extras) + ([] if cover else ["no cover"]) + ([] if lyrics or not self.embed_lyrics else ["no lyrics found"])
        self.manager.update_item(jid, persist=True, state="done", step=4, step_detail=str(final),
                                 note="Saved" + (f" · {', '.join(notes)}" if notes else ""), path=str(final),
                                 progress=100, finished=time.time(), size_mb=round(nbytes / 1048576, 2),
                                 format=fmt, speed=0, eta=0, year=year, track_no=track_no)
        self.manager.record({"title": title, "artists": artists, "album": album, "source": self.source,
                             "path": str(final), "site": (job.get("payload") or {}).get("site", ""),
                             "query": job.get("query", ""), "link": job.get("link", "")})
        self._tidy([final])
        fingerprints = getattr(self.studio, "fingerprints", None)  # fork v109: same audio already in the library?
        if fingerprints is not None:
            fingerprints.check_download(final, jid)
        return final
