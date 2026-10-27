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

from .artwork import ArtworkError, from_url
from .library import LibraryError, number
from .sources import SourceError, TheAudioDB

MAX_COVERS_CHECKED = 8
MIN_FACE_FRACTION = 0.12  # a detected face must be at least this fraction of the image's shorter side
ANALYSIS_SIZE = 500       # fetched once per candidate cover, purely to look for a face in it

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
                try:
                    source, note = self._pick(artist_id, name)
                except Exception as error:
                    source, note = "", str(error)
                with self.lock:
                    self.jobs.insert(0, {"artist": name, "artist_id": str(artist_id), "source": source, "note": note})
                    del self.jobs[200:]
                    self.progress["done"] += 1
        finally:
            with self.lock:
                self.running = False
                self.progress = None
            self.stop_flag.clear()

    # -- one artist -----------------------------------------------------------------

    def _covers_of(self, library, artist_id):
        covers = []
        for album_id in library.album_ids():
            album = library.albums[album_id]
            if artist_id not in library.album_artist_ids(album):
                continue
            artwork_id = number(album.get("artwork_id"))
            if artwork_id and artwork_id not in covers:
                covers.append(artwork_id)
        return covers

    def _pick(self, artist_id, name):
        """One artist: face in an album cover, else TheAudioDB, else just a cover."""
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
                return "face", "Found a face in one of this artist's own album covers."

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
            return "cover", "No face or online photo found; used one of this artist's own album covers."

        return "", "No album cover to use yet, and no photo found online."

    def _apply(self, artist_id, name, source):
        # Any failure here (a conflict, a save-lock clash, a bad image) is caught by the
        # caller, logged against this one artist, and the run moves on to the next.
        library, _ = self.studio._library()
        before = number(library.artists.get(artist_id, {}).get("artwork_id"))
        self.studio.change_artwork({"target": "artist", "id": str(artist_id), "label": name,
                                    "before": {"artwork_id": before}, "source": source})
