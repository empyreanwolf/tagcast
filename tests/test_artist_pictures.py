"""Give every artist with no image one (fork v27): a face found in their own album
art, else a real TheAudioDB photo, else just one of their own covers."""

import time
import unittest
from unittest.mock import Mock, patch

from tagcast.artist_pictures import ArtistPictures, detect_face
from tagcast.library import ConflictError, Library
from tagcast.sources import SourceError, TheAudioDB


def data():
    artists = {1: "Two Covers", 2: "One Cover", 3: "No Cover At All", 4: "Already Has One"}
    albums = {
        10: {"name": "Album A", "artist_id": 1, "artwork_id": 500, "tracks": [100]},
        11: {"name": "Album B", "artist_id": 1, "artwork_id": 501, "tracks": [101]},
        12: {"name": "Album C", "artist_id": 2, "artwork_id": 600, "tracks": [102]},
        13: {"name": "Album D", "artist_id": 3, "tracks": [103]},
        14: {"name": "Album E", "artist_id": 4, "artwork_id": 700, "tracks": [104]},
    }
    tracks = {100: {"title": "T", "artist_id": 1, "album_id": 10}, 101: {"title": "T", "artist_id": 1, "album_id": 11},
              102: {"title": "T", "artist_id": 2, "album_id": 12}, 103: {"title": "T", "artist_id": 3, "album_id": 13},
              104: {"title": "T", "artist_id": 4, "album_id": 14}}
    artist_rows = {str(i): {"name": n, **({"artwork_id": 999} if i == 4 else {})} for i, n in artists.items()}
    return {"library": {"artists": artist_rows, "albums": {str(i): a for i, a in albums.items()},
                        "tracks": {str(i): t for i, t in tracks.items()}}}


class LocalStudio:
    def __init__(self, disabled=None, change_artwork=None):
        self.library = Library(data())
        self._disabled = list(disabled or [])
        self.lookup = type("Lookup", (), {"http": object()})()
        self.calls = []
        self.checked = []   # the {artist_id: artwork_id} of each read-back asked for
        self.drop = set()   # artists whose image iBroadcast "didn't take"
        self.apply_artist_image = Mock(side_effect=change_artwork or self._default_change)
        self.artist_pictures = ArtistPictures(self)

    def _require_client(self):
        pass

    def _library(self):
        return self.library, "user"

    def disabled_sources(self):
        return self._disabled

    def _default_change(self, body):
        self.calls.append(body)
        artist_id, source = int(body["id"]), body["source"]
        new_id = source.get("artwork_id") or 9000 + artist_id
        self.library.artists.rows[artist_id]["artwork_id"] = new_id
        return {"artwork_id": new_id, "image": f"https://art.test/{new_id}-300"}

    def check_artist_images(self, expected):
        self.checked.append(dict(expected))
        self._results = [{"kind": "artist", "id": str(i), "status": "unverified" if int(i) in self.drop else "saved"}
                         for i in expected]
        return "job1"

    def job(self, job_id):
        return {"state": "done", "results": self._results}


def finished(pictures):
    for _ in range(500):
        if not pictures.running:
            return
        time.sleep(.01)
    raise AssertionError("artist pictures run did not finish")


class DetectFaceTests(unittest.TestCase):
    def test_garbage_or_tiny_input_never_raises_and_just_means_no_face(self):
        self.assertFalse(detect_face(b"not an image"))
        self.assertFalse(detect_face(b""))


class PickOneArtistTests(unittest.TestCase):
    def test_a_face_in_an_album_cover_wins_and_reuses_its_artwork_id_without_an_upload(self):
        studio = LocalStudio()
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=True) as detect:
            source, note = studio.artist_pictures._pick(1, "Two Covers")
        self.assertEqual(source, "face")
        self.assertEqual(studio.calls[-1]["source"], {"artwork_id": 500})  # the first of this artist's covers
        detect.assert_called_once()

    def test_no_face_falls_back_to_a_theaudiodb_photo(self):
        studio = LocalStudio()
        found = {"status": "ready", "image": "https://example.test/photo.jpg"}
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", return_value=found) as lookup:
            source, note = studio.artist_pictures._pick(1, "Two Covers")
        self.assertEqual(source, "theaudiodb")
        self.assertEqual(studio.calls[-1]["source"], {"url": "https://example.test/photo.jpg"})
        lookup.assert_called_once_with("Two Covers")

    def test_no_face_and_no_theaudiodb_photo_falls_back_to_a_cover(self):
        studio = LocalStudio()
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            source, note = studio.artist_pictures._pick(2, "One Cover")
        self.assertEqual(source, "cover")
        self.assertEqual(studio.calls[-1]["source"], {"artwork_id": 600})

    def test_theaudiodb_failure_is_not_fatal_and_still_falls_back_to_a_cover(self):
        studio = LocalStudio()
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", side_effect=SourceError("down")):
            source, note = studio.artist_pictures._pick(2, "One Cover")
        self.assertEqual(source, "cover")

    def test_disabled_theaudiodb_is_never_called(self):
        studio = LocalStudio(disabled=["theaudiodb"])
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image") as lookup:
            source, note = studio.artist_pictures._pick(2, "One Cover")
        lookup.assert_not_called()
        self.assertEqual(source, "cover")

    def test_no_cover_and_no_theaudiodb_photo_leaves_the_artist_untouched(self):
        studio = LocalStudio()
        with patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            source, note = studio.artist_pictures._pick(3, "No Cover At All")
        self.assertEqual(source, "")
        self.assertEqual(studio.calls, [])

    def _song_cover_studio(self):
        # fork v33: Album D has no cover field of its own, but its track has artwork (what
        # iBroadcast shows as the album's cover). "Guest Only" has an album with no artwork
        # anywhere, but is a featured guest on Album A's track, which has artwork.
        studio = LocalStudio()
        lib = data()["library"]
        lib["tracks"]["103"]["artwork_id"] = 800
        lib["tracks"]["100"]["artwork_id"] = 500
        lib["tracks"]["100"]["artists_additional"] = [{"artist_id": 5, "phrase": "feat.", "type": "artist"}]
        lib["artists"]["5"] = {"name": "Guest Only"}
        lib["albums"]["15"] = {"name": "Album F", "artist_id": 5, "tracks": [105]}
        lib["tracks"]["105"] = {"title": "T", "artist_id": 5, "album_id": 15}
        studio.library = Library({"library": lib})
        return studio

    def test_an_album_with_no_cover_field_uses_its_tracks_artwork(self):
        studio = self._song_cover_studio()
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            source, note = studio.artist_pictures._pick(3, "No Cover At All")
        self.assertEqual(source, "cover", note)
        self.assertEqual(studio.calls[-1]["source"], {"artwork_id": 800})

    def test_an_artist_with_no_album_cover_uses_a_song_they_feature_on(self):
        studio = self._song_cover_studio()
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            source, note = studio.artist_pictures._pick(5, "Guest Only")
        self.assertEqual(source, "cover", note)
        self.assertEqual(studio.calls[-1]["source"], {"artwork_id": 500})

    def test_a_download_failure_on_one_cover_tries_the_next_before_falling_back(self):
        from tagcast.artwork import ArtworkError
        studio = LocalStudio()
        calls = []

        def flaky(url):
            calls.append(url)
            if "/artwork/500-" in url:
                raise ArtworkError("boom")
            return b"data", "n", "image/jpeg"

        with patch("tagcast.artist_pictures.from_url", side_effect=flaky), \
             patch("tagcast.artist_pictures.detect_face", return_value=True):
            source, note = studio.artist_pictures._pick(1, "Two Covers")
        self.assertEqual(source, "face")
        self.assertEqual(studio.calls[-1]["source"], {"artwork_id": 501})  # the second cover, after the first failed

    def test_stopping_mid_scan_skips_the_remaining_covers_without_a_fallback(self):
        studio = LocalStudio()
        studio.artist_pictures.stop_flag.set()
        with patch("tagcast.artist_pictures.from_url") as from_url:
            source, note = studio.artist_pictures._pick(1, "Two Covers")
        self.assertEqual(source, "")
        self.assertEqual(note, "Stopped.")
        from_url.assert_not_called()
        self.assertEqual(studio.calls, [])


class RunTests(unittest.TestCase):
    def test_only_artists_without_an_image_are_queued_and_the_run_logs_each_outcome(self):
        studio = LocalStudio()
        self.assertEqual(studio.artist_pictures.status()["missing"], 3)  # not "Already Has One"
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=False), \
             patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            studio.artist_pictures.start()
            finished(studio.artist_pictures)
        names = {job["artist"] for job in studio.artist_pictures.jobs}
        self.assertEqual(names, {"Two Covers", "One Cover", "No Cover At All"})
        self.assertNotIn("Already Has One", names)
        self.assertEqual(studio.artist_pictures.status()["missing"], 1)  # "No Cover At All" still has none

    def test_cannot_start_a_second_run_while_one_is_already_going(self):
        studio = LocalStudio()
        release = []

        def slow_change(body):
            while not release:
                time.sleep(.01)
            return studio._default_change(body)

        studio.apply_artist_image = Mock(side_effect=slow_change)
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=True):
            studio.artist_pictures.start()
            try:
                with self.assertRaises(Exception):
                    studio.artist_pictures.start()
            finally:
                release.append(True)
                studio.artist_pictures.stop()
                finished(studio.artist_pictures)

    def test_a_conflict_on_one_artist_is_logged_and_the_run_continues(self):
        studio = LocalStudio()

        def change(body):
            if body["id"] == "1":
                raise ConflictError("changed elsewhere")
            return studio._default_change(body)

        studio.apply_artist_image = Mock(side_effect=change)
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=True):
            studio.artist_pictures.start()
            finished(studio.artist_pictures)
        by_name = {job["artist"]: job for job in studio.artist_pictures.jobs}
        self.assertEqual(by_name["Two Covers"]["source"], "")
        self.assertIn("changed", by_name["Two Covers"]["note"])
        self.assertEqual(by_name["One Cover"]["source"], "face")  # other artists still get processed


class ConfirmationTests(unittest.TestCase):
    """Fork v36: a saved picture is only called Saved once iBroadcast has been asked, once per batch."""

    def _run(self, studio):
        with patch("tagcast.artist_pictures.from_url", return_value=(b"data", "n", "image/jpeg")), \
             patch("tagcast.artist_pictures.detect_face", return_value=True), \
             patch.object(TheAudioDB, "artist_image", return_value={"status": "missing", "image": ""}):
            studio.artist_pictures.start()
            finished(studio.artist_pictures)

    def test_each_artist_is_confirmed_and_carries_the_image_the_page_needs(self):
        studio = LocalStudio()
        self._run(studio)
        by_name = {job["artist"]: job for job in studio.artist_pictures.jobs}
        self.assertEqual({j["status"] for j in by_name.values()} - {"saved", "skipped"}, set())
        self.assertEqual(by_name["Two Covers"]["status"], "saved")
        self.assertEqual(by_name["Two Covers"]["artwork_id"], 500)
        self.assertEqual(by_name["Two Covers"]["image"], "https://art.test/500-300")
        self.assertEqual(by_name["No Cover At All"]["status"], "skipped")

    def test_the_whole_run_is_confirmed_in_one_readback_when_it_fits_one_batch(self):
        studio = LocalStudio()
        self._run(studio)
        self.assertEqual(len(studio.checked), 1)
        self.assertEqual(set(studio.checked[0]), {"1", "2"})

    def test_a_picture_iBroadcast_did_not_take_says_so(self):
        studio = LocalStudio()
        studio.drop = {2}
        self._run(studio)
        by_name = {job["artist"]: job for job in studio.artist_pictures.jobs}
        self.assertEqual(by_name["One Cover"]["status"], "unverified")
        self.assertEqual(by_name["Two Covers"]["status"], "saved")

    def test_a_failed_readback_leaves_the_pictures_unconfirmed_rather_than_saved(self):
        studio = LocalStudio()
        studio.job = Mock(return_value={"state": "error", "error": "no answer"})
        self._run(studio)
        statuses = {job["status"] for job in studio.artist_pictures.jobs if job["artwork_id"]}
        self.assertEqual(statuses, {"unverified"})

    def test_a_save_that_failed_is_marked_failed_not_skipped(self):
        studio = LocalStudio()

        def change(body):
            if body["id"] == "1":
                raise ConflictError("changed elsewhere")
            return studio._default_change(body)

        studio.apply_artist_image = Mock(side_effect=change)
        self._run(studio)
        by_name = {job["artist"]: job for job in studio.artist_pictures.jobs}
        self.assertEqual(by_name["Two Covers"]["status"], "failed")
        self.assertEqual(by_name["One Cover"]["status"], "saved")
        self.assertNotIn("1", studio.checked[0])  # nothing was claimed for the artist that failed

    def test_large_runs_are_confirmed_a_batch_at_a_time(self):
        import tagcast.artist_pictures as module
        studio = LocalStudio()
        with patch.object(module, "BATCH", 1):
            self._run(studio)
        self.assertEqual([set(c) for c in studio.checked], [{"1"}, {"2"}])  # artists run in id order

    def test_stopping_still_confirms_what_was_saved(self):
        studio = LocalStudio()
        original = studio._default_change

        def change(body):
            result = original(body)
            studio.artist_pictures.stop_flag.set()  # Stop pressed right after the first save
            return result

        studio.apply_artist_image = Mock(side_effect=change)
        self._run(studio)
        self.assertEqual(len(studio.checked), 1)
        self.assertEqual(len(studio.checked[0]), 1)
