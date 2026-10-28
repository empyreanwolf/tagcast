"""Tag clean-up before upload (fork v19)."""

import tempfile
import time
import unittest
from pathlib import Path

from tagcast.galaxy import detect_audio_format, write_tags
from tagcast.library import Library, LibraryError
from tagcast.tagclean import TagClean, pick_genre, split_artists

from test_discography import FakeHttp
from test_downloads import LocalStudio as DownloadsStudio
from test_downloads import data as library_data

MP3 = (Path(__file__).parent / "fixtures" / "galaxy-silence.mp3").read_bytes()


def library():
    raw = library_data()
    lib = raw["library"]
    lib["tracks"]["102"]["genre"] = "Hip-Hop"  # Company Flow (US) - should become Underground Hip Hop
    lib["artists"]["5"] = {"name": "Gai"}
    lib["tracks"]["106"] = {"title": "Freestyle", "artist_id": 5, "album_id": 99, "genre": "Rap"}  # Chinese, via MB
    lib["artists"]["6"] = {"name": "Mystery Artist"}
    lib["tracks"]["111"] = {"title": "Who Knows", "artist_id": 6, "album_id": 99, "genre": "Rap"}  # ambiguous on MB
    # Genres the library already has, so a target genre is never invented.
    lib["albums"]["30"] = {"name": "UG Example", "artist_id": 2, "disc": 1, "tracks": [120]}
    lib["tracks"]["120"] = {"title": "Already UG", "artist_id": 2, "album_id": 30, "genre": "Underground Hip Hop"}
    lib["albums"]["31"] = {"name": "CN Example", "artist_id": 5, "disc": 1, "tracks": [121]}
    lib["tracks"]["121"] = {"title": "Already CN", "artist_id": 5, "album_id": 31, "genre": "Chinese Hip Hop"}
    return raw


def mb_artist(params):
    query = params.get("query", "")
    if "Gai" in query:
        return {"artists": [{"name": "Gai", "country": "CN", "area": {"name": "China"}}]}
    if "Company Flow" in query:
        return {"artists": [{"name": "Company Flow", "country": "US"}]}
    if "Artist A" in query:
        return {"artists": [{"name": "Artist A", "country": "US"}]}
    if "Mystery Artist" in query:
        return {"artists": [{"name": "Mystery Artist", "country": "US"}, {"name": "Mystery Artist", "country": "CN"}]}
    return {"artists": []}


class LocalStudio(DownloadsStudio):
    def __init__(self):
        super().__init__()
        self.library = Library(library())
        self.http = FakeHttp({"/ws/2/artist": mb_artist})
        self.tagclean = TagClean(self, http=self.http)
        self.saved = []

    def save(self, changes):
        self.saved.append(changes)
        results = []
        for change in changes:
            track = self.library.tracks[int(change["id"])]
            for key, values in change["fields"].items():
                track._row[key] = values["after"]  # Record is read-only; poke the backing dict directly
            results.append({"kind": change["kind"], "id": change["id"], "status": "sent"})
        return {"results": results}


def wait(tc):
    for _ in range(200):
        if not tc.progress:
            return tc.view()
        time.sleep(0.02)
    raise AssertionError("scan did not finish")


class SplittingTests(unittest.TestCase):
    def test_split_on_several_separators(self):
        seps = ["feat.", "feat", "ft.", "ft", "featuring", "&", ",", "/", "//", "\\"]
        self.assertEqual(split_artists(["Artist A feat. Artist B & Artist C, Artist D"], seps),
                         ["Artist A", "Artist B", "Artist C", "Artist D"])
        self.assertEqual(split_artists(["Artist A", "Artist A"], seps), ["Artist A"])  # de-duplicated
        self.assertEqual(split_artists([""], seps), [])

    def test_pick_genre_from_existing_labels_only(self):
        labels = ["Pop", "Underground Hip Hop", "Chinese Hip Hop"]
        self.assertEqual(pick_genre(labels, "underground"), "Underground Hip Hop")
        self.assertEqual(pick_genre(labels, "chinese"), "Chinese Hip Hop")
        self.assertIsNone(pick_genre(labels, "jazz"))


class LibraryScanTests(unittest.TestCase):
    def test_genre_classified_from_musicbrainz_country(self):
        studio = LocalStudio()
        studio.tagclean.scan("library")
        view = wait(studio.tagclean)
        self.assertIn("Underground Hip Hop", view["genres"])
        self.assertIn("Chinese Hip Hop", view["genres"])
        by_title = {r["title"]: r for r in view["genre_ready"]}
        self.assertEqual(by_title["Blind"]["new_genre"], "Underground Hip Hop")  # Company Flow, US
        self.assertEqual(by_title["Freestyle"]["new_genre"], "Chinese Hip Hop")  # Gai, CN
        # An artist MusicBrainz can't place with confidence needs a manual decision instead of a guess.
        decide = {d["artist"]: d["rows"] for d in view["needs_decision"]}
        self.assertEqual([r["title"] for r in decide["Mystery Artist"]], ["Who Knows"])

    def test_apply_genre_writes_through_studio_save(self):
        studio = LocalStudio()
        studio.tagclean.scan("library")
        view = wait(studio.tagclean)
        row = next(r for r in view["genre_ready"] if r["title"] == "Blind")
        result = studio.tagclean.apply_genre([row["id"]])
        self.assertEqual(result["results"], [{"id": row["id"], "status": "saved"}])
        self.assertEqual(studio.library.tracks[102]["genre"], "Underground Hip Hop")
        self.assertEqual([r["title"] for r in studio.tagclean.view()["genre_ready"]], ["Freestyle"])

    def test_assign_genre_for_a_needs_decision_group(self):
        studio = LocalStudio()
        studio.tagclean.scan("library")
        wait(studio.tagclean)
        group = next(d for d in studio.tagclean.view()["needs_decision"] if d["artist"] == "Mystery Artist")
        with self.assertRaises(LibraryError):
            studio.tagclean.assign_genre([group["rows"][0]["id"]], "   ")
        result = studio.tagclean.assign_genre([group["rows"][0]["id"]], "underground hip hop")
        self.assertEqual(result["results"], [{"id": group["rows"][0]["id"], "status": "saved"}])
        self.assertEqual(studio.library.tracks[111]["genre"], "Underground Hip Hop")
        self.assertNotIn("Mystery Artist", [d["artist"] for d in studio.tagclean.view()["needs_decision"]])

    def test_cached_musicbrainz_lookup_is_not_repeated(self):
        studio = LocalStudio()
        studio.tagclean.scan("library")
        wait(studio.tagclean)
        before = len(studio.http.calls)
        studio.tagclean.scan("library")
        wait(studio.tagclean)
        self.assertEqual(len(studio.http.calls), before)


class FolderScanTests(unittest.TestCase):
    def test_folder_scan_fixes_artists_and_genre_then_writes_tags(self):
        studio = LocalStudio()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.mp3"
            path.write_bytes(MP3)
            write_tags(path, "mp3", title="Track One", artists=["Artist A feat. Artist B"], album_artist="Artist A",
                      album="Demo", track_no=1, track_total=1, genre="Hip-Hop")
            studio.tagclean.scan("folder", str(tmp))
            view = wait(studio.tagclean)
            self.assertEqual(len(view["artist_ready"]), 1)
            self.assertEqual(len(view["genre_ready"]), 1)
            row = view["artist_ready"][0]
            self.assertEqual(row["new_artists"], ["Artist A", "Artist B"])
            self.assertEqual(row["new_album_artist"], "Artist A")
            self.assertEqual(row["new_genre"], "Underground Hip Hop")

            studio.tagclean.apply_artists([row["id"]])
            studio.tagclean.apply_genre([row["id"]])
            fmt = detect_audio_format(path)
            from tagcast.tagclean import read_tags
            after = read_tags(path, fmt)
            self.assertEqual(after["artists"], ["Artist A; Artist B"])  # one field, as typed in Mp3tag
            self.assertEqual(after["album_artist"], "Artist A")
            self.assertEqual(after["genre"], "Underground Hip Hop")
            self.assertEqual(after["title"], "Track One")  # untouched fields survive the rewrite
            self.assertEqual(studio.tagclean.view()["artist_ready"], [])
            self.assertEqual(studio.tagclean.view()["genre_ready"], [])

    def test_unknown_folder_is_rejected(self):
        studio = LocalStudio()
        with self.assertRaises(LibraryError):
            studio.tagclean.scan("folder", "/no/such/folder")


class SettingsTests(unittest.TestCase):
    def test_settings_validate_and_default_when_cleared(self):
        studio = LocalStudio()
        view = studio.tagclean.save_settings({"separators": ["feat.", " & "]})
        self.assertEqual(view["separators"], ["feat.", "&"])
        with self.assertRaises(LibraryError):
            studio.tagclean.save_settings({"separators": "not a list"})
        reset = studio.tagclean.save_settings({"separators": []})
        self.assertIn("feat.", reset["separators"])  # falls back to the defaults


if __name__ == "__main__":
    unittest.main()


class Mp3tagStyleTests(unittest.TestCase):
    """Fork v41: the artist field is written the way it is typed in Mp3tag, and any genre can be set."""

    def scan(self, studio, tmp, **tags):
        path = Path(tmp) / "song.mp3"
        path.write_bytes(MP3)
        write_tags(path, "mp3", **{"title": "T", "album_artist": "Artist A", "album": "Demo", "track_no": 1,
                                   "track_total": 1, "genre": "Pop", **tags})
        studio.tagclean.scan("folder", str(tmp))
        return path, wait(studio.tagclean)

    def test_a_multi_value_artist_field_becomes_one_field_joined_with_semicolons(self):
        studio = LocalStudio()
        with tempfile.TemporaryDirectory() as tmp:
            path, view = self.scan(studio, tmp, artists=["Artist A", "Artist B"])  # Mp3tag shows "Artist A\\Artist B"
            row = view["artist_ready"][0]
            self.assertEqual(row["new_artists"], ["Artist A", "Artist B"])
            studio.tagclean.apply_artists([row["id"]])
            from tagcast.tagclean import read_tags
            self.assertEqual(read_tags(path, "mp3")["artists"], ["Artist A; Artist B"])

    def test_an_artist_field_already_written_like_mp3tag_is_left_alone(self):
        studio = LocalStudio()
        with tempfile.TemporaryDirectory() as tmp:
            _, view = self.scan(studio, tmp, artists=["Artist A; Artist B"])
            self.assertEqual(view["artist_ready"], [])

    def test_slashes_and_backslashes_become_semicolons(self):
        self.assertEqual(split_artists(["A // B \\ C / D;E"], ["/", "//", "\\"]), ["A", "B", "C", "D", "E"])
        self.assertEqual(split_artists(["A;B"], []), ["A", "B"])

    def test_a_file_with_no_genre_asks_for_one_and_a_new_genre_can_be_typed(self):
        studio = LocalStudio()
        with tempfile.TemporaryDirectory() as tmp:
            path, view = self.scan(studio, tmp, artists=["Artist A"], genre="")
            group = next(d for d in view["needs_decision"] if d["artist"] == "Artist A")
            studio.tagclean.assign_genre([group["rows"][0]["id"]], "  Mandopop  ")
            from tagcast.tagclean import read_tags
            self.assertEqual(read_tags(path, "mp3")["genre"], "Mandopop")


class SettledGenreTests(unittest.TestCase):
    def test_a_track_already_in_one_of_the_target_genres_is_never_asked_about(self):
        studio = LocalStudio()
        with tempfile.TemporaryDirectory() as tmp:
            for name, artist, genre in (("a.mp3", "Mystery Artist", "Underground Hip Hop"),
                                        ("b.mp3", "Unknown To MusicBrainz", "chinese hip hop")):
                path = Path(tmp) / name
                path.write_bytes(MP3)
                write_tags(path, "mp3", title=name, artists=[artist], album_artist=artist, album="Demo",
                           track_no=1, track_total=1, genre=genre)
            studio.tagclean.scan("folder", str(tmp))
            view = wait(studio.tagclean)
            self.assertEqual(view["needs_decision"], [])
            self.assertEqual(view["genre_ready"], [])
