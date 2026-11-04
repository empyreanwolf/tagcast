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


def build(studio):
    home = Path(studio.home)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        info = {"tagcast": __version__, "fork": f"v{local_fork()}", "python": sys.version.split()[0], "system": platform.platform(),
                "made": time.strftime("%Y-%m-%d %H:%M:%S"), "settings_folder": str(home)}
        z.writestr("info.json", json.dumps(info, indent=2))
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
