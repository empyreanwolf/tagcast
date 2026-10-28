"""Artist pictures (fork v27).

Give every artist with no image one, once asked to: a face found in one of their
own album covers, else a real photo from TheAudioDB (free, no key -- the same source
already used for biographies), else just one of their own album covers, which still
looks better than the default blank initial. The existing Change artwork dialog is
untouched and can always override whatever this picks.

Face detection is OpenCV's bundled frontal-face Haar cascade: no extra download, no
extra API key, and a best-effort signal -- it can miss a face (stylised art, side
profile, a small or busy cover) or occasionally flag something that isn't one. That
is fine here: a missed face just falls through to the next, still-reasonable source.
"""

import threading
import time

from . import activity
from .artwork import ArtworkError, from_url
from .library import LibraryError, number
from .sources import SourceError, TheAudioDB

MAX_COVERS_CHECKED = 8
MIN_FACE_FRACTION = 0.12  # a detected face must be at least this fraction of the image's shorter side
ANALYSIS_SIZE = 500       # fetched once per candidate cover, purely to look for a face in it
BATCH = 10                # artists saved before iBroadcast is asked once whether they all took (fork v36)
CONFIRM_TIMEOUT = 300     # seconds to wait for that read-back

_cascade = None
_cascade_lock = threading.Lock()


def _face_cascade():
    global _cascade
    with _cascade_lock:
        if _cascade is None:
            import cv2
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if cascade.empty():
                raise RuntimeError("OpenCV's bundled face cascade could not be loaded.")
            _cascade = cascade
    return _cascade


def detect_face(image_bytes):
    """Best-effort only: any decode or detection problem just means "no face found here"."""
    try:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or min(image.shape[:2]) < 40:
            return False
        minimum = max(20, int(min(image.shape[:2]) * MIN_FACE_FRACTION))
        faces = _face_cascade().detectMultiScale(image, scaleFactor=1.1, minNeighbors=5,
                                                  minSize=(minimum, minimum))
        return len(faces) > 0
    except Exception:
        return False


class ArtistPictures:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.running = False
        self.progress = None   # {"done", "total", "name"} while a run is in progress
        self.jobs = []          # recent per-artist outcomes, most recent first
        self._song_index = None  # (library, {artist_id: [artwork ids of songs they're on]}) for one run
        self._applied = None     # what the last save answered ({"artwork_id", "image"}), for the run to record

    # -- reading ----------------------------------------------------------------

    def _missing(self, library):
        album_artists = {i for a in library.album_ids() for i in library.album_artist_ids(library.albums[a])} - {0}
        return sorted(i for i in album_artists if not number(library.artists.get(i, {}).get("artwork_id")))

    def status(self):
        self.studio._require_client()
        library, _ = self.studio._library()
        with self.lock:
            return {"running": self.running, "progress": dict(self.progress) if self.progress else None,
                    "jobs": list(self.jobs[:50]), "missing": len(self._missing(library))}

    # -- running ------------------------------------------------------------------

    def start(self):
        self.studio._require_client()
        with self.lock:
            if self.running:
                raise LibraryError("Already filling in artist pictures.")
            self.running = True
            self.stop_flag.clear()
            self.progress = {"done": 0, "total": 0, "name": ""}
        threading.Thread(target=self._run, name="artist-pictures", daemon=True).start()
        return {"started": True}

    def stop(self):
        with self.lock:
            if not self.running:
                raise LibraryError("Nothing is running.")
        self.stop_flag.set()
        return {"stopping": True}

    def _run(self):
        pending = []  # saved but not yet confirmed by iBroadcast: this batch's job entries
        try:
            library, _ = self.studio._library()
            missing = self._missing(library)
            with self.lock:
                self.progress = {"done": 0, "total": len(missing), "name": ""}
            for artist_id in missing:
                if self.stop_flag.is_set():
                    break
                name = library.artist_name(artist_id)
                with self.lock:
                    self.progress["name"] = name
                self._applied = None
                failed = False
                try:
                    source, note = self._pick(artist_id, name)
                except Exception as error:
                    source, note, failed = "", str(error), True
                activity.record("pictures", "ok" if source else "warn" if note != "Stopped." else "info",
                                f"{name}: {note}", source=source or "skipped")
                applied = self._applied if source else None
                job = {"artist": name, "artist_id": str(artist_id), "source": source, "note": note,
                       "status": "saving" if applied else "failed" if failed else "skipped",
                       "artwork_id": applied.get("artwork_id") if applied else None,
                       "image": applied.get("image") if applied else None}
                with self.lock:
                    self.jobs.insert(0, job)
                    del self.jobs[200:]
                    self.progress["done"] += 1
                if applied:
                    pending.append(job)
                if len(pending) >= BATCH:
                    self._confirm(pending)
                    pending = []
        finally:
            try:
                if pending:
                    self._confirm(pending)  # also after Stop and after a crash: what was saved gets checked
            finally:
                with self.lock:
                    done = (self.progress or {}).get("done", 0)
                    self.running = False
                    self.progress = None
                activity.record("pictures", "info", f"Artist pictures run ended after {done} artists")
                self.stop_flag.clear()

    def _confirm(self, jobs):
        """Ask iBroadcast, once for the whole batch, whether the images really are there.

        Each artist's line then says Saved (confirmed) or Not showing yet, instead of only
        saying that a request was sent. This is also the library download the next batch
        starts from, so it costs nothing extra.
        """
        with self.lock:
            if self.progress is not None:
                self.progress["confirming"] = True
        statuses, problem = {}, ""
        try:
            job_id = self.studio.check_artist_images({j["artist_id"]: j["artwork_id"] for j in jobs})
            end = time.monotonic() + CONFIRM_TIMEOUT
            while True:
                state = self.studio.job(job_id)
                if state.get("state") != "checking":
                    break
                if time.monotonic() > end:
                    state = {"state": "error", "error": "iBroadcast took too long to answer."}
                    break
                time.sleep(.5)
            if state.get("state") == "done":
                statuses = {r["id"]: r["status"] for r in state.get("results") or []}
            else:
                problem = state.get("error") or "The result could not be read back."
        except Exception as error:
            problem = str(error)
        with self.lock:
            for job in jobs:
                job["status"] = statuses.get(job["artist_id"], "unverified")
            if self.progress is not None:
                self.progress["confirming"] = False
        saved = sum(1 for j in jobs if j["status"] == "saved")
        activity.record("pictures", "ok" if saved == len(jobs) else "warn",
                        f"iBroadcast confirmed {saved} of {len(jobs)} artist pictures"
                        + (f" ({problem})" if problem else ""),
                        "\n".join(f"{j['status']}: {j['artist']}" for j in jobs), source="confirm")

    # -- one artist -----------------------------------------------------------------

    def _covers_of(self, library, artist_id):
        """This artist's album covers, then covers of any song they're on (fork v33).

        An album record often has no cover of its own: iBroadcast shows its first track's
        artwork instead, so that counts as the album's cover. After the albums come the
        covers of songs the artist performs on, featured guests included, so an artist
        with no usable album still gets one of their songs' covers."""
        covers = []
        for album_id in library.album_ids():
            album = library.albums[album_id]
            if artist_id not in library.album_artist_ids(album):
                continue
            artwork_id = number(album.get("artwork_id")) or library.album_artwork(library.active_track_ids(album_id))
            if artwork_id and artwork_id not in covers:
                covers.append(artwork_id)
        for artwork_id in self._song_covers(library).get(artist_id, ()):
            if artwork_id not in covers:
                covers.append(artwork_id)
        return covers

    def _song_covers(self, library):
        """{artist_id: song artwork ids}, built once per library rather than once per artist."""
        cached = self._song_index
        if cached and cached[0] is library:
            return cached[1]
        index = {}
        for track_id, track in library.tracks.items():
            artwork_id = number(track.get("artwork_id"))
            if not artwork_id or track.get("trashed"):
                continue
            for artist_id in library.performer_ids(track):
                if artist_id:
                    found = index.setdefault(artist_id, [])
                    if artwork_id not in found:
                        found.append(artwork_id)
        self._song_index = (library, index)
        return index

    def _pick(self, artist_id, name):
        """One artist: face in an album or song cover, else TheAudioDB, else just a cover."""
        library, _ = self.studio._library()
        if number(library.artists.get(artist_id, {}).get("artwork_id")):
            return "", "Already has an image by the time this artist was reached."
        covers = self._covers_of(library, artist_id)

        for artwork_id in covers[:MAX_COVERS_CHECKED]:
            if self.stop_flag.is_set():
                return "", "Stopped."
            try:
                data, _, _ = from_url(library.art_url(artwork_id, ANALYSIS_SIZE))
            except ArtworkError:
                continue
            if detect_face(data):
                self._apply(artist_id, name, {"artwork_id": artwork_id})
                return "face", "Found a face in the cover of one of this artist's albums or songs."

        if "theaudiodb" not in self.studio.disabled_sources():
            try:
                found = TheAudioDB(self.studio.lookup.http, {}).artist_image(name)
            except SourceError:
                found = None  # not fatal: a cover is still a reasonable fallback below
            if found and found["status"] == "ready":
                self._apply(artist_id, name, {"url": found["image"]})
                return "theaudiodb", "Used a photo from TheAudioDB."

        if covers:
            self._apply(artist_id, name, {"artwork_id": covers[0]})
            return "cover", "No face or online photo found; used the cover of one of this artist's albums or songs."

        return "", "None of this artist's albums or songs has a cover yet, and no photo was found online."

    def _apply(self, artist_id, name, source):
        # Any failure here (a conflict, a save-lock clash, a bad image) is caught by the
        # caller, logged against this one artist, and the run moves on to the next.
        library, _ = self.studio._library()
        before = number(library.artists.get(artist_id, {}).get("artwork_id"))
        self._applied = self.studio.apply_artist_image(
            {"target": "artist", "id": str(artist_id), "label": name,
             "before": {"artwork_id": before}, "source": source})
