"""New-release alerts (fork v16)."""

import time
import unittest

from tagcast.library import Library, LibraryError
from tagcast.releases import NewReleases, date_ts, library_artists

from test_discography import ANSWERS
from test_discography import LocalStudio as DiscographyStudio
from test_downloads import data as library_data


def recent(days):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))


class LocalStudio(DiscographyStudio):
    def __init__(self):
        super().__init__()
        self.new_releases = NewReleases(self, scheduler=False)


def wait(nr):
    for _ in range(200):
        if not nr.progress:
            return nr.view()
        time.sleep(0.02)
    raise AssertionError("check did not finish")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.nr = self.studio.new_releases
        albums = dict(ANSWERS["/artist/1/albums"])
        albums["data"] = albums["data"] + [
            {"id": 14, "title": "Moon Music", "release_date": recent(10), "record_type": "album"},
            {"id": 15, "title": "Old Single", "release_date": recent(400), "record_type": "single"}]
        self.studio.http.answers["/artist/1/albums"] = albums

    def test_first_check_lists_the_last_90_days(self):
        self.nr.save_settings({"add": "Coldplay", "auto": False})
        view = wait(self.nr.check() and self.nr)
        self.assertEqual([(r["title"], r["status"]) for r in view["results"]], [("Moon Music", "missing")])
        self.assertEqual(view["unseen"], 1)
        self.assertTrue(view["last_check"])
        # The next check finds nothing new (and doesn't list Moon Music twice).
        wait(self.nr.check() and self.nr)
        self.assertEqual(len(self.nr.view()["results"]), 1)
        self.assertEqual(self.nr.mark_seen()["unseen"], 0)
        rid = self.nr.view()["results"][0]["id"]
        self.assertEqual(self.nr.dismiss([rid])["results"], [])

    def test_a_later_release_after_the_previous_check(self):
        self.nr.save_settings({"add": "Coldplay", "auto": False})
        self.nr.state["checked"]["coldplay"] = time.time() - 5 * 86400  # checked 5 days ago
        wait(self.nr.check() and self.nr)
        self.assertEqual(self.nr.view()["results"], [])  # Moon Music came out 10 days ago

    def test_settings_and_library_artists(self):
        with self.assertRaises(LibraryError):
            self.nr.save_settings({"every_days": 0})
        view = self.nr.save_settings({"every_days": 14, "add": "Coldplay"})
        self.assertEqual((view["every_days"], view["artists"]), (14, ["Coldplay"]))
        self.nr.save_settings({"add": "COLDPLAY"})
        self.assertEqual(self.nr.view()["artists"], ["Coldplay"])
        self.assertEqual(self.nr.save_settings({"remove": "coldplay"})["artists"], [])
        lib = Library(library_data())
        self.assertEqual(library_artists(lib, 1), ["Coldplay", "Company Flow", "Faye Wong 王菲"])
        self.assertEqual(library_artists(lib), [])

    def test_dates(self):
        self.assertGreater(date_ts("2024-05-01"), date_ts("2024"))
        self.assertEqual(date_ts(""), 0.0)


if __name__ == "__main__":
    unittest.main()


class GenreScopeTests(unittest.TestCase):
    def test_only_the_chosen_genres_count_toward_the_three_albums(self):
        from tagcast.releases import library_artists

        class Lib:
            albums = {i: {"artist_id": 1, "name": f"A{i}"} for i in range(1, 5)}
            tracks = {i: {"genre": "Underground Hip Hop" if i < 4 else "Pop"} for i in range(1, 5)}

            def album_ids(self): return list(self.albums)
            def artist_name(self, _id): return "Rapper"
            def active_track_ids(self, album_id): return [album_id]

        self.assertEqual(library_artists(Lib(), genres=["Underground Hip Hop"]), ["Rapper"])
        self.assertEqual(library_artists(Lib(), genres=["Pop"]), [])
        self.assertEqual(library_artists(Lib()), ["Rapper"])
