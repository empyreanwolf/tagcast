"""Complete my albums (fork v18)."""

import time
import unittest

from tagcast.completion import Completion, gaps
from tagcast.library import Library, LibraryError

from test_discography import FakeHttp
from test_downloads import LocalStudio as DownloadsStudio
from test_downloads import data as library_data


def library():
    raw = library_data()
    lib = raw["library"]
    # Funcrusher Plus: tracks 1, 2 and 5 (3 and 4 missing).
    for tid, n in (("102", 1), ("103", 2), ("104", 5)):
        lib["tracks"][tid]["track"] = n
    lib["tracks"]["105"]["track"] = 1  # Parachutes: 1 track, Deezer says 10
    lib["tracks"]["100"]["track"], lib["tracks"]["101"]["track"] = 1, 2  # Fable: complete
    return raw


ANSWERS = {
    "/search/album": lambda p: {"data": [
        {"title": "Parachutes", "artist": {"name": "Coldplay"}, "nb_tracks": 10, "id": 7, "cover_medium": "c"},
        {"title": "Parachutes", "artist": {"name": "Coldplay"}, "nb_tracks": 12, "id": 8},
        {"title": "Funcrusher Plus", "artist": {"name": "Company Flow"}, "nb_tracks": 4, "id": 9},  # too few: ignored
        {"title": "Fable", "artist": {"name": "Faye Wong 王菲"}, "nb_tracks": 2, "id": 10}]},
    "/album/7/tracks": {"data": [{"title": "Don't Panic", "track_position": 1}, {"title": "Yellow", "track_position": 5}]},
}


class LocalStudio(DownloadsStudio):
    def __init__(self):
        super().__init__()
        self.library = Library(library())
        self.http = FakeHttp(dict(ANSWERS))
        self.completion = Completion(self, http=self.http)


def wait(c):
    for _ in range(200):
        if not c.progress:
            return c.view()
        time.sleep(0.02)
    raise AssertionError("scan did not finish")


class CompletionTests(unittest.TestCase):
    def test_gaps(self):
        self.assertEqual(gaps([{"number": 1, "disc": 1}, {"number": 4, "disc": 1}, {"number": 2, "disc": 2}]), {1: [2, 3], 2: [1]})
        self.assertEqual(gaps([{"number": 0, "disc": 1}]), {})

    def test_scan_finds_partial_albums(self):
        studio = LocalStudio()
        studio.completion.scan()
        view = wait(studio.completion)
        rows = {r["title"]: r for r in view["albums"]}
        self.assertEqual(set(rows), {"Funcrusher Plus", "Parachutes"})
        self.assertEqual((rows["Parachutes"]["have"], rows["Parachutes"]["expected"], rows["Parachutes"]["basis"]), (1, 10, "Deezer"))
        self.assertEqual((rows["Funcrusher Plus"]["expected"], rows["Funcrusher Plus"]["gaps"]), (5, {"1": [3, 4]}))
        self.assertEqual(rows["Funcrusher Plus"]["basis"], "track numbers")
        self.assertEqual(view["total"], 3)
        # Deezer counts are cached: a second scan asks nothing again.
        calls = len(studio.http.calls)
        studio.completion.scan()
        wait(studio.completion)
        self.assertEqual(len(studio.http.calls), calls)

    def test_missing_titles_and_refresh(self):
        studio = LocalStudio()
        studio.completion.scan()
        key = next(r["key"] for r in wait(studio.completion)["albums"] if r["title"] == "Parachutes")
        tracks = studio.completion.missing_tracks(key)["tracks"]
        self.assertEqual([(t["title"], t["have"]) for t in tracks], [("Don't Panic", False), ("Yellow", True)])
        with self.assertRaises(LibraryError):
            studio.completion.missing_tracks("nope")
        # The album got complete (e.g. after downloads and a reload): it leaves the report.
        lib = library()
        lib["library"]["albums"]["22"]["tracks"] = [105] + list(range(900, 909))
        for n, tid in enumerate(range(900, 909), 2):
            lib["library"]["tracks"][str(tid)] = {"title": f"T{n}", "artist_id": 3, "album_id": 22, "track": n}
        studio.library = Library(lib)
        self.assertNotIn("Parachutes", {r["title"] for r in studio.completion.view()["albums"]})


if __name__ == "__main__":
    unittest.main()
