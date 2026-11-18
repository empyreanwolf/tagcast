"""Credit a song to the artist you were downloading (fork v109).

When you get songs *by A* (Get the missing ones, the wishlist), the sources often hand back a
song that is really B's with A featured ("B - Song (feat. A)"). Tagged as it comes, it lands
under B in iBroadcast - one more thing to clean up. So, for downloads started from an artist:

- **Artist**: A first, then everyone else credited (the featured names moved out of the title).
- **Album Artist**: A, so the song is filed under A.
- **Title**: without "(feat. …)" - nobody is lost, they are all in the Artist tag now.
- **Album**: unchanged (B's album name stays).

``for_artist(name)`` marks every item queued from this thread while it is active; the download
workers then call ``credit_for`` when they tag the file.
"""

import contextlib
import re
import threading

from . import artist_match as match
from .galaxy import fold

_tls = threading.local()

# "(feat. X)", "[ft. X & Y]", "(featuring X)" in brackets, or a bare " feat. X" tail. "(with X)" only
# counts when X is the artist asked for - "(With You)" is part of a title, not a credit.
_FEAT_BRACKET = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]", re.IGNORECASE)
_WITH_BRACKET = re.compile(r"\s*[\(\[]\s*with\s+([^\)\]]+)[\)\]]", re.IGNORECASE)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.IGNORECASE)
_SPLIT = re.compile(r"\s*(?:,|;|/|\s&\s|\sand\s|\sx\s|\s×\s)\s*", re.IGNORECASE)


@contextlib.contextmanager
def for_artist(name):
    """Everything queued from this thread inside the block was asked for as a song by ``name``."""
    previous = getattr(_tls, "artist", "")
    _tls.artist = match.clean(name or "")
    try:
        yield
    finally:
        _tls.artist = previous


def current():
    return getattr(_tls, "artist", "")


def _names(text):
    return [n for n in (match.clean(p) for p in _SPLIT.split(text or "")) if n]


def split_featured(title, want=""):
    """("Song", ["X", "Y"]) from "Song (feat. X & Y)"; the title is unchanged when nobody is featured."""
    found = []
    clean = str(title or "")
    for m in list(_FEAT_BRACKET.finditer(clean)):
        found += _names(m.group(1))
    clean = _FEAT_BRACKET.sub("", clean)
    for m in list(_WITH_BRACKET.finditer(clean)):
        names = _names(m.group(1))
        if any(_same(n, want) for n in names):
            found += names
            clean = clean.replace(m.group(0), "")
    tail = _FEAT_TAIL.search(clean)
    if tail:
        found += _names(tail.group(1))
        clean = clean[:tail.start()]
    clean = " ".join(clean.split())
    return (clean or str(title or "").strip()), found


def _same(a, b):
    return bool(a and b) and fold(a) == fold(b)


def credits(want, title, artists):
    """Is ``want`` credited on this song at all (as an artist, or featured in the title)?"""
    if not want:
        return False
    _, featured = split_featured(title, want)
    every = [n for a in artists or [] for n in ([a] + _names(a))] + featured
    return any(_same(n, want) for n in every)


def credit_for(want, title, artists, album_artist):
    """(title, artists, album_artist) as they should be tagged for a song asked for as by ``want``.
    Unchanged when ``want`` is empty or isn't credited on the song at all."""
    artists = [a for a in (artists or []) if str(a or "").strip()]
    if not want or not credits(want, title, artists):
        return title, artists, album_artist
    plain, featured = split_featured(title, want)
    everyone = []
    for name in [*artists, *featured]:
        # "Bee & Jay" as one credit is split only when Jay is the one asked for, so a duo
        # like "Simon & Garfunkel" stays whole.
        split = _names(name)
        parts = split if not _same(name, want) and any(_same(p, want) for p in split) else [name]
        for part in parts:
            if not any(_same(part, e) for e in everyone):
                everyone.append(part)
    own = next((n for n in everyone if _same(n, want)), want)
    others = [n for n in everyone if not _same(n, want)]
    return plain, [own, *others], own


def read_lyrics(path, fmt):
    """The lyrics already inside a file ("" when none), so a re-tag never drops them."""
    try:
        if fmt == "flac":
            from mutagen.flac import FLAC
            tags = FLAC(str(path)).tags or {}
            return (list(tags.get("LYRICS", [])) or list(tags.get("UNSYNCEDLYRICS", [])) or [""])[0]
        if fmt == "m4a":
            from mutagen.mp4 import MP4
            return str(((MP4(str(path)).tags or {}).get("\xa9lyr") or [""])[0])
        from mutagen.id3 import ID3
        frames = ID3(str(path)).getall("USLT")
        return str(frames[0].text) if frames else ""
    except Exception:
        return ""


def retag_file(path, want, lyrics_finder=None):
    """Re-tag a file that arrived already tagged (Soulseek): credited to ``want`` as above, and - when
    ``lyrics_finder(path, fmt, title, artists, album)`` is given and the file has none - with lyrics.
    Everything else in the file's tags is kept. True when the file changed."""
    from .galaxy import detect_audio_format, write_tags
    from .tagclean import read_tags
    fmt = detect_audio_format(path)
    tags = read_tags(path, fmt)
    artists = [a for whole in tags["artists"] for a in str(whole).split("; ") if a.strip()]
    title, new_artists, album_artist = credit_for(want, tags["title"], artists, tags["album_artist"])
    lyrics = read_lyrics(path, fmt)
    added = ""
    if not lyrics and lyrics_finder:
        added = lyrics = lyrics_finder(path, fmt, title, new_artists, tags["album"]) or ""
    if (title, new_artists, album_artist) == (tags["title"], artists, tags["album_artist"]) and not added:
        return False
    write_tags(path, fmt, title=title, artists=new_artists, album_artist=album_artist, album=tags["album"],
               track_no=tags["track_no"], track_total=tags["track_total"], year=tags["year"], genre=tags["genre"],
               cover=tags["cover"], mb_track_id=tags["mb_track_id"], mb_release_id=tags["mb_release_id"], lyrics=lyrics)
    return True
