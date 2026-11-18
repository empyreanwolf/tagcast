"""Same song, different name: audio fingerprints (fork v109).

A title can't tell that "Song (Official Audio)" and "Song - Remastered" are the same recording;
the sound can. Chromaprint's ``fpcalc`` (the tool behind AcoustID/MusicBrainz Picard) turns the
first 30 seconds of a song into a short list of numbers that stays the same across formats,
bitrates and re-encodes, and differs for a different recording.

- **Your library**: iBroadcast keeps the music, not this PC, so "Fingerprint my library" streams
  the start of every song once (≈0.6 MB each at 128 kbps, one at a time, in the background,
  resumable) and keeps the fingerprints in ``fingerprints.json.gz``. Only new songs are done on
  later runs.
- **New files**: every finished download is fingerprinted (from the file, a fraction of a second)
  and compared with the library. The Upload page's duplicate check then also flags "same audio"
  - you still choose Skip or Upload anyway there; nothing is skipped on its own.
- No account or key is needed: fingerprints are compared here, not sent anywhere.

``fpcalc`` is used from PATH when it is there, else downloaded once from Chromaprint's own GitHub
release (checked against a fixed SHA-256) into ``<settings folder>/tools/``.
"""

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import platform
import shutil
import stat
import struct
import subprocess
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import requests

from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "fingerprints.json.gz"
SECONDS = 30            # the fingerprinted part of each song (from the start)
STREAM_BYTES = 700_000  # ≈ 35 s of the 128 kbps stream
SAME = 0.85             # share of matching bits above which two fingerprints are the same recording (different songs score ~0.6)
MIN_ITEMS = 40          # ≈ 5 s of non-silent audio needed to compare at all
LENGTH_SLACK = 12       # seconds two songs' lengths may differ and still be compared
RELEASE = "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/"
FPCALC = {  # (asset, SHA-256 of the asset, file inside it)
    "windows": ("chromaprint-fpcalc-1.5.1-windows-x86_64.zip",
                "36b478e16aa69f757f376645db0d436073a42c0097b6bb2677109e7835b59bbc", "fpcalc.exe"),
    "linux": ("chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz",
              "4d7433a7f778e5946d7225230681cbcd634e153316ecac87c538c33ac32387a5", "fpcalc"),
    "darwin": ("chromaprint-fpcalc-1.5.1-macos-universal.tar.gz",
               "d4d8faff4b5f7c558d9be053da47804f9501eaa6c2f87906a9f040f38d61c860", "fpcalc"),
}


class FingerprintError(LibraryError):
    pass


# -- the tool ---------------------------------------------------------------------------------

def _tools_dir(home):
    return Path(home) / "tools"


def fpcalc_path(home=None):
    """fpcalc on PATH, else the copy Tagcast downloaded into its settings folder, else ""."""
    found = shutil.which("fpcalc")
    if found:
        return found
    if home:
        for name in ("fpcalc.exe", "fpcalc"):
            candidate = _tools_dir(home) / name
            if candidate.is_file():
                return str(candidate)
    return ""


def install_fpcalc(home, fetch=None):
    """Download fpcalc from Chromaprint's GitHub release, check its SHA-256, put it in tools/."""
    system = platform.system().lower()
    if system not in FPCALC or (system != "darwin" and platform.machine().lower() not in ("amd64", "x86_64")):
        raise FingerprintError(f"There is no ready-made fpcalc for this computer ({platform.system()} {platform.machine()}). "
                               "Install Chromaprint's fpcalc yourself and put it on PATH.")
    asset, digest, member = FPCALC[system]
    try:
        reply = (fetch or requests.get)(RELEASE + asset, timeout=60)
        data = reply.content if reply.status_code == 200 else b""
    except requests.RequestException as error:
        raise FingerprintError(f"Could not download fpcalc ({type(error).__name__}).") from None
    if not data:
        raise FingerprintError(f"Could not download fpcalc (HTTP {getattr(reply, 'status_code', '?')}).")
    if hashlib.sha256(data).hexdigest() != digest:
        raise FingerprintError("The downloaded fpcalc did not match its expected checksum, so it was not installed.")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.endswith("/" + member))
            body = z.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            name = next(m for m in t.getmembers() if m.name.endswith("/" + member) and m.isfile())
            body = t.extractfile(name).read()
    folder = _tools_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / member
    tmp = target.with_name(member + ".tmp")
    tmp.write_bytes(body)
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp, target)
    return str(target)


def run_fpcalc(exe, path=None, data=None, seconds=SECONDS):
    """(fingerprint as a list of ints, duration in seconds) for a file, or for audio bytes on stdin."""
    if not exe:
        raise FingerprintError("fpcalc isn't installed yet.")
    cmd = [exe, "-raw", "-json", "-length", str(int(seconds)), str(path) if data is None else "-"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(cmd, input=data, capture_output=True, timeout=60, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FingerprintError(f"fpcalc did not run ({type(error).__name__}).") from None
    try:
        result = json.loads(out.stdout or b"{}")
    except ValueError:
        result = {}
    fp = result.get("fingerprint")
    if out.returncode != 0 or not isinstance(fp, list) or not fp:
        raise FingerprintError((out.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "fpcalc could not read this audio.")
    return [int(x) & 0xFFFFFFFF for x in fp], float(result.get("duration") or 0)


# -- comparing --------------------------------------------------------------------------------

def pack(fp):
    return base64.b64encode(struct.pack(f"<{len(fp)}I", *fp)).decode()


def unpack(text):
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 4}I", raw))


def _trim(fp):
    """Without the leading silence (a run of one repeated value), which every quiet intro shares."""
    i = 0
    while i + 1 < len(fp) and fp[i] == fp[i + 1]:
        i += 1
    return fp[i:] if i else fp


def similarity(a, b, max_shift=12):
    """Share of matching bits (0..1) at the best small offset between two fingerprints; 0 when too short."""
    a, b = _trim(a), _trim(b)
    if len(a) < MIN_ITEMS or len(b) < MIN_ITEMS:
        return 0.0
    best = 0.0
    for shift in range(-max_shift, max_shift + 1):
        x, y = (a[shift:], b) if shift >= 0 else (a, b[-shift:])
        n = min(len(x), len(y))
        if n < MIN_ITEMS:
            continue
        differ = sum(bin(x[i] ^ y[i]).count("1") for i in range(n))
        best = max(best, 1.0 - differ / (32.0 * n))
    return best


# -- the library index ------------------------------------------------------------------------

class Fingerprints:
    def __init__(self, studio, stream=None, exe=None):
        self.studio = studio
        self.home = Path(studio.home)
        self._stream = stream  # (track_id) -> bytes of the song's start; tests pass their own
        self._exe = exe
        self.lock = threading.RLock()
        self.state = self._load()
        self.running = False
        self.stop_requested = False
        self.progress = None
        self.installing = False
        self.error = ""

    # state

    def _load(self):
        try:
            data = json.loads(gzip.decompress((self.home / STATE_FILE).read_bytes()))
        except (OSError, ValueError, EOFError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key in ("tracks", "failed", "files"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def _persist(self):
        with self.lock:
            body = gzip.compress(json.dumps(self.state, separators=(",", ":")).encode())
        tmp = self.home / (STATE_FILE + ".tmp")
        self.home.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(body)
        os.replace(tmp, self.home / STATE_FILE)

    def exe(self):
        return self._exe or fpcalc_path(self.home)

    def view(self):
        library = getattr(self.studio, "library", None)
        total = sum(1 for t in library.tracks if not library.tracks[t].get("trashed")) if library is not None else 0
        with self.lock:
            return {"installed": bool(self.exe()), "installing": self.installing, "tool": self.exe(),
                    "library_total": total, "done": len(self.state["tracks"]), "failed": len(self.state["failed"]),
                    "running": self.running, "progress": dict(self.progress) if self.progress else None,
                    "finished": self.state.get("finished", 0), "error": self.error}

    # the tool

    def install(self):
        with self.lock:
            if self.installing or self.exe():
                return self.view()
            self.installing, self.error = True, ""

        def run():
            try:
                install_fpcalc(self.home)
            except Exception as error:
                self.error = str(error)
                log.info("Installing fpcalc failed: %s", error)
            finally:
                self.installing = False
        threading.Thread(target=run, name="fpcalc-install", daemon=True).start()
        return self.view()

    # fingerprinting the library

    def start(self):
        with self.lock:
            if self.running:
                return self.view()
            if not self.exe():
                raise FingerprintError("Install fpcalc first (the button above).")
            if getattr(self.studio, "library", None) is None:
                raise FingerprintError("Load your library first.")
            self.running, self.stop_requested, self.error = True, False, ""
        threading.Thread(target=self._run, name="fingerprints", daemon=True).start()
        return self.view()

    def stop(self):
        self.stop_requested = True
        return self.view()

    def _stream_start(self, track_id):
        if self._stream:
            return self._stream(track_id)
        response, _ = self.studio.stream(track_id, quality="128")
        try:
            data = bytearray()
            for chunk in response.iter_content(65536):
                data += chunk
                if len(data) >= STREAM_BYTES:
                    break
            return bytes(data)
        finally:
            response.close()

    def _run(self):
        done_since_save = 0
        try:
            library = self.studio.library
            with self.lock:
                todo = [t for t in library.tracks if not library.tracks[t].get("trashed")
                        and str(t) not in self.state["tracks"] and str(t) not in self.state["failed"]]
                self.progress = {"done": 0, "total": len(todo), "current": ""}
            exe = self.exe()
            for track_id in todo:
                if self.stop_requested:
                    break
                track = library.tracks.get(track_id)
                if not track:
                    continue
                self.progress["current"] = str(track.get("title") or "")
                try:
                    fp, _ = run_fpcalc(exe, data=self._stream_start(track_id))
                    with self.lock:
                        self.state["tracks"][str(track_id)] = [int(track.get("length") or 0), pack(fp)]
                except Exception as error:  # one song never stops the rest; it is not retried automatically
                    with self.lock:
                        self.state["failed"][str(track_id)] = str(error)[:200]
                self.progress["done"] += 1
                done_since_save += 1
                if done_since_save >= 25:
                    self._persist()
                    done_since_save = 0
            if not self.stop_requested:
                with self.lock:
                    self.state["finished"] = time.time()
        except Exception as error:
            log.exception("Fingerprinting the library stopped")
            self.error = f"Stopped: {type(error).__name__}: {error}"[:300]
        finally:
            self._persist()
            with self.lock:
                self.running, self.progress = False, None

    def retry_failed(self):
        with self.lock:
            self.state["failed"] = {}
        return self.start()

    # comparing new files

    def file_fingerprint(self, path):
        """The cached fingerprint of a file on this PC (recomputed when the file changed), or None."""
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        key = str(path)
        with self.lock:
            cached = self.state["files"].get(key)
        if cached and cached[0] == int(st.st_mtime) and cached[1] == st.st_size:
            return unpack(cached[3]), cached[2]
        exe = self.exe()
        if not exe:
            return None
        try:
            fp, seconds = run_fpcalc(exe, path=path)
        except FingerprintError as error:
            log.info("Could not fingerprint %s: %s", path, error)
            return None
        with self.lock:
            files = self.state["files"]
            files[key] = [int(st.st_mtime), st.st_size, seconds, pack(fp)]
            if len(files) > 20000:  # the oldest file entries go first
                for old in list(files)[:len(files) - 20000]:
                    files.pop(old, None)
        return fp, seconds

    def match(self, fp, seconds=0):
        """The library song with the same audio, as {"track_id", "title", "artist", "album", "score"}, or None."""
        library = getattr(self.studio, "library", None)
        if library is None or not fp:
            return None
        with self.lock:
            known = list(self.state["tracks"].items())
        best = None
        for track_id, (length, packed) in known:
            if seconds and length and abs(length - seconds) > LENGTH_SLACK:
                continue
            score = similarity(fp, unpack(packed))
            if score >= SAME and (best is None or score > best[1]):
                best = (track_id, score)
        if not best:
            return None
        track_id = int(best[0]) if best[0].isdigit() else best[0]
        try:
            view = library.track_view(track_id)
        except LibraryError:
            return None  # trashed since
        album = library.albums.get(view["album_id"] if not str(view["album_id"]).isdigit() else int(view["album_id"]))
        return {"track_id": str(track_id), "title": view["title"], "artist": view["artist"],
                "album": str((album or {}).get("name") or ""), "score": round(best[1], 3)}

    def ready(self):
        """True once there is something to compare against (the library has been fingerprinted, at least partly)."""
        with self.lock:
            return bool(self.state["tracks"])

    def is_cached(self, path):
        try:
            st = Path(path).stat()
        except OSError:
            return False
        with self.lock:
            cached = self.state["files"].get(str(path))
        return bool(cached) and cached[0] == int(st.st_mtime) and cached[1] == st.st_size

    def match_file(self, path):
        if not self.ready():
            return None
        got = self.file_fingerprint(path)
        return self.match(*got) if got else None

    def check_download(self, path, item_id=None):
        """After a download: fingerprint it in the background, and note on its queue row when the same
        recording is already in your library. Never blocks or fails the download."""
        if not self.exe() or not self.state["tracks"]:
            return

        def run():
            try:
                hit = self.match_file(path)
                if hit and item_id is not None:
                    manager = getattr(self.studio, "download_manager", None)
                    item = manager.item(item_id) if manager else None
                    if item:
                        note = f"{item.get('note') or ''} · same audio as “{hit['title']}” by {hit['artist']} already in your library"
                        manager.update_item(item_id, persist=True, note=note.strip(" ·"), same_audio=hit)
            except Exception:
                log.exception("Fingerprint check of a download failed")
            finally:
                self.save()
        threading.Thread(target=run, name="fingerprint-check", daemon=True).start()

    def save(self):
        try:
            self._persist()
        except OSError as error:
            log.info("Could not save the fingerprints: %s", error)
