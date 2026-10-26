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
import os
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from . import artist_match as match
from .library import LibraryError, fold, number, text, title_key

STATE_FILE = "downloads.json"
KINDS = ("song", "album")
SOURCES = ("galaxy", "telegram", "bandcamp", "soulseek", "manual")
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
                                             "keys": artist_keys(*album_artists), "tracks": 0, "album_id": album_id})
            entry["tracks"] += len(tracks)
            for track_id in tracks:
                track = library.tracks[track_id]
                names = [library.artist_name(track.get("artist_id")),
                         *(library.artist_name(e["artist_id"]) for e in library.extra_artists(track)
                           if e.get("type") in (None, "", "artist", "band"))]
                self.songs.setdefault(song_key(track.get("title")), []).append({
                    "keys": artist_keys(*names) | entry["keys"], "title": text(track.get("title")),
                    "artist": names[0], "album": album_name, "track_id": track_id, "album_id": album_id})
        for entry in sets.values():
            self.albums.setdefault(album_key(entry["title"]), []).append(entry)


class HistoryIndex:
    def __init__(self, entries):
        self.songs, self.albums = {}, {}
        for entry in entries:
            keys = artist_keys(*entry.get("artists", []))
            self.songs.setdefault(song_key(entry.get("title")), []).append((keys, entry))
            if entry.get("album"):
                self.albums.setdefault(album_key(entry["album"]), []).append((keys, entry))


def _overlap(keys, other):
    # Unknown artist (a Telegram file without " - "): the title alone decides.
    return not keys or not other or bool(keys & other)


def check_item(item, library_index, history_index):
    """{"status": have / partly / downloaded / new, "you_have": [...]} for one item."""
    keys = artist_keys(item["artist"]) if item["artist"] else set()
    found = []
    if item["kind"] == "song":
        for hit in library_index.songs.get(song_key(item["title"]), []) if library_index else []:
            if _overlap(keys, hit["keys"]):
                found.append({"where": "ibroadcast", "title": hit["title"], "artist": hit["artist"],
                              "album": hit["album"], "album_id": hit["album_id"]})
        if found:
            return {"status": "have", "you_have": found[:EXAMPLES]}
        for other, entry in history_index.songs.get(song_key(item["title"]), []):
            if _overlap(keys, other):
                found.append(_history_hit(entry))
        return {"status": "downloaded" if found else "new", "you_have": found[-EXAMPLES:]}
    partly = []
    for hit in library_index.albums.get(album_key(item["title"]), []) if library_index else []:
        if _overlap(keys, hit["keys"]):
            row = {"where": "ibroadcast", "title": hit["title"], "artist": hit["artist"],
                   "tracks": hit["tracks"], "album_id": hit["album_id"]}
            (partly if item.get("tracks") and hit["tracks"] != item["tracks"] else found).append(row)
    if found:
        return {"status": "have", "you_have": found[:EXAMPLES]}
    if partly:
        return {"status": "partly", "you_have": partly[:EXAMPLES]}
    songs = [entry for other, entry in history_index.albums.get(album_key(item["title"]), []) if _overlap(keys, other)]
    if songs:
        hit = _history_hit(songs[-1])
        hit["songs"] = len({song_key(e.get("title")) for e in songs})
        return {"status": "downloaded", "you_have": [hit]}
    return {"status": "new", "you_have": []}


def _history_hit(entry):
    return {"where": "history", "title": entry.get("title", ""), "artist": ", ".join(entry.get("artists", [])),
            "album": entry.get("album", ""), "source": entry.get("source", ""), "when": entry.get("when")}


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
                      "imports": data.get("imports") if isinstance(data.get("imports"), dict) else {}}
        self._history_index = None
        self._library_index = (None, None)

    def _persist(self):
        self.studio._write(STATE_FILE, self.state)

    def _index(self, need_library=True):
        library = None
        if need_library:
            self.studio._require_client()
            library, _ = self.studio._library()
        if library is not None and self._library_index[0] is not library:
            self._library_index = (library, LibraryIndex(library))
        if self._history_index is None:
            self._history_index = HistoryIndex(self.state["history"])
        return (self._library_index[1] if library is not None else None), self._history_index

    def folder(self):
        return self.state["folder"] or default_folder()

    # -- reading ---------------------------------------------------------------

    def view(self):
        with self.lock:
            sources = Counter(e.get("source", "") for e in self.state["history"])
            return {"folder": self.folder(), "folder_set": bool(self.state["folder"]),
                    "queue": len([i for i in self.state["queue"] if i.get("state") == "queued"]),
                    "held": len(self.state["held"]), "history": len(self.state["history"]),
                    "history_sources": dict(sources), "imports": self.state["imports"],
                    "telegram_folder": str(telegram_folder())}

    def queue_view(self):
        with self.lock:
            return {"queue": list(reversed(self.state["queue"][-500:])), "held": list(self.state["held"])}

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

    def submit(self, items, source="manual"):
        """Queue new items; hold everything you may already have for your decision."""
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
                row = {**item, "id": secrets.token_hex(6), "added": time.time(), **result}
                if result["status"] == "new":
                    row["state"] = "queued"
                    self.state["queue"].append(row)
                    queued.append(row)
                else:
                    self.state["held"].append(row)
                    held.append(row)
            self._persist()
        return {"queued": len(queued), "held": len(held), "skipped": len(cleaned) - len(queued) - len(held)}

    @staticmethod
    def _same(item):
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
        return {"decided": len(chosen), "action": action, "missing": len(wanted) - len(chosen)}

    def remove(self, ids):
        """Take items off the queue list (never touches files or the history)."""
        wanted = set(map(str, ids or []))
        with self.lock:
            before = len(self.state["queue"])
            self.state["queue"] = [i for i in self.state["queue"] if i["id"] not in wanted]
            self._persist()
            return {"removed": before - len(self.state["queue"])}

    def record(self, entry):
        """Add a finished download to the history (used by the sources from v10 on)."""
        row = {"title": match.clean(entry.get("title")), "artists": [match.clean(a) for a in entry.get("artists") or []],
               "album": match.clean(entry.get("album")), "source": entry.get("source", "manual"),
               "when": entry.get("when") or time.time(), "path": text(entry.get("path"))}
        with self.lock:
            self.state["history"].append(row)
            self._history_index = None
            self._persist()
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
