"""Downloads (fork v9): duplicate check, Needs your decision, queue, history and folder.

Every song or album that a source wants to fetch passes ``submit()`` first. It is
compared with the loaded iBroadcast library and with the download history:

- new items go to the queue;
- anything you already have (in iBroadcast, or downloaded before) is held in
  **Needs your decision** until you choose Skip or Download anyway. A duplicate is
  never queued automatically.

The sources themselves (Bandcamp, Soulseek, Galaxy, Telegram) arrive in later
versions; this module holds the shared parts. Nothing is uploaded to iBroadcast and
no request is sent to it: the check only reads the library copy Tagcast already has.

The state lives in ``downloads.json`` in the settings folder. The old download
histories of the Galaxy app (``history.json``) and the Telegram downloader
(``%APPDATA%\\TelegramAutoDownloader\\state.json`` / ``download_history.csv``) can
be imported; importing twice adds nothing twice.
"""

import csv
import io
import json
import logging
import os
import platform
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from . import activity
from . import artist_match as match
from .library import LibraryError, fold, number, text, title_key

log = logging.getLogger("tagcast")

STATE_FILE = "downloads.json"
KINDS = ("song", "album")
SOURCES = ("youtube", "galaxy", "telegram", "bandcamp", "soulseek", "manual")
MAX_ITEMS = 500           # per check or submit
MAX_IMPORT_BYTES = 50 * 1024 * 1024
HISTORY_PAGE = 50
EXAMPLES = 3
# "(feat. X)", "[ft. X]", "(with X)" and a trailing "feat. X" do not make another song.
FEAT_BRACKET = re.compile(r"\s*[(\[（【]\s*(?:feat|ft|featuring|with)\b\.?[^)\]）】]*[)\]）】]", re.I)
FEAT_TAIL = re.compile(r"\s+(?:feat|ft|featuring)\.?\s.*$", re.I)
TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*(?:[-._)]\s*|\s+)(?=\S)")
AUDIO_EXT = re.compile(r"\.(?:mp3|m4a|flac|ogg|opus|wav|aac|wma)$", re.I)
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "appdata", "$recycle.bin",
             "windows", "program files", "program files (x86)", "library"}


# -- matching --------------------------------------------------------------------

def song_key(title):
    """Titles ignore "(feat. …)", case, spacing, quotes and Chinese script; version words stay."""
    value = match.clean(title)
    value = FEAT_BRACKET.sub("", value)
    value = FEAT_TAIL.sub("", value)
    return match.simplified(title_key(value))


def album_key(title):
    return match.simplified(title_key(match.clean(title)))


def artist_keys(*names):
    """Every key a credited name can match on: the whole name, its parts ("A/B") and the
    Latin and Chinese halves of a bilingual name ("Faye Wong 王菲")."""
    keys = set()
    for name in names:
        for part in [name, *match.split_parts(name)[1]]:
            part = match.clean(part)
            if not part:
                continue
            keys.add(match.match_key(part))
            pair = match.bilingual(part)
            if pair:
                keys.update(match.match_key(p) for p in pair)
    keys.discard("")
    keys.discard(match.match_key("Various Artists"))
    return keys


def _named(*names):
    """True if at least one of these is a real artist name, even one that produces no
    usable key (e.g. "Various Artists") -- as opposed to no artist text at all."""
    return any(match.clean(name) for name in names)


def parse_file_name(name):
    """"03 - Artist - Title.mp3" → ("Artist", "Title"); without " - " the artist is unknown."""
    stem = AUDIO_EXT.sub("", text(name).strip())
    stem = TRACK_NUMBER.sub("", stem).replace("_", " ").strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return match.clean(artist), match.clean(title)
    return "", match.clean(stem)


class LibraryIndex:
    """Songs and albums of one library copy, keyed for the duplicate check."""

    def __init__(self, library):
        self.songs, self.albums = {}, {}
        sets = {}
        for album_id in library.album_ids():
            album = library.albums[album_id]
            album_name = text(album.get("name"))
            album_artists = [library.artist_name(i) for i in library.album_artist_ids(album)]
            tracks = library.active_track_ids(album_id)
            # Discs of one set (same title and album artist) count as one album.
            set_id = (album_key(album_name), number(album.get("artist_id")))
            entry = sets.setdefault(set_id, {"title": album_name, "artist": album_artists[0] if album_artists else "",
                                             "keys": artist_keys(*album_artists), "named": _named(*album_artists),
                                             "tracks": 0, "album_id": album_id})
            entry["tracks"] += len(tracks)
            for track_id in tracks:
                track = library.tracks[track_id]
                names = [library.artist_name(track.get("artist_id")),
                         *(library.artist_name(e["artist_id"]) for e in library.extra_artists(track)
                           if e.get("type") in (None, "", "artist", "band"))]
                self.songs.setdefault(song_key(track.get("title")), []).append({
                    "keys": artist_keys(*names) | entry["keys"], "named": _named(*names) or entry["named"],
                    "title": text(track.get("title")),
                    "artist": names[0], "album": album_name, "track_id": track_id, "album_id": album_id})
        for entry in sets.values():
            self.albums.setdefault(album_key(entry["title"]), []).append(entry)


class HistoryIndex:
    def __init__(self, entries):
        self.songs, self.albums = {}, {}
        for entry in entries:
            artists = entry.get("artists", [])
            keys, named = artist_keys(*artists), _named(*artists)
            self.songs.setdefault(song_key(entry.get("title")), []).append((keys, named, entry))
            if entry.get("album"):
                self.albums.setdefault(album_key(entry["album"]), []).append((keys, named, entry))


def _overlap(keys, named, other_keys, other_named):
    # Only a side with no artist text at all (a Telegram file without " - ") falls back
    # to the title alone. A *known* artist that happens to carry no usable key -- "Various
    # Artists" on a compilation is the common case -- must never become a wildcard that
    # matches a generic title ("Intro", "Interlude") from an unrelated artist.
    if not named or not other_named:
        return True
    return bool(keys & other_keys)


def check_item(item, library_index, history_index):
    """{"status": have / partly / downloaded / new, "you_have": [...]} for one item."""
    keys = artist_keys(item["artist"]) if item["artist"] else set()
    named = _named(item["artist"])
    found = []
    if item["kind"] == "song":
        for hit in library_index.songs.get(song_key(item["title"]), []) if library_index else []:
            if _overlap(keys, named, hit["keys"], hit["named"]):
                found.append({"where": "ibroadcast", "title": hit["title"], "artist": hit["artist"],
                              "album": hit["album"], "album_id": hit["album_id"]})
        if found:
            return {"status": "have", "you_have": found[:EXAMPLES]}
        for other, other_named, entry in history_index.songs.get(song_key(item["title"]), []):
            if _overlap(keys, named, other, other_named):
                found.append(_history_hit(entry))
        return {"status": "downloaded" if found else "new", "you_have": found[-EXAMPLES:]}
    partly = []
    for hit in library_index.albums.get(album_key(item["title"]), []) if library_index else []:
        if _overlap(keys, named, hit["keys"], hit["named"]):
            row = {"where": "ibroadcast", "title": hit["title"], "artist": hit["artist"],
                   "tracks": hit["tracks"], "album_id": hit["album_id"]}
            (partly if item.get("tracks") and hit["tracks"] != item["tracks"] else found).append(row)
    if found:
        return {"status": "have", "you_have": found[:EXAMPLES]}
    if partly:
        return {"status": "partly", "you_have": partly[:EXAMPLES]}
    songs = [entry for other, other_named, entry in history_index.albums.get(album_key(item["title"]), [])
             if _overlap(keys, named, other, other_named)]
    if songs:
        hit = _history_hit(songs[-1])
        hit["songs"] = len({song_key(e.get("title")) for e in songs})
        return {"status": "downloaded", "you_have": [hit]}
    return {"status": "new", "you_have": []}


def _history_hit(entry):
    return {"where": "history", "title": entry.get("title", ""), "artist": ", ".join(entry.get("artists", [])),
            "album": entry.get("album", ""), "source": entry.get("source", ""), "when": entry.get("when"),
            "pc": entry.get("pc", "")}


# -- Activity log (fork v35) ------------------------------------------------------------------

def _name(item):
    """How one queue item reads in the Activity log: Song “Title” · Artist (Album)."""
    kind = "Album" if item.get("kind") == "album" else "Song"
    album = item.get("album") if item.get("kind") != "album" else ""
    return (f"{kind} “{item.get('title', '?')}”" + (f" · {item['artist']}" if item.get("artist") else "")
            + (f" ({album})" if album else ""))


def _names(items, shown=3):
    names = [_name(i) for i in items[:shown]]
    return "; ".join(names) + (f" and {len(items) - shown} more" if len(items) > shown else "")


def _have(item):
    places = []
    for hit in item.get("you_have") or []:
        if isinstance(hit, dict):
            places.append(f"{hit.get('where', '?')}: {hit.get('title', '')} {hit.get('album') or ''}".strip())
    return "; ".join(places[:3]) or "a likely match"


def _record_state(item):
    """A queue item changed state: saved, failed, skipped, queued again."""
    state, source = item.get("state"), item.get("source", "")
    took = ""
    if item.get("started_at") and state in ("done", "failed"):
        took = f" in {max(0, time.time() - item['started_at']):.0f} s"
    if state == "done":
        activity.record("downloads", "ok", f"Saved {_name(item)}{took}",
                        "\n".join(x for x in (item.get("path", ""), item.get("note", "")) if x and x != "Saved"), source=source)
    elif state == "failed":
        activity.record("downloads", "error", f"Failed {_name(item)}{took}: {item.get('note', '')}",
                        f"Step {item.get('step', '?')}: {item.get('step_detail', '')}", source=source)
    elif state == "skipped":
        activity.record("downloads", "info", f"Skipped {_name(item)}", item.get("note", ""), source=source)


def clean_item(raw, source="manual"):
    if not isinstance(raw, dict):
        raise LibraryError("Each item needs a title and an artist.")
    kind = raw.get("kind", "song")
    if kind not in KINDS:
        raise LibraryError("Choose song or album.")
    title, artist = match.clean(raw.get("title")), match.clean(raw.get("artist"))
    if not title or len(title) > 500 or len(artist) > 500:
        raise LibraryError("Each item needs a title of at most 500 characters.")
    tracks = raw.get("tracks")
    if tracks not in (None, ""):
        try:
            tracks = int(tracks)
        except (TypeError, ValueError):
            raise LibraryError("The track count must be a number.") from None
        if not 0 < tracks < 1000:
            raise LibraryError("The track count must be between 1 and 999.")
    else:
        tracks = None
    item = {"kind": kind, "title": title, "artist": artist, "album": match.clean(raw.get("album"))[:500],
            "tracks": tracks, "source": source if source in SOURCES else "manual"}
    link = text(raw.get("link")).strip()
    if link:
        item["link"] = link[:2000]
    if isinstance(raw.get("payload"), dict) and len(json.dumps(raw["payload"])) < 20000:
        item["payload"] = raw["payload"]  # what the source needs to fetch it (e.g. a Galaxy song id)
    return item


def parse_lines(value, kind="song"):
    """"Artist - Title" per line; albums may end with "| 12" for the track count."""
    if kind not in KINDS:
        raise LibraryError("Choose songs or albums.")
    items = []
    for line in text(value).splitlines():
        line = line.strip()
        if not line:
            continue
        tracks = None
        if kind == "album" and "|" in line:
            line, _, count = line.rpartition("|")
            tracks = count.strip() or None
        if " - " in line:
            artist, title = line.split(" - ", 1)
        else:
            artist, title = "", line
        items.append({"kind": kind, "artist": artist.strip(), "title": title.strip(), "tracks": tracks})
    if len(items) > MAX_ITEMS:
        raise LibraryError(f"Check at most {MAX_ITEMS} lines at a time.")
    return items


# -- importing old histories -----------------------------------------------------

def galaxy_entries(data):
    """Rows of the Galaxy app's history.json ({"version": 1, "entries": [...]})."""
    items = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise LibraryError("This is not a Galaxy history.json (no entries list).")
    out = []
    for e in items:
        if not isinstance(e, dict) or not text(e.get("title")).strip():
            continue
        artists = [match.clean(a) for a in e.get("artists") or [] if text(a).strip()]
        when = e.get("when")
        try:
            when = float(when)
        except (TypeError, ValueError):
            when = None
        key = e.get("key") or f"{e.get('ident') or song_key(e['title'])}|{when}"
        out.append({"title": match.clean(e["title"]), "artists": artists, "album": match.clean(e.get("album")),
                    "source": "galaxy", "when": when, "path": text(e.get("path")), "format": text(e.get("format")),
                    "import_key": f"galaxy:{key}|{when}"})
    return out


def telegram_entries(state=None, csv_text=None):
    """Rows of the Telegram downloader: state.json "history" (sha256 → name/size/date) plus
    download_history.csv. One row per file hash."""
    rows = {}
    history = (state or {}).get("history") if isinstance(state, dict) else None
    if isinstance(history, dict):
        for sha, info in history.items():
            if isinstance(info, dict) and text(info.get("name")).strip():
                rows[str(sha)] = (info["name"], info.get("size"), info.get("date"))
    if csv_text:
        for row in csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))):
            sha, name = text(row.get("SHA-256")).strip(), text(row.get("File name")).strip()
            if name and (sha or name) not in rows:
                size = row.get("Size (MB)")
                try:
                    size = int(float(size) * 1048576)
                except (TypeError, ValueError):
                    size = None
                rows[sha or name] = (name, size, row.get("Downloaded"))
    out = []
    for sha, (name, size, date) in rows.items():
        artist, title = parse_file_name(name)
        if not title:
            continue
        out.append({"title": title, "artists": [artist] if artist else [], "album": "", "source": "telegram",
                    "when": _when(date), "file": text(name), "size": size if isinstance(size, int) else None,
                    "sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "", "import_key": f"telegram:{sha}"})
    return out


def _when(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return time.mktime(time.strptime(text(value).strip()[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _read_file(path):
    path = Path(path)
    if not path.is_file():
        raise LibraryError(f"No file at {path}.")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise LibraryError("That file is larger than 50 MB; it is not a download history.")
    return path.read_text(encoding="utf-8-sig", errors="replace")


def telegram_folder():
    return Path(os.environ.get("APPDATA") or Path.home()) / "TelegramAutoDownloader"


def default_folder():
    return str(Path.home() / "Music")


def find_galaxy_histories(roots=None, depth=3, limit=10):
    """history.json files of the Galaxy app (next to its app.py) under the usual folders."""
    home = Path.home()
    roots = roots or [home / n for n in ("Desktop", "Documents", "Downloads", "Music", "OneDrive")] + [home]
    found, seen = [], set()

    def walk(folder, level):
        if len(found) >= limit or level > depth:
            return
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        names = {e.name for e in entries}
        if "history.json" in names and ("app.py" in names or "run.bat" in names):
            path = str(Path(folder) / "history.json")
            if path not in seen:
                seen.add(path)
                found.append(path)
        for e in entries:
            if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and e.name.casefold() not in SKIP_DIRS:
                walk(e.path, level + 1)

    for root in roots:
        walk(root, 0 if root != home else depth)  # home itself: only its direct files
    return found


# -- the shared state ----------------------------------------------------------------

class Downloads:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        data = studio._read(STATE_FILE)
        data = data if isinstance(data, dict) else {}
        self.state = {"version": 1, "folder": data.get("folder") or "",
                      "queue": [i for i in data.get("queue") or [] if isinstance(i, dict)],
                      "held": [i for i in data.get("held") or [] if isinstance(i, dict)],
                      "history": [i for i in data.get("history") or [] if isinstance(i, dict)],
                      "imports": data.get("imports") if isinstance(data.get("imports"), dict) else {},
                      "pc": text(data.get("pc")).strip()[:40], "sync_folder": text(data.get("sync_folder")).strip()}
        self.sync_error = ""
        self._others = {}   # file name -> (mtime, rows) from the other PCs in the shared folder (v54)
        self._history_index = None
        self._library_index = (None, None)
        self.state["paused"] = bool(data.get("paused"))
        self.workers = {}  # source name -> its download worker (Galaxy, Bandcamp, Soulseek)
        self._last_persist = 0.0
        self.persist_error = ""  # fork, v29: last failure saving the queue to disk, if any

    def _persist(self):
        # A save failure here (a disk hiccup, an antivirus lock, a OneDrive sync conflict --
        # all seen on Windows) must never propagate: every worker's loop calls into this on
        # its way to picking the next job, outside its own per-song error handling, so an
        # unguarded raise here used to kill that worker's thread silently -- "stuck" forever,
        # with nothing in the queue explaining why. It's logged and remembered instead.
        try:
            self.studio._write(STATE_FILE, self.state)
            self.persist_error = ""
        except Exception as e:
            self.persist_error = f"Could not save the download queue: {type(e).__name__}: {e}"
            log.exception("Could not save the download queue to disk")

    def _index(self, need_library=True):
        library = None
        if need_library:
            self.studio._require_client()
            library, _ = self.studio._library()
        if library is not None and self._library_index[0] is not library:
            self._library_index = (library, LibraryIndex(library))
        others = self._other_rows()
        signature = tuple(sorted((name, mtime) for name, (mtime, _) in self._others.items()))
        if self._history_index is None or getattr(self, "_history_sig", None) != signature:
            self._history_index = HistoryIndex(self.state["history"] + others)
            self._history_sig = signature
        return (self._library_index[1] if library is not None else None), self._history_index

    def folder(self):
        return self.state["folder"] or default_folder()

    # -- Which PC downloaded what (fork v54) ----------------------------------------------------
    # Every download is stamped with this PC's name. With a shared folder (OneDrive, Dropbox, a network
    # share) each PC writes its own file there and reads the others', so a song fetched at the office
    # still reads "downloaded at Office, not uploaded yet" at home, instead of being downloaded twice.

    def pc_name(self):
        return self.state.get("pc") or text(platform.node()).strip()[:40] or "this PC"

    def _sync_dir(self):
        value = self.state.get("sync_folder")
        return Path(value) if value else None

    def _sync_file(self):
        safe = re.sub(r"[^\w .-]", "_", self.pc_name())
        return f"tagcast-downloads-{safe}.json"

    def _sync_write(self):
        folder = self._sync_dir()
        if not folder:
            return
        try:
            mine = [e for e in self.state["history"] if e.get("pc") == self.pc_name()]
            target = folder / self._sync_file()
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps({"version": 1, "pc": self.pc_name(), "entries": mine}), encoding="utf-8")
            os.replace(temp, target)
            self.sync_error = ""
        except OSError as error:
            self.sync_error = f"Could not write to the shared folder ({error.strerror or error})."
            log.info("Download sync write failed: %s", error)

    def _other_rows(self):
        """Rows other PCs wrote into the shared folder, re-read only when a file changed."""
        folder = self._sync_dir()
        if not folder:
            self._others = {}
            return []
        mine = self._sync_file()
        try:
            files = {f.name: f for f in folder.glob("tagcast-downloads-*.json") if f.name != mine}
            seen = {}
            for name, path in files.items():
                mtime = path.stat().st_mtime
                old = self._others.get(name)
                if old and old[0] == mtime:
                    seen[name] = old
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                pc = text(data.get("pc")) or name
                rows = [{**e, "pc": pc} for e in data.get("entries") or [] if isinstance(e, dict)]
                seen[name] = (mtime, rows)
            self._others = seen
            self.sync_error = ""
        except (OSError, ValueError) as error:
            self.sync_error = f"Could not read the shared folder ({getattr(error, 'strerror', None) or error})."
        return [row for _, rows in self._others.values() for row in rows]

    def set_pc(self, name, sync_folder):
        name, folder = text(name).strip()[:40], text(sync_folder).strip().strip('"')
        if folder:
            path = Path(os.path.expandvars(os.path.expanduser(folder)))
            if not path.is_absolute():
                raise LibraryError("Enter a full folder path for the shared folder, for example C:\\Users\\you\\OneDrive\\Tagcast.")
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise LibraryError(f"Tagcast cannot create or use that folder ({error.strerror or error}).") from None
            folder = str(path)
        with self.lock:
            old = self.pc_name()
            self.state["pc"], self.state["sync_folder"] = name, folder
            for row in self.state["history"]:   # earlier downloads of this PC follow the new name
                if row.get("pc") == old:
                    row["pc"] = self.pc_name()
            self._history_index = None
            self._persist()
            self._sync_write()
        return self.view()

    # -- reading ---------------------------------------------------------------

    def view(self):
        with self.lock:
            self._other_rows()
            sources = Counter(e.get("source", "") for e in self.state["history"])
            return {"folder": self.folder(), "folder_set": bool(self.state["folder"]),
                    "queue": len([i for i in self.state["queue"] if i.get("state") == "queued"]),
                    "held": len(self.state["held"]), "history": len(self.state["history"]),
                    "history_sources": dict(sources), "imports": self.state["imports"],
                    "telegram_folder": str(telegram_folder()),
                    "pc": self.pc_name(), "pc_set": bool(self.state["pc"]), "sync_folder": self.state["sync_folder"],
                    "genre_waiting": self._genre_waiting(),
                    "sync_error": self.sync_error, "other_pcs": sorted({r.get("pc", "") for _, rows in self._others.values() for r in rows})}

    def _genre_waiting(self):
        """Downloaded files whose genre Tagcast could not place and is waiting for you to choose (v56)."""
        tagclean = getattr(self.studio, "tagclean", None)
        try:
            return sum(1 for r in tagclean.state.get("rows", []) if r.get("needs_genre")) if tagclean else 0
        except Exception:
            return 0

    def queue_view(self):
        self._watchdog()
        with self.lock:
            return {"queue": [dict(i) for i in self.state["queue"][-1000:]], "held": list(self.state["held"]),
                    "persist_error": self.persist_error,
                    "stalled": sorted(self._stalled_sources())}

    def _requeue_orphaned(self):
        """An item a dead worker had just picked up (flipped to "active") never gets looked
        at again on its own -- nothing marks it queued, failed or done. Put it back in line
        for whichever worker restarts next."""
        with self.lock:
            changed = False
            for item in self.state["queue"]:
                worker = self.workers.get(item.get("source"))
                if item.get("state") == "active" and worker and not (worker.thread and worker.thread.is_alive()):
                    item.update(state="queued", step=-1, step_detail="", progress=0)
                    changed = True
            if changed:
                self._persist()

    def _stalled_sources(self):
        """Sources with work waiting on them -- queued, or "active" because a now-dead
        worker had just picked it up -- whose worker thread isn't running, while not paused.
        Normally only right after an unexpected crash; ``wake_workers()`` below recovers it."""
        if self.state["paused"]:
            return []
        with self.lock:
            busy = {i.get("source") for i in self.state["queue"]
                    if i.get("state") in ("queued", "active") and i.get("payload")}
        return [source for source in busy if source in self.workers
                and not (self.workers[source].thread and self.workers[source].thread.is_alive())]

    def _watchdog(self):
        """Polled from the Downloads page every few seconds: if a worker's thread died (it
        should not, but see _persist above for the kind of thing that used to cause it) and
        there is still queued work waiting for it, start it again -- same as a manual Restart,
        just automatic, so a crash recovers within one poll instead of needing a page reload."""
        stalled = self._stalled_sources()
        if stalled:
            self._requeue_orphaned()
            self.wake_workers()

    def history_view(self, query="", source="", offset=0, limit=HISTORY_PAGE):
        offset, limit = max(0, number(offset)), min(200, max(1, number(limit) or HISTORY_PAGE))
        query = fold(query).strip()
        with self.lock:
            rows = self.state["history"]
            if source:
                rows = [e for e in rows if e.get("source") == source]
            if query:
                rows = [e for e in rows if query in fold(" ".join([e.get("title", ""), *e.get("artists", []),
                                                                      e.get("album", ""), e.get("file", "")]))]
            rows = sorted(rows, key=lambda e: -(e.get("when") or 0))
            return {"total": len(rows), "offset": offset, "entries": rows[offset:offset + limit]}

    def check(self, items=None, lines=None, kind="song"):
        """Read-only: how each item compares with iBroadcast and the history."""
        raw = parse_lines(lines, kind) if lines is not None else items
        if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
            raise LibraryError(f"Check between 1 and {MAX_ITEMS} items.")
        cleaned = [clean_item(i) for i in raw]
        library_index, history_index = self._index()
        with self.lock:
            return {"results": [{**item, **check_item(item, library_index, history_index)} for item in cleaned]}

    # -- writing ---------------------------------------------------------------

    def submit(self, items, source="manual", pre_check=None):
        """Queue new items; hold everything you may already have for your decision.
        ``pre_check(item)`` may report one more place you have it (e.g. the download folder)."""
        if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
            raise LibraryError(f"Add between 1 and {MAX_ITEMS} items.")
        cleaned = [clean_item(i, source) for i in items]
        library_index, history_index = self._index()
        queued, held = [], []
        with self.lock:
            waiting = {self._same(i) for i in self.state["queue"] if i.get("state") == "queued"} \
                | {self._same(i) for i in self.state["held"]}
            for item in cleaned:
                if self._same(item) in waiting:
                    continue  # already queued or already waiting for you
                waiting.add(self._same(item))
                result = check_item(item, library_index, history_index)
                if result["status"] == "new" and pre_check:
                    hit = pre_check(item)
                    if hit:
                        result = {"status": "folder", "you_have": [hit]}
                row = {**item, "id": secrets.token_hex(6), "added": time.time(), **result}
                if result["status"] == "new":
                    row["state"] = "queued"
                    self.state["queue"].append(row)
                    queued.append(row)
                else:
                    self.state["held"].append(row)
                    held.append(row)
            self._persist()
        skipped = len(cleaned) - len(queued) - len(held)
        if queued:  # recorded before waking the workers, so "Queued" precedes "Started"
            activity.record("downloads", "info", f"Queued {len(queued)} on {source}: {_names(queued)}", source=source)
        if held:
            activity.record("downloads", "info", f"{len(held)} held for your decision (you may have them): {_names(held)}",
                            "\n".join(f"{_name(i)} - {i.get('status')}: {_have(i)}" for i in held[:50]), source=source)
        if skipped:
            activity.record("downloads", "info", f"{skipped} already queued or waiting, not added again", source=source)
        if queued:
            self.wake_workers()
        return {"queued": len(queued), "held": len(held), "skipped": skipped}

    @staticmethod
    def _same(item):
        payload = item.get("payload")
        if isinstance(payload, dict) and payload.get("id"):
            return (item.get("source"), str(payload.get("source")), str(payload["id"]))
        return (item.get("kind"), song_key(item.get("title")), match.match_key(item.get("artist")), item.get("source"))

    def decide(self, ids, action):
        """Skip or Download anyway, for one held item or many at once."""
        if action not in ("skip", "download"):
            raise LibraryError("Choose Skip or Download anyway.")
        if not isinstance(ids, list) or not ids:
            raise LibraryError("Choose at least one item.")
        wanted = set(map(str, ids))
        with self.lock:
            chosen = [i for i in self.state["held"] if i["id"] in wanted]
            if not chosen:
                raise LibraryError("Those items were already decided. Reopen Needs your decision.")
            self.state["held"] = [i for i in self.state["held"] if i["id"] not in wanted]
            for item in chosen:
                item["decided"] = time.time()
                if action == "download":
                    item["state"], item["allow_duplicate"] = "queued", True
                else:
                    item["state"] = "skipped"
                self.state["queue"].append(item)
            self._persist()
        if chosen:
            activity.record("downloads", "info", f"{'Download anyway' if action == 'download' else 'Skip'}: {_names(chosen)}",
                            source=chosen[0].get("source", ""))
        if action == "download":
            self.wake_workers()
        return {"decided": len(chosen), "action": action, "missing": len(wanted) - len(chosen)}

    def remove(self, ids):
        """Take items off the queue list (never touches files or the history)."""
        wanted = set(map(str, ids or []))
        with self.lock:
            gone = [i for i in self.state["queue"] if i["id"] in wanted]
            if gone:
                activity.record("downloads", "info", f"Removed from the queue: {_names(gone)}", source=gone[0].get("source", ""))
            before = len(self.state["queue"])
            self.state["queue"] = [i for i in self.state["queue"] if i["id"] not in wanted]
            self._persist()
            return {"removed": before - len(self.state["queue"])}

    # -- the download workers of the sources ---------------------------------------------
    # Each worker (Galaxy, Bandcamp, Soulseek) takes queued items of its own source. A worker
    # has ``ensure_worker()``, ``active_ids()`` and ``cancel(item_id)``. Pause is shared.

    def register(self, source, worker):
        self.workers[source] = worker

    def wake_workers(self):
        for worker in list(self.workers.values()):
            try:
                worker.ensure_worker()
            except Exception:  # one source never blocks another
                pass

    def is_paused(self):
        return self.state["paused"]

    def active_ids(self):
        out = set()
        for worker in self.workers.values():
            out |= set(worker.active_ids())
        return out

    def control(self, action, ids=None):
        """The queue buttons: pause/resume, cancel current, remove, retry, move to top, clear."""
        ids = [str(i) for i in ids or []]
        active = self.active_ids()
        if action in ("pause", "resume"):
            with self.lock:
                self.state["paused"] = action == "pause"
                self._persist()
            self.wake_workers()
        elif action == "cancel":
            for worker in self.workers.values():
                for item_id in worker.active_ids():
                    worker.cancel(item_id)
        elif action == "remove":
            for worker in self.workers.values():
                for item_id in set(worker.active_ids()) & set(ids):
                    worker.cancel(item_id)
            self.remove([i for i in ids if i not in active])
        elif action == "retry":
            self.requeue(ids or None)
            self.wake_workers()
        elif action == "allow_short":
            # "Keep it (short track)": download again without the short-file check (v31).
            if ids:
                self.requeue(ids, allow_short=True)
                self.wake_workers()
        elif action == "restart":
            # A worker's thread should never actually die mid-queue (see _persist), but this
            # gives a way back without restarting Tagcast itself if one somehow does.
            self._requeue_orphaned()
            self.wake_workers()
        elif action == "top":
            self.move_to_top(ids)
        elif action in ("clear_done", "clear_failed", "clear_waiting", "clear_skipped"):
            state = {"clear_done": "done", "clear_failed": "failed", "clear_waiting": "queued", "clear_skipped": "skipped"}[action]
            with self.lock:
                self.state["queue"] = [i for i in self.state["queue"] if i.get("state") != state or i["id"] in active]
                self._persist()
        else:
            raise LibraryError("Unknown queue action.")
        return {"paused": self.state["paused"], "active": sorted(self.active_ids())}


    def item(self, item_id):
        with self.lock:
            return next((dict(i) for i in self.state["queue"] if i["id"] == item_id), None)

    def update_item(self, item_id, persist=False, **fields):
        """Progress updates stay in memory (saved at most every 5 s); state changes are saved."""
        with self.lock:
            for item in self.state["queue"]:
                if item["id"] == item_id:
                    before = item.get("state")
                    item.update(fields)
                    if "state" in fields and fields["state"] != before:
                        _record_state(item)
                    break
            else:
                return
            if persist or time.monotonic() - self._last_persist > 5:
                self._last_persist = time.monotonic()
                self._persist()

    def next_queued(self, source):
        with self.lock:
            for item in self.state["queue"]:
                if item.get("state") == "queued" and item.get("source") == source and item.get("payload"):
                    item.update(state="active", step=0, step_detail="Requesting link…", progress=0, note="",
                                started_at=time.time())
                    activity.record("downloads", "info", f"Started {_name(item)}", source=source)
                    self._persist()
                    return dict(item)
        return None

    def has_queued(self, source):
        with self.lock:
            return any(i.get("state") == "queued" and i.get("source") == source and i.get("payload")
                       for i in self.state["queue"])

    def requeue(self, ids=None, allow_short=False):
        """Retry failed items (all of them when ``ids`` is None)."""
        wanted = set(ids) if ids else None
        with self.lock:
            for item in self.state["queue"]:
                if item.get("state") == "failed" and (wanted is None or item["id"] in wanted):
                    activity.record("downloads", "info", f"Retry {_name(item)}" + (" (keep it, short track)" if allow_short else ""),
                                    f"It had failed with: {item.get('note', '')}", source=item.get("source", ""))
                    item.update(state="queued", note="", step=-1, step_detail="", progress=0)
                    item.pop("short_seconds", None)
                    if allow_short and isinstance(item.get("payload"), dict):
                        item["payload"] = {**item["payload"], "allow_short": True}
            self._persist()

    def move_to_top(self, ids):
        wanted = set(ids)
        with self.lock:
            queue = self.state["queue"]
            moving = [i for i in queue if i["id"] in wanted and i.get("state") == "queued"]
            rest = [i for i in queue if i not in moving]
            first = next((n for n, i in enumerate(rest) if i.get("state") == "queued"), len(rest))
            self.state["queue"] = rest[:first] + moving + rest[first:]
            self._persist()

    def clear_state(self, state, keep=None):
        with self.lock:
            self.state["queue"] = [i for i in self.state["queue"] if i.get("state") != state or i["id"] == keep]
            self._persist()

    def record(self, entry):
        """Add a finished download to the history (used by the sources from v10 on)."""
        row = {"title": match.clean(entry.get("title")), "artists": [match.clean(a) for a in entry.get("artists") or []],
               "album": match.clean(entry.get("album")), "source": entry.get("source", "manual"),
               "when": entry.get("when") or time.time(), "path": text(entry.get("path")), "pc": self.pc_name()}
        with self.lock:
            self.state["history"].append(row)
            self._history_index = None
            self._persist()
            self._sync_write()
        return row

    def set_folder(self, folder):
        value = text(folder).strip().strip('"')
        if not value:
            with self.lock:
                self.state["folder"] = ""
                self._persist()
            return self.view()
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.is_absolute() or len(value) > 400:
            raise LibraryError("Enter a full folder path, for example C:\\Users\\you\\Music.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LibraryError(f"Tagcast cannot create or use that folder ({error.strerror or error}).") from None
        if not path.is_dir() or not os.access(path, os.W_OK):
            raise LibraryError("Tagcast cannot write to that folder.")
        with self.lock:
            self.state["folder"] = str(path)
            self._persist()
        return self.view()

    def find_galaxy(self):
        return {"paths": find_galaxy_histories()}

    def import_history(self, kind, path=None):
        if kind == "galaxy":
            if not text(path).strip():
                raise LibraryError("Enter where the Galaxy app's history.json is, or use Find it.")
            try:
                rows = galaxy_entries(json.loads(_read_file(text(path).strip().strip('"'))))
            except ValueError as error:
                if isinstance(error, LibraryError):
                    raise
                raise LibraryError("That history.json could not be read (it is not valid JSON).") from None
            where = text(path).strip()
        elif kind == "telegram":
            folder = Path(text(path).strip().strip('"')) if text(path).strip() else telegram_folder()
            if folder.is_file():
                folder = folder.parent
            state, csv_text = None, None
            if (folder / "state.json").is_file():
                try:
                    state = json.loads(_read_file(folder / "state.json"))
                except ValueError:
                    state = None
            if (folder / "download_history.csv").is_file():
                csv_text = _read_file(folder / "download_history.csv")
            if state is None and csv_text is None:
                raise LibraryError(f"No Telegram history (state.json or download_history.csv) in {folder}.")
            rows = telegram_entries(state, csv_text)
            where = str(folder)
        else:
            raise LibraryError("Choose the Galaxy or the Telegram history.")
        with self.lock:
            known = {e.get("import_key") for e in self.state["history"] if e.get("import_key")}
            added = [r for r in rows if r["import_key"] not in known and not known.add(r["import_key"])]
            self.state["history"].extend(added)
            self.state["imports"][kind] = {"path": where, "when": time.time(), "added": len(added), "found": len(rows)}
            self._history_index = None
            self._persist()
        return {"kind": kind, "found": len(rows), "added": len(added), "already": len(rows) - len(added),
                "history": len(self.state["history"])}
