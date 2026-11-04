"""v72: YouTube name matching, queued links leaving the box (frontend test), Upload page backend."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tagcast import uploader as up
from tagcast import youtube as yt


class YouTubeNameTests(unittest.TestCase):
    VIDEO = "PACT派克特【重塑巴别塔/ImPact 0.5 - Rise Of Babel】01 INTRO"

    def test_a_bilingual_artist_is_found_in_any_part_and_noise_is_ignored(self):
        self.assertEqual(yt.artist_parts("PACT派克特"), ["PACT派克特", "PACT", "派克特"])
        self.assertTrue(yt.mentions(self.VIDEO, "PACT派克特"))
        self.assertFalse(yt.mentions("ImPact 0.5", "PACT派克特"))  # "Pact" is not a word of "ImPact"
        self.assertGreater(yt.score_video({"title": self.VIDEO, "duration": 120, "channel": "MYMUSIC"}, "PACT派克特", "Intro"), 0)

    def test_album_brackets_track_numbers_and_feat_are_not_the_title(self):
        self.assertEqual(yt.clean_video("02 Song Name (feat. Someone)"), "Song Name")
        self.assertEqual(yt.YouTube._track_name("PACT派克特【x】03 Rise Up Feat. Foo", "PACT派克特"), "Rise Up")

    def test_other_versions_stay_out(self):
        self.assertEqual(yt.score_video({"title": "PACT派克特 Intro (Live)", "duration": 120, "channel": "x"}, "PACT派克特", "Intro"), 0)

    def test_each_name_part_is_searched_when_the_whole_name_finds_nothing(self):
        queries = []

        def search(query, playlists=False):
            queries.append(query)
            if query == "派克特 Intro":
                return [{"id": "abcdefghijk", "title": self.VIDEO, "duration": 100, "channel": "MYMUSIC"}]
            return []
        song = yt.YouTube.__new__(yt.YouTube)
        song._search = search
        song._try = lambda q, playlists=False: search(q, playlists)
        found = song.find_song("PACT派克特", "Intro")
        self.assertEqual(found["id"], "abcdefghijk")
        self.assertIn("PACT Intro", queries)

    def test_an_album_playlist_with_another_channel_is_accepted_when_the_artist_is_in_the_videos(self):
        songs = [{"id": f"id{i:09d}", "title": f"PACT派克特【重塑巴别塔/ImPact 0.5 - Rise Of Babel】0{i} Song {i}"} for i in range(1, 5)]
        song = yt.YouTube.__new__(yt.YouTube)
        song._search_playlist = lambda entry: songs
        got = song._loose_album({"title": "PACT派克特【重塑巴别塔/ImPact 0.5 - Rise Of Babel】", "id": "PLx"}, "PACT派克特", "ImPact 0.5 - Rise Of Babel")
        self.assertEqual([s["title"] for s in got], ["Song 1", "Song 2", "Song 3", "Song 4"])
        self.assertIsNone(song._loose_album({"title": "Some other mix", "id": "PLy"}, "PACT派克特", "ImPact 0.5 - Rise Of Babel"))


class Studio:
    def __init__(self):
        self.files, self.client = {}, None

    def _read(self, name):
        return self.files.get(name)

    def _write(self, name, data):
        self.files[name] = data

    def _require_client(self):
        raise RuntimeError("not connected")


def fake_tags(meta):
    def read(path, fmt):
        m = meta.get(Path(path).name, {})
        return {"title": m.get("title", ""), "artists": m.get("artists", []), "album_artist": m.get("album_artist", ""),
                "album": m.get("album", ""), "track_no": m.get("track_no", 0), "track_total": 0, "year": "2020", "genre": m.get("genre", ""),
                "genres": [], "cover": b"x" if m.get("cover") else None, "mb_track_id": "", "mb_release_id": ""}
    return read


class UploaderTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.meta = {}
        for name, m in {"a.mp3": {"title": "Powdered Water", "artists": ["Antiheroes", "Neroche"]},
                        "b.mp3": {"title": "Powdered Water (Remix)", "artists": ["Antiheroes"]},
                        "c.mp3": {"title": "Powder Water", "artists": ["Antiheroes"]},
                        "d.mp3": {"title": "Other", "artists": ["Someone"]}}.items():
            (self.dir / name).write_bytes(b"\0" * 10)
            self.meta[name] = m
        self.patches = [patch.object(up, "read_tags", fake_tags(self.meta)), patch.object(up, "detect_audio_format", lambda p: "mp3"),
                        patch.object(up, "read_disc", lambda p, f: 1)]
        for p in self.patches:
            p.start()
        self.studio = Studio()
        self.sent = []
        self.up = up.Uploader(self.studio, put=self.put)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def put(self, path):
        if path.name == "d.mp3":
            raise RuntimeError("iBroadcast did not accept the file (HTTP 400).")
        self.sent.append(path.name)

    def wait(self):
        for _ in range(200):
            if not (self.up.progress or {}).get("running"):
                return
            time.sleep(0.01)
        self.fail("upload did not finish")

    def test_scan_lists_files_with_created_date_and_multi_artists(self):
        view = self.up.scan(str(self.dir))
        self.assertEqual(len(view["rows"]), 4)
        row = next(r for r in view["rows"] if r["name"] == "a.mp3")
        self.assertEqual(row["artist"], "Antiheroes \\ Neroche")
        self.assertTrue(row["created"] > 0)
        self.assertEqual(self.up.state["folder"], str(self.dir))

    def test_scan_needs_a_folder(self):
        with self.assertRaises(up.UploadError):
            self.up.scan("")

    def test_version_words_keep_remixes_separate_and_close_titles_are_flagged(self):
        match = self.up._title_match
        self.assertEqual(match("Powdered Water", "Powdered Water"), "same")
        self.assertEqual(match("Powdered Water", "Powder Water"), "close")
        self.assertEqual(match("Powdered Water", "Powdered Water (Remix)"), "")
        self.assertEqual(match("Forever", "Forever (Live)"), "")

    def test_duplicates_within_the_folder_are_flagged_not_decided(self):
        view = self.up.scan(str(self.dir))
        ids = {r["name"]: r["id"] for r in view["rows"]}
        found = self.up.duplicates({"ids": list(ids.values())})["duplicates"]
        self.assertIn(ids["c.mp3"], found)
        self.assertEqual(found[ids["c.mp3"]][0]["where"], "folder")
        self.assertNotIn(ids["b.mp3"], found)  # the remix is another song
        self.assertNotIn(ids["d.mp3"], found)

    def test_upload_keeps_files_marks_them_and_lists_failures(self):
        view = self.up.scan(str(self.dir))
        self.up.upload({"ids": [r["id"] for r in view["rows"]]})
        self.wait()
        self.assertEqual(sorted(self.sent), ["a.mp3", "b.mp3", "c.mp3"])
        self.assertTrue(all((self.dir / n).exists() for n in ("a.mp3", "b.mp3", "c.mp3", "d.mp3")))  # nothing deleted
        after = self.up.view()
        marks = {r["name"]: r["uploaded"] for r in after["rows"]}
        self.assertTrue(marks["a.mp3"] and not marks["d.mp3"])
        self.assertEqual([f["name"] for f in after["failed"]], ["d.mp3"])
        self.assertIn("HTTP 400", after["failed"][0]["reason"])
        self.assertTrue(after["last_upload"])
        self.up.remove_failed({"paths": [after["failed"][0]["path"]]})
        self.assertEqual(self.up.view()["failed"], [])

    def test_retry_failed_uploads_again(self):
        view = self.up.scan(str(self.dir))
        d = next(r for r in view["rows"] if r["name"] == "d.mp3")
        self.up.upload({"ids": [d["id"]]})
        self.wait()
        self.assertEqual(len(self.up.view()["failed"]), 1)
        self.up._put = lambda p: self.sent.append(p.name)
        self.up.retry_failed({})
        self.wait()
        self.assertEqual(self.up.view()["failed"], [])
        self.assertIn("d.mp3", self.sent)

    def test_split_proposes_first_artist_as_album_artist_without_writing(self):
        view = self.up.scan(str(self.dir))
        a = next(r for r in view["rows"] if r["name"] == "a.mp3")
        proposal = self.up.split({"ids": [a["id"]]})["edits"][a["id"]]
        self.assertEqual(proposal, {"artist": "Antiheroes; Neroche", "album_artist": "Antiheroes"})

    def test_genre_list_puts_yours_first_then_the_rest(self):
        self.up.save_settings({"genres": ["Chinese Hip Hop", "Underground Hip Hop", "chinese hip hop"]})
        self.assertEqual(self.up.settings()["genres"], ["Chinese Hip Hop", "Underground Hip Hop"])
        self.assertEqual(self.up.ordered_genres(["Rock", "underground hip hop", "Ambient"]), ["Chinese Hip Hop", "Underground Hip Hop", "Ambient", "Rock"])

    def test_default_folder_is_saved_and_checked(self):
        self.up.save_settings({"folder": str(self.dir)})
        self.assertEqual(self.up.scan()["folder"], str(self.dir))
        with self.assertRaises(up.UploadError):
            self.up.save_settings({"folder": str(self.dir / "nope")})

    def test_save_writes_only_changed_fields_and_undo_restores(self):
        view = self.up.scan(str(self.dir))
        row = next(r for r in view["rows"] if r["name"] == "d.mp3")
        written = []
        with patch.object(up, "apply_tags", lambda path, fmt, fields, **k: written.append((Path(path).name, dict(fields)))):
            out = self.up.save({"edits": [{"id": row["id"], "fields": {"genre": "Chinese Hip Hop", "track_no": "3", "bogus": "x"}}]})
            self.assertEqual(written[0], ("d.mp3", {"genre": "Chinese Hip Hop", "track_no": 3}))
            self.assertEqual(out["saved"], 1)
            self.assertTrue(out["can_undo"])
            self.up.undo()
        self.assertEqual(written[1][1], {"genre": "", "track_no": 0})
        self.assertFalse(self.up.view()["can_undo"])

    def test_rename_uses_the_tags_and_refuses_to_overwrite(self):
        view = self.up.scan(str(self.dir))
        ids = [r["id"] for r in view["rows"]]
        out = self.up.rename({"ids": ids, "pattern": "%artist% - %title%"})
        self.assertTrue((self.dir / "Someone - Other.mp3").exists())
        self.assertEqual(out["renamed"], 4)
        names = {r["name"] for r in out["rows"]}
        self.assertIn("Someone - Other.mp3", names)

    def test_artist_suggestions_without_a_library_say_so(self):
        self.assertFalse(self.up.artist_suggestions({"names": ["PACT"]})["library"])


if __name__ == "__main__":
    unittest.main()
