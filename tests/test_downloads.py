"""Downloads (fork v9): duplicate check, decisions, queue, history import, folder."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tagcast import downloads as dl
from tagcast.downloads import Downloads
from tagcast.library import Library, LibraryError


def data():
    artists = {1: "Faye Wong 王菲", 2: "Company Flow", 3: "Coldplay", 4: "Various Artists"}
    albums = {
        20: {"name": "Fable", "artist_id": 1, "disc": 1, "tracks": [100, 101]},
        21: {"name": "Funcrusher Plus", "artist_id": 2, "disc": 1, "tracks": [102, 103, 104]},
        22: {"name": "Parachutes", "artist_id": 3, "disc": 1, "tracks": [105]},
    }
    tracks = {
        100: {"title": "寓言", "artist_id": 1, "album_id": 20},
        101: {"title": "Eyes On Me (feat. Someone)", "artist_id": 1, "album_id": 20},
        102: {"title": "Blind", "artist_id": 2, "album_id": 21},
        103: {"title": "8 Steps to Perfection", "artist_id": 2, "album_id": 21},
        104: {"title": "Vital Nerve", "artist_id": 2, "album_id": 21},
        105: {"title": "Yellow", "artist_id": 3, "album_id": 22},
    }
    return {"library": {
        "artists": {str(i): {"name": n} for i, n in artists.items()},
        "albums": {str(i): a for i, a in albums.items()},
        "tracks": {str(i): t for i, t in tracks.items()}}}


class LocalStudio:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.client = self
        self.library = Library(data())
        self.download_manager = Downloads(self)

    def _read(self, name):
        return copy.deepcopy(self.store.get(name))

    def _write(self, name, value):
        self.store[name] = copy.deepcopy(value)

    def _require_client(self):
        if self.client is None:
            raise LibraryError("Disconnected")
        return self.client

    def _library(self):
        return self.library, "owner"


def song(artist, title, **extra):
    return {"kind": "song", "artist": artist, "title": title, **extra}


class MatchTests(unittest.TestCase):
    def test_song_keys_ignore_feat_case_and_script_but_keep_versions(self):
        self.assertEqual(dl.song_key("Eyes On Me (feat. Someone)"), dl.song_key("eyes  on me"))
        self.assertEqual(dl.song_key("Eyes On Me ft. X"), dl.song_key("Eyes On Me"))
        self.assertEqual(dl.song_key("寓言"), dl.song_key("寓言"))
        self.assertEqual(dl.song_key("後來"), dl.song_key("后来"))
        self.assertNotEqual(dl.song_key("Yellow (Live)"), dl.song_key("Yellow"))
        self.assertNotEqual(dl.song_key("Blind (Remix)"), dl.song_key("Blind"))

    def test_artist_keys_cover_bilingual_halves_and_combined_names(self):
        self.assertTrue(dl.artist_keys("王菲") & dl.artist_keys("Faye Wong 王菲"))
        self.assertTrue(dl.artist_keys("FAYE WONG") & dl.artist_keys("Faye Wong 王菲"))
        self.assertTrue(dl.artist_keys("Coldplay/BTS") & dl.artist_keys("Coldplay"))
        self.assertEqual(dl.artist_keys("Various Artists"), set())

    def test_file_names(self):
        self.assertEqual(dl.parse_file_name("03 - Company Flow - Blind.mp3"), ("Company Flow", "Blind"))
        self.assertEqual(dl.parse_file_name("Coldplay - Yellow (Live).m4a"), ("Coldplay", "Yellow (Live)"))
        self.assertEqual(dl.parse_file_name("audio_123.mp3"), ("", "audio 123"))

    def test_lines(self):
        items = dl.parse_lines("Coldplay - Yellow\n\nBlind", "song")
        self.assertEqual([(i["artist"], i["title"]) for i in items], [("Coldplay", "Yellow"), ("", "Blind")])
        album = dl.parse_lines("Company Flow - Funcrusher Plus | 15", "album")[0]
        self.assertEqual((album["title"], album["tracks"]), ("Funcrusher Plus", "15"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.manager = self.studio.download_manager

    def status(self, *items):
        return [r["status"] for r in self.manager.check(list(items))["results"]]

    def test_songs_against_ibroadcast(self):
        self.assertEqual(self.status(song("faye wong", "Eyes on Me"), song("王菲", "寓言"),
                                     song("Coldplay", "Yellow (Live)"), song("Coldplay", "Fix You"),
                                     song("Someone Else", "Yellow")),
                         ["have", "have", "new", "new", "new"])

    def test_albums_with_track_counts(self):
        album = lambda t, n=None: {"kind": "album", "artist": "Company Flow", "title": t, "tracks": n}
        self.assertEqual(self.status(album("Funcrusher Plus"), album("Funcrusher Plus", 3),
                                     album("funcrusher plus", 15), album("Little Johnny")),
                         ["have", "have", "partly", "new"])

    def test_history_counts_as_downloaded_before(self):
        self.manager.record({"title": "Fix You", "artists": ["Coldplay"], "album": "X&Y", "source": "galaxy"})
        results = self.manager.check([song("COLDPLAY", "Fix You"),
                                      {"kind": "album", "artist": "Coldplay", "title": "X&Y"}])["results"]
        self.assertEqual([r["status"] for r in results], ["downloaded", "downloaded"])
        self.assertEqual(results[0]["you_have"][0]["source"], "galaxy")

    def test_check_needs_the_library(self):
        self.studio.client = None
        with self.assertRaises(LibraryError):
            self.manager.check([song("Coldplay", "Yellow")])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.manager = self.studio.download_manager

    def test_duplicates_are_held_never_queued(self):
        result = self.manager.submit([song("Coldplay", "Yellow"), song("Coldplay", "Fix You"),
                                      song("Coldplay", "Fix You")])
        self.assertEqual((result["queued"], result["held"], result["skipped"]), (1, 1, 1))
        view = self.manager.queue_view()
        self.assertEqual([i["title"] for i in view["queue"]], ["Fix You"])
        self.assertEqual(view["held"][0]["status"], "have")
        self.assertEqual(view["held"][0]["you_have"][0]["album"], "Parachutes")
        # Submitting the same duplicate again does not hold it twice.
        self.assertEqual(self.manager.submit([song("Coldplay", "Yellow")])["held"], 0)

    def test_bulk_and_single_decisions(self):
        self.manager.submit([song("Coldplay", "Yellow"), song("Company Flow", "Blind"), song("王菲", "寓言")])
        held = self.manager.queue_view()["held"]
        self.assertEqual(len(held), 3)
        self.manager.decide([held[0]["id"]], "download")
        self.manager.decide([h["id"] for h in held[1:]], "skip")
        view = self.manager.queue_view()
        self.assertEqual(view["held"], [])
        states = {i["title"]: (i["state"], i.get("allow_duplicate")) for i in view["queue"]}
        self.assertEqual(states["Yellow"], ("queued", True))
        self.assertEqual(states["Blind"], ("skipped", None))
        with self.assertRaises(LibraryError):
            self.manager.decide([held[0]["id"]], "skip")  # already decided
        with self.assertRaises(LibraryError):
            self.manager.decide([held[0]["id"]], "maybe")

    def test_state_survives_a_restart(self):
        self.manager.submit([song("Coldplay", "Yellow")])
        again = Downloads(self.studio)
        self.assertEqual(len(again.queue_view()["held"]), 1)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.manager = self.studio.download_manager
        self.tmp = Path(tempfile.mkdtemp())

    def test_galaxy_history_imports_once(self):
        path = self.tmp / "history.json"
        path.write_text(json.dumps({"version": 1, "entries": [
            {"key": "netease:1", "title": "Fix You", "artists": ["Coldplay"], "album": "X&Y", "when": 1700000000},
            {"key": "netease:2", "title": "Blind", "artists": ["Company Flow"], "album": "Funcrusher Plus", "when": 1700000001},
            {"title": ""}]}), encoding="utf-8")
        first = self.manager.import_history("galaxy", str(path))
        self.assertEqual((first["found"], first["added"]), (2, 2))
        second = self.manager.import_history("galaxy", f'"{path}"')
        self.assertEqual((second["added"], second["already"]), (0, 2))
        self.assertEqual(self.manager.check([song("Coldplay", "Fix You")])["results"][0]["status"], "downloaded")
        self.assertEqual(self.manager.history_view("company")["total"], 1)

    def test_telegram_state_and_csv_merge_by_hash(self):
        sha = "a" * 64
        (self.tmp / "state.json").write_text(json.dumps({"history": {
            sha: {"name": "Coldplay - Fix You.mp3", "size": 100, "date": "2026-01-02 03:04:05"}}}), encoding="utf-8")
        (self.tmp / "download_history.csv").write_text(
            "﻿Downloaded,File name,Size (MB),Source,SHA-256\n"
            f"2026-01-02 03:04:05,Coldplay - Fix You.mp3,0.0,deezload2bot,{sha}\n"
            f"2026-01-03 03:04:05,Blind.mp3,3.5,deezload2bot,{'b' * 64}\n", encoding="utf-8")
        result = self.manager.import_history("telegram", str(self.tmp))
        self.assertEqual((result["found"], result["added"]), (2, 2))
        self.assertEqual(self.manager.import_history("telegram", str(self.tmp))["added"], 0)
        entries = {e["title"]: e for e in self.manager.history_view(source="telegram")["entries"]}
        self.assertEqual(entries["Fix You"]["artists"], ["Coldplay"])
        self.assertEqual(entries["Blind"]["artists"], [])
        # A file without an artist still flags the title (better a question than a duplicate).
        self.assertEqual(self.manager.check([song("Anyone", "Blind"), song("Coldplay", "Clocks")])["results"][0]["status"],
                         "downloaded")

    def test_bad_files_are_reported(self):
        with self.assertRaises(LibraryError):
            self.manager.import_history("galaxy", str(self.tmp / "missing.json"))
        (self.tmp / "broken.json").write_text("{", encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.manager.import_history("galaxy", str(self.tmp / "broken.json"))
        with self.assertRaises(LibraryError):
            self.manager.import_history("telegram", str(self.tmp))

    def test_find_galaxy_history_next_to_the_app(self):
        app = self.tmp / "Documents" / "gd-music-downloader"
        app.mkdir(parents=True)
        (app / "app.py").write_text("", encoding="utf-8")
        (app / "history.json").write_text("{}", encoding="utf-8")
        (self.tmp / "Documents" / "history.json").write_text("{}", encoding="utf-8")  # no app.py: not Galaxy
        self.assertEqual(dl.find_galaxy_histories([self.tmp]), [str(app / "history.json")])


class FolderTests(unittest.TestCase):
    def test_folder_setting(self):
        studio = LocalStudio()
        manager = studio.download_manager
        self.assertEqual(manager.view()["folder"], str(Path.home() / "Music"))
        target = Path(tempfile.mkdtemp()) / "Downloads" / "Tagcast"
        self.assertEqual(manager.set_folder(str(target))["folder"], str(target))
        self.assertTrue(target.is_dir())
        self.assertEqual(Downloads(studio).folder(), str(target))
        with self.assertRaises(LibraryError):
            manager.set_folder("relative\\folder")
        self.assertFalse(manager.set_folder("")["folder_set"])


if __name__ == "__main__":
    unittest.main()
