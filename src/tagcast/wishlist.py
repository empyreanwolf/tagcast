"""Wishlist with retry (fork v17).

Albums and songs that "Get the missing" could not find on Galaxy, Bandcamp or Soulseek (including
a Soulseek job that failed, after Galaxy had already found nothing) go on the wishlist. While
Tagcast is open it tries them again every 3 days (adjustable), in the same source order; Soulseek
especially changes as people come online. A found item leaves the wishlist and goes to the queue
(still through the duplicate check). An item you meanwhile have is tagged "In your library?" and
leaves the list once you confirm it (fork v95; before, it was removed without asking). State: ``wishlist.json``.
"""

import logging
import secrets
import threading
import time

from . import artist_match as match
from .downloads import album_key, artist_keys, song_key
from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "wishlist.json"
MAX_ITEMS = 2000
CHECK_BATCH = 500


def owned_from(item, check):
    """Fork v95: {"sure", "you_have"} when the library check says you have this wish, else None.
    Sure = the same title (album: the same album, not another edition) by the main artist; the rest is a
    "maybe" you tick yourself. An album you only partly have is not owned (Complete my albums covers it)."""
    if check.get("status") != "have":
        return None
    rows = [r for r in check.get("you_have") or [] if r.get("where") == "ibroadcast"]
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
        if scheduler:
            threading.Thread(target=self._schedule, name="wishlist", daemon=True).start()

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    @staticmethod
    def _key(kind, artist, title):
        return f"{kind}|{match.match_key(artist)}|{album_key(title) if kind == 'album' else song_key(title)}"

    # -- changing the list ---------------------------------------------------------------------

    def add(self, kind, artist, title, album="", status="missing", note=""):
        kind = "album" if kind == "album" else "song"
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

    def check_library(self):
        """Fork v95: "Check my library": compare every wish with iBroadcast and tag the ones you have."""
        manager = self.studio.download_manager
        with self.lock:
            todo = [dict(i) for i in self.state["items"] if not i.get("keep")]
        found = {}
        for start in range(0, len(todo), CHECK_BATCH):
            chunk = todo[start:start + CHECK_BATCH]
            results = manager.check([{"kind": i["kind"], "artist": i["artist"], "title": i["title"],
                                      "album": i.get("album", "")} for i in chunk])["results"]
            for item, check in zip(chunk, results):
                found[item["id"]] = owned_from(item, check)
        with self.lock:
            for i in self.state["items"]:
                if i["id"] in found:
                    if found[i["id"]]:
                        i["owned"] = found[i["id"]]
                    else:
                        i.pop("owned", None)
        self._persist()
        view = self.view()
        view["checked"] = {"total": len(todo), "found": sum(1 for v in found.values() if v)}
        return view

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
        owned = None if item.get("keep") else owned_from(item, check)
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
            credited = d.credited_as(artist, title)
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
