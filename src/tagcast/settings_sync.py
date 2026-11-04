"""Same choices on both PCs (fork v73).

When a shared folder is set (Downloads › Settings › "This PC and the other PC", for example in OneDrive or Dropbox),
every PC also writes ``tagcast-settings-<pc>.json`` there and merges what the other PCs wrote: keep-separate artists,
the wishlist, genre look-ups, your genre list and the files already uploaded. Merging only ever **adds**: something
you remove on one PC can come back from the other until it is removed there too. Download history already syncs
the same way (downloads.py). Sign-in tokens and keys are never written to the shared folder.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("tagcast")
PREFIX = "tagcast-settings-"
EVERY = 300


def _key(pair):
    return tuple(sorted(str(n).strip() for n in pair))


def merge_pairs(mine, theirs):
    seen, out = set(), []
    for pair in [*mine, *theirs]:
        if isinstance(pair, list) and len(pair) == 2 and all(isinstance(n, str) and n for n in pair) and _key(pair) not in seen:
            seen.add(_key(pair))
            out.append(list(_key(pair)))
    return out


def merge_wishlist(mine, theirs):
    """(items, found) with the other PCs' items added; the same song or album is one item."""
    def ident(i):
        return (i.get("kind"), str(i.get("artist", "")).casefold(), str(i.get("title", "")).casefold(), str(i.get("album", "")).casefold())
    items = list(mine.get("items") or [])
    seen = {ident(i) for i in items}
    for item in theirs.get("items") or []:
        if isinstance(item, dict) and ident(item) not in seen:
            seen.add(ident(item))
            items.append(item)
    found = list(mine.get("found") or [])
    have = {(f.get("kind"), str(f.get("artist", "")).casefold(), str(f.get("title", "")).casefold()) for f in found}
    for f in theirs.get("found") or []:
        if isinstance(f, dict) and (f.get("kind"), str(f.get("artist", "")).casefold(), str(f.get("title", "")).casefold()) not in have:
            found.append(f)
    return items, found


def merge_dict(mine, theirs):
    """Entries the other PC has and this one lacks (this PC's own entries win)."""
    out = dict(mine or {})
    for k, v in (theirs or {}).items():
        out.setdefault(k, v)
    return out


def merge_list(mine, theirs):
    out, seen = list(mine or []), {str(x).casefold() for x in mine or []}
    for x in theirs or []:
        if isinstance(x, str) and x.casefold() not in seen:
            seen.add(x.casefold())
            out.append(x)
    return out


class SettingsSync:
    def __init__(self, studio):
        self.studio = studio
        self.status = {"last": 0, "error": "", "merged": {}}
        self.thread = None
        self.lock = threading.RLock()

    def folder(self):
        value = getattr(self.studio.download_manager, "state", {}).get("sync_folder") if hasattr(self.studio, "download_manager") else ""
        return Path(value) if value else None

    def pc(self):
        return self.studio.download_manager.pc_name()

    def _file(self):
        return f"{PREFIX}{re.sub(r'[^\w .-]', '_', self.pc())}.json"

    def snapshot(self):
        s = self.studio
        pairs = (s._read("artist_separate.json") or {}).get("pairs") or []
        wish = s.wishlist.state
        return {"version": 1, "pc": self.pc(), "written": time.time(), "separate": pairs,
                "wishlist": {"items": wish.get("items", []), "found": wish.get("found", [])[:100]},
                "genre_cache": s.tagclean.state.get("genre_cache", {}), "mb_cache": s.tagclean.state.get("mb_cache", {}),
                "genres": s.uploader.state.get("genres", []), "uploaded": s.uploader.state.get("uploaded", {})}

    def write(self):
        folder = self.folder()
        target = folder / self._file()
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        os.replace(temp, target)

    def read_others(self):
        folder, mine = self.folder(), self._file()
        out = []
        for path in sorted(folder.glob(PREFIX + "*.json")):
            if path.name == mine:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out.append(data)
            except (OSError, ValueError):
                continue
        return out

    def merge(self, other):
        s, changed = self.studio, {}
        pairs = merge_pairs((s._read("artist_separate.json") or {}).get("pairs") or [], other.get("separate") or [])
        if len(pairs) != len((s._read("artist_separate.json") or {}).get("pairs") or []):
            s._write("artist_separate.json", {"pairs": pairs})
            changed["separate"] = len(pairs)
        wish = s.wishlist
        items, found = merge_wishlist(wish.state, other.get("wishlist") or {})
        if len(items) != len(wish.state.get("items", [])) or len(found) != len(wish.state.get("found", [])):
            with wish.lock:
                wish.state["items"], wish.state["found"] = items, found
            wish._persist()
            changed["wishlist"] = len(items)
        tc = s.tagclean
        with tc.lock:
            before = len(tc.state.get("genre_cache", {})) + len(tc.state.get("mb_cache", {}))
            tc.state["genre_cache"] = merge_dict(tc.state.get("genre_cache"), other.get("genre_cache"))
            tc.state["mb_cache"] = merge_dict(tc.state.get("mb_cache"), other.get("mb_cache"))
            after = len(tc.state["genre_cache"]) + len(tc.state["mb_cache"])
        if after != before:
            tc._persist()
            changed["genre_lookups"] = after - before
        up = s.uploader
        with up.lock:
            genres = merge_list(up.state.get("genres"), other.get("genres"))
            uploaded = merge_dict(up.state.get("uploaded"), other.get("uploaded"))
            if len(genres) != len(up.state.get("genres", [])) or len(uploaded) != len(up.state.get("uploaded", {})):
                up.state["genres"], up.state["uploaded"] = genres, uploaded
                changed["upload_settings"] = True
        if changed.get("upload_settings"):
            up._persist()
        return changed

    def sync_now(self):
        with self.lock:
            if not self.folder():
                self.status.update(error="Choose a shared folder first (Downloads › Settings › This PC and the other PC).", merged={})
                return self.view()
            try:
                self.folder().mkdir(parents=True, exist_ok=True)
                merged = {}
                for other in self.read_others():
                    for k, v in self.merge(other).items():
                        merged[k] = v
                self.write()
                self.status.update(last=time.time(), error="", merged=merged)
            except OSError as error:
                self.status.update(error=f"Could not use the shared folder ({error.strerror or error}).")
            return self.view()

    def view(self):
        folder = self.folder()
        others = []
        try:
            others = [d.get("pc", "?") for d in self.read_others()] if folder and folder.is_dir() else []
        except OSError:
            pass
        return {"folder": str(folder or ""), "pc": self.pc() if hasattr(self.studio, "download_manager") else "", "others": others, **self.status}

    def start(self):
        """Sync now and then every few minutes, in the background, while a shared folder is set."""
        if self.thread:
            return

        def loop():
            while True:
                try:
                    if self.folder():
                        self.sync_now()
                except Exception:
                    log.exception("Settings sync failed")
                time.sleep(EVERY)
        self.thread = threading.Thread(target=loop, daemon=True, name="tagcast-settings-sync")
        self.thread.start()
