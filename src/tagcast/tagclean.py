"""Tag clean-up before upload (fork v19).

Mirrors the fields cleaned by hand in Mp3tag before an upload: a combined artist
field ("Artist A feat. Artist B", "Artist A & Artist B", ...) is split on your own
separators into a clean list joined by "; ", with the first one set as album
artist. For hip-hop and rap, Tagcast also offers to collapse the genre into
whichever of your own iBroadcast genres already covers "underground" and
"chinese" hip-hop -- never a genre you don't already have -- deciding Chinese
artists from MusicBrainz's artist record (country/area), since that is a steadier
signal than guessing from the genre text or the title alone.

Works on a folder of files before they are uploaded (tags are rewritten in
place, preserving everything else on the file: title, album, track number,
year, cover, MusicBrainz IDs), or on tracks already in your iBroadcast library
(only the genre field there, since the library's artist/featured-artist
structure is already handled by Artist cleanup).

When a track can't be placed with confidence -- the artist isn't on
MusicBrainz, or several same-named artists came back -- it is grouped by
artist instead of guessed, so you can pick one of your existing genres once
and apply it to every pending row by that artist at once, the same
"select all, set genre" you'd do in Mp3tag. State: ``tagclean.json``.
"""

import logging
import re
import secrets
import threading
import time
from pathlib import Path

from . import activity
from . import artist_match as match
from .galaxy import AUDIO_EXTS, detect_audio_format, to_int, write_tags
from .library import LibraryError, number, text
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
STATE_FILE = "tagclean.json"
MB = "https://musicbrainz.org/ws/2"
CACHE_DAYS = 30
MAX_ROWS = 5000
HIPHOP_WORDS = ("hip hop", "hip-hop", "rap", "trap", "grime")
CHINESE_CODES = ("CN", "TW", "HK", "MO")
CHINESE_WORDS = ("china", "hong kong", "taiwan", "macau", "macao")
DEFAULT_SEPARATORS = ["feat.", "feat", "ft.", "ft", "featuring", "&", ",", "/", "//", "\\"]
# Written like Mp3tag users type it (fork v41): ONE artist field, artists joined by "; ".
# Never a multi-value field, which Mp3tag shows as "\\" and older players show as "/" or "//".
ARTIST_JOIN = "; "
MAX_GENRE = 100


def _is_hiphop(genre):
    folded = (genre or "").casefold()
    return any(w in folded for w in HIPHOP_WORDS)


AFRICA = ("ZA", "NG", "GH", "KE", "TZ", "UG", "ZW", "SN", "CI", "CM", "ET", "EG", "MA", "DZ", "TN", "AO", "ZM", "BW", "NA", "RW")
ENGLISH_COUNTRIES = ("US", "GB", "CA", "AU", "NZ", "IE")
# MusicBrainz country -> your hip-hop genres, most specific first; the first one you actually have wins.
HIPHOP_BY_COUNTRY = {"CN": ("Chinese Hip Hop",), "MO": ("Chinese Hip Hop",), "HK": ("Hong Kong Hip Hop", "Chinese Hip Hop"),
                     "TW": ("Taiwan Hip Hop", "Chinese Hip Hop"), "SG": ("Singapore Hip Hop",), "MY": ("Malaysian Hip Hop",),
                     "JP": ("Japanese Hip Hop",), "KR": ("Korean Hip Hop",), "AU": ("Australian Hip Hop",), "GB": ("UK Rap",),
                     **{code: ("African Hip Hop",) for code in AFRICA}}
TAG_GENRES = (  # (words in a MusicBrainz tag or your file's genre, your genres to try)
    (("k-pop", "kpop", "korean pop"), ("K-Pop",)),
    (("electronic", "electronica", "house", "techno", "edm", "synth", "ambient", "dance"), ("Electronica",)),
    (("soundtrack", "score", "original score", "ost", "film music", "video game"), ("OST", "Game")),
    (("christmas",), ("Christmas",)),
    (("children", "kids", "nursery"), ("Children",)),
    (("cantopop", "cantonese pop"), ("Cantopop",)),
    (("mandopop", "c-pop", "chinese pop", "mandarin"), ("Chinese Song",)),
    (("oldies", "golden"), ("Chinese Oldies", "Oldies")),
)


def genre_settled(genres, labels):
    """Exactly one value and it is one of your genres: never touched again."""
    values = [text(g).strip() for g in genres if text(g).strip()]
    return len(values) == 1 and values[0].casefold() in {l.casefold() for l in labels}


def _have(labels, *names):
    folded = {l.casefold(): l for l in labels}
    return next((folded[n.casefold()] for n in names if n.casefold() in folded), None)


def candidate_labels(info, genres, labels, limit=4):
    """Your genres that fit this artist, best first (for the buttons)."""
    info = info or {}
    out = []

    def add(label):
        if label and label not in out:
            out.append(label)
    tags = list(info.get("tags") or []) + [text(g).casefold() for g in genres or []]
    hiphop = any(w in tag for tag in tags for w in HIPHOP_WORDS)
    country = info.get("country") or ""
    if hiphop:
        if any("instrumental" in tag for tag in tags):
            add(_have(labels, "Instrumental Hip Hop"))
        add(_have(labels, *HIPHOP_BY_COUNTRY.get(country, ())))
        if country not in HIPHOP_BY_COUNTRY:
            add(_have(labels, "Underground Hip Hop"))
        add(_have(labels, "English Hip Hop", "Underground Hip Hop"))
    for words, names in TAG_GENRES:
        if any(w in tag for tag in tags for w in words):
            add(_have(labels, *names))
    # A country alone never decides a non-hip-hop song: there must be some tag or genre text to go on.
    if country in ("CN", "TW", "MO") and not hiphop and tags:
        add(_have(labels, "Chinese Song"))
    if country == "HK" and not hiphop and tags:
        add(_have(labels, "Cantopop", "Chinese Song"))
    if country in ENGLISH_COUNTRIES and not hiphop and tags:
        add(_have(labels, "English Song"))
    return out[:limit]


def pick_label(info, genres, labels):
    """-> (one of your genres or None, confident). Confident means MusicBrainz knows the artist and
    the country/tags point at exactly one of your genres; hip-hop with no better answer is
    "Underground Hip Hop" (your default)."""
    if not info or not info.get("found"):
        return (candidate_labels(info, genres, labels) or [None])[0], False
    picks = candidate_labels(info, genres, labels, limit=10)
    if not picks:
        return None, False
    tags = list(info.get("tags") or []) + [text(g).casefold() for g in genres or []]
    hiphop = any(w in tag for tag in tags for w in HIPHOP_WORDS)
    if hiphop:
        return picks[0], True
    # Not hip-hop: only a clear country or tag rule is trusted; two rules disagreeing is a question.
    return picks[0], len(picks) == 1


def pick_genre(labels, *words):
    """The first of your existing genres whose name contains all of ``words``."""
    for label in labels:
        folded = label.casefold()
        if all(w in folded for w in words):
            return label
    return None


def split_artists(raw, separators):
    """Raw artist value(s) from a tag -> a clean, ordered, de-duplicated list."""
    wanted = sorted({s for s in (separators or []) if s} | {";"}, key=len, reverse=True)
    pattern = "|".join(re.escape(s) for s in wanted)
    out, seen = [], set()
    for value in raw or []:
        value = text(value)
        if not value:
            continue
        pieces = re.split(rf"\s*(?:{pattern})\s*", value, flags=re.IGNORECASE) if pattern else [value]
        for piece in pieces:
            piece = piece.strip(" ;")
            if piece and piece.casefold() not in seen:
                seen.add(piece.casefold())
                out.append(piece)
    return out


def read_tags(path, fmt):
    """The fields a clean-up needs, read as they are now (everything else is preserved on write)."""
    if fmt == "flac":
        from mutagen.flac import FLAC
        audio = FLAC(str(path))
        t = audio.tags or {}
        get = lambda k: list(t.get(k, []))
        return {"title": (get("TITLE") or [""])[0], "artists": get("ARTIST"),
                "album_artist": (get("ALBUMARTIST") or [""])[0], "album": (get("ALBUM") or [""])[0],
                "track_no": to_int((get("TRACKNUMBER") or ["0"])[0]),
                "track_total": to_int((get("TRACKTOTAL") or get("TOTALTRACKS") or ["0"])[0]),
                "year": (get("DATE") or [""])[0], "genre": (get("GENRE") or [""])[0], "genres": get("GENRE"),
                "cover": audio.pictures[0].data if audio.pictures else None,
                "mb_track_id": (get("MUSICBRAINZ_TRACKID") or [""])[0],
                "mb_release_id": (get("MUSICBRAINZ_ALBUMID") or [""])[0]}
    if fmt == "m4a":
        from mutagen.mp4 import MP4
        audio = MP4(str(path))
        t = audio.tags or {}
        trkn = t.get("trkn") or [(0, 0)]
        return {"title": str((t.get("\xa9nam") or [""])[0]), "artists": [str(a) for a in (t.get("\xa9ART") or [])],
                "album_artist": str((t.get("aART") or [""])[0]), "album": str((t.get("\xa9alb") or [""])[0]),
                "track_no": to_int(trkn[0][0]), "track_total": to_int(trkn[0][1]),
                "year": str((t.get("\xa9day") or [""])[0]), "genre": str((t.get("\xa9gen") or [""])[0]),
                "genres": [str(g) for g in (t.get("\xa9gen") or [])],
                "cover": bytes(t["covr"][0]) if t.get("covr") else None, "mb_track_id": "", "mb_release_id": ""}
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    audio = MP3(str(path))
    t = audio.tags if audio.tags is not None else ID3()
    frame = lambda fid: list(t[fid].text) if fid in t else []
    bits = str((frame("TRCK") or ["0"])[0]).split("/")
    apic = t.getall("APIC") if hasattr(t, "getall") else []
    return {"title": (frame("TIT2") or [""])[0], "artists": frame("TPE1"),
            "album_artist": (frame("TPE2") or [""])[0], "album": (frame("TALB") or [""])[0],
            "track_no": to_int(bits[0]) if bits and bits[0] else 0,
            "track_total": to_int(bits[1]) if len(bits) > 1 else 0,
            "year": (frame("TDRC") or [""])[0], "genre": (frame("TCON") or [""])[0], "genres": [str(g) for g in frame("TCON")],
            "cover": apic[0].data if apic else None, "mb_track_id": "", "mb_release_id": ""}


class TagClean:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("separators", list(DEFAULT_SEPARATORS)), ("mb_cache", {}), ("rows", []), ("scanned", 0)):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.progress = None
        self.tidy_queue, self.tidy_thread = [], None

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    # -- settings --------------------------------------------------------------------------

    def save_settings(self, body):
        seps = (body or {}).get("separators")
        if not isinstance(seps, list) or not all(isinstance(s, str) for s in seps) or len(seps) > 30:
            raise LibraryError("Separators must be a list of at most 30 short bits of text.")
        cleaned = [s.strip() for s in seps if s.strip()][:30]
        with self.lock:
            self.state["separators"] = cleaned or list(DEFAULT_SEPARATORS)
        self._persist()
        return self.view()

    def genres(self):
        """Your own iBroadcast genres, the only targets a genre fix can land on."""
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return []
        return [g["label"] for g in library.genre_labels()]

    def view(self):
        with self.lock:
            rows = list(self.state["rows"])
            grouped = {}
            for r in rows:
                if r.get("needs_genre"):
                    grouped.setdefault(r["artist"], []).append(r)
        # One short line per artist (v46): the name, how many songs, MusicBrainz's genres. No song lists.
        labels = self.genres()
        needs_decision = []
        for artist, rs in sorted(grouped.items(), key=lambda kv: match.match_key(kv[0])):
            with self.lock:
                info = (self.state["mb_cache"].get(match.match_key(artist)) or {}).get("info")
            current = [g for r in rs for g in [r.get("current_genre")] if g][:1]
            needs_decision.append({"artist": artist, "count": len(rs), "ids": [r["id"] for r in rs],
                                   "suggestions": candidate_labels(info, current, labels),
                                   "tags": (info or {}).get("tags", [])[:4], "current": current[0] if current else ""})
        return {"artist_ready": [r for r in rows if r.get("new_artists")],
                "genre_ready": [r for r in rows if r.get("new_genre")],
                "needs_decision": needs_decision, "genres": labels,
                "separators": self.state["separators"], "scanned": self.state["scanned"], "progress": self.progress}

    # -- scanning ----------------------------------------------------------------------------

    def scan(self, scope, path=""):
        if scope not in ("folder", "library"):
            raise LibraryError("Choose a folder or your iBroadcast library to scan.")
        if scope == "folder" and not Path(text(path) or "").is_dir():
            raise LibraryError("That folder doesn't exist.")
        with self.lock:
            if self.progress:
                return self.view()
            self.progress = {"scope": scope, "done": 0, "total": 0, "title": ""}
        threading.Thread(target=self._run, args=(scope, path), name="tagclean-scan", daemon=True).start()
        return self.view()

    def _run(self, scope, path):
        try:
            labels = self.genres()
            rows = self._scan_folder(path, labels) if scope == "folder" else self._scan_library(labels)
            with self.lock:
                self.state.update(rows=rows, scanned=time.time())
        finally:
            with self.lock:
                self.progress = None
            self._persist()

    def _scan_folder(self, path, labels):
        separators = self.state["separators"]
        files = [p for p in sorted(Path(path).rglob("*")) if p.suffix.lower().lstrip(".") in AUDIO_EXTS][:MAX_ROWS]
        self.progress["total"] = len(files)
        rows = []
        for p in files:
            self.progress["title"] = p.name
            try:
                tags = read_tags(p, detect_audio_format(p))
                row = self._build_folder_row(str(p), tags, separators, labels)
                if row:
                    rows.append(row)
            except Exception as error:
                log.info("Tag clean-up skipped %s: %s", p, error)
            self.progress["done"] += 1
        return rows

    def _scan_library(self, labels):
        self.studio._require_client()
        library, _ = self.studio._library()
        ids = list(library.tracks)
        self.progress["total"] = len(ids)
        rows = []
        for track_id in ids:
            self.progress["done"] += 1
            if len(rows) >= MAX_ROWS:
                break
            track = library.tracks[track_id]
            genre = text(track.get("genre"))
            if genre_settled([genre] if genre.strip() else [], labels):
                continue  # already one of your genres: never looked at again
            artist = library.artist_name(number(track.get("artist_id")))
            if not artist:
                continue
            self.progress["title"] = artist
            new_genre, needs_genre = self._resolve_genre(artist, [genre] if genre.strip() else [], labels)
            if not (new_genre or needs_genre):
                continue
            rows.append({"id": secrets.token_hex(6), "scope": "library", "ref": str(track_id),
                         "album_id": str(number(track.get("album_id"))), "title": text(track.get("title")),
                         "artist": artist, "current_artists": [], "current_album_artist": "",
                         "new_artists": None, "new_album_artist": None,
                         "current_genre": genre, "new_genre": new_genre, "needs_genre": needs_genre})
        return rows

    def _build_folder_row(self, path, tags, separators, labels):
        current = [a.strip() for a in tags["artists"] if text(a).strip()]
        new_artists = split_artists(tags["artists"], separators)
        joined = ARTIST_JOIN.join(new_artists)
        # A multi-value field ("A\\B" in Mp3tag) or anything not exactly "A; B" is rewritten.
        changed = bool(new_artists) and (len(current) != 1 or current[0] != joined
                                         or tags["album_artist"].strip() != new_artists[0])
        artist = (new_artists[0] if new_artists else "") or tags["album_artist"].strip() or (current[0] if current else "")
        genres = tags.get("genres") or ([tags["genre"]] if tags.get("genre") else [])
        new_genre, needs_genre = (None, False) if genre_settled(genres, labels) else self._resolve_genre(artist, genres, labels)
        if not (changed or new_genre or needs_genre):
            return None
        return {"id": secrets.token_hex(6), "scope": "folder", "ref": path, "album_id": "", "title": tags["title"],
                "artist": artist, "current_artists": current, "current_album_artist": tags["album_artist"],
                "new_artists": new_artists if changed else None, "new_album_artist": new_artists[0] if changed else None,
                "current_genre": " \\ ".join(genres) if len(genres) > 1 else tags["genre"],
                "new_genre": new_genre, "needs_genre": needs_genre}

    # -- deciding a genre (v46) ----------------------------------------------------------------

    def _resolve_genre(self, artist, genres, labels):
        """-> (one of YOUR genres to write, or None; needs a manual decision).

        Anything that is not already exactly one of your genres is placed from MusicBrainz's
        record of the artist (country and tags). When it can't be placed with confidence the
        artist is asked about, with MusicBrainz's own genres beside the buttons."""
        if not artist or not labels:
            return None, bool(artist)
        info = self._mb_info(artist)
        label, confident = pick_label(info, genres, labels)
        if label and confident:
            return label, False
        return None, True

    def suggestions(self, artist, genres=()):
        """(labels worth offering, MusicBrainz's own genre names) for one artist; no network."""
        key = match.match_key(artist)
        with self.lock:
            hit = (self.state["mb_cache"].get(key) or {}).get("info")
        labels = self.genres()
        picks = candidate_labels(hit, genres, labels)
        return picks, [t for t in (hit or {}).get("tags", [])][:4]

    def _mb_info(self, artist):
        """MusicBrainz's record of the artist: {"found", "country", "area", "tags"} (cached 30 days)."""
        key = match.match_key(artist)
        with self.lock:
            hit = self.state["mb_cache"].get(key)
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400 and isinstance(hit.get("info"), dict):
            return hit["info"]
        info, rows = {"found": False, "country": "", "area": "", "tags": []}, []
        try:
            rows = (self.http.json(f"{MB}/artist", {"query": f'artist:"{artist}"', "fmt": "json", "limit": 5}) or {}).get("artists") or []
        except SourceError as error:
            log.info("MusicBrainz artist lookup for %s failed: %s", artist, error)
            return info  # not cached: try again next time
        exact = [a for a in rows if match.match_key(a.get("name")) == key]
        candidates = exact if len(exact) == 1 else (rows if len(rows) == 1 else [])
        if candidates:
            a = candidates[0]
            tags = sorted((a.get("tags") or []), key=lambda t: -int(t.get("count") or 0))
            info = {"found": True, "country": text(a.get("country")), "area": text((a.get("area") or {}).get("name")),
                    "tags": [text(t.get("name")).casefold() for t in tags if text(t.get("name"))][:8]}
        with self.lock:
            self.state["mb_cache"][key] = {"info": info, "when": time.time()}
        return info

    # -- right after a download (v46) --------------------------------------------------------------

    def tidy_downloaded(self, paths):
        """Every file a source just saved: artist "A; B", album artist = the first one, and a genre
        from your list when MusicBrainz places the artist. Runs in the background, one file at a time,
        so the MusicBrainz pacing never slows a download. What can't be placed lands in Clean up."""
        with self.lock:
            self.tidy_queue.extend(str(p) for p in paths if p)
            if self.tidy_thread and self.tidy_thread.is_alive():
                return
            self.tidy_thread = threading.Thread(target=self._tidy_loop, name="tagclean-downloads", daemon=True)
            self.tidy_thread.start()

    def _tidy_loop(self):
        while True:
            with self.lock:
                if not self.tidy_queue:
                    self.tidy_thread = None
                    return
                path = self.tidy_queue.pop(0)
            try:
                self.tidy_file(path)
            except Exception:
                log.exception("Tagging the download %s failed", path)

    def tidy_file(self, path):
        p = Path(path)
        if not p.is_file():
            return None
        fmt = detect_audio_format(p)
        tags = read_tags(p, fmt)
        labels = self.genres()
        row = self._build_folder_row(str(p), tags, self.state["separators"], labels)
        if not row:
            return None
        self._write_folder_tags(row, artists=row["new_artists"], album_artist=row["new_album_artist"], genre=row["new_genre"])
        if row["needs_genre"]:
            row["new_artists"] = row["new_album_artist"] = row["new_genre"] = None
            with self.lock:
                self.state["rows"] = [r for r in self.state["rows"] if r.get("ref") != row["ref"]] + [row]
            self._persist()
        return row

    # -- applying ------------------------------------------------------------------------------

    def _write_folder_tags(self, row, artists=None, album_artist=None, genre=None):
        path = Path(row["ref"])
        fmt = detect_audio_format(path)
        current = read_tags(path, fmt)
        if artists is not None:
            artists = [ARTIST_JOIN.join(artists)] if artists else []
        write_tags(path, fmt, title=current["title"], artists=artists if artists is not None else current["artists"],
                   album_artist=album_artist if album_artist is not None else current["album_artist"],
                   album=current["album"], track_no=current["track_no"], track_total=current["track_total"],
                   year=current["year"], genre=genre if genre is not None else current["genre"],
                   cover=current["cover"], mb_track_id=current["mb_track_id"], mb_release_id=current["mb_release_id"])

    def _mark_done(self, row_id, fields):
        with self.lock:
            rows = self.state["rows"]
            for i, r in enumerate(rows):
                if r["id"] == row_id:
                    for field in fields:
                        r[field] = False if field == "needs_genre" else None
                    if not (r.get("new_artists") or r.get("new_genre") or r.get("needs_genre")):
                        rows.pop(i)
                    break
        self._persist()

    @staticmethod
    def _report(what, rows, results):
        """One Activity log entry per file or track this clean-up wrote, or failed to (v35)."""
        by_id = {r["id"]: r for r in rows}
        for result in results:
            row = by_id.get(result["id"], {})
            name = row.get("title") or Path(str(row.get("ref", ""))).name or result["id"]
            change = (f"artists → {'; '.join(row.get('new_artists') or [])}" if what == "artists"
                      else f"genre → {row.get('new_genre') or ''}")
            where = "file" if row.get("scope") == "folder" else "iBroadcast"
            if result["status"] == "saved":
                activity.record("metadata", "ok", f"Tag clean-up ({where}): “{name}” {change}", str(row.get("ref", "")),
                                source="tagclean")
            else:
                activity.record("metadata", "error", f"Tag clean-up ({where}) failed: “{name}” {change}",
                                f"{row.get('ref', '')}\n{result.get('error', '')}", source="tagclean")

    def apply_artists(self, ids):
        wanted = set(map(str, ids or []))
        with self.lock:
            rows = [r for r in self.state["rows"] if r["id"] in wanted and r.get("new_artists")]
        results = []
        for row in rows:
            try:
                self._write_folder_tags(row, artists=row["new_artists"], album_artist=row["new_album_artist"])
                self._mark_done(row["id"], ["new_artists", "new_album_artist"])
                results.append({"id": row["id"], "status": "saved"})
            except Exception as error:
                log.info("Tag clean-up could not save artists for %s: %s", row["ref"], error)
                results.append({"id": row["id"], "status": "failed", "error": str(error)})
        self._report("artists", rows, results)
        return {"results": results, **self.view()}

    def apply_genre(self, ids):
        wanted = set(map(str, ids or []))
        with self.lock:
            rows = [r for r in self.state["rows"] if r["id"] in wanted and r.get("new_genre")]
        results = []
        for row in [r for r in rows if r["scope"] == "folder"]:
            try:
                self._write_folder_tags(row, genre=row["new_genre"])
                self._mark_done(row["id"], ["new_genre", "needs_genre"])
                results.append({"id": row["id"], "status": "saved"})
            except Exception as error:
                log.info("Tag clean-up could not save genre for %s: %s", row["ref"], error)
                results.append({"id": row["id"], "status": "failed", "error": str(error)})
        library_rows = [r for r in rows if r["scope"] == "library"]
        if library_rows:
            changes = [{"kind": "track", "id": r["ref"], "albumId": r["album_id"] or None, "label": r["title"],
                        "fields": {"genre": {"before": r["current_genre"], "after": r["new_genre"]}}}
                       for r in library_rows]
            try:
                saved = self.studio.save(changes)
                sent = {s["id"] for s in saved.get("results", []) if s.get("status") not in ("failed", "blocked", "not_sent")}
            except LibraryError as error:
                for r in library_rows:
                    results.append({"id": r["id"], "status": "failed", "error": str(error)})
                self._report("genre", rows, results)
                return {"results": results, **self.view()}
            for r in library_rows:
                status = "saved" if r["ref"] in sent else "failed"
                results.append({"id": r["id"], "status": status})
                if status == "saved":
                    self._mark_done(r["id"], ["new_genre", "needs_genre"])
        self._report("genre", rows, results)
        return {"results": results, **self.view()}

    def assign_genre(self, ids, genre):
        genre = " ".join(text(genre).split())
        if not genre or len(genre) > MAX_GENRE:
            raise LibraryError("Type or choose a genre (at most 100 characters).")
        # An existing iBroadcast genre keeps its exact spelling; anything else is used as typed.
        allowed = {g.casefold(): g for g in self.genres()}
        allowed.setdefault(genre.casefold(), genre)
        wanted = set(map(str, ids or []))
        with self.lock:
            for r in self.state["rows"]:
                if r["id"] in wanted and r.get("needs_genre"):
                    r["new_genre"], r["needs_genre"] = allowed[genre.casefold()], False
        self._persist()
        return self.apply_genre(ids)
