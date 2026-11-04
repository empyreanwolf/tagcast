"""Upload (fork v72): an Mp3tag-style page for the files you are about to put into iBroadcast.

A folder is scanned into a grid (file name, title, artist, album artist, album, track, disc, year, genre, cover,
format, size, created date). You edit the tags (one file or many at once), Tagcast flags songs you may already
have (in iBroadcast, in the grid or uploaded before: same artist, same or close title, "Remix" and "Live" stay
separate), and you choose what to upload. Nothing is skipped or uploaded automatically, and **your files are never
moved or deleted**: an uploaded file stays where it is, marked as uploaded (with the date) and hidden by default.

Tags are edited in place with mutagen, touching only the fields you changed, so comments, lyrics and replay gain
survive. Undo puts the last save back. Failed uploads are listed with their reason (Retry / Remove).
State: ``uploader.json`` (folder, genre list, uploaded files, failed uploads).
"""

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path

from . import artist_match as match
from .downloads import LibraryIndex, artist_keys, song_key
from .galaxy import AUDIO_EXTS, detect_audio_format, image_mime, plain_title, sim, to_int
from .library import LibraryError
from .tagclean import ARTIST_JOIN, DEFAULT_SEPARATORS, read_tags, split_artists

log = logging.getLogger("tagcast")
STATE_FILE = "uploader.json"
DEFAULT_GENRES = ["Underground Hip Hop", "Chinese Hip Hop"]
MAX_FILES = 3000
MAX_FAILED = 500
VERSION_WORDS = ("remix", "live", "acoustic", "instrumental", "demo", "remaster", "remastered", "edit", "mix", "cover",
                 "karaoke", "sped up", "slowed", "radio", "extended", "version", "ver")
EDITABLE = ("title", "artist", "album_artist", "album", "track_no", "track_total", "disc", "year", "genre")
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class UploadError(LibraryError):
    pass


def _fields_text(values):
    return {k: ("" if v is None else str(v)) for k, v in values.items()}


def version_words(title):
    """The words that make a song another version ("Remix", "Live", …), as a set."""
    folded = " ".join(re.sub(r"[^\w]+", " ", str(title or "").casefold()).split())
    return {w for w in VERSION_WORDS if f" {w} " in f" {folded} "}


def read_disc(path, fmt):
    try:
        if fmt == "flac":
            from mutagen.flac import FLAC
            return to_int(((FLAC(str(path)).tags or {}).get("DISCNUMBER") or ["0"])[0].split("/")[0])
        if fmt == "m4a":
            from mutagen.mp4 import MP4
            disk = (MP4(str(path)).tags or {}).get("disk") or [(0, 0)]
            return to_int(disk[0][0])
        from mutagen.id3 import ID3
        tpos = ID3(str(path)).get("TPOS")
        return to_int(str(tpos.text[0]).split("/")[0]) if tpos and tpos.text else 0
    except Exception:
        return 0


def apply_tags(path, fmt, fields, cover=None, clear_cover=False):
    """Write only the given fields (title, artist, album_artist, album, track_no, track_total, disc, year, genre), and
    a cover when one is given. Everything else on the file stays as it is."""
    f = fields
    if fmt == "flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags

        def put(name, values):  # set or (when empty) remove one Vorbis comment
            if name in t:
                del t[name]
            if values:
                t[name] = values
        for key, name in {"title": "TITLE", "artist": "ARTIST", "album_artist": "ALBUMARTIST", "album": "ALBUM", "year": "DATE", "genre": "GENRE"}.items():
            if key in f:
                put(name, [str(f[key])] if str(f[key]) != "" else [])
        if "track_no" in f:
            put("TRACKNUMBER", [str(to_int(f["track_no"]))] if to_int(f["track_no"]) else [])
        if "track_total" in f:
            put("TRACKTOTAL", [str(to_int(f["track_total"]))] if to_int(f["track_total"]) else [])
        if "disc" in f:
            put("DISCNUMBER", [str(to_int(f["disc"]))] if to_int(f["disc"]) else [])
        if cover is not None or clear_cover:
            audio.clear_pictures()
            if cover and image_mime(cover):
                pic = Picture()
                pic.type, pic.mime, pic.desc, pic.data = 3, image_mime(cover), "Cover", cover
                audio.add_picture(pic)
        audio.save()
    elif fmt == "m4a":
        from mutagen.mp4 import MP4, MP4Cover
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        for key, atom in {"title": "\xa9nam", "artist": "\xa9ART", "album_artist": "aART", "album": "\xa9alb", "year": "\xa9day", "genre": "\xa9gen"}.items():
            if key in f:
                if str(f[key]) != "":
                    t[atom] = [str(f[key])]
                elif atom in t:
                    del t[atom]
        if "track_no" in f or "track_total" in f:
            old = (t.get("trkn") or [(0, 0)])[0]
            no, total = to_int(f.get("track_no", old[0])), to_int(f.get("track_total", old[1]))
            if no or total:
                t["trkn"] = [(no, total)]
            elif "trkn" in t:
                del t["trkn"]
        if "disc" in f:
            old = (t.get("disk") or [(0, 0)])[0]
            if to_int(f["disc"]):
                t["disk"] = [(to_int(f["disc"]), old[1])]
            elif "disk" in t:
                del t["disk"]
        if cover is not None or clear_cover:
            if "covr" in t:
                del t["covr"]
            if cover and image_mime(cover):
                t["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG if image_mime(cover) == "image/jpeg" else MP4Cover.FORMAT_PNG)]
        audio.save()
    else:
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        frames = {"title": ("TIT2", TIT2), "artist": ("TPE1", TPE1), "album_artist": ("TPE2", TPE2), "album": ("TALB", TALB),
                  "year": ("TDRC", TDRC), "genre": ("TCON", TCON)}
        for key, (frame_id, frame) in frames.items():
            if key in f:
                tags.delall(frame_id)
                if str(f[key]) != "":
                    tags.add(frame(encoding=3, text=[str(f[key])]))
        if "track_no" in f or "track_total" in f:
            old = str((tags.get("TRCK").text[0] if tags.get("TRCK") and tags.get("TRCK").text else "0")).split("/")
            no = to_int(f.get("track_no", old[0]))
            total = to_int(f.get("track_total", old[1] if len(old) > 1 else 0))
            tags.delall("TRCK")
            if no:
                tags.add(TRCK(encoding=3, text=[f"{no}/{total}" if total else str(no)]))
        if "disc" in f:
            old = str((tags.get("TPOS").text[0] if tags.get("TPOS") and tags.get("TPOS").text else "0")).split("/")
            tags.delall("TPOS")
            if to_int(f["disc"]):
                total = to_int(old[1]) if len(old) > 1 else 0
                tags.add(TPOS(encoding=3, text=[f"{to_int(f['disc'])}/{total}" if total else str(to_int(f["disc"]))]))
        if cover is not None or clear_cover:
            tags.delall("APIC")
            if cover and image_mime(cover):
                tags.add(APIC(encoding=3, mime=image_mime(cover), type=3, desc="Cover", data=cover))
        tags.save(str(path), v2_version=4, v1=0)


def safe_name(value):
    return " ".join(BAD_NAME.sub("", str(value or "")).split()).strip(" .")


class Uploader:
    def __init__(self, studio, put=None):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("folder", ""), ("genres", list(DEFAULT_GENRES)), ("uploaded", {}), ("failed", []), ("last_upload", 0)):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.rows, self.scanned_at, self.scanned_folder = {}, 0, ""
        self.undo_stack = []
        self.progress = None
        self._put = put  # tests: replaces the real upload call
        self._index = (None, None)
        self._artists = (None, None)

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    # -- settings and genres ------------------------------------------------------------------

    def settings(self):
        return {"folder": self.state["folder"], "genres": list(self.state["genres"])}

    def save_settings(self, body):
        body = body if isinstance(body, dict) else {}
        with self.lock:
            if "folder" in body:
                folder = str(body["folder"] or "").strip()
                if folder and not Path(folder).is_dir():
                    raise UploadError("That folder does not exist on this computer.")
                self.state["folder"] = folder
            if "genres" in body:
                genres = body["genres"]
                if not isinstance(genres, list) or not all(isinstance(g, str) for g in genres) or len(genres) > 300:
                    raise UploadError("The genre list must be a list of at most 300 names.")
                seen, clean = set(), []
                for g in genres:
                    g = " ".join(g.split())[:100]
                    if g and g.casefold() not in seen:
                        seen.add(g.casefold())
                        clean.append(g)
                self.state["genres"] = clean
        self._persist()
        return self.view()

    def library_genres(self):
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
            return [g["label"] for g in library.genre_labels()]
        except Exception:
            return []

    def ordered_genres(self, labels=None):
        """Your own list first, in your order, then every other genre iBroadcast has (A to Z)."""
        labels = self.library_genres() if labels is None else list(labels)
        mine = list(self.state["genres"])
        known = {g.casefold() for g in mine}
        return mine + sorted((g for g in labels if g.casefold() not in known), key=str.casefold)

    # -- scanning ------------------------------------------------------------------------------

    @staticmethod
    def _row_id(path):
        return hashlib.sha1(str(path).encode("utf-8", "surrogatepass")).hexdigest()[:12]

    def _row(self, path, root):
        st = path.stat()
        row = {"id": self._row_id(path), "path": str(path), "name": path.name,
               "folder": str(path.parent.relative_to(root)) if path.parent != root else "",
               "size": st.st_size, "created": getattr(st, "st_birthtime", None) or st.st_ctime, "modified": st.st_mtime,
               "format": path.suffix.lower().lstrip("."), "editable": True, "error": ""}
        try:
            fmt = detect_audio_format(path)
            tags = read_tags(path, fmt)
            artists = [a for a in tags["artists"] if str(a).strip()]
            row.update(fmt=fmt, title=str(tags["title"] or ""),
                       artist=str(artists[0]) if len(artists) == 1 else " \\ ".join(str(a) for a in artists),  # many values show as A \\ B, like Mp3tag
                       artists=[str(a) for a in artists], album_artist=str(tags["album_artist"] or ""), album=str(tags["album"] or ""),
                       track_no=tags["track_no"] or 0, track_total=tags["track_total"] or 0, disc=read_disc(path, fmt),
                       year=str(tags["year"] or "")[:10], genre=str(tags["genre"] or ""), has_cover=bool(tags["cover"]))
        except Exception as error:
            row.update(fmt="", title="", artist="", artists=[], album_artist="", album="", track_no=0, track_total=0, disc=0,
                       year="", genre="", has_cover=False, editable=False, error=f"{type(error).__name__}: {error}"[:160])
        return row

    def _mark(self, row):
        done = self.state["uploaded"].get(row["path"])
        row["uploaded"] = done["when"] if done else 0
        return row

    def scan(self, folder=None):
        folder = str(folder or self.state["folder"] or "").strip()
        if not folder:
            raise UploadError("Choose the folder with the files to upload first (Settings or the box above).")
        root = Path(folder)
        if not root.is_dir():
            raise UploadError("That folder does not exist on this computer.")
        files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower().lstrip(".") in (*AUDIO_EXTS, "ogg", "wav", "opus", "wma", "aac")][:MAX_FILES]
        rows = {}
        for p in files:
            try:
                row = self._row(p, root)
            except OSError:
                continue
            rows[row["id"]] = row
        with self.lock:
            self.rows, self.scanned_at, self.scanned_folder = rows, time.time(), str(root)
            self.undo_stack = []
            if self.state["folder"] != str(root):
                self.state["folder"] = str(root)
        self._persist()
        return self.view()

    def view(self):
        with self.lock:
            rows = [self._mark(dict(r)) for r in self.rows.values()]
            return {"settings": self.settings(), "genres": self.ordered_genres(), "rows": rows, "folder": self.scanned_folder,
                    "scanned": self.scanned_at, "failed": list(self.state["failed"]), "last_upload": self.state["last_upload"],
                    "progress": dict(self.progress) if self.progress else None, "can_undo": bool(self.undo_stack),
                    "connected": bool(getattr(self.studio, "client", None))}

    def _get(self, ids):
        out = []
        for i in ids if isinstance(ids, list) else []:
            row = self.rows.get(str(i))
            if row and Path(row["path"]).is_file():
                out.append(row)
        return out

    # -- editing ---------------------------------------------------------------------------------

    def save(self, body):
        """{"edits": [{"id", "fields": {title, artist, …}}]} → writes only those fields; the batch can be undone."""
        edits = (body or {}).get("edits")
        if not isinstance(edits, list) or not edits:
            raise UploadError("Nothing to save.")
        batch, errors = [], []
        with self.lock:
            for edit in edits[:MAX_FILES]:
                row = self.rows.get(str((edit or {}).get("id")))
                fields = {k: v for k, v in ((edit or {}).get("fields") or {}).items() if k in EDITABLE}
                if not row or not fields:
                    continue
                if not row["editable"]:
                    errors.append({"name": row["name"], "reason": "This file type can't be tagged here."})
                    continue
                before = {k: row[k] for k in fields}
                try:
                    cleaned = self._clean(fields)
                    apply_tags(Path(row["path"]), row["fmt"], cleaned)
                    batch.append({"id": row["id"], "before": before})
                    row.update(self._row_fields(cleaned, row))
                except Exception as error:
                    errors.append({"name": row["name"], "reason": f"{type(error).__name__}: {error}"[:160]})
            if batch:
                self.undo_stack = (self.undo_stack + [batch])[-10:]
        return {**self.view(), "errors": errors, "saved": len(batch)}

    @staticmethod
    def _clean(fields):
        out = {}
        for k, v in fields.items():
            if k in ("track_no", "track_total", "disc"):
                out[k] = max(0, min(9999, to_int(v)))
            else:
                out[k] = match.clean(v) if k != "year" else re.sub(r"[^\d\-]", "", str(v))[:10]
        return out

    @staticmethod
    def _row_fields(cleaned, row):
        out = dict(cleaned)
        if "artist" in cleaned:
            out["artists"] = [cleaned["artist"]] if cleaned["artist"] else []
        return out

    def undo(self, body=None):
        with self.lock:
            if not self.undo_stack:
                raise UploadError("Nothing to undo.")
            batch = self.undo_stack.pop()
            errors = []
            for item in batch:
                row = self.rows.get(item["id"])
                if not row:
                    continue
                try:
                    apply_tags(Path(row["path"]), row["fmt"], self._clean(item["before"]))
                    row.update(self._row_fields(self._clean(item["before"]), row))
                except Exception as error:
                    errors.append({"name": row["name"], "reason": str(error)[:160]})
        return {**self.view(), "errors": errors}

    def copy_cover(self, body):
        """Put the cover of one file on the other files (Mp3tag: select, copy cover)."""
        body = body or {}
        source = self.rows.get(str(body.get("from")))
        if not source or not source["editable"]:
            raise UploadError("Choose a file with a cover to copy from.")
        cover = read_tags(Path(source["path"]), source["fmt"])["cover"]
        if not cover:
            raise UploadError("That file has no cover.")
        done = 0
        with self.lock:
            for row in self._get(body.get("ids")):
                if row["id"] == source["id"] or not row["editable"]:
                    continue
                apply_tags(Path(row["path"]), row["fmt"], {}, cover=cover)
                row["has_cover"] = True
                done += 1
        return {**self.view(), "saved": done}

    def rename(self, body):
        """Tag → file name: ``pattern`` like "%track% %artist% - %title%" (%track% %disc% %artist% %album_artist% %album% %title% %year% %genre%)."""
        body = body or {}
        pattern = str(body.get("pattern") or "").strip()
        if not pattern or "%" not in pattern:
            raise UploadError("Write a pattern such as %artist% - %title%.")
        renamed, errors = 0, []
        with self.lock:
            for row in self._get(body.get("ids")):
                values = {k: (f"{row[k]:02d}" if k == "track_no" and row[k] else str(row[k] or "")) for k in
                          ("title", "artist", "album_artist", "album", "year", "genre", "track_no", "disc")}
                values["track"] = values.pop("track_no")
                name = re.sub(r"%(\w+)%", lambda m: values.get(m.group(1), ""), pattern)
                name = safe_name(name)
                if not name:
                    errors.append({"name": row["name"], "reason": "The pattern gave an empty name."})
                    continue
                path = Path(row["path"])
                target = path.with_name(name + path.suffix)
                if target == path:
                    continue
                if target.exists():
                    errors.append({"name": row["name"], "reason": f"{target.name} already exists."})
                    continue
                try:
                    path.rename(target)
                except OSError as error:
                    errors.append({"name": row["name"], "reason": str(error)[:160]})
                    continue
                old_id = row["id"]
                moved = self.state["uploaded"].pop(str(path), None)
                if moved:
                    self.state["uploaded"][str(target)] = moved
                row.update(path=str(target), name=target.name, id=self._row_id(target))
                self.rows[row["id"]] = self.rows.pop(old_id)
                renamed += 1
        self._persist()
        return {**self.view(), "renamed": renamed, "errors": errors}

    def split(self, body):
        """Proposals only (nothing is written): a combined artist field split on your separators (Clean up > separators), joined
        "A; B", the first artist as album artist. The page puts them in the grid as pending edits."""
        separators = getattr(getattr(self.studio, "tagclean", None), "state", {}).get("separators") or list(DEFAULT_SEPARATORS)
        out = {}
        for row in self._get((body or {}).get("ids")):
            names = split_artists(row["artists"] or ([row["artist"]] if row["artist"] else []), separators)
            if not names:
                continue
            joined = ARTIST_JOIN.join(names)
            fields = {k: v for k, v in (("artist", joined), ("album_artist", names[0])) if row[k] != v}
            if fields:
                out[row["id"]] = fields
        return {"edits": out}

    # -- duplicates and artist names -----------------------------------------------------------------

    def _library_index(self):
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return None, None
        if self._index[0] is not library:
            index = LibraryIndex(library)
            by_artist = {}
            for hits in index.songs.values():
                for hit in hits:
                    for key in hit["keys"]:
                        by_artist.setdefault(key, []).append(hit)
            self._index = (library, (index, by_artist))
        return library, self._index[1]

    @staticmethod
    def _same_artist(keys_a, name_a, keys_b, name_b):
        if not keys_a or not keys_b:
            return not name_a or not name_b
        return bool(keys_a & keys_b) or sim(name_a, name_b) >= 0.9

    @staticmethod
    def _title_match(a, b):
        """"same" for the same title, "close" for a slightly different one; "" when it is another song or version."""
        if version_words(a) != version_words(b):
            return ""
        if song_key(a) == song_key(b):
            return "same"
        return "close" if sim(plain_title(a), plain_title(b)) >= 0.86 else ""

    def duplicates(self, body):
        """For each chosen file: songs you may already have. Never decides anything; the page asks you."""
        _, indexed = self._library_index()
        by_artist = indexed[1] if indexed else {}
        rows = self._get((body or {}).get("ids"))
        everything = [r for r in self.rows.values() if r.get("title")]
        uploaded = [{"title": u.get("title", ""), "artist": u.get("artist", ""), "path": p} for p, u in self.state["uploaded"].items()]
        found = {}
        for row in rows:
            names = [a for a in re.split(r"\s*;\s*|\s*\\\s*", row["artist"] or "") if a] or ([row["album_artist"]] if row["album_artist"] else [])
            keys = artist_keys(*names) if names else set()
            hits, seen = [], set()

            def add(where, other, how, **extra):
                tag = (where, other.get("title"), other.get("artist"))
                if tag not in seen and len(hits) < 6:
                    seen.add(tag)
                    hits.append({"where": where, "title": other.get("title", ""), "artist": other.get("artist", ""), "how": how, **extra})
            for key in keys or [None]:
                for hit in (by_artist.get(key, []) if key else []):
                    how = self._title_match(row["title"], hit["title"])
                    if how:
                        add("ibroadcast", hit, how, album=hit["album"])
            for other in everything:
                if other["id"] == row["id"] or other.get("uploaded"):
                    continue
                other_names = [a for a in re.split(r"\s*;\s*|\s*\\\s*", other["artist"] or "") if a]
                if self._same_artist(keys, row["artist"], artist_keys(*other_names) if other_names else set(), other["artist"]):
                    how = self._title_match(row["title"], other["title"])
                    if how:
                        add("folder", other, how, name=other["name"])
            for other in uploaded:
                if other["path"] == row["path"]:
                    continue
                other_names = [a for a in re.split(r"\s*;\s*|\s*\\\s*", other["artist"] or "") if a]
                if self._same_artist(keys, row["artist"], artist_keys(*other_names) if other_names else set(), other["artist"]):
                    how = self._title_match(row["title"], other["title"])
                    if how:
                        add("uploaded", other, how)
            if hits:
                found[row["id"]] = hits
        return {"duplicates": found, "library": indexed is not None}

    def _library_artists(self):
        library, indexed = self._library_index()
        if library is None:
            return {}
        if self._artists[0] is not library:
            names = {}
            for album_id in library.album_ids():
                for artist_id in library.album_artist_ids(library.albums[album_id]):
                    name = library.artist_name(artist_id)
                    if name:
                        names[name] = names.get(name, 0) + 1
            self._artists = (library, names)
        return self._artists[1]

    def artist_suggestions(self, body):
        """Artist names iBroadcast already has that look like these ("pact" for "PACT派克特"); [] when it is already spelled the same."""
        names = self._library_artists()
        out = {}
        for wanted in [str(n) for n in (body or {}).get("names") or []][:200]:
            for part in [p for p in re.split(r"\s*;\s*", wanted) if p]:
                if part in out:
                    continue
                key, keys = match.match_key(part), artist_keys(part)
                if any(match.match_key(n) == key for n in names):
                    out[part] = {"known": True, "suggest": []}
                    continue
                close = [(n, c) for n, c in names.items() if (keys & artist_keys(n)) or sim(part, n) >= 0.88]
                out[part] = {"known": False, "suggest": [{"name": n, "albums": c} for n, c in sorted(close, key=lambda x: -x[1])[:3]]}
        return {"artists": out, "library": bool(names)}

    # -- uploading -----------------------------------------------------------------------------------

    def upload(self, body):
        ids = (body or {}).get("ids")
        paths = [r["path"] for r in self._get(ids)]
        extra = [str(p) for p in (body or {}).get("paths") or [] if Path(str(p)).is_file()]
        return self._start([*paths, *[p for p in extra if p not in paths]])

    def retry_failed(self, body):
        wanted = (body or {}).get("paths")
        failed = [f["path"] for f in self.state["failed"] if wanted is None or f["path"] in (wanted or [])]
        return self._start([p for p in failed if Path(p).is_file()])

    def remove_failed(self, body):
        wanted = set((body or {}).get("paths") or [])
        with self.lock:
            self.state["failed"] = [f for f in self.state["failed"] if f["path"] not in wanted]
        self._persist()
        return self.view()

    def _start(self, paths):
        if not paths:
            raise UploadError("Choose at least one file that still exists.")
        if not self._put:
            self.studio._require_client()
        with self.lock:
            if self.progress and self.progress.get("running"):
                raise UploadError("An upload is already running.")
            self.progress = {"running": True, "total": len(paths), "done": 0, "ok": 0, "failed": 0, "name": ""}
        threading.Thread(target=self._run, args=(paths,), daemon=True, name="tagcast-upload").start()
        return self.view()

    def _send(self, path):
        if self._put:
            return self._put(path)
        client = self.studio._require_client()
        _, user_id = self.studio._library()
        return client.upload_track(path, user_id)

    def _run(self, paths):
        for path in paths:
            p = Path(path)
            with self.lock:
                self.progress["name"] = p.name
            try:
                self._send(p)
                row = next((r for r in self.rows.values() if r["path"] == path), None)
                with self.lock:
                    self.state["uploaded"][path] = {"when": time.time(), "size": p.stat().st_size if p.exists() else 0,
                                                    "title": (row or {}).get("title", ""), "artist": (row or {}).get("artist", "")}
                    self.state["failed"] = [f for f in self.state["failed"] if f["path"] != path]
                    self.progress["ok"] += 1
            except Exception as error:
                log.info("Upload failed for %s: %s", p, error)
                reason = str(error) or type(error).__name__
                with self.lock:
                    self.state["failed"] = [f for f in self.state["failed"] if f["path"] != path]
                    self.state["failed"].insert(0, {"path": path, "name": p.name, "reason": reason[:300], "when": time.time()})
                    del self.state["failed"][MAX_FAILED:]
                    self.progress["failed"] += 1
            with self.lock:
                self.progress["done"] += 1
            self._persist()
        with self.lock:
            self.state["last_upload"] = time.time()
            self.progress["running"] = False
        self._persist()

    def reveal(self, body):
        path = Path(str((body or {}).get("path") or ""))
        if not path.exists():
            raise UploadError("That file is no longer there.")
        if os.name != "nt":
            raise UploadError("Showing a file in its folder works on Windows only.")
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607
        return {"ok": True}
