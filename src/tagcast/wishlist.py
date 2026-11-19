"""Wishlist with retry (fork v17).

Albums and songs that "Get the missing" could not find on Galaxy, Bandcamp or Soulseek (including
a Soulseek job that failed, after Galaxy had already found nothing) go on the wishlist. While
Tagcast is open it tries them again every 3 days (adjustable), in the same source order; Soulseek
especially changes as people come online. A found item leaves the wishlist and goes to the queue
(still through the duplicate check). An item you meanwhile have is tagged "In your library?" and
leaves the list once you confirm it (fork v95; before, it was removed without asking). State: ``wishlist.json``.
"""

import logging
import re
import secrets
import threading
import time
from pathlib import Path

from . import artist_match as match
from . import featured
from .downloads import album_key, artist_keys, song_key
from .galaxy import find_existing_by_title, safe_name
from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "wishlist.json"
MAX_ITEMS = 2000
CHECK_BATCH = 500


def _folder_hit(manager, item):
    """fork v105: the Music folder itself, as one more place you might already have this - a file
    can sit there before iBroadcast has re-scanned it, or if it was saved outside Tagcast's own
    download history (e.g. replaced by hand). Only tried once iBroadcast/history both say "new"."""
    folder = manager.folder()
    try:
        if item["kind"] == "song":
            if not item.get("album"):
                return None  # find_existing_by_title() needs a real album folder to look in
            found = find_existing_by_title(folder, item["artist"], item["album"], item["title"])
            if not found:
                return None
        else:
            path = Path(folder) / safe_name(item["artist"], "Unknown Artist") / safe_name(item["title"], "Unknown Album")
            if not path.is_dir() or not (any(path.glob("*.mp3")) or any(path.glob("*.flac")) or any(path.glob("*.m4a"))):
                return None
    except OSError:
        return None
    return {"where": "folder", "title": item["title"], "artist": item["artist"], "album": item.get("album", "")}


def _with_folder_fallback(manager, item, check):
    """check, or a synthetic "have" built from _folder_hit() when check itself came up empty."""
    if check.get("status") == "have":
        return check
    hit = _folder_hit(manager, item)
    return {"status": "have", "you_have": [hit]} if hit else check


RELEASES = ("album", "ep", "single", "song")
SPLIT = re.compile(r"\s+//?\s+")
LOOKUPS_PER_CHECK = 40  # fork v110: at most this many fresh Discogs / Deezer look-ups in one "Check my library"


def split_titles(title):
    """fork v110: "The Ocean / No Image" -> ["The Ocean", "No Image"]; [] when the title is not a split release."""
    parts = [p for p in (x.strip() for x in SPLIT.split(match.clean(title))) if p]
    return parts if len(parts) > 1 else []


def release_from_count(count):
    return "" if not count else "single" if count <= 3 else "ep" if count <= 6 else "album"


def owned_from(item, check):
    """Fork v95: {"sure", "you_have"} when the library check says you have this wish, else None.
    Sure = the same title (album: the same album, not another edition) by the main artist; the rest is a
    "maybe" you tick yourself. An album you only partly have is not owned (Complete my albums covers it).
    fork v110: a song you downloaded but iBroadcast has not scanned yet (download history) counts as a "maybe"."""
    if check.get("status") == "downloaded" and item.get("kind") == "song":
        rows = [r for r in check.get("you_have") or [] if r.get("where") == "history"]
        if not rows:
            return None
        return {"sure": False, "you_have": [{k: r.get(k) for k in ("title", "artist", "album", "tracks")} | {"downloaded": True}
                                            for r in rows[:3]]}
    if check.get("status") != "have":
        return None
    rows = [r for r in check.get("you_have") or [] if r.get("where") in ("ibroadcast", "folder")]
    if not rows:
        return None
    keys = artist_keys(match.main_artist(item["artist"]))
    sure = not check.get("other_edition") and any(
        not r.get("close") and not r.get("edition") and keys & artist_keys(r.get("artist") or "") for r in rows)
    return {"sure": bool(sure), "you_have": [{k: r.get(k) for k in ("title", "artist", "album", "tracks")} for r in rows[:3]]}


class Wishlist:
    def __init__(self, studio, scheduler=True):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        for key, value in (("items", []), ("every_days", 3), ("found", [])):
            self.state.setdefault(key, value)
        self.lock = threading.RLock()
        self.progress = None
        seen = set()
        for item in self.state["items"]:  # fork v110: keys follow the looser title matching; drop doubles that now collide
            item["key"] = self._key(item.get("kind", "song"), item.get("artist", ""), item.get("title", ""))
        self.state["items"] = [i for i in self.state["items"] if not (i["key"] in seen or seen.add(i["key"]))]
        if scheduler:
            threading.Thread(target=self._schedule, name="wishlist", daemon=True).start()

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    @staticmethod
    def _key(kind, artist, title):
        return f"{kind}|{match.match_key(artist)}|{album_key(title) if kind == 'album' else song_key(title)}"

    # -- changing the list ---------------------------------------------------------------------

    def add(self, kind, artist, title, album="", status="missing", note="", release="", featured=""):
        release = str(release or (kind if kind in ("ep", "single", "song") else "")).lower()  # fork v110: Album, EP, Single or Song
        release = release if release in RELEASES else ("song" if kind == "song" else "")
        kind = "song" if release == "song" or kind == "song" else "album"
        artist, title = match.clean(artist), match.clean(title)
        if not artist or not title:
            raise LibraryError("A wish needs an artist and a title.")
        key = self._key(kind, artist, title)
        with self.lock:
            for item in self.state["items"]:
                if item["key"] == key:
                    return item
            item = {"id": secrets.token_hex(6), "key": key, "kind": kind, "artist": artist, "title": title,
                    "album": match.clean(album), "status": status if status in ("missing", "partly") else "missing",
                    "added": time.time(), "tries": 1, "last_try": time.time(), "note": note or "Not found yet"}
            if release:
                item["release"] = release
            if featured:
                item["featured"] = match.clean(featured)
            self.state["items"].insert(0, item)
            del self.state["items"][MAX_ITEMS:]
        self._persist()
        return item

    def remove(self, ids):
        wanted = set(map(str, ids or []))
        with self.lock:
            self.state["items"] = [i for i in self.state["items"] if i["id"] not in wanted]
        self._persist()
        return self.view()

    def _release_songs(self, item, budget):
        """fork v110: {"have", "missing", "total", "release"} for an album / EP / single wish, from its own title
        ("A / B") or Discogs' tracklist; None when the songs are not known."""
        manager, d = self.studio.download_manager, self.studio.discography
        titles = split_titles(item["title"])
        release = "single" if titles else ""
        if not titles and budget[0] > 0:
            budget[0] -= 1
            try:
                titles, release = d.tracklist(match.main_artist(item["artist"]), item["title"])
            except Exception as error:
                log.info("Tracklist of %s failed: %s", item["title"], error)
                titles = []
        if not titles:
            return None
        results = manager.check([{"kind": "song", "artist": item["artist"], "title": t, "album": ""} for t in titles])["results"]
        have, missing = [], []
        for title, res in zip(titles, results):
            row = next(iter(res.get("you_have") or []), None)
            if res.get("status") in ("have", "downloaded") and row:
                have.append({"title": title, "album": row.get("album", ""), "close": bool(row.get("close")),
                             "downloaded": res.get("status") == "downloaded"})
            else:
                missing.append(title)
        return {"have": have, "missing": missing, "total": len(titles), "release": release or release_from_count(len(titles))}

    def check_library(self):
        """Fork v95: "Check my library": compare every wish with iBroadcast and tag the ones you have.
        fork v110: also looks at the songs of a split single / EP, the download history, and who an album is really
        credited to."""
        manager, d = self.studio.download_manager, self.studio.discography
        with self.lock:
            todo = [dict(i) for i in self.state["items"] if not i.get("keep")]
        found, parts, credits, kinds = {}, {}, {}, {}
        budget = [LOOKUPS_PER_CHECK]
        for start in range(0, len(todo), CHECK_BATCH):
            chunk = todo[start:start + CHECK_BATCH]
            results = manager.check([{"kind": i["kind"], "artist": i["artist"], "title": i["title"],
                                      "album": i.get("album", "")} for i in chunk])["results"]
            for item, check in zip(chunk, results):
                owned = owned_from(item, _with_folder_fallback(manager, item, check))
                found[item["id"]] = owned
                if owned or item["kind"] != "album":
                    continue
                try:
                    songs = self._release_songs(item, budget)
                    if songs:
                        kinds[item["id"]] = songs["release"]
                        if songs["have"] and not songs["missing"]:
                            found[item["id"]] = {"sure": not any(h["close"] or h["downloaded"] for h in songs["have"]),
                                                 "you_have": [{"title": h["title"], "artist": item["artist"], "album": h["album"]}
                                                              for h in songs["have"][:3]], "all_songs": True}
                        elif songs["have"]:
                            parts[item["id"]] = songs
                    if budget[0] > 0 and not item.get("search_artist") and not item.get("featured"):
                        budget[0] -= 1
                        credited = d.album_credit(match.main_artist(item["artist"]), item["title"])
                        if credited:
                            credits[item["id"]] = credited
                except Exception as error:
                    log.info("Library check of %s went further wrong: %s", item["title"], error)
        with self.lock:
            for i in self.state["items"]:
                if i["id"] not in found:
                    continue
                if found[i["id"]]:
                    i["owned"] = found[i["id"]]
                else:
                    i.pop("owned", None)
                if i["id"] in parts:
                    i["partial"] = parts[i["id"]]
                else:
                    i.pop("partial", None)
                if i["id"] in kinds and not i.get("release"):
                    i["release"] = kinds[i["id"]]
                if i["id"] in credits:
                    i["suggest"] = credits[i["id"]]
                    i["note"] = f"Credited to “{credits[i['id']]}” on Deezer; {i['artist']} is a guest here"
        self._persist()
        view = self.view()
        view["checked"] = {"total": len(todo), "found": sum(1 for v in found.values() if v)}
        return view

    def split(self, ids):
        """fork v110: "Keep only the missing songs": an album / single wish you only partly have becomes one song
        wish for each song you do not have yet."""
        wanted = set(map(str, ids or []))
        with self.lock:
            items = [i for i in self.state["items"] if i["id"] in wanted and i.get("partial", {}).get("missing")]
        for item in items:
            for title in item["partial"]["missing"]:
                self.add("song", item["artist"], title, item["title"], note=f"Missing song of “{item['title']}”")
            self.remove([item["id"]])
        return self.view()

    def search(self, query):
        """fork v110: the Missing page's search box: what you have (library, download history, Music folder) and which
        wishes match, each with its id so one click removes it."""
        manager = self.studio.download_manager
        found = manager.library_search(query)
        with self.lock:
            items = list(self.state["items"])
        by_key = {}
        for i in items:
            key = album_key(i["title"]) if i["kind"] == "album" else song_key(i["title"])
            by_key.setdefault((i["kind"], key), []).append(i)

        def wishes_for(kind, title, artist):
            out = []
            for i in by_key.get((kind, album_key(title) if kind == "album" else song_key(title)), []):
                keys = artist_keys(match.main_artist(i["artist"]))
                if not keys or not artist or keys & artist_keys(artist):
                    out.append({"id": i["id"], "title": i["title"], "artist": i["artist"], "release": i.get("release", "")})
            return out

        for row in found["songs"]:
            row["wishes"] = wishes_for("song", row["title"], row["artist"])
        for row in found["albums"]:
            row["wishes"] = wishes_for("album", row["title"], row["artist"])
        for row in found["history"]:
            row["wishes"] = wishes_for("song", row["title"], row["artist"])
        words = [w for w in match.clean(query).casefold().split() if w]
        wished = []
        for i in items:
            hay = match.clean(f"{i['artist']} {i['title']} {i.get('album', '')}").casefold()
            if words and all(w in hay for w in words):
                wished.append({"id": i["id"], "kind": i["kind"], "title": i["title"], "artist": i["artist"],
                               "release": i.get("release", ""), "owned": bool(i.get("owned"))})
        found["wished"] = wished[:40]
        return found

    def keep(self, ids):
        """Fork v95: "Not the same": these stay on the wishlist and the library check leaves them alone."""
        wanted = set(map(str, ids or []))
        with self.lock:
            for i in self.state["items"]:
                if i["id"] in wanted:
                    i.pop("owned", None)
                    i["keep"] = True
        self._persist()
        return self.view()

    def save_settings(self, body):
        try:
            days = int((body or {}).get("every_days"))
        except (TypeError, ValueError):
            raise LibraryError("Try again every 1 to 30 days.") from None
        if not 1 <= days <= 30:
            raise LibraryError("Try again every 1 to 30 days.")
        with self.lock:
            self.state["every_days"] = days
        self._persist()
        return self.view()

    def view(self):
        with self.lock:
            every = self.state["every_days"] * 86400
            items = [{**i, "next_try": i["last_try"] + every} for i in self.state["items"]]
            return {"items": items, "every_days": self.state["every_days"], "found": self.state["found"][:50],
                    "progress": self.progress}

    # -- retrying ----------------------------------------------------------------------------

    def due(self, now=None):
        now = now or time.time()
        every = self.state["every_days"] * 86400
        return [i["id"] for i in self.state["items"] if now - i["last_try"] >= every]

    def retry(self, ids=None):
        """Try the chosen items (or every item that is due) again, in the background."""
        self.studio._require_client()
        with self.lock:
            if self.progress:
                return self.view()
            wanted = set(map(str, ids)) if ids else set(self.due())
            todo = [dict(i) for i in self.state["items"] if i["id"] in wanted]
            self.progress = {"done": 0, "total": len(todo), "title": ""}
        threading.Thread(target=self._run, args=(todo,), name="wishlist-retry", daemon=True).start()
        return self.view()

    def _run(self, todo):
        ctx = {}
        try:
            for item in todo:
                self.progress["title"] = item["title"]
                try:
                    with featured.for_artist(item.get("artist", "")):  # fork v109: credited to the artist wished for
                        self._try(item, ctx)
                except Exception as error:
                    log.info("Wishlist retry of %s failed: %s", item["title"], error)
                    self._update(item["id"], note=str(error))
                self.progress["done"] += 1
        finally:
            with self.lock:
                self.progress = None
            self._persist()

    def _update(self, item_id, **fields):
        with self.lock:
            for i in self.state["items"]:
                if i["id"] == item_id:
                    i.update(fields)

    def _done(self, item, source, note):
        with self.lock:
            self.state["items"] = [i for i in self.state["items"] if i["id"] != item["id"]]
            self.state["found"].insert(0, {"kind": item["kind"], "artist": item["artist"], "title": item["title"],
                                           "source": source, "note": note, "when": time.time()})
            del self.state["found"][100:]

    def _try(self, item, ctx):
        manager = self.studio.download_manager
        check = manager.check([{"kind": item["kind"], "artist": item["artist"], "title": item["title"],
                                "album": item.get("album", "")}])["results"][0]
        check = _with_folder_fallback(manager, item, check)
        owned = None if item.get("keep") else owned_from(item, check)
        if not owned and not item.get("keep") and item["kind"] == "album":  # fork v110: songs of a split single / EP
            songs = self._release_songs(item, [LOOKUPS_PER_CHECK])
            if songs and songs["have"] and not songs["missing"]:
                owned = {"sure": not any(h["close"] or h["downloaded"] for h in songs["have"]),
                         "you_have": [{"title": h["title"], "artist": item["artist"], "album": h["album"]} for h in songs["have"][:3]],
                         "all_songs": True}
            elif songs and songs["have"]:
                self._update(item["id"], partial=songs, last_try=time.time(),
                             note=f"You have {len(songs['have'])} of {songs['total']} songs - use “Keep only the missing songs”")
                return
        if owned:
            # Fork v95: you confirm before a wish leaves the list; it is not searched while it waits.
            self._update(item["id"], owned=owned, last_try=time.time(), note="In your library? Confirm under “Found in your library”")
            return
        with self.lock:
            for i in self.state["items"]:
                if i["id"] == item["id"]:
                    i.pop("owned", None)
        d = self.studio.discography
        # Fork v91: "Search as..." names win; otherwise your own spelling, then MusicBrainz aliases.
        artist = item.get("search_artist") or item["artist"]
        title = item.get("search_title") or item["title"]
        names = [artist]
        if item["kind"] == "album" and not item.get("search_artist"):
            names += d.aliases(artist)
        source = note = None
        for index, name in enumerate(names):
            # fork v105: an alias (index > 0, for an album) is only tried if its own Deezer
            # catalog backs it up - otherwise it's skipped, not searched under blind.
            if index and item["kind"] == "album" and not d.alias_has_album(name, title):
                continue
            artist_ctx = ctx.setdefault(name, {"bandcamp": None, "bandcamp_checked": False})
            if item["kind"] == "album":
                source, note = d._album(name, title, item.get("status"), None, artist_ctx)
            else:
                source, note = d._song(name, title, item.get("album", ""), artist_ctx)
            if source:
                if index:
                    note = f"{note} (searched as “{name}”, an alias on MusicBrainz)"
                break
        if source:
            self._done(item, source, note)
            return
        fields = {"tries": item.get("tries", 1) + 1, "last_try": time.time(), "note": note}
        if item["kind"] == "album" and not item.get("search_artist"):
            credited = d.credited_as(artist, title) or d.album_credit(match.main_artist(artist), title)  # fork v110: Deezer too
            if credited:
                fields["suggest"] = credited
                fields["note"] = f"{note} · MusicBrainz credits an album with this title to “{credited}”"
        self._update(item["id"], **fields)

    def search_as(self, body):
        """Fork v91: one click on a row: look for it under another artist / title right now."""
        body = body or {}
        artist, title = match.clean(body.get("artist") or ""), match.clean(body.get("title") or "")
        item_id = str(body.get("id") or "")
        if not artist or not title:
            raise LibraryError("Type an artist and a title to search as.")
        with self.lock:
            if not any(i["id"] == item_id for i in self.state["items"]):
                raise LibraryError("That wish is no longer on the list.")
            for i in self.state["items"]:
                if i["id"] == item_id:
                    i.update(search_artist=artist, search_title=title, note=f"Searching as “{artist} - {title}”")
                    i.pop("suggest", None)
        self._persist()
        return self.retry([item_id])

    def _schedule(self):
        time.sleep(60)
        while True:
            try:
                if self.due() and not self.progress and self.studio.client:
                    self.retry()
            except Exception as error:
                log.info("Scheduled wishlist retry skipped: %s", error)
            time.sleep(900)
