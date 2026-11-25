"""Back up and restore your settings as one file (fork v109).

**Back up** downloads a zip with Tagcast's settings files: your settings, the wishlist, the
download history and queue, each source's settings (Galaxy, YouTube, Bandcamp, Soulseek, Spotify,
…), ignore lists and keep-separate pairs. Left out: sign-in tokens (``tokens.json``), passwords
and other secrets inside the files, the logs, and caches that rebuild themselves.

**Restore** reads such a zip and first shows what would change, file by file (new / changed /
the same, with counts for the wishlist and history). Only on "Restore" are the files written -
the current ones are copied to ``before-restore-<date>/`` first - and Tagcast then needs a
restart to load them. Passwords you have now are kept (the backup has none to restore).
"""

import base64
import binascii
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import __version__
from .library import LibraryError

LEFT_OUT = {"tokens.json", "logsync.json", "library-cache.json.gz", "discography_cache.json", "artist_cards_cache.json",
            "galaxy_quota.json", "lyrics_translations.json", "fingerprints.json"}
SECRET = re.compile(r"(?i)token|password|secret|cookie|authorization")
NAME = re.compile(r"^[A-Za-z0-9_.-]+\.json$")
MAX_FILE = 40 * 1024 * 1024


class BackupFileError(LibraryError):
    pass


def _strip(value):
    """A copy without secret values (keys named like a token or password)."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not (SECRET.search(str(k)) and v)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _keep_secrets(restored, current):
    """The restored settings, with the secrets you have now put back (the backup carries none)."""
    if isinstance(restored, dict) and isinstance(current, dict):
        out = {k: _keep_secrets(v, current.get(k)) for k, v in restored.items()}
        for k, v in current.items():
            if SECRET.search(str(k)) and v and k not in out:
                out[k] = v
        return out
    return restored


def _files(home):
    return sorted(p for p in Path(home).glob("*.json") if p.name not in LEFT_OUT and p.is_file())


def export(home, fork=""):
    """The backup zip as bytes."""
    out = io.BytesIO()
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in _files(home):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # a damaged file is not worth backing up
            z.writestr(f"settings/{path.name}", json.dumps(_strip(data), ensure_ascii=False, indent=1))
            names.append(path.name)
        z.writestr("manifest.json", json.dumps({"application": "Tagcast settings backup", "tagcast": __version__,
                                                "fork": fork, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "files": names}, indent=2))
    return out.getvalue()


def _read_zip(data):
    if isinstance(data, str):
        try:
            data = base64.b64decode(data.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise BackupFileError("That isn't a Tagcast backup file.") from None
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise BackupFileError("That isn't a Tagcast backup file (no manifest inside).") from None
    if manifest.get("application") != "Tagcast settings backup":
        raise BackupFileError("That isn't a Tagcast backup file.")
    files = {}
    for info in z.infolist():
        name = info.filename.removeprefix("settings/")
        if not info.filename.startswith("settings/") or not NAME.match(name) or name in LEFT_OUT:
            continue
        if info.file_size > MAX_FILE:
            raise BackupFileError(f"{name} in that backup is too large.")
        try:
            files[name] = json.loads(z.read(info))
        except ValueError:
            raise BackupFileError(f"{name} in that backup is damaged.") from None
    return manifest, files


def _summary(name, data):
    if not isinstance(data, dict):
        return ""
    if name == "wishlist.json":
        return f"{len(data.get('items') or [])} wished for"
    if name == "downloads.json":
        return f"{len(data.get('history') or [])} in history, {len(data.get('queue') or [])} queued"
    return ""


def preview(home, data):
    """What a restore would change, without changing anything."""
    manifest, files = _read_zip(data)
    rows = []
    for name, restored in sorted(files.items()):
        path = Path(home) / name
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, ValueError):
            current = None
        merged = _keep_secrets(restored, current)
        status = "new" if current is None else "same" if merged == current else "changed"
        rows.append({"file": name, "status": status, "backup": _summary(name, restored),
                     "now": _summary(name, current) if current is not None else ""})
    return {"made": manifest.get("made", ""), "fork": manifest.get("fork", ""), "tagcast": manifest.get("tagcast", ""),
            "files": rows, "changes": sum(1 for r in rows if r["status"] != "same")}


def plan(home, data):
    """[(file name, new content)] for the files a restore would change; nothing is written yet."""
    _, files = _read_zip(data)
    out = []
    for name, restored in sorted(files.items()):
        path = Path(home) / name
        current = None
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = None
        merged = _keep_secrets(restored, current)
        if merged != current:
            out.append((name, merged))
    return out


def write(home, changes):
    """Write a plan from ``plan()`` (current files copied to before-restore-<date>/ first).
    Called as Tagcast stops, so nothing running can write over the restored files afterwards."""
    home = Path(home)
    keep = home / time.strftime("before-restore-%Y%m%d-%H%M%S")
    for name, content in changes:
        path = home / name
        if path.exists():
            keep.mkdir(exist_ok=True)
            shutil.copy2(path, keep / name)
        tmp = path.with_name(path.name + ".restore")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(content, ensure_ascii=False, indent=2))
        os.replace(tmp, path)
    return {"restored": [n for n, _ in changes], "kept_in": keep.name if keep.exists() else ""}
