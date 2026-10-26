"""Soulseek (fork v14): albums and songs from Soulseek, through Sockseek.

Tagcast has no Soulseek client of its own. It runs Sockseek (github.com/fiso64/sockseek, AGPL,
formerly slsk-batchdl) as ``sockseek daemon`` in the background and drives its local HTTP API
(default http://127.0.0.1:5030):

- ``POST /api/jobs/downloads/album`` {albumQuery: {artist, album}} - album mode, Sockseek picks
  a complete folder shared by another user;
- ``POST /api/jobs/downloads/song`` {songQuery: {artist, title, album}};
- ``GET /api/jobs/{id}`` until ``lifecycleState`` is ``Terminal``; ``POST /api/jobs/{id}/cancel``.

Your Soulseek login, the Sockseek program and how many files/folders you share are set under
Downloads → Settings. The password stays in Tagcast's settings folder (``soulseek.json`` and
the generated ``sockseek.conf``) and is never sent to the page. Like every source, a song or
album you may already have waits in Needs your decision. Files are saved in the download
folder as ``Album Artist\\Album\\## - Title`` when the shared files are tagged.
"""

import atexit
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import requests

from . import artist_match as match
from .downloads import parse_file_name
from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "soulseek.json"
CONF_FILE = "sockseek.conf"
NAME_FORMAT = "{albumartist|sartist|artist}/{album|salbum}/{track( - )title|slsk-filename}"
MAX_ACTIVE = 2
POLL = 2.0


class SoulseekError(LibraryError):
    pass


class Soulseek:
    source = "soulseek"

    def __init__(self, studio, http=None, popen=None):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("exe", ""), ("username", ""), ("password", ""), ("port", 5030),
                           ("shared_files", 0), ("shared_folders", 0)):
            self.state.setdefault(key, value)
        self.session = http or requests.Session()
        if http is None:
            self.session.trust_env = False  # localhost: never through a proxy
        self.popen = popen or subprocess.Popen
        self.process = None
        atexit.register(self._kill)  # a Sockseek that Tagcast started stops with Tagcast
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.thread = None
        self.stopping = False
        self.on_failed = None  # set by "get missing": try the next source
        manager = studio.download_manager
        manager.register(self.source, self)
        with manager.lock:  # a job submitted before a restart is followed again; others start again
            for item in manager.state["queue"]:
                if item.get("source") == self.source and item.get("state") == "active" and not item.get("job_id"):
                    item.update(state="queued", step=-1, step_detail="")
        if any(i.get("source") == self.source and i.get("state") in ("queued", "active") for i in manager.state["queue"]):
            self.ensure_worker()

    @property
    def manager(self):
        return self.studio.download_manager

    def _persist(self):
        self.studio._write(STATE_FILE, self.state)

    @property
    def base(self):
        return f"http://127.0.0.1:{int(self.state['port'])}"

    # -- settings ---------------------------------------------------------------------------

    def settings(self):
        s = self.state
        return {"exe": s["exe"], "username": s["username"], "has_password": bool(s["password"]), "port": int(s["port"]),
                "shared_files": int(s["shared_files"]), "shared_folders": int(s["shared_folders"]),
                "configured": self.configured()}

    def configured(self):
        return bool(self.state["exe"] and self.state["username"] and self.state["password"])

    def save_settings(self, body):
        if not isinstance(body, dict):
            raise SoulseekError("Choose the Soulseek settings.")
        new = dict(self.state)
        if "exe" in body:
            exe = str(body["exe"] or "").strip().strip('"')
            if exe and not Path(exe).is_file():
                raise SoulseekError(f"No Sockseek program at {exe}. Choose sockseek.exe from the Sockseek download.")
            new["exe"] = exe
        if "username" in body:
            name = str(body["username"] or "").strip()
            if len(name) > 100 or any(c in name for c in "\r\n"):
                raise SoulseekError("That Soulseek username doesn't look right.")
            new["username"] = name
        if body.get("password"):  # empty keeps the saved password
            password = str(body["password"])
            if len(password) > 200 or any(c in password for c in "\r\n"):
                raise SoulseekError("That Soulseek password doesn't look right.")
            new["password"] = password
        for key, low, high in (("port", 1024, 65535), ("shared_files", 0, 1_000_000), ("shared_folders", 0, 100_000)):
            if key in body:
                try:
                    value = int(body[key])
                except (TypeError, ValueError):
                    raise SoulseekError("Enter whole numbers for the port and the shared counts.") from None
                if not low <= value <= high:
                    raise SoulseekError(f"The {key.replace('_', ' ')} must be between {low} and {high}.")
                new[key] = value
        with self.lock:
            self.state = new
            self._persist()
        return self.status()

    def _write_conf(self):
        s = self.state
        lines = ["# Written by Tagcast; changes here are replaced when Tagcast starts Sockseek.",
                 f"username = {s['username']}", f"password = {s['password']}",
                 f"path = {self.manager.folder()}", f"name-format = {NAME_FORMAT}",
                 f"shared-files = {int(s['shared_files'])}", f"shared-folders = {int(s['shared_folders'])}"]
        self.studio._write_bytes(CONF_FILE, ("\n".join(lines) + "\n").encode("utf-8"))
        return Path(self.studio.home) / CONF_FILE

    # -- the daemon -------------------------------------------------------------------------

    def _call(self, method, path, body=None, timeout=10):
        try:
            r = self.session.request(method, self.base + path, json=body, timeout=timeout)
        except requests.RequestException:
            return None
        if r.status_code >= 400:
            text = " ".join(str(getattr(r, "text", "")).split())[:200]
            raise SoulseekError(f"Sockseek answered HTTP {r.status_code}{f': {text}' if text else ''}")
        try:
            return r.json()
        except ValueError:
            return {}

    def ping(self):
        try:
            return self._call("GET", "/api/server/info", timeout=3)
        except SoulseekError:
            return None

    def status(self):
        info = self.ping()
        client = None
        if info:
            try:
                client = (self._call("GET", "/api/server/status", timeout=3) or {}).get("soulseekClient")
            except SoulseekError:
                client = None
        return {"settings": self.settings(), "running": bool(info), "started_here": bool(self.process and self.process.poll() is None),
                "version": (info or {}).get("version", ""), "client": client}

    def start(self):
        if self.ping():
            return self.status()
        if not self.configured():
            raise SoulseekError("Set the Sockseek program and your Soulseek login under Downloads → Settings first.")
        conf = self._write_conf()
        log_path = Path(self.studio.home) / "sockseek.log"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            with open(log_path, "ab") as out:
                self.process = self.popen([self.state["exe"], "daemon", "--config", str(conf),
                                           "--server-port", str(int(self.state["port"]))],
                                          stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError as error:
            raise SoulseekError(f"Sockseek could not be started ({error.strerror or error}).") from None
        for _ in range(40):
            if self.ping():
                return self.status()
            if self.process.poll() is not None:
                break
            time.sleep(0.5)
        tail = ""
        try:
            tail = " ".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-3:])[:300]
        except OSError:
            pass
        raise SoulseekError("Sockseek did not start." + (f" Its last words: {tail}" if tail else " See sockseek.log in the settings folder."))

    def _kill(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None

    def stop(self):
        self._kill()
        return self.status()

    # -- queueing -------------------------------------------------------------------------

    def add(self, items, fallback=False):
        """Queue albums ({kind: album, artist, album}) or songs ({kind: song, artist, title, album})."""
        if not self.configured():
            raise SoulseekError("Set the Sockseek program and your Soulseek login under Downloads → Settings first.")
        if not isinstance(items, list) or not items or len(items) > 200:
            raise SoulseekError("Choose between 1 and 200 albums or songs.")
        out = []
        for raw in items:
            kind = raw.get("kind") if isinstance(raw, dict) else None
            artist = match.clean((raw or {}).get("artist"))
            album = match.clean((raw or {}).get("album"))
            title = album if kind == "album" else match.clean((raw or {}).get("title"))
            if kind not in ("album", "song") or not artist or not title:
                raise SoulseekError("Each album needs an artist and a title; each song an artist and a title.")
            out.append({"kind": kind, "title": title, "artist": artist, "album": album if kind == "song" else "",
                        "tracks": raw.get("tracks"),
                        "payload": {"id": f"{kind}:{match.match_key(artist)}|{match.match_key(album)}|{match.match_key(title)}",
                                    "source": "soulseek", "kind": kind, "artist": artist, "album": album, "title": title,
                                    "fallback": bool(fallback or raw.get("fallback"))}})
        return self.manager.submit(out, "soulseek")

    # -- the worker -----------------------------------------------------------------------

    def active_ids(self):
        return [i["id"] for i in self.manager.state["queue"] if i.get("source") == self.source and i.get("state") == "active"]

    def cancel(self, item_id):
        item = self.manager.item(item_id)
        if item and item.get("job_id"):
            try:
                self._call("POST", f"/api/jobs/{item['job_id']}/cancel")
            except SoulseekError:
                pass
        self.manager.remove([item_id])

    def ensure_worker(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.wake.set()
                return
            self.stopping = False
            self.thread = threading.Thread(target=self._loop, name="soulseek-downloads", daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stopping = True
        self.wake.set()

    def _loop(self):
        while not self.stopping:
            try:
                busy = self.tick()
            except Exception:
                log.exception("Soulseek queue check failed")
                busy = True
            if not busy:
                return
            self.wake.wait(POLL)
            self.wake.clear()

    def tick(self):
        """One round: follow running jobs, start new ones. False when nothing is left to do."""
        m = self.manager
        for item in [m.item(i) for i in self.active_ids()]:
            if item:
                self._follow(item)
        while not m.is_paused() and len(self.active_ids()) < MAX_ACTIVE:
            item = m.next_queued(self.source)
            if not item:
                break
            self._submit(item)
        return bool(self.active_ids()) or (m.has_queued(self.source) and m.is_paused())

    def _fail(self, item, note):
        self.manager.update_item(item["id"], persist=True, state="failed", note=note, speed=0, eta=0)
        if item["payload"].get("fallback") and self.on_failed:
            try:
                self.on_failed(self.manager.item(item["id"]))
            except Exception:
                log.exception("Trying the next source failed")

    def _submit(self, item):
        p = item["payload"]
        if not self.ping():
            try:
                self.start()
            except SoulseekError as error:
                self._fail(item, str(error))
                return
        options = {"outputParentDir": self.manager.folder()}
        if p["kind"] == "album":
            body = {"albumQuery": {"artist": p["artist"], "album": p["title"]}, "options": options}
            path = "/api/jobs/downloads/album"
        else:
            body = {"songQuery": {"artist": p["artist"], "title": p["title"], "album": p.get("album") or None}, "options": options}
            path = "/api/jobs/downloads/song"
        try:
            job = self._call("POST", path, body)
        except SoulseekError as error:
            self._fail(item, str(error))
            return
        if not job or not job.get("jobId"):
            self._fail(item, "Sockseek did not accept the job.")
            return
        self.manager.update_item(item["id"], persist=True, job_id=job["jobId"], step=0,
                                 step_detail="Searching Soulseek…", started=time.time())

    def _follow(self, item):
        try:
            detail = self._call("GET", f"/api/jobs/{item['job_id']}")
        except SoulseekError as error:
            self._fail(item, str(error))
            return
        if detail is None:  # the daemon is gone
            self._fail(item, "Sockseek stopped while this was downloading. Retry starts it again.")
            return
        summary, payload = detail.get("summary") or {}, detail.get("payload") or {}
        phase = summary.get("activityPhase") or ""
        progress, text = 0, {"Searching": "Searching Soulseek…", "WaitingForSearchConcurrency": "Waiting to search…",
                             "SearchRateLimited": "Soulseek limits searches - waiting…", "Downloading": "Downloading…",
                             "ProcessingSearchResults": "Choosing a source…", "RetrievingFolder": "Reading the folder…",
                             "Organizing": "Saving…"}.get(phase, phase or "Waiting…")
        if payload.get("kind") == "album" and payload.get("selectedFolderFileCount"):
            total = int(payload.get("selectedFolderFileCount") or 0)
            done = int(payload.get("selectedFolderCompletedFileCount") or 0)
            progress = int(done * 100 / total) if total else 0
            text = f"{done} of {total} files from {payload.get('resolvedFolderUsername') or 'a Soulseek user'}"
        elif payload.get("progressPercent") is not None:
            progress = int(float(payload.get("progressPercent") or 0))
        if summary.get("lifecycleState") != "Terminal":
            self.manager.update_item(item["id"], step=1 if phase == "Downloading" else 0, step_detail=text, progress=progress)
            return
        outcome = summary.get("terminalOutcome")
        if outcome in ("Succeeded", "PartialSuccess"):
            files = self._files(payload)
            for f in files:
                self.manager.record({"title": f["title"], "artists": [f["artist"] or item["artist"]],
                                     "album": f["album"] or (item["title"] if item["kind"] == "album" else item.get("album", "")),
                                     "source": "soulseek", "path": f["path"]})
            first = files[0]["path"] if files else ""
            note = f"Saved {len(files)} {'file' if len(files) == 1 else 'files'}" + (" (some failed)" if outcome == "PartialSuccess" else "")
            self.manager.update_item(item["id"], persist=True, state="done", step=4, progress=100, finished=time.time(),
                                     path=str(Path(first).parent) if item["kind"] == "album" and first else first,
                                     step_detail=first, note=note)
        else:
            reason = summary.get("failureMessage") or {"NoSearchResults": "Nobody on Soulseek shares it right now",
                                                       "NoMatchingResults": "No matching files on Soulseek",
                                                       "AllDownloadsFailed": "Every download attempt failed",
                                                       "OutOfDownloadRetries": "Gave up after several sources"}.get(
                summary.get("failureReason"), f"Soulseek: {outcome or 'failed'}")
            self._fail(item, reason)

    @staticmethod
    def _files(payload):
        songs = payload.get("tracks") if payload.get("kind") == "album" else [payload]
        out = []
        for s in songs or []:
            path = s.get("downloadPath")
            if not path:
                continue
            q = s.get("query") or {}
            artist, title = q.get("artist") or "", q.get("title") or ""
            if not title:
                artist2, title = parse_file_name(Path(path).name)
                artist = artist or artist2
            out.append({"path": path, "title": match.clean(title), "artist": match.clean(artist), "album": match.clean(q.get("album"))})
        return out
