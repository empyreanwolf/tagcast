"""Wishlist with retry (fork v17).

Albums and songs that "Get the missing" could not find on Galaxy, Bandcamp or Soulseek (including
a Soulseek job that failed, after Galaxy had already found nothing) go on the wishlist. While
Tagcast is open it tries them again every 3 days (adjustable), in the same source order; Soulseek
especially changes as people come online. A found item leaves the wishlist and goes to the queue
(still through the duplicate check); an item you meanwhile have is removed. State: ``wishlist.json``.
"""

import logging
import secrets
import threading
import time

from . import artist_match as match
from .downloads import album_key, song_key
from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "wishlist.json"
MAX_ITEMS = 2000


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
        if check["status"] == "have":
            self._done(item, "Library", "You have it now")
            return
        d = self.studio.discography
        ctx.setdefault(item["artist"], {"bandcamp": None, "bandcamp_checked": False})
        artist_ctx = ctx[item["artist"]]
        if item["kind"] == "album":
            source, note = d._album(item["artist"], item["title"], item.get("status"), None, artist_ctx)
        else:
            source, note = d._song(item["artist"], item["title"], item.get("album", ""), artist_ctx)
        if source:
            self._done(item, source, note)
        else:
            self._update(item["id"], tries=item.get("tries", 1) + 1, last_try=time.time(), note=note)

    def _schedule(self):
        time.sleep(60)
        while True:
            try:
                if self.due() and not self.progress and self.studio.client:
                    self.retry()
            except Exception as error:
                log.info("Scheduled wishlist retry skipped: %s", error)
            time.sleep(900)
