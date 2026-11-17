"""Google Drive backup (fork v100).

Zips the whole settings folder (sign-in, caches, download/wishlist/history, every
per-source setting) and writes it into a folder you point at - normally one Google Drive
for Desktop already watches, so Drive's own app uploads it from there; Tagcast never talks
to Google itself. Runs once a week while Tagcast is running, and on request ("Back up now").
Only the latest zip is kept (overwritten each time) - this is "get my settings back", not
version history.

Lyrics aren't part of this: they're saved straight onto the song in your iBroadcast library
(``lyrics_job.py``, v86-v88), which is itself the backup for those.

State: ``backup.json`` (the Drive folder path, and the result of the last run).
"""

import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("tagcast")
STATE_FILE = "backup.json"
EVERY_SECONDS = 7 * 86400  # weekly
FILE_NAME = "tagcast-backup.zip"


class BackupError(Exception):
    pass


class Backup:
    def __init__(self, studio, scheduler=True):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("path", ""), ("last", 0), ("last_ok", None), ("last_error", ""), ("last_bytes", 0)):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.running = False
        if scheduler:
            threading.Thread(target=self._schedule, name="backup", daemon=True).start()

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    def view(self):
        with self.lock:
            return {"path": self.state["path"], "last": self.state["last"], "last_ok": self.state["last_ok"],
                    "last_error": self.state["last_error"], "last_bytes": self.state["last_bytes"],
                    "next": (self.state["last"] + EVERY_SECONDS) if self.state["last"] else 0, "running": self.running}

    def save_settings(self, path):
        with self.lock:
            self.state["path"] = str(path or "").strip()
            self._persist()
        return self.view()

    def run_now(self):
        with self.lock:
            if self.running:
                return self.view()
            self.running = True  # set here, synchronously, so the response already shows it running
        threading.Thread(target=self._run, name="backup-run", daemon=True).start()
        return self.view()

    # -- the actual work ---------------------------------------------------------------------

    def _run(self):
        """Call via ``run_now()`` (or the scheduler below), which sets ``self.running`` first."""
        try:
            path = self.state["path"]
            if not path:
                raise BackupError("Set a Google Drive folder path first.")
            folder = Path(path)
            if not folder.is_dir():
                raise BackupError(f'"{path}" is not a folder Tagcast can see right now (check the path, and that the drive is connected).')
            size = self._write(folder)
            with self.lock:
                self.state["last"], self.state["last_ok"] = time.time(), True
                self.state["last_error"], self.state["last_bytes"] = "", size
                self._persist()
        except Exception as error:
            log.info("Backup failed: %s", error)
            with self.lock:
                self.state["last"], self.state["last_ok"], self.state["last_error"] = time.time(), False, str(error)
                self._persist()
        finally:
            with self.lock:
                self.running = False

    def _write(self, folder):
        """Zip ``self.studio.home`` into ``folder/tagcast-backup.zip``, replacing it atomically."""
        target = folder / FILE_NAME
        tmp = target.with_name(target.name + ".tmp")
        with tempfile.TemporaryDirectory(prefix="tagcast-backup-") as work:
            archive = Path(shutil.make_archive(str(Path(work) / "tagcast-backup"), "zip", root_dir=str(self.studio.home)))
            size = archive.stat().st_size
            shutil.copyfile(archive, tmp)
        os.replace(tmp, target)
        return size

    def _schedule(self):
        time.sleep(45)  # let Tagcast and the library start first
        while True:
            try:
                due = time.time() - self.state["last"] >= EVERY_SECONDS
                if due and self.state["path"] and not self.running:
                    self.run_now()
            except Exception as error:
                log.info("Scheduled backup skipped: %s", error)
            time.sleep(3600)
