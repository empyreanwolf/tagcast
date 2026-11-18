"""Real track names for a split full-album video (fork v109).

After a full-album video is split by its chapters, the album is looked up on MusicBrainz, then
Deezer. A release only counts when it has **the same number of tracks** and **most track lengths
match the chapter lengths** (within 8 s or 8 %), so a wrong guess can't rename your songs. A match
gives the official titles, track order, year, genre and a cover; no match leaves the video's own
names in place.
"""

import logging

from . import artist_match as match
from .galaxy import sim
from .sources import SourceError

log = logging.getLogger("tagcast")
MB = "https://musicbrainz.org/ws/2"
DEEZER = "https://api.deezer.com"
CAA = "https://coverartarchive.org/release"


def chapter_lengths(chapters):
    out = []
    for c in chapters:
        start, end = c.get("start_time") or 0, c.get("end_time")
        out.append(float(end) - float(start) if end else None)
    return out


def lengths_match(chapters, lengths):
    """Same count, and at least 70 % of the comparable tracks within 8 s / 8 % of their chapter."""
    if len(lengths) != len(chapters) or len(chapters) < 2:
        return False
    pairs = [(a, b) for a, b in zip(chapter_lengths(chapters), lengths) if a and b]
    if len(pairs) < max(2, len(chapters) // 2):
        return False
    close = sum(1 for a, b in pairs if abs(a - b) <= max(8.0, 0.08 * b))
    return close >= 0.7 * len(pairs)


def _musicbrainz(http, artist, album, chapters):
    query = f'release:"{album}"' + (f' AND artist:"{artist}"' if artist else "")
    found = (http.json(f"{MB}/release", {"query": query, "limit": 8, "fmt": "json"}) or {}).get("releases") or []
    for release in found:
        if release.get("track-count") != len(chapters) or sim(release.get("title") or "", album) < 0.8:
            continue
        detail = http.json(f"{MB}/release/{release['id']}", {"inc": "recordings genres release-groups", "fmt": "json"}) or {}
        tracks = [t for medium in detail.get("media") or [] for t in medium.get("tracks") or []]
        lengths = [(t.get("length") or 0) / 1000 or None for t in tracks]
        if not lengths_match(chapters, lengths):
            continue
        genres = detail.get("genres") or (detail.get("release-group") or {}).get("genres") or []
        genre = max(genres, key=lambda g: g.get("count") or 0)["name"].title() if genres else ""
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in detail.get("artist-credit") or [])
        return {"source": "MusicBrainz", "album": detail.get("title") or album, "artist": credit,
                "titles": [match.clean(t.get("title")) for t in tracks], "year": (detail.get("date") or "")[:4],
                "genre": genre, "cover_url": f"{CAA}/{release['id']}/front-500"}
    return None


def _deezer(http, artist, album, chapters):
    query = f'album:"{album}"' + (f' artist:"{artist}"' if artist else "")
    found = (http.json(f"{DEEZER}/search/album", {"q": query, "limit": 10}) or {}).get("data") or []
    for hit in found:
        if hit.get("nb_tracks") not in (None, len(chapters)) or sim(hit.get("title") or "", album) < 0.8:
            continue
        detail = http.json(f"{DEEZER}/album/{hit['id']}") or {}
        tracks = (detail.get("tracks") or {}).get("data") or []
        if not lengths_match(chapters, [t.get("duration") or None for t in tracks]):
            continue
        genres = (detail.get("genres") or {}).get("data") or []
        return {"source": "Deezer", "album": detail.get("title") or album,
                "artist": (detail.get("artist") or {}).get("name") or artist,
                "titles": [match.clean(t.get("title")) for t in tracks], "year": (detail.get("release_date") or "")[:4],
                "genre": genres[0]["name"] if genres else "", "cover_url": detail.get("cover_xl") or detail.get("cover_big") or ""}
    return None


def album_info(http, artist, album, chapters):
    """The matching release's details, or None. Never raises: a lookup that fails is just no match."""
    if not http or not album or len(chapters) < 2:
        return None
    for lookup in (_musicbrainz, _deezer):
        try:
            found = lookup(http, artist, album, chapters)
        except (SourceError, KeyError, TypeError, ValueError) as error:
            log.info("%s lookup for %s - %s failed: %s", lookup.__name__.strip("_"), artist, album, error)
            continue
        if found and all(found["titles"]):
            return found
    return None
