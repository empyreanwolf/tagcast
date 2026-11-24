"""fork v120: one box for every link (the Missing tab's search box).

Paste any number of links, one per line or run together, and each goes straight to the right downloader with
no preview: YouTube / YouTube Music / Spotify / SoundCloud / Audiomack to More sources, Bandcamp to the Bandcamp
downloader (an artist page = every release), any other page to "From a page" (every row you don't have yet).
The work runs in the background; each link's outcome goes into the activity log."""

import logging
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

from . import activity
from .library import LibraryError
from .moresources import site_of

log = logging.getLogger("tagcast")
MAX_LINKS = 50
PAGE_WAIT = 600   # seconds a page's rows may take to be checked before we give up on it
_URL = re.compile(r"https?://.+?(?=https?://|\s|$)", re.I)


def split_links(text):
    """Every http(s) link in the text, in order, without repeats (pasting into a one-line box glues lines together)."""
    seen, out = set(), []
    for m in _URL.finditer(str(text or "")):
        url = m.group(0).rstrip(".,;)>]\"'")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out[:MAX_LINKS]


def kind_of(url):
    host = (urlparse(url).hostname or "").lower()
    if host == "bandcamp.com" or host.endswith(".bandcamp.com"):
        return "bandcamp"
    return "more" if site_of(url) else "page"


def youtube_mode(url):
    """A plain video, or a video opened from a playlist, is just that song; a playlist/album link is the whole list."""
    q = parse_qs(urlparse(url).query)
    return "song" if q.get("v") or "youtu.be" in url else "list"


class AnyLinks:
    def __init__(self, studio):
        self.studio = studio

    def paste(self, text):
        urls = split_links(text)
        if not urls:
            raise LibraryError("Paste at least one link (YouTube, Spotify, SoundCloud, Audiomack, Bandcamp or any page).")
        plan = [{"line": u, "kind": kind_of(u)} for u in urls]
        threading.Thread(target=self._run, args=(plan,), daemon=True, name="any-links").start()
        counts = {k: sum(1 for p in plan if p["kind"] == k) for k in ("more", "bandcamp", "page")}
        return {"links": plan, "counts": counts}

    # -- background -------------------------------------------------------------------------

    def _run(self, plan):
        groups = {}
        for p in plan:
            if p["kind"] == "more":
                mode = youtube_mode(p["line"]) if site_of(p["line"]) == "YouTube" else "list"
                groups.setdefault(mode, []).append(p["line"])
        for mode, lines in groups.items():
            for i in range(0, len(lines), 20):   # More sources reads at most 20 links per job
                self._safe(lines[i], lambda chunk=lines[i:i + 20], m=mode: self.studio.moresources.add_links("\n".join(chunk), m))
        for p in plan:
            if p["kind"] == "bandcamp":
                self._safe(p["line"], lambda u=p["line"]: self._bandcamp(u))
            elif p["kind"] == "page":
                self._safe(p["line"], lambda u=p["line"]: self._page(u))

    @staticmethod
    def _safe(line, work):
        try:
            work()
        except Exception as error:  # noqa: BLE001 - one bad link never stops the others
            log.info("Pasted link %s failed: %s", line, error)
            activity.record("downloads", "error", f"Link not downloaded: {line[:120]}", str(error)[:300])

    def _bandcamp(self, url):
        bc = self.studio.bandcamp
        path = urlparse(url).path
        releases = [url] if re.match(r"^/(album|track)/", path) else bc.discography(url)["releases"]
        for release in releases:
            self._safe(release, lambda r=release: bc.add(r))
        activity.record("downloads", "ok", f"Bandcamp link queued: {url[:120]}", f"{len(releases)} release(s)")

    def _page(self, url):
        pl = self.studio.pagelist
        job = pl.read(url, "")
        until = time.time() + PAGE_WAIT
        while pl.view(job["id"])["state"] == "running":
            if time.time() > until:
                raise ValueError("The page took too long to check. Open it in “From a page”.")
            time.sleep(1)
        items = pl.view(job["id"])["items"]
        wanted = [i["id"] for i in items if i.get("album") and i.get("status") in ("new", "partly")]
        if not wanted:
            activity.record("downloads", "ok", f"Page link: nothing new on {url[:120]}", f"{len(items)} row(s), you have them all")
            return
        pl.get(job["id"], wanted)
        activity.record("downloads", "ok", f"Page link: {len(wanted)} of {len(items)} row(s) searched", url[:300])
