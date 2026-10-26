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

from .galaxy import (AUDIO_EXTS, JobCancelled, JobFailed, StopRequested, TransientHTTPError, build_path,
                     detect_audio_format, read_duration, to_int, write_tags)

log = logging.getLogger("tagcast")


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
        if self.active and self.cancel_id == self.active:
            raise JobCancelled()

    def _step(self, item_id, idx, detail="", **extra):
        self.manager.update_item(item_id, step=idx, step_detail=detail, **extra)

    def _loop(self):
        while not self.stopping:
            job = None if self.manager.is_paused() else self.manager.next_queued(self.source)
            if job is None:
                self.wake.wait(0.5)
                self.wake.clear()
                if not self.manager.has_queued(self.source) and not self.manager.is_paused():
                    return
                continue
            self.active, self.cancel_id = job["id"], None
            try:
                self.process(job)
            except StopRequested:
                self.manager.update_item(job["id"], persist=True, state="queued", step=-1, step_detail="", progress=0)
                break
            except JobCancelled:
                self.manager.remove([job["id"]])
            except (JobFailed, ValueError) as e:  # LibraryError is a ValueError
                self.manager.update_item(job["id"], persist=True, state="failed", note=str(e), speed=0, eta=0)
            except Exception as e:
                log.exception("%s download failed", self.source)
                self.manager.update_item(job["id"], persist=True, state="failed", note=f"{type(e).__name__}: {e}")
            finally:
                self._cleanup(job["id"])
                self.active = self.cancel_id = None
            if self.manager.has_queued(self.source) and not self.manager.is_paused() and not self.stopping:
                try:
                    self._sleep(self.track_delay)
                except (StopRequested, JobCancelled):
                    pass

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

    def save(self, job, part, fmt, nbytes, *, title, artists, album_artist, album, track_no, track_total,
             year="", genre="", cover=None, extras=()):
        """Tag the finished .part file, move it into place and add it to the history."""
        jid = job["id"]
        self._step(jid, 3, {"flac": "Writing FLAC tags…", "m4a": "Writing M4A tags…"}.get(fmt, "Writing ID3v2.4…"))
        write_tags(part, fmt, title=title, artists=artists, album_artist=album_artist, album=album,
                   track_no=track_no, track_total=track_total, year=year, genre=genre, cover=cover)
        final = build_path(self.folder(), album_artist, album, track_no, title, fmt)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, final)
        for other in AUDIO_EXTS:
            if other != fmt:
                try:
                    final.with_suffix("." + other).unlink(missing_ok=True)
                except OSError:
                    pass
        notes = list(extras) + ([] if cover else ["no cover"])
        self.manager.update_item(jid, persist=True, state="done", step=4, step_detail=str(final),
                                 note="Saved" + (f" · {', '.join(notes)}" if notes else ""), path=str(final),
                                 progress=100, finished=time.time(), size_mb=round(nbytes / 1048576, 2),
                                 format=fmt, speed=0, eta=0, year=year, track_no=track_no)
        self.manager.record({"title": title, "artists": artists, "album": album, "source": self.source,
                             "path": str(final)})
        return final
