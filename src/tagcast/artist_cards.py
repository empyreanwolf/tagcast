"""Artist cards (fork v11): tell apart artists that share a name ("Lotus") in Galaxy results.

Galaxy gives only a name, no artist id. For that name Tagcast collects every artist with exactly
that name on MusicBrainz (description, country, years, type) and Deezer (photo, fans, album
count), plus their album lists. A MusicBrainz and a Deezer entry with the same albums become one
card. Each Galaxy song goes to the card whose albums include its album; your iBroadcast albums
under that name are shown on the card they match. Songs that fit no card, or more than one, go to
"Which artist?"; your pick is remembered per name and album.

Lookups are cached for 7 days in ``artist_cards_cache.json``; MusicBrainz is paced at one request
per second by ``sources.Http``. Only public read-only requests; nothing is written anywhere.
"""

import time
from collections import Counter

from . import artist_match as match
from .downloads import album_key
from .library import number, stored_genres, text
from .sources import PACING, Http, SourceError

CACHE_FILE = "artist_cards_cache.json"
CACHE_DAYS = 7
MAX_CANDIDATES = 6
MB = "https://musicbrainz.org/ws/2"
DEEZER = "https://api.deezer.com"


def name_key(name):
    return match.match_key(name)


def overlap(a, b):
    return len(set(a) & set(b))


class ArtistCards:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        data = studio._read(CACHE_FILE)
        self.cache = data if isinstance(data, dict) else {}

    # -- collecting candidates --------------------------------------------------------

    def _musicbrainz(self, name):
        data = self.http.json(f"{MB}/artist", {"query": f'artist:"{name}"', "limit": 15, "fmt": "json"}) or {}
        out = []
        for a in data.get("artists") or []:
            if name_key(a.get("name")) != name_key(name) and not any(
                    name_key(x.get("name")) == name_key(name) for x in a.get("aliases") or []):
                continue
            life = a.get("life-span") or {}
            years = "–".join(y for y in [(life.get("begin") or "")[:4], (life.get("end") or "")[:4]] if y)
            groups = self.http.json(f"{MB}/release-group", {"artist": a["id"], "limit": 100, "fmt": "json"}) or {}
            out.append({"mb_id": a["id"], "name": a.get("name", name), "description": a.get("disambiguation", ""),
                        "country": a.get("country") or (a.get("area") or {}).get("name", ""),
                        "type": a.get("type") or "", "years": years,
                        "albums": sorted({g.get("title", "") for g in groups.get("release-groups") or [] if g.get("title")})})
            if len(out) >= MAX_CANDIDATES:
                break
        return out

    def _deezer(self, name):
        data = self.http.json(f"{DEEZER}/search/artist", {"q": name, "limit": 15}) or {}
        out = []
        for a in data.get("data") or []:
            if name_key(a.get("name")) != name_key(name):
                continue
            albums = self.http.json(f"{DEEZER}/artist/{a['id']}/albums", {"limit": 100}) or {}
            out.append({"deezer_id": a["id"], "name": a.get("name", name), "picture": a.get("picture_medium", ""),
                        "fans": a.get("nb_fan", 0), "album_count": a.get("nb_album", 0),
                        "link": a.get("link", ""),
                        "albums": sorted({x.get("title", "") for x in albums.get("data") or [] if x.get("title")})})
            if len(out) >= MAX_CANDIDATES:
                break
        return out

    def candidates(self, name):
        """Cards for exactly this name, merged across MusicBrainz and Deezer (cached 7 days)."""
        key = name_key(name)
        hit = self.cache.get(key)
        if hit and hit.get("when", 0) > time.time() - CACHE_DAYS * 86400:
            return hit["cards"], hit.get("notes", [])
        notes, mb, dz = [], [], []
        try:
            mb = self._musicbrainz(name)
        except SourceError as error:
            notes.append(f"MusicBrainz {error}")
        try:
            dz = self._deezer(name)
        except SourceError as error:
            notes.append(f"Deezer {error}")
        cards, used = [], set()
        for m in mb:
            m_keys = {album_key(t) for t in m["albums"]}
            best, score = None, 0
            for n, d in enumerate(dz):
                if n in used:
                    continue
                s = overlap(m_keys, {album_key(t) for t in d["albums"]})
                if s > score:
                    best, score = n, s
            card = {"id": f"mb:{m['mb_id']}", **m}
            if best is not None and (score >= 2 or (score >= 1 and min(len(m["albums"]), len(dz[best]["albums"])) <= 3)):
                used.add(best)
                d = dz[best]
                card.update(deezer_id=d["deezer_id"], picture=d["picture"], fans=d["fans"],
                            album_count=d["album_count"], link=d["link"],
                            albums=sorted(set(m["albums"]) | set(d["albums"])))
            cards.append(card)
        for n, d in enumerate(dz):
            if n not in used:
                cards.append({"id": f"dz:{d['deezer_id']}", **d})
        if not notes:  # only cache complete answers
            self.cache[key] = {"when": time.time(), "cards": cards, "notes": notes}
            if len(self.cache) > 500:
                for old in sorted(self.cache, key=lambda k: self.cache[k].get("when", 0))[:len(self.cache) - 500]:
                    self.cache.pop(old)
            self.studio._write(CACHE_FILE, self.cache)
        return cards, notes

    # -- your library ------------------------------------------------------------------

    def library_albums(self, name):
        """Your iBroadcast albums whose album artist is this name, with their genres."""
        try:
            self.studio._require_client()
            library, _ = self.studio._library()
        except Exception:
            return []
        key, out = name_key(name), []
        for album_id in library.album_ids():
            album = library.albums[album_id]
            names = [library.artist_name(i) for i in library.album_artist_ids(album)]
            if not any(name_key(n) == key for n in names):
                continue
            genres = Counter(g for t in library.active_track_ids(album_id) for g in stored_genres(library.tracks[t]))
            out.append({"title": text(album.get("name")), "album_id": number(album_id),
                        "genres": [g for g, _ in genres.most_common(2)]})
        return out

    # -- placing songs -----------------------------------------------------------------

    def cards_for(self, name, albums, picks):
        """{"cards": [...], "assign": {album name: card id or "unsure"}, "library": [...]}.
        ``albums``: album names of the Galaxy songs. ``picks``: remembered "name|album" → card id."""
        name = match.clean(name)
        cards, notes = self.candidates(name)
        mine = self.library_albums(name)
        keyed = [(c, {album_key(t) for t in c["albums"]}) for c in cards]
        for c, keys in keyed:
            c["yours"] = [a for a in mine if album_key(a["title"]) in keys]
            c["genres"] = [g for g, _ in Counter(g for a in c["yours"] for g in a["genres"]).most_common(3)]
        assign = {}
        for album in albums:
            pick = picks.get(f"{name_key(name)}|{album_key(album)}")
            if pick and any(c["id"] == pick for c in cards):
                assign[album] = pick
                continue
            fits = [c["id"] for c, keys in keyed if album_key(album) in keys]
            if len(cards) == 1:
                assign[album] = cards[0]["id"]  # nothing to tell apart
            elif len(fits) == 1:
                assign[album] = fits[0]
            else:
                assign[album] = "unsure" if cards else ""
        for c in cards:
            c["songs_albums"] = sorted(a for a, card in assign.items() if card == c["id"])
            c["album_count"] = c.get("album_count") or len(c["albums"])
            c["albums"] = c["albums"][:60]
        unplaced = [a for a in mine if not any(a in c["yours"] for c in cards)]
        return {"name": name, "cards": cards, "assign": assign, "library": mine, "library_unplaced": unplaced,
                "notes": notes}
