"""Debug report (fork v73): one zip with what is needed to find a problem, and nothing private.

Contains: versions and system, the Activity log, tagcast.log, the download queue and failed uploads (titles and reasons),
and the non-secret settings. Never contains sign-in tokens, API keys or passwords (tokens.json, gemini.json, config
keys and Soulseek/Discogs/Spotify credentials are left out). Song and artist names appear because logs name them.
"""

import io
import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from . import __version__
from .updates import local_fork

SECRET_WORDS = ("token", "password", "secret", "key", "authorization", "cookie")


def scrub(value):
    """A copy of a settings structure with every secret-looking value replaced."""
    if isinstance(value, dict):
        return {k: ("<hidden>" if any(w in str(k).lower() for w in SECRET_WORDS) and v else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value[:500]]
    return value


def _version(program, *args):
    """First line of ``program --version`` (or the given args), or "" when it isn't installed."""
    import shutil
    import subprocess
    exe = program if Path(str(program)).is_file() else shutil.which(program)
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, *(args or ("--version",))], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0][:200]
    except Exception as error:
        return f"installed, but did not answer ({type(error).__name__})"


def _tools(studio):
    import shutil
    tools = {}
    try:
        from .youtube import ffmpeg_path
        tools["ffmpeg"] = _version(ffmpeg_path() or "ffmpeg", "-version") or "not found"
    except Exception:
        tools["ffmpeg"] = "unknown"
    try:
        import yt_dlp
        tools["yt-dlp"] = yt_dlp.version.__version__
    except Exception:
        tools["yt-dlp"] = "not installed"
    try:
        from .fingerprint import fpcalc_path
        tools["fpcalc"] = _version(fpcalc_path() or "fpcalc", "-version") or "not found"
    except Exception:
        tools["fpcalc"] = "unknown"
    out = {"tools": tools}
    try:
        folder = studio.download_manager.folder()
        free = shutil.disk_usage(folder).free
        out["downloads_folder_free_gb"] = round(free / 1e9, 1)
    except Exception:
        pass
    wishlist = studio._read("wishlist.json") or {}
    out["wishlist_items"] = len(wishlist.get("items") or []) if isinstance(wishlist, dict) else 0
    return out


def build(studio):
    home = Path(studio.home)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        info = {"tagcast": __version__, "fork": f"v{local_fork()}", "python": sys.version.split()[0], "system": platform.platform(),
                "made": time.strftime("%Y-%m-%d %H:%M:%S"), "settings_folder": str(home)}
        info.update(_tools(studio))  # fork v109: helper programs, free space, wishlist size
        z.writestr("info.json", json.dumps(info, indent=2))
        failed = [{k: i.get(k) for k in ("source", "title", "artist", "album", "note", "query", "link", "finished")}
                  for i in (studio._read("downloads.json") or {}).get("queue", []) if i.get("state") == "failed"]
        z.writestr("failed-downloads.json", json.dumps(failed[-200:], indent=2, default=str))
        log = getattr(studio, "activity", None)
        z.writestr("activity.txt", log.export_text() if log else "No activity recorded.\n")
        for n, name in enumerate(["tagcast.log", "tagcast.log.1", "tagcast.log.2"]):
            try:
                z.writestr(name, (home / name).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        for name, label in (("config.json", "settings.json"), ("downloads.json", "download-state.json"), ("uploader.json", "upload-state.json"),
                            ("youtube.json", "youtube-state.json"), ("galaxy.json", "galaxy-state.json")):
            data = studio._read(name)
            if isinstance(data, dict):
                if name == "downloads.json":
                    data = {"queue": [{k: i.get(k) for k in ("state", "source", "title", "artist", "album", "note", "query")} for i in data.get("queue", [])][:300],
                            "held": len(data.get("held", [])), "history": len(data.get("history", [])), "pc": data.get("pc", ""), "folder": data.get("folder", "")}
                z.writestr(label, json.dumps(scrub(data), indent=2, default=str)[:400000])
    return out.getvalue()
