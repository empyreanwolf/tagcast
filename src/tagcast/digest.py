"""Daily Digest (fork v24).

A "Daily Digest" page, its own top-level sidebar entry, that pulls together what the other
pages already track into one read-first page instead of four separate checks:

- **New releases**: the undismissed results `new_releases` already has (its own 7-day
  scheduled check keeps these current; this page just surfaces them).
- **Wishlist, just found**: the most recent entries from `wishlist.state["found"]`, flagged
  as new until "Mark seen" is pressed -- the same manual acknowledgement `new_releases`
  already uses for its own unseen count, rather than silently clearing on every page view.
- **Chart movement**: for the six fixed charts (not the Browse tab's open-ended country/
  genre combinations), which songs are on the chart now that weren't the last time this
  page looked. Charts itself only keeps an in-memory 6-hour cache with no history, so this
  is the one genuinely new piece of state here: ``digest.json`` persists each chart's last
  seen set of (artist, title) pairs, and a diff against the current ``charts.view()`` rows
  is "chart movement". The first time a chart is ever seen there is nothing to diff against
  yet, so it is reported as a first scan with no movement rather than listing the whole
  chart as "new".
- **Recommendations**: the top of each of Recommendations' own two lists (Deezer-related,
  Gemini-based), read via their existing 24-hour cache -- opening this page doesn't force a
  fresh scan of either.

Every piece degrades independently: if one source errors (not signed in, a chart's site
changed, no Gemini key yet) its section shows that error and the rest of the page still
renders. Nothing here fetches songs or starts downloads; that still happens from each
source's own page ("Get it" on Charts/Recommendations, "Get the missing" on New releases).
State: ``digest.json`` (chart snapshots, and when wishlist "just found" was last acknowledged).
"""

import logging
import threading
import time

from . import artist_match as match
from .charts import CHARTS
from .library import LibraryError, text

log = logging.getLogger("tagcast")
STATE_FILE = "digest.json"
MAX_NEW_ENTRIES = 10
MAX_FOUND_SHOWN = 20
MAX_RECOMMENDATION_ROWS = 5


def _row_key(row):
    return f"{match.match_key(row.get('artist'))}|{match.match_key(row.get('title'))}"


class Digest:
    def __init__(self, studio):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("chart_snapshots", {})  # chart_id -> {"keys": [...], "when": ts}
        self.state.setdefault("found_seen", 0)  # wishlist "found" entries at/before this time are no longer "new"
        self.lock = threading.RLock()

    def _persist(self):
        with self.lock:
            self.studio._write(STATE_FILE, self.state)

    @staticmethod
    def _safe(fn):
        try:
            return {"ok": True, **fn()}
        except Exception as error:  # every source here has its own specific error types; keep the page up regardless
            log.info("Digest section failed: %s", error)
            return {"ok": False, "error": str(error)}

    # -- sections --------------------------------------------------------------------------

    def _new_releases(self):
        data = self.studio.new_releases.view()
        return {"results": data["results"][:MAX_NEW_ENTRIES], "unseen": data["unseen"], "total": len(data["results"])}

    def _wishlist_found(self):
        with self.lock:
            seen_before = self.state["found_seen"]
        found = [dict(item) for item in (self.studio.wishlist.state.get("found") or [])[:MAX_FOUND_SHOWN]]
        for item in found:
            item["is_new"] = item.get("when", 0) > seen_before
        return {"items": found, "new_count": sum(1 for i in found if i["is_new"])}

    def _chart_movement(self, chart_id):
        data = self.studio.charts.view(chart_id)
        if data.get("error"):
            raise LibraryError(data["error"])
        rows = data.get("rows") or []
        current_keys = {_row_key(r) for r in rows}
        with self.lock:
            snapshot = self.state["chart_snapshots"].get(chart_id)
            first_scan = snapshot is None
            previous = set(snapshot["keys"]) if snapshot else set()
            new_entries = [] if first_scan else [r for r in rows if _row_key(r) not in previous][:MAX_NEW_ENTRIES]
            self.state["chart_snapshots"][chart_id] = {"keys": sorted(current_keys), "when": time.time()}
        return {"chart": chart_id, "label": CHARTS[chart_id]["label"], "first_scan": first_scan, "new_entries": new_entries}

    def _recommendations(self, source):
        data = self.studio.recommendations.view(source)
        if data.get("error"):
            raise LibraryError(data["error"])
        return {"rows": data["rows"][:MAX_RECOMMENDATION_ROWS], "total": len(data["rows"])}

    # -- reading --------------------------------------------------------------------------

    def view(self):
        new_releases = self._safe(self._new_releases)
        wishlist_found = self._safe(self._wishlist_found)
        charts = {chart_id: self._safe(lambda chart_id=chart_id: self._chart_movement(chart_id)) for chart_id in CHARTS}
        recommendations = {source: self._safe(lambda source=source: self._recommendations(source))
                            for source in ("deezer", "gemini")}
        self._persist()
        return {"new_releases": new_releases, "wishlist_found": wishlist_found, "charts": charts,
                "recommendations": recommendations}

    def mark_found_seen(self):
        with self.lock:
            self.state["found_seen"] = time.time()
        self._persist()
        return self.view()
