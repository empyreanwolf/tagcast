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
DISCOGS = "https://api.discogs.com"
ITUNES = "https://itunes.apple.com"
QQ = "https://c.y.qq.com"
# v70: where a genre decision looks, in order; the first source that is sure decides (QQ only for Chinese names).
GENRE_SOURCES = (("discogs", "Discogs"), ("apple", "Apple"), ("qq", "QQ Music"), ("musicbrainz", "MusicBrainz"))
RETRY_AFTER = 10 * 60  # a source that didn't answer is asked again after this long
DISCOGS_COUNTRIES = {"US": "US", "UK": "GB", "Canada": "CA", "Australia": "AU", "New Zealand": "NZ", "Ireland": "IE",
                     "China": "CN", "Taiwan": "TW", "Hong Kong": "HK", "Macau": "MO", "Japan": "JP", "South Korea": "KR",
                     "Korea": "KR", "Singapore": "SG", "Malaysia": "MY", "South Africa": "ZA", "Nigeria": "NG",
                     "Ghana": "GH", "Kenya": "KE", "Germany": "DE", "France": "FR", "Netherlands": "NL", "Sweden": "SE"}
# QQ Music's 流派 (genre) as words the rules below understand.
QQ_GENRES = {"说唱": "hip hop", "嘻哈": "hip hop", "rap": "hip hop", "hip hop": "hip hop", "流行": "pop", "电子": "electronic",
             "舞曲": "dance", "影视原声": "soundtrack", "原声": "soundtrack", "游戏": "video game", "儿童": "children",
             "民谣": "folk", "摇滚": "rock", "r&b": "r&b", "节奏布鲁斯": "r&b", "爵士": "jazz", "古典": "classical",
             "轻音乐": "ambient", "新世纪": "ambient", "国风": "chinese traditional", "中国风": "chinese traditional"}
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


def _chinese_name(name):
    """An artist name written with Chinese characters (and no Japanese kana or Korean hangul)."""
    name = text(name)
    return bool(re.search(r"[\u4e00-\u9fff]", name)) and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", name)


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
        self.failures, self.rechecking, self.retry_pause = {}, False, 3.0

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

    def _fill_albums(self):
        """Rows saved before v70 have no album: read it once from the file (or the library) so they group by album."""
        changed = False
        with self.lock:
            missing = [r for r in self.state["rows"] if "album" not in r][:500]
        library = None
        for r in missing:
            album = ""
            try:
                if r.get("scope") == "folder" and Path(r["ref"]).is_file():
                    album = text(read_tags(Path(r["ref"]), detect_audio_format(Path(r["ref"]))).get("album"))
                elif r.get("scope") == "library" and r.get("album_id"):
                    library = library or self.studio._library()[0]
                    album = text((library.albums.get(number(r["album_id"])) or {}).get("name"))
            except Exception as error:
                log.info("Tag clean-up could not read the album of %s: %s", r.get("ref"), error)
            r["album"], changed = album, True
        if changed:
            self._persist()

    def view(self):
        self._fill_albums()
        with self.lock:
            rows = list(self.state["rows"])
            grouped, albums = {}, {}
            for r in rows:
                if r.get("needs_genre"):
                    grouped.setdefault(r["artist"], []).append(r)
                    albums.setdefault(text(r.get("album")).strip().casefold(), []).append(r)
        # One short line per artist (v46): the name, how many songs, every source's genres (v70). No song lists.
        labels = self.genres()
        needs_decision = []
        for artist, rs in sorted(grouped.items(), key=lambda kv: match.match_key(kv[0])):
            current = [g for r in rs for g in [r.get("current_genre")] if g][:1]
            picks, tags = self.suggestions(artist, current)
            needs_decision.append({"artist": artist, "count": len(rs), "ids": [r["id"] for r in rs],
                                   "suggestions": picks, "tags": tags, "evidence": self.evidence(artist),
                                   "current": current[0] if current else ""})
        # v70: the same songs by album, so one click sets a whole album (a compilation is one row, not one per artist).
        by_artist = {g["artist"]: g for g in needs_decision}
        needs_album = []
        for key, rs in sorted(albums.items(), key=lambda kv: kv[0]):
            artists, picks = [], []
            for r in rs:
                if r["artist"] not in artists:
                    artists.append(r["artist"])
            votes = {}
            for artist in artists:
                for i, label in enumerate(by_artist.get(artist, {}).get("suggestions") or []):
                    votes[label] = votes.get(label, 0) + (4 - i)
            picks = [l for l, _ in sorted(votes.items(), key=lambda kv: -kv[1])][:4]
            needs_album.append({"album": text(rs[0].get("album")).strip(), "count": len(rs), "ids": [r["id"] for r in rs],
                                "artists": artists, "suggestions": picks})
        return {"artist_ready": [r for r in rows if r.get("new_artists")],
                "genre_ready": [r for r in rows if r.get("new_genre")],
                "needs_decision": needs_decision, "needs_album": needs_album, "genres": labels,
                "rechecking": self.rechecking, "discogs": bool(self._discogs_token()),
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
            album_id = number(track.get("album_id"))
            rows.append({"id": secrets.token_hex(6), "scope": "library", "ref": str(track_id),
                         "album_id": str(album_id), "title": text(track.get("title")),
                         "album": text((library.albums.get(album_id) or {}).get("name")),
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
                "album": text(tags.get("album")),
                "artist": artist, "current_artists": current, "current_album_artist": tags["album_artist"],
                "new_artists": new_artists if changed else None, "new_album_artist": new_artists[0] if changed else None,
                "current_genre": " \\ ".join(genres) if len(genres) > 1 else tags["genre"],
                "new_genre": new_genre, "needs_genre": needs_genre}

    # -- deciding a genre (v46) ----------------------------------------------------------------

    def _resolve_genre(self, artist, genres, labels):
        """-> (one of YOUR genres to write, or None; needs a manual decision).

        v70: asks Discogs (with a token), Apple, QQ Music (Chinese names) and then MusicBrainz, in that order;
        the first one that knows the artist and points at exactly one of your genres decides. When none is sure
        the artist is asked about, with every source's own genres beside the buttons."""
        if not artist or not labels:
            return None, bool(artist)
        for source, _ in GENRE_SOURCES:
            info = self._source_info(source, artist)
            if not info or not info.get("found"):
                continue
            label, confident = pick_label(self._with_country_hint(info, artist), genres, labels)
            if label and confident:
                return label, False
        if _chinese_name(artist) and _have(labels, "Chinese Hip Hop"):
            # Most of your Chinese downloads are Chinese hip-hop: the default unless a source says otherwise (v57).
            return _have(labels, "Chinese Hip Hop"), False
        return None, True

    @staticmethod
    def _with_country_hint(info, artist):
        """A source with no country (Apple, QQ) and a name written in Chinese: treat it as Chinese (v70)."""
        if info.get("country") or not _chinese_name(artist):
            return info
        return {**info, "country": "CN"}

    def _sources_for(self, artist):
        return [(s, label) for s, label in GENRE_SOURCES if s != "qq" or _chinese_name(artist)]

    def evidence(self, artist):
        """What every source said about this artist, from the caches only (no network): for the decision page."""
        out = []
        for source, label in self._sources_for(artist):
            cached = self._cached_info(source, artist)
            failed = self._failed(source, artist)
            if source == "discogs" and not self._discogs_token():
                continue
            if cached is None and not failed:
                continue
            out.append({"source": label, "found": bool(cached and cached.get("found")),
                        "tags": (cached or {}).get("tags", [])[:4], "failed": bool(failed) and cached is None})
        return out

    def suggestions(self, artist, genres=()):
        """(labels worth offering, every source's own genre names) for one artist; no network."""
        labels, picks, tags = self.genres(), [], []
        for source, _ in self._sources_for(artist):
            hit = self._cached_info(source, artist)
            if not hit:
                continue
            for label in candidate_labels(self._with_country_hint(hit, artist), genres, labels):
                if label not in picks:
                    picks.append(label)
            tags += [t for t in hit.get("tags", []) if t not in tags]
        if not picks:
            picks = candidate_labels(None, genres, labels)
        return picks[:4], tags[:6]

    # -- the sources (v70) ---------------------------------------------------------------------

    def _cache_key(self, source, artist):
        return match.match_key(artist) if source == "musicbrainz" else f"{source}|{match.match_key(artist)}"

    def _cache(self, source):
        return self.state["mb_cache"] if source == "musicbrainz" else self.state.setdefault("genre_cache", {})

    def _cached_info(self, source, artist):
        with self.lock:
            hit = self._cache(source).get(self._cache_key(source, artist))
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400 and isinstance(hit.get("info"), dict):
            return hit["info"]
        return None

    def _failed(self, source, artist):
        with self.lock:
            when = self.failures.get((source, self._cache_key(source, artist)))
        return when and when > time.time() - RETRY_AFTER

    def forget_failures(self, artists=None):
        with self.lock:
            if artists is None:
                self.failures.clear()
                return
            keys = {match.match_key(a) for a in artists}
            for key in [k for k in self.failures if k[1].split("|")[-1] in keys]:
                self.failures.pop(key, None)

    def _source_info(self, source, artist):
        """One source's record of the artist (cached 30 days); None when it is skipped or didn't answer."""
        if source == "qq" and not _chinese_name(artist):
            return None
        if source == "discogs" and not self._discogs_token():
            return None
        cached = self._cached_info(source, artist)
        if cached is not None:
            return cached
        if self._failed(source, artist):
            return None
        fetch = {"discogs": self._discogs_info, "apple": self._apple_info, "qq": self._qq_info, "musicbrainz": self._mb_fetch}[source]
        info = None
        for attempt in range(3):  # v70: a busy or slow source is asked again, slowly, before giving up
            try:
                info = fetch(artist)
                break
            except SourceError as error:
                log.info("%s genre lookup for %s failed (try %d): %s", source, artist, attempt + 1, error)
                if attempt < 2:
                    time.sleep(self.retry_pause * (attempt + 1))
        if info is None:
            with self.lock:
                self.failures[(source, self._cache_key(source, artist))] = time.time()
            return None
        with self.lock:
            self._cache(source)[self._cache_key(source, artist)] = {"info": info, "when": time.time()}
            self.failures.pop((source, self._cache_key(source, artist)), None)
        return info

    def _discogs_token(self):
        try:
            return (self.studio.source_keys() or {}).get("discogs_token", "")
        except Exception:
            return ""

    def _discogs_info(self, artist):
        """Discogs: the genres and styles of the artist's releases, most common first, and their usual country."""
        headers = {"Authorization": f"Discogs token={self._discogs_token()}"}
        data = self.http.json(f"{DISCOGS}/database/search", {"artist": artist, "type": "release", "per_page": 50},
                              headers=headers) or {}
        key = match.match_key(artist)
        rows = [r for r in data.get("results") or []
                if key and match.match_key(re.sub(r"\s*\(\d+\)", "", str(r.get("title") or "").split(" - ")[0]).replace("*", "")) == key]
        tags, countries = {}, {}
        for r in rows:
            for t in list(r.get("genre") or []) + list(r.get("style") or []):
                tags[text(t).casefold()] = tags.get(text(t).casefold(), 0) + 1
            code = DISCOGS_COUNTRIES.get(text(r.get("country")))
            if code:
                countries[code] = countries.get(code, 0) + 1
        return {"found": bool(rows), "country": max(countries, key=countries.get) if countries else "", "area": "",
                "tags": [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1]) if t][:8]}

    def _apple_info(self, artist):
        """Apple Music: the genre of the artist's albums (Mandopop, Hip-Hop/Rap, …)."""
        data = self.http.json(f"{ITUNES}/search", {"term": artist, "entity": "album", "limit": 25, "country": "SG"}) or {}
        key = match.match_key(artist)
        rows = [r for r in data.get("results") or [] if key and match.match_key(r.get("artistName")) == key]
        tags = {}
        for r in rows:
            genre = text(r.get("primaryGenreName")).casefold()
            if genre and genre != "music":
                tags[genre] = tags.get(genre, 0) + 1
        return {"found": bool(rows), "country": "", "area": "",
                "tags": [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])][:6]}

    def _qq_info(self, artist):
        """QQ Music (unofficial): the 流派 and language of up to three of the artist's albums."""
        headers = {"Referer": "https://y.qq.com/"}
        data = self.http.json(f"{QQ}/soso/fcgi-bin/client_search_cp", {"w": artist, "format": "json", "t": 8, "n": 10},
                              headers=headers) or {}
        key = match.match_key(artist)
        albums = [a for a in (((data.get("data") or {}).get("album") or {}).get("list") or [])
                  if key and any(match.match_key(s.get("name")) == key for s in a.get("singer_list") or [{"name": a.get("singerName")}])]
        tags, langs = {}, {}
        for album in albums[:3]:
            if not album.get("albumMID"):
                continue
            info = (self.http.json(f"{QQ}/v8/fcg-bin/fcg_v8_album_info_cp.fcg", {"albummid": album["albumMID"], "format": "json"},
                                   headers=headers) or {}).get("data") or {}
            genre = QQ_GENRES.get(text(info.get("genre")).strip().casefold(), "")
            lang = text(info.get("lan")).strip()
            if genre:
                tags[genre] = tags.get(genre, 0) + 1
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
        lang = max(langs, key=langs.get) if langs else ""
        if "pop" in tags:
            tags["cantopop" if lang == "粤语" else "mandopop"] = tags["pop"]
        return {"found": bool(albums), "country": "HK" if lang == "粤语" else "", "area": "",
                "tags": [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])][:6]}

    def _mb_info(self, artist):
        """MusicBrainz's record of the artist (kept for older callers): never None."""
        return self._source_info("musicbrainz", artist) or {"found": False, "country": "", "area": "", "tags": []}

    def _mb_fetch(self, artist):
        """MusicBrainz's record of the artist: {"found", "country", "area", "tags"}."""
        key = match.match_key(artist)
        info = {"found": False, "country": "", "area": "", "tags": []}
        rows = (self.http.json(f"{MB}/artist", {"query": f'artist:"{artist}"', "fmt": "json", "limit": 5}) or {}).get("artists") or []
        exact = [a for a in rows if match.match_key(a.get("name")) == key]
        candidates = exact if len(exact) == 1 else (rows if len(rows) == 1 else [])
        if candidates:
            a = candidates[0]
            tags = sorted((a.get("tags") or []), key=lambda t: -int(t.get("count") or 0))
            info = {"found": True, "country": text(a.get("country")), "area": text((a.get("area") or {}).get("name")),
                    "tags": [text(t.get("name")).casefold() for t in tags if text(t.get("name"))][:8]}
        return info

    # -- checking again (v70) -------------------------------------------------------------------

    def recheck(self, artists=None):
        """↻: forget which sources didn't answer and place the waiting songs again, in the background."""
        names = [text(a) for a in (artists or []) if text(a)] or None
        self.forget_failures(names)
        with self.lock:
            rows = [r for r in self.state["rows"] if r.get("needs_genre") and (names is None or r["artist"] in names)]
            if self.rechecking:
                return self.view()
            self.rechecking = True
        threading.Thread(target=self._recheck, args=(rows,), name="tagclean-recheck", daemon=True).start()
        return self.view()

    def _recheck(self, rows):
        try:
            labels = self.genres()
            by_artist = {}
            for r in rows:
                by_artist.setdefault(r["artist"], []).append(r)
            for artist, rs in by_artist.items():
                current = [g for r in rs for g in [r.get("current_genre")] if g][:1]
                label, needs = self._resolve_genre(artist, current, labels)
                if label and not needs:
                    try:
                        self.assign_genre([r["id"] for r in rs], label)
                    except LibraryError as error:
                        log.info("Tag clean-up recheck could not save %s: %s", artist, error)
        finally:
            with self.lock:
                self.rechecking = False
            self._persist()

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
