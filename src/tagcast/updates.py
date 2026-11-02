"""Update checks for this fork (fork v66); never use iBroadcast or GitHub credentials.

The original Tagcast's release check (cyberdeliaAI/tagcast) is replaced: following it would install
the original over this fork. Instead Tagcast reads the "fork vN" number in ``artist-cleanup.js`` on
the fork's ``main`` branch on GitHub and compares it with the running one. A newer fork offers the
zip of ``main`` to extract over the Tagcast folder."""

import json
import logging
import math
import re
import threading
import time

from pathlib import Path

import requests

from . import __version__

FORK_REPO = "empyreanwolf/tagcast"
FORK_VERSION_URL = f"https://raw.githubusercontent.com/{FORK_REPO}/main/src/tagcast/static/artist-cleanup.js"
FORK_ZIP = f"https://github.com/{FORK_REPO}/archive/refs/heads/main.zip"
FORK_PAGE = f"https://github.com/{FORK_REPO}/commits/main"
FORK_RE = re.compile(r'artistCleanupVersion\s*=\s*"fork v(\d+)"')
LOCAL_SCRIPT = Path(__file__).resolve().parent / "static" / "artist-cleanup.js"


def fork_number(text):
    m = FORK_RE.search(text or "")
    if not m:
        raise ValueError("No fork version found")
    return int(m.group(1))


def local_fork():
    try:
        return fork_number(LOCAL_SCRIPT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
CHECK_INTERVAL = 24 * 60 * 60
MAX_RESPONSE = 1024 * 1024
log = logging.getLogger("tagcast")


def version_order(version):
    """Compare the project's Python versions, including a beta before its final release."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:b(\d+))?", version)
    if not match:
        raise ValueError("Unsupported Tagcast version")
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), beta is None, int(beta or 0)


def release_info(data):
    """Keep only a stable version and a bounded label; construct our own release URL."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("Not a stable release")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Unsupported release tag")
    version = tag[1:]
    version_order(version)
    name = data.get("name")
    return {"version": version, "name": name[:200] if isinstance(name, str) else f"Tagcast {version}"}


class UpdateChecker:
    """Serialize checks and persist attempts, including failures, across restarts."""

    def __init__(self, read, write):
        self.write = write
        self.lock = threading.Lock()
        self.cache = {}
        cached = read("updates.json")
        if isinstance(cached, dict):
            for key in ("attempted_at", "checked_at"):
                value = cached.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= time.time():
                    self.cache[key] = value
            latest = cached.get("fork_latest")
            if isinstance(latest, int) and not isinstance(latest, bool) and 0 < latest < 100000:
                self.cache["fork_latest"] = latest
            if cached.get("error"):
                self.cache["error"] = "Could not check for updates. Try again later or view GitHub releases."

    def _persist(self):
        try:
            self.write("updates.json", self.cache)
        except OSError:
            log.warning("Could not save the update cache; keeping it in memory.")

    def _result(self, enabled):
        latest, current = self.cache.get("fork_latest"), local_fork()
        available = bool(latest and current and latest > current)
        error = self.cache.get("error", "")
        state = "error" if error else "available" if available else "current" if latest else "unchecked" if enabled else "disabled"
        return {"current": f"fork v{current}" if current else __version__, "latest": f"fork v{latest}" if latest else None,
                "name": f"Tagcast fork v{latest}" if latest else "", "available": available,
                "url": FORK_ZIP if available else "", "page": FORK_PAGE, "fork": True,
                "checked_at": self.cache.get("checked_at"), "auto_updates": enabled,
                "state": state, "error": error}

    def check(self, enabled=True, manual=False):
        with self.lock:
            now = time.time()
            attempted = self.cache.get("attempted_at")
            if not manual and (not enabled or attempted is not None and 0 <= now - attempted < CHECK_INTERVAL):
                return self._result(enabled)
            self.cache["attempted_at"] = now
            self._persist()
            try:
                # No netrc credentials, cookies or iBroadcast session. Do not follow
                # redirects: the only destination is GitHub's public release API.
                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(FORK_VERSION_URL, timeout=(3.05, 7), stream=True, allow_redirects=False,
                                     headers={"User-Agent": f"Tagcast/{__version__}", "Cache-Control": "no-cache"}) as response:
                        if response.status_code != 200:
                            raise ValueError("Fork check failed")
                        chunks, size = [], 0
                        for chunk in response.iter_content(8192):
                            size += len(chunk)
                            if size > MAX_RESPONSE:
                                raise ValueError("Response too large")
                            chunks.append(chunk)
                        latest = fork_number(b"".join(chunks).decode("utf-8", "replace"))
                self.cache.update(fork_latest=latest, checked_at=time.time())
                self.cache.pop("latest", None)
                self.cache.pop("error", None)
            except (requests.RequestException, ValueError):
                self.cache["error"] = f"Could not check the fork on GitHub ({FORK_REPO}). Try again later."
            self._persist()
            return self._result(enabled)
