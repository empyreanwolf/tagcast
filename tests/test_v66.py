import unittest
from unittest.mock import patch

from test_discography import LocalStudio
from tagcast import updates
from tagcast.downloads import album_base_key
from tagcast.galaxy import album_candidates
from tagcast.moresources import MoreSources
from tagcast.spotify import SpotDL, song_row, spotify_link
from tagcast.youtube import YtdlpWorker


def track(tid, title, album, artists):
    return {"id": tid, "source": "netease", "title": title, "album": album, "artists": artists, "pic_id": "cover-" + artists[0]}


class FakeApi:
    def __init__(self, results):
        self.results = results

    def search(self, kind, q, count, page):
        return self.results


class AlbumListTests(unittest.TestCase):
    def test_same_named_albums_are_listed_with_the_other_artist_flagged(self):
        results = [track(str(i), f"S{i}", "The Soulution", ["St Soul"]) for i in range(14)] + \
                  [track(f"e{i}", f"E{i}", "The Soulution", ["Epidemic", "Tantu"]) for i in range(6)] + \
                  [track("x", "Other", "Unrelated", ["Nobody"])]
        rows = album_candidates(FakeApi(results), "netease", "The Soulution Epidemic")
        self.assertEqual([(r["artist"], r["tracks"], r["different_artist"]) for r in rows],
                         [("Epidemic", 6, False), ("St Soul", 14, True)])
        rows = album_candidates(FakeApi(results), "netease", "The Soulution")
        self.assertEqual({r["artist"] for r in rows}, {"Epidemic", "St Soul"})
        self.assertFalse(any(r["different_artist"] for r in rows))


class EditionTests(unittest.TestCase):
    def test_edition_words_are_ignored(self):
        self.assertEqual(album_base_key("None Shall Pass (Bonus Edition)"), album_base_key("None Shall Pass"))
        self.assertEqual(album_base_key("Madvillainy - Deluxe Edition"), album_base_key("Madvillainy"))
        self.assertNotEqual(album_base_key("Live at the BBC"), album_base_key("Live"))

    def test_another_edition_counts_as_have(self):
        from tagcast.downloads import LibraryIndex, HistoryIndex, check_item
        lib = type("L", (), {})()
        index = LibraryIndex.__new__(LibraryIndex)
        entry = {"title": "None Shall Pass (Bonus Edition)", "artist": "Aesop Rock", "keys": {"aesop rock"}, "named": True,
                 "tracks": 14, "album_id": 1}
        index.songs, index.albums, index.album_bases = {}, {"x": [entry]}, {album_base_key(entry["title"]): [entry]}
        from tagcast.downloads import artist_keys
        entry["keys"] = artist_keys("Aesop Rock")
        result = check_item({"kind": "album", "title": "None Shall Pass", "artist": "Aesop Rock"}, index, HistoryIndex([]))
        self.assertEqual((result["status"], result.get("other_edition"), result["you_have"][0]["edition"]), ("have", True, True))


class ForkUpdateTests(unittest.TestCase):
    def test_fork_numbers_and_result(self):
        self.assertEqual(updates.fork_number('const artistCleanupVersion = "fork v66";'), 66)
        self.assertGreater(updates.local_fork(), 60)
        checker = updates.UpdateChecker(lambda name: None, lambda name, data: None)
        checker.cache["fork_latest"] = updates.local_fork() + 1
        out = checker._result(True)
        self.assertTrue(out["available"])
        self.assertTrue(out["latest"].startswith("fork v"))
        self.assertIn("empyreanwolf/tagcast", out["url"])
        checker.cache["fork_latest"] = updates.local_fork()
        self.assertFalse(checker._result(True)["available"])


SONGS = [{"name": f"Track {i}", "artists": ["Epidemic", "Tantu"], "album_name": "The Soulution", "album_artist": "Epidemic",
          "track_number": i, "tracks_count": 2, "year": 2017, "genres": ["underground hip hop"], "duration": 200,
          "cover_url": "https://i.scdn.co/image/abc", "download_url": f"https://music.youtube.com/watch?v=vid{i}xxxxx",
          "url": f"https://open.spotify.com/track/t{i}"} for i in (1, 2)]


class SpotifyTests(unittest.TestCase):
    def test_links_and_rows(self):
        self.assertEqual(spotify_link("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy?si=x"), ("album", "4aawyAB9vmqN3uQ7FjRGTy"))
        self.assertEqual(spotify_link("https://open.spotify.com/intl-de/track/4aawyAB9vmqN3uQ7FjRGTy")[0], "track")
        self.assertIsNone(spotify_link("https://example.com/album/x"))
        row = song_row(SONGS[0])
        self.assertEqual((row["artist"], row["album"], row["track_no"], row["spotify"]["year"], row["spotify"]["genre"]),
                         ("Epidemic", "The Soulution", 1, "2017", "Underground Hip Hop"))
        self.assertIsNone(song_row({**SONGS[0], "download_url": None}))

    def test_a_spotify_album_link_queues_every_song_with_spotify_tags(self):
        studio = LocalStudio()
        studio.spotify = SpotDL(studio, runner=lambda link: SONGS)
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None)
        studio.moresources.ensure_worker = lambda: None
        out = studio.moresources.add_links("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")["results"][0]
        self.assertTrue(out["ok"])
        q = [i for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(i["payload"]["site"], i["payload"]["album"], i["payload"]["track_no"], i["payload"]["artists"]) for i in q],
                         [("Spotify", "The Soulution", 1, ["Epidemic", "Tantu"]), ("Spotify", "The Soulution", 2, ["Epidemic", "Tantu"])])
        tags = YtdlpWorker.tags_for(q[0], {"track": "Wrong", "release_year": 1999})
        self.assertEqual((tags["title"], tags["year"], tags["genre"], tags["album_artist"]), ("Track 1", "2017", "Underground Hip Hop", "Epidemic"))

    def test_not_installed_starts_the_install(self):
        studio = LocalStudio()
        spot = SpotDL(studio)
        with patch.object(SpotDL, "install", return_value={}) as install, patch.object(SpotDL, "installed", return_value=False):
            with self.assertRaisesRegex(Exception, "being installed"):
                spot.songs("https://open.spotify.com/track/4aawyAB9vmqN3uQ7FjRGTy")
            install.assert_called_once()
