"""One-click update (fork v73): download the fork's ``main`` from GitHub, copy its files over this folder, restart.

Only files from the zip are written (nothing is deleted), never anything outside this Tagcast folder, and never
``.venv``, ``.git`` or your settings folder (``.tagcast``: sign-in, decisions, history). The zip must hold a newer
fork version than the one running. Restart is Windows-only (it starts the same launcher again); elsewhere the files
are replaced and you restart yourself.
"""

import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import requests

from . import __version__
from .library import LibraryError
from .updates import FORK_ZIP, fork_number, local_fork

log = logging.getLogger("tagcast")
ROOT = Path(__file__).resolve().parents[2]
MAX_ZIP = 80 * 1024 * 1024
SKIP_TOP = {".git", ".venv", "venv", ".tagcast", "__pycache__", "node_modules", "docs", "media", "tools"}
VERSION_PATH = "src/tagcast/static/artist-cleanup.js"


class UpdateError(LibraryError):
    pass


def project_root():
    return ROOT


def installable(root=None):
    root = Path(root or ROOT)
    return (root / "pyproject.toml").is_file() and (root / "src" / "tagcast").is_dir()


def download(url=FORK_ZIP):
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(5, 60), stream=True, headers={"User-Agent": f"Tagcast/{__version__}"}) as response:
                if response.status_code != 200:
                    raise UpdateError(f"GitHub answered HTTP {response.status_code}. Try again later.")
                data, size = io.BytesIO(), 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_ZIP:
                        raise UpdateError("The update is larger than expected, so it was not installed.")
                    data.write(chunk)
                return data.getvalue()
    except requests.RequestException as error:
        raise UpdateError(f"Could not reach GitHub ({error.__class__.__name__}). Check the internet connection.") from None


def apply_zip(data, root=None, current=None):
    """Copy the zip's files over ``root``. Returns (new fork number, files written)."""
    root = Path(root or ROOT).resolve()
    if not installable(root):
        raise UpdateError("This copy of Tagcast is not a source folder, so it can't update itself. Extract the zip over it instead.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("The download was not a valid zip.") from None
    with archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        prefix = names[0].split("/", 1)[0] + "/" if names else ""
        if not names or not all(n.startswith(prefix) for n in names) or prefix + VERSION_PATH not in names:
            raise UpdateError("The download does not look like this Tagcast fork, so nothing was changed.")
        new = fork_number(archive.read(prefix + VERSION_PATH).decode("utf-8", "replace"))
        have = local_fork() if current is None else current
        if new <= have:
            raise UpdateError(f"Already up to date (fork v{have}).")
        jobs = []
        for name in names:
            rel = Path(name[len(prefix):])
            if not rel.parts or rel.parts[0] in SKIP_TOP or ".." in rel.parts or rel.is_absolute():
                continue
            target = (root / rel).resolve()
            if root not in target.parents:
                continue
            jobs.append((name, target))
        written = 0
        for name, target in jobs:  # write everything to side files first, then swap, so a failure leaves the old version
            target.parent.mkdir(parents=True, exist_ok=True)
            target.with_name(target.name + ".new").write_bytes(archive.read(name))
        try:
            for name, target in jobs:
                os.replace(target.with_name(target.name + ".new"), target)
                written += 1
        finally:
            for _, target in jobs:
                target.with_name(target.name + ".new").unlink(missing_ok=True)
    return new, written


def launcher(root=None):
    root = Path(root or ROOT)
    for name in ("start-tagcast-fork.bat", "start-tagcast.bat"):
        if (root / name).is_file():
            return root / name
    return None


def restart(root=None, delay=1.5, exit_now=os._exit):
    """Start the launcher again in a new window once this server has let go of its port, then stop this server."""
    bat = launcher(root)
    if os.name != "nt" or not bat:
        return False
    port = os.environ.get("TAGCAST_PORT", "")
    command = f'timeout /t 3 /nobreak >nul & call "{bat}"'
    subprocess.Popen(["cmd", "/c", "start", "Tagcast", "cmd", "/c", command], cwd=str(bat.parent),  # noqa: S603,S607
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    threading.Timer(delay, lambda: exit_now(0)).start()
    return True


def install(do_restart=True):
    """Download, install and restart. Called from the page; the answer is sent before the server stops."""
    activity_note = f"Update from {FORK_ZIP}"
    log.info(activity_note)
    data = download()
    new, written = apply_zip(data)
    restarted = restart() if do_restart else False
    return {"installed": f"fork v{new}", "files": written, "restarting": restarted,
            "message": (f"Updated to fork v{new} ({written} files). Tagcast is restarting…" if restarted
                        else f"Updated to fork v{new} ({written} files). Close Tagcast and start it again.")}
