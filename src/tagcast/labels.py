"""Labels (fork v64): a record label's catalogue, with what you are missing marked.

Type a label name (Rhymesayers, Stones Throw, Def Jux…) or paste a label's Bandcamp link.

- **Bandcamp link**: the releases on the label's ``/music`` page, each with its own Bandcamp link,
  so ⬇ queues that exact release on Bandcamp.
- **Label name**: the label on MusicBrainz (the best name match; the other matches are offered),
  then its releases, one row per release group (albums and EPs; singles too when asked). ⬇ goes
  through the usual YouTube -> Galaxy -> Bandcamp -> Soulseek chain, per artist, like Discography.

Every row is compared with your library and download history (Have / Partly / New). Nothing is
written to iBroadcast. State: an in-memory cache only.
"""

import html as htmllib
import json
import logging
import re
import threading
import time
from urllib.parse import urlparse

from . import artist_match as match
from .library import LibraryError, text
from .sources import PACING, Http, SourceError

log = logging.getLogger("tagcast")
MB = "https://musicbrainz.org/ws/2"
MAX_PAGES = 5          # 5 x 100 MusicBrainz releases
MAX_ROWS = 400
CACHE_SECONDS = 6 * 3600
KEEP_TYPES = {"Album": "album", "EP": "ep", "Single": "single"}


class LabelError(LibraryError):
    pass


def _credit(entries):
    return "".join(text(e.get("name") or (e.get("artist") or {}).get("name")) + text(e.get("joinphrase"))
                   for e in entries or []).strip()


def bandcamp_items(page):
    """(title, artist, path) for each release on a Bandcamp /music page. Label pages name the artist
    per release ("artist-override"); big pages ship part of the grid as JSON (data-client-items)."""
    out = []
    for block in re.findall(r'<li[^>]*class="[^"]*music-grid-item[^"]*"[^>]*>(.*?)</li>', page or "", re.S):
        link = re.search(r'href="((?:https?://[^"/]+)?/(?:album|track)/[^"?#]+)', block)
        title = re.search(r'<p class="title">(.*?)</p>', block, re.S)
        if not link or not title:
            continue
        inner = title.group(1)
        artist = re.search(r'<span[^>]*class="[^"]*artist-override[^"]*"[^>]*>(.*?)</span>', inner, re.S)
        name = re.sub(r"<span.*", "", inner, flags=re.S)
        clean = lambda s: " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).split())
        out.append((clean(name), clean(artist.group(1)) if artist else "", re.sub(r"^https?://[^/]+", "", link.group(1))))
    blob = re.search(r'data-client-items="([^"]+)"', page or "")
    if blob:
        try:
            for item in json.loads(htmllib.unescape(blob.group(1))):
                path = text(item.get("page_url"))
                if path and item.get("title"):
                    out.append((text(item["title"]), text(item.get("artist")), re.sub(r"^https?://[^/]+", "", path)))
        except (ValueError, AttributeError, TypeError):
            pass
    seen, unique = set(), []
    for row in out:
        if row[2] not in seen:
            seen.add(row[2])
            unique.append(row)
    return unique


def band_name(page):
    m = re.search(r'<meta property="og:site_name" content="([^"]+)"', page or "") \
        or re.search(r'<p id="band-name-location">\s*<span class="title">([^<]+)</span>', page or "")
    return " ".join(htmllib.unescape(m.group(1)).split()) if m else ""


class Labels:
    def __init__(self, studio, http=None):
        self.studio = studio
        self.http = http or Http(PACING)
        self.lock = threading.Lock()
        self.cache = {}

    # -- reading ---------------------------------------------------------------------------

    def view(self, query, label_id="", singles=False, refresh=False):
        query = text(query).strip()
        if not query and not label_id:
            raise LabelError("Type a label name, or paste the label's Bandcamp link.")
        key = (query.casefold(), label_id, bool(singles))
        with self.lock:
            hit = self.cache.get(key)
        if hit and not refresh and hit["when"] > time.time() - CACHE_SECONDS:
            data = hit["data"]
        else:
            data = self._bandcamp(query) if "bandcamp.com" in query.casefold() else self._musicbrainz(query, label_id, singles)
            with self.lock:
                self.cache[key] = {"when": time.time(), "data": data}
        return {**data, "rows": self._with_status(data["rows"])}

    def _with_status(self, rows):
        rows = [dict(r) for r in rows]
        if not rows:
            return rows
        try:
            checked = self.studio.download_manager.check(
                [{"kind": "album", "artist": r["artist"], "title": r["title"]} for r in rows])["results"]
        except LibraryError:
            checked = [{"status": "new"}] * len(rows)
        for r, c in zip(rows, checked):
            r["status"] = c.get("status", "new")
        return rows

    def _bandcamp(self, link):
        bandcamp = self.studio.bandcamp
        from .bandcamp import base_url
        base, _ = base_url(link)
        page = bandcamp.page(base + "/music")
        label = band_name(page) or urlparse(base).hostname.split(".")[0]
        items = bandcamp_items(page)
        if not items:
            from .bandcamp import music_page_releases
            items = [(p.rsplit("/", 1)[-1].replace("-", " ").title(), "", p) for p in music_page_releases(page)]
        if not items:
            raise LabelError("No releases found on this Bandcamp page.")
        rows = [{"id": path, "title": title, "artist": artist or label, "year": "", "type": "track" if path.startswith("/track/") else "album",
                 "url": base + path, "source": "bandcamp"} for title, artist, path in items[:MAX_ROWS]]
        return {"label": label, "where": "Bandcamp", "url": base, "others": [], "rows": rows}

    def _musicbrainz(self, name, label_id, singles):
        others = []
        if not label_id:
            try:
                found = (self.http.json(f"{MB}/label", {"query": f'label:"{name}"', "fmt": "json", "limit": 6}) or {}).get("labels") or []
            except SourceError as error:
                raise LabelError(f"MusicBrainz {error}.") from None
            if not found:
                raise LabelError(f'MusicBrainz knows no label called "{name}".')
            key = match.match_key(name)
            exact = [lab for lab in found if match.match_key(lab.get("name")) == key]
            best = (exact or found)[0]
            label_id = best["id"]
            others = [{"id": lab["id"], "name": text(lab.get("name")),
                       "note": " · ".join(x for x in (text(lab.get("disambiguation")), text(lab.get("country"))) if x)}
                      for lab in found if lab["id"] != label_id]
        groups, label_name = {}, name
        for page in range(MAX_PAGES):
            try:
                data = self.http.json(f"{MB}/release", {"label": label_id, "inc": "artist-credits release-groups labels",
                                                         "fmt": "json", "limit": 100, "offset": page * 100}) or {}
            except SourceError as error:
                if page == 0:
                    raise LabelError(f"MusicBrainz {error}.") from None
                break
            releases = data.get("releases") or []
            for r in releases:
                for info in r.get("label-info") or []:
                    if (info.get("label") or {}).get("id") == label_id:
                        label_name = text(info["label"].get("name")) or label_name
                group = r.get("release-group") or {}
                kind = KEEP_TYPES.get(group.get("primary-type"))
                if not kind or (kind == "single" and not singles) or group.get("secondary-types"):
                    continue
                gid = group.get("id") or r.get("id")
                if gid in groups:
                    continue
                groups[gid] = {"id": gid, "title": text(group.get("title") or r.get("title")),
                               "artist": _credit(group.get("artist-credit") or r.get("artist-credit")) or "Various Artists",
                               "year": text(group.get("first-release-date") or r.get("date"))[:4], "type": kind,
                               "url": "", "source": "musicbrainz"}
            if len(releases) < 100 or page * 100 + len(releases) >= int(data.get("release-count") or 0):
                break
        rows = sorted(groups.values(), key=lambda r: (r["year"] or "0000"), reverse=True)[:MAX_ROWS]
        if not rows:
            raise LabelError(f"MusicBrainz lists no albums or EPs for {label_name}.")
        return {"label": label_name, "where": "MusicBrainz", "url": f"https://musicbrainz.org/label/{label_id}",
                "label_id": label_id, "others": others, "rows": rows}

    # -- getting ---------------------------------------------------------------------------

    def get(self, rows):
        """Queue the chosen releases: Bandcamp rows straight from their page, the rest per artist
        through the Discography chain (shown under Downloads > Queue > Finding sources)."""
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise LabelError("Choose between 1 and 200 releases.")
        queued, jobs, failed = 0, 0, []
        by_artist = {}
        for r in rows:
            r = r or {}
            title, artist, url = match.clean(r.get("title")), match.clean(r.get("artist")), text(r.get("url"))
            if not title:
                continue
            if url and "bandcamp.com" in url:
                try:
                    result = self.studio.bandcamp.add(url)
                    queued += result.get("queued", 0) + result.get("held", 0)
                except LibraryError as error:
                    failed.append(f"{title}: {error}")
            elif artist:
                by_artist.setdefault(artist, []).append({"title": title, "status": r.get("status")})
        for artist, albums in by_artist.items():
            self.studio.discography.get(artist, albums, [])
            jobs += len(albums)
        if not (queued or jobs or failed):
            raise LabelError("Nothing to get in that choice.")
        return {"queued": queued, "jobs": jobs, "failed": failed}
