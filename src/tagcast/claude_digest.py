"""Claude digest (fork v125): a short, secret-free summary of what went wrong, small enough to paste into a chat.

Holds the versions and helper programs, the latest errors and warnings from the Activity log, failed downloads,
the state of the saved search line and the last yt-dlp timeout (URL and its last output lines).
Everything goes through ``debug_report.scrub`` and a pattern scrub, and the result is capped at 8000 bytes.
"""

import re
import threading
import time

from . import __version__, activity, debug_report
from .updates import local_fork

MAX_BYTES = 8000
_ytdlp = {}
_lock = threading.Lock()
_SECRET = re.compile(r"(?i)((?:token|password|secret|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;&\"']+")


def note_ytdlp(url, tail):
    """Remember the last yt-dlp timeout: the address and the last lines it printed."""
    with _lock:
        _ytdlp.clear()
        _ytdlp.update({"url": str(url or ""), "tail": "\n".join(str(tail or "").splitlines()[-20:]), "t": time.time()})


def youtube_context():
    with _lock:
        return dict(_ytdlp)


def _clean(text):
    return _SECRET.sub(r"\1<hidden>", str(text))


def _sections(studio):
    info = {"tagcast": __version__, "fork": f"v{local_fork()}", "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        info.update(debug_report._tools(studio))
    except Exception:
        pass
    head = "# Tagcast digest\n" + "\n".join(f"- {k}: {v}" for k, v in debug_report.scrub(info).items())
    log = activity.current()
    errors = [e for e in (list(log.entries) if log else []) if e.get("status") in ("error", "warn")][-15:]
    problems = "## Latest problems\n" + ("\n".join(activity.format_line(e) for e in reversed(errors)) or "none")
    try:
        queue = (studio._read("downloads.json") or {}).get("queue", [])
    except Exception:
        queue = []
    failed = [i for i in queue if isinstance(i, dict) and i.get("state") == "failed"][-10:]
    failed_text = "## Failed downloads\n" + ("\n".join(
        f"- {i.get('source', '')} | {i.get('artist', '')} - {i.get('title', '')} | {i.get('note', '')}" for i in failed) or "none")
    try:
        line = studio._read("search_line.json") or {}
    except Exception:
        line = {}
    jobs = line.get("jobs") or [] if isinstance(line, dict) else []
    line_text = "## Search line\n" + f"{len(jobs)} saved job(s); skipped sources: {', '.join(line.get('skipped', [])) if isinstance(line, dict) else ''}"
    ctx = youtube_context()
    yt = "## Last yt-dlp timeout\n" + (f"{ctx['url']}\n{ctx['tail']}" if ctx else "none")
    return [head, problems, failed_text, line_text, yt]


def build_digest(studio, max_bytes=MAX_BYTES):
    parts = [_clean(p) for p in _sections(studio)]
    text = "\n\n".join(parts)
    while len(text.encode("utf-8")) > max_bytes and len(parts[1]) > 80:
        lines = parts[1].splitlines()
        parts[1] = "\n".join(lines[: max(2, len(lines) // 2)])
        text = "\n\n".join(parts)
    raw = text.encode("utf-8")
    return text if len(raw) <= max_bytes else raw[:max_bytes].decode("utf-8", "ignore")
