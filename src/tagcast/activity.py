"""Activity log (fork v35): everything Tagcast does, in one place.

Every action you take that reaches the server (a button, a search, a save), every step
of the download queue (queued, held, started, saved, failed, skipped), every tag or
metadata save and its read-back, background runs (cleanups, Artist pictures) and every
warning or error Tagcast logs is recorded as one entry:

    {"id", "t" (epoch seconds), "cat", "status" (ok | error | warn | info), "title",
     "detail", "source", "ms"}

Entries go to <settings folder>/activity.log as one JSON object per line (rotated at
2 MB, three files kept) and the most recent ones stay in memory for the Activity log
page. Nothing secret is written: request summaries leave out passwords, tokens and keys.

Modules call ``record(...)``; it does nothing until a Studio has called ``start(home)``,
so code (and tests) that run without one are unaffected. (``start``, not ``open``:
the module writes files with the builtin ``open``.)
"""

import json
import logging
import os
import threading
import time
from collections import Counter, deque
from pathlib import Path

FILE = "activity.log"
MAX_BYTES = 2_000_000
KEEP_FILES = 3
MEMORY = 5000
CATEGORIES = {
    "downloads": "Downloads",
    "metadata": "Tags & metadata",
    "cleanup": "Cleanup",
    "pictures": "Artist pictures",
    "library": "Library",
    "playback": "Playback",
    "settings": "Settings",
    "page": "Page errors",
    "system": "System",
    "app": "Other actions",
}
STATUSES = ("ok", "error", "warn", "info")
SECRET_WORDS = ("password", "token", "secret", "key", "client_id", "auth", "code", "cookie")

_current = None
_current_lock = threading.Lock()


class ActivityLog:
    def __init__(self, home):
        self.path = Path(home) / FILE
        self.lock = threading.RLock()
        self.entries = deque(maxlen=MEMORY)
        self.next_id = 1
        self.started = time.time()
        self._load()

    # -- writing -----------------------------------------------------------------

    def _load(self):
        """The tail of the existing log, so the page keeps its history across restarts."""
        lines = []
        for path in (self.path.with_name(FILE + ".1"), self.path):
            try:
                lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
        for line in lines[-MEMORY:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and "t" in entry:
                entry["id"] = self.next_id
                self.next_id += 1
                self.entries.append(entry)

    def record(self, cat, status, title, detail="", source="", ms=None):
        entry = {"id": 0, "t": round(time.time(), 3), "cat": cat if cat in CATEGORIES else "app",
                 "status": status if status in STATUSES else "info", "title": _short(title, 300)}
        if detail:
            entry["detail"] = _short(detail, 4000)
        if source:
            entry["source"] = _short(source, 40)
        if ms is not None:
            entry["ms"] = int(ms)
        with self.lock:
            entry["id"] = self.next_id
            self.next_id += 1
            self.entries.append(entry)
            self._write(entry)
        return entry

    def _write(self, entry):
        # A disk problem here must never break what was being logged.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                for n in range(KEEP_FILES - 1, 0, -1):
                    older = self.path.with_name(f"{FILE}.{n}")
                    if n == KEEP_FILES - 1:
                        older.unlink(missing_ok=True)
                    newer = self.path if n == 1 else self.path.with_name(f"{FILE}.{n - 1}")
                    if newer.exists():
                        os.replace(newer, older)
            line = json.dumps({k: v for k, v in entry.items() if k != "id"}, ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        except OSError:
            pass

    # -- reading -----------------------------------------------------------------

    def view(self, cat="", status="", query="", after=0, limit=300):
        """Newest first. ``status`` "problems" means errors and warnings; ``after`` returns
        only entries newer than that id (the page's live refresh)."""
        words = str(query or "").casefold().split()
        limit = max(1, min(int(limit or 300), 2000))
        with self.lock:
            entries = list(self.entries)
        found = []
        for entry in reversed(entries):
            if after and entry["id"] <= after:
                break
            if cat and entry.get("cat") != cat:
                continue
            if status == "problems" and entry.get("status") not in ("error", "warn"):
                continue
            if status and status != "problems" and entry.get("status") != status:
                continue
            if words:
                text = " ".join(str(entry.get(k, "")) for k in ("title", "detail", "source", "cat")).casefold()
                if not all(w in text for w in words):
                    continue
            found.append(entry)
            if len(found) >= limit:
                break
        return {"entries": found, "latest": entries[-1]["id"] if entries else 0,
                "summary": self.summary(entries), "categories": CATEGORIES, "file": str(self.path)}

    @staticmethod
    def summary(entries, hours=24):
        """What worked and what didn't in the last day, by category."""
        since = time.time() - hours * 3600
        counts, last_problem = {}, {}
        for entry in entries:
            if entry.get("t", 0) < since:
                continue
            cat = entry.get("cat", "app")
            counts.setdefault(cat, Counter())[entry.get("status", "info")] += 1
            if entry.get("status") in ("error", "warn"):
                last_problem[cat] = {"t": entry["t"], "title": entry.get("title", "")}
        return [{"cat": cat, "label": CATEGORIES.get(cat, cat), "ok": c["ok"], "error": c["error"],
                 "warn": c["warn"], "info": c["info"], "last_problem": last_problem.get(cat)}
                for cat, c in sorted(counts.items(), key=lambda kv: -(kv[1]["error"] * 1000 + kv[1]["warn"]))]

    def export_text(self):
        """Every stored entry (all rotated files), oldest first, as readable lines."""
        lines = []
        for n in range(KEEP_FILES - 1, -1, -1):
            path = self.path if n == 0 else self.path.with_name(f"{FILE}.{n}")
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in raw:
                try:
                    lines.append(format_line(json.loads(line)))
                except (ValueError, AttributeError):
                    continue
        header = f"Tagcast activity log, exported {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + "\n".join(lines) + "\n"


def format_line(entry):
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("t", 0)))
    parts = [when, entry.get("status", "info").upper().ljust(5), CATEGORIES.get(entry.get("cat"), entry.get("cat", ""))]
    if entry.get("source"):
        parts.append(f"[{entry['source']}]")
    parts.append(entry.get("title", ""))
    if entry.get("ms") is not None:
        parts.append(f"({entry['ms']} ms)")
    line = " ".join(p for p in parts if p)
    if entry.get("detail"):
        line += "\n    " + str(entry["detail"]).replace("\n", "\n    ")
    return line


def _short(value, size):
    text = str(value if value is not None else "")
    return text if len(text) <= size else text[:size - 1] + "…"


# -- the module-level entry points ------------------------------------------------------

def start(home):
    """Start recording for this settings folder (the newest Studio wins)."""
    global _current
    with _current_lock:
        _current = ActivityLog(home)
        _install_handler()
        return _current


def current():
    return _current


def record(cat, status, title, detail="", source="", ms=None):
    log = _current
    if log is None:
        return None
    try:
        return log.record(cat, status, title, detail, source, ms)
    except Exception:  # the activity log never breaks the thing it records
        return None


def summarize(data, size=240):
    """A short, secret-free description of a request body or a response."""
    if not isinstance(data, dict):
        return ""
    parts = []
    for key, value in data.items():
        if any(word in str(key).lower() for word in SECRET_WORDS):
            continue
        if isinstance(value, bool) or value is None:
            text = str(value).lower()
        elif isinstance(value, (int, float)):
            text = str(value)
        elif isinstance(value, str):
            if not value:
                continue
            text = f"“{_short(value, 70)}”"
        elif isinstance(value, (list, tuple)):
            text = f"{len(value)} item{'s' if len(value) != 1 else ''}"
            if value and all(isinstance(v, str) for v in value[:3]) and len(value) <= 3:
                text = ", ".join(_short(v, 40) for v in value)
        elif isinstance(value, dict):
            continue
        else:
            continue
        parts.append(f"{key}: {text}")
    return _short(" · ".join(parts), size)


def record_run(cat, name, task):
    """How a background run (a cleanup) ended, with its items counted by outcome."""
    if not isinstance(task, dict):
        return None
    counts = Counter()

    def walk(value):
        if isinstance(value, dict):
            if "status" in value and isinstance(value.get("status"), str) and ("id" in value or "title" in value):
                counts[value["status"]] += 1
            for child in value.values():
                if isinstance(child, (list, dict)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(task.get("items") or task.get("positions") or [])
    state = task.get("state", "")
    status = "ok" if state == "done" else "error" if state == "error" else "warn"
    tally = ", ".join(f"{n} {s}" for s, n in counts.most_common())
    return record(cat, status, f"{name} {state}" + (f": {tally}" if tally else ""), task.get("error", ""))


# -- Python logging → activity ------------------------------------------------------------

class _Handler(logging.Handler):
    """Every INFO-or-worse message from Tagcast's own modules becomes an entry, so warnings
    that used to sit only in tagcast.log (or nowhere, at INFO) show on the Activity page."""

    def emit(self, record_):
        if getattr(_tls, "busy", False):
            return
        _tls.busy = True
        try:
            status = "error" if record_.levelno >= logging.ERROR else "warn" if record_.levelno >= logging.WARNING else "info"
            title = record_.getMessage()
            detail = ""
            if record_.exc_info:
                detail = logging.Formatter().formatException(record_.exc_info)
            name = record_.name.rsplit(".", 1)[-1]
            cat = _module_category(name)
            record(cat, status, title.split("\n", 1)[0], detail or ("\n".join(title.split("\n")[1:]) or ""), source=name)
        except Exception:
            pass
        finally:
            _tls.busy = False


_tls = threading.local()
_handler = None


def _module_category(name):
    if name in ("galaxy", "bandcamp", "soulseek", "youtube", "moresources", "downloads", "source_worker", "discography", "wishlist",
                "completion", "charts", "recommendations", "releases", "telegram"):
        return "downloads"
    if name in ("cleanup", "artist_cleanup", "duplicate_cleanup", "tagclean"):
        return "cleanup"
    if name in ("artist_pictures",):
        return "pictures"
    if name in ("library", "client", "tags", "lyrics", "artwork", "collections"):
        return "library" if name in ("library", "client") else "metadata"
    return "system"


def _install_handler():
    global _handler
    if _handler is not None:
        return
    _handler = _Handler(level=logging.INFO)
    logger = logging.getLogger("tagcast")
    logger.addHandler(_handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)  # the root logger's handlers still only show warnings
