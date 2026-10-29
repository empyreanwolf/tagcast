"""Discography, Top 30 and "get the missing" (fork v15)."""

import time
import unittest

from tagcast.discography import Discography, DiscographyError

from test_bandcamp import PAGES, FakeGet
from test_bandcamp import fetch as bc_fetch
from test_galaxy import LocalStudio as GalaxyStudio
from test_galaxy import song as gd_song
from test_soulseek import FakeDaemon, Proc


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def json(self, url, params=None, **kw):
        self.calls.append(url)
        for key, value in self.answers.items():
            if url.endswith(key):
                return value(params) if callable(value) else value
        return None


ANSWERS = {
    "/search/artist": {"data": [{"id": 1, "name": "Coldplay", "nb_fan": 9, "picture_medium": "p"},
                                {"id": 2, "name": "Coldplay", "nb_fan": 1000}]},  # a same-name act with more fans
    "/artist/1/albums": {"data": [{"id": 11, "title": "Parachutes", "release_date": "2000-07-10", "record_type": "album"},
                                  {"id": 12, "title": "Ghost Stories", "release_date": "2014-05-16", "record_type": "album"},
                                  {"id": 13, "title": "X&Y", "release_date": "2005-06-06", "record_type": "album"}]},
    "/artist/2/albums": {"data": [{"id": 21, "title": "Covers", "record_type": "album"}]},
    "/artist/1/top": {"data": [{"title": "Yellow", "preview": "https://p/1.mp3", "album": {"title": "Parachutes"}},
                               {"title": "Magic", "preview": "https://p/2.mp3", "album": {"title": "Ghost Stories"}},
                               {"title": "Fix You", "preview": "https://p/3.mp3", "album": {"title": "X&Y"}}]},
    "/search": {"results": [{"artistName": "Coldplay", "artistId": 5}]},
    "/lookup": {"results": [{"wrapperType": "artist"},
                            {"wrapperType": "collection", "collectionName": "Parachutes", "trackCount": 10, "releaseDate": "2000-07-10"},
                            {"wrapperType": "collection", "collectionName": "Christmas Lights - Single", "trackCount": 1, "releaseDate": "2010-12-01"}]},
}


class LocalStudio(GalaxyStudio):
    def __init__(self, soulseek=False):
        super().__init__()
        from tagcast.bandcamp import Bandcamp
        self.bandcamp = Bandcamp(self, get=FakeGet(), fetch=bc_fetch)
        self.bandcamp.track_delay = 0
        self.bandcamp.pace = 0
        self.bandcamp.ensure_worker = lambda: None  # queueing is what these tests check
        self.galaxy.ensure_worker = lambda: None
        if soulseek:
            import tempfile
            from pathlib import Path

            from tagcast.soulseek import Soulseek
            self.home = tempfile.mkdtemp()
            self.soulseek = Soulseek(self, http=FakeDaemon(), popen=lambda *a, **k: Proc())
            self.soulseek.ensure_worker = lambda: None
            exe = Path(self.home) / "sockseek.exe"
            exe.write_text("")
            self.soulseek.state.update(exe=str(exe), username="me", password="pw")
        self.http = FakeHttp(dict(ANSWERS))
        self.discography = Discography(self, http=self.http)


def wait_job(d, job):
    for _ in range(200):
        if d.status(job["id"])["state"] == "done":
            return d.status(job["id"])
        time.sleep(0.02)
    raise AssertionError("get-missing did not finish")


class DiscographyTests(unittest.TestCase):
    def setUp(self):
        from tagcast import discography
        discography._galaxy_block["until"] = 0.0

    def test_merged_discography_with_statuses(self):
        studio = LocalStudio()
        data = studio.discography.view("Coldplay")
        self.assertEqual(data["artist"]["id"], 1)  # your library decides between same-name artists
        rel = {r["title"]: r for r in data["releases"]}
        self.assertEqual(set(rel), {"Parachutes", "Ghost Stories", "X&Y", "Christmas Lights"})
        self.assertEqual((rel["Parachutes"]["status"], rel["Parachutes"]["tracks"]), ("partly", 10))  # you have 1 of 10
        self.assertEqual(rel["Ghost Stories"]["status"], "missing")
        self.assertEqual((rel["Christmas Lights"]["type"], rel["Christmas Lights"]["sources"]), ("single", ["Apple Music"]))
        self.assertEqual(rel["Parachutes"]["sources"], ["Deezer", "Apple Music"])
        self.assertEqual([r["year"] for r in data["releases"]][:1], ["2014"])  # newest first
        calls = len(studio.http.calls)
        studio.discography.view("COLDPLAY")  # cached for 7 days
        self.assertEqual(len(studio.http.calls), calls)
        with self.assertRaises(DiscographyError):
            studio.discography.view("")

    def test_musicbrainz_only_on_request(self):
        studio = LocalStudio()
        studio.http.answers.update({"/ws/2/artist": {"artists": [{"id": "mb", "name": "Coldplay"}]},
                                    "/ws/2/release-group": {"release-groups": [{"title": "Safety", "primary-type": "EP", "first-release-date": "1998"}]}})
        self.assertNotIn("Safety", {r["title"] for r in studio.discography.view("Coldplay")["releases"]})
        self.assertIn("Safety", {r["title"] for r in studio.discography.view("Coldplay", musicbrainz=True)["releases"]})

    def test_top_songs(self):
        studio = LocalStudio()
        top = studio.discography.top("Coldplay", 1)
        self.assertEqual(top["source"], "Deezer")
        self.assertEqual([(s["title"], s["status"]) for s in top["songs"]], [("Yellow", "have"), ("Magic", "missing"), ("Fix You", "missing")])
        self.assertTrue(top["songs"][1]["preview"])

    def test_top_songs_fall_back_to_galaxy(self):
        studio = LocalStudio()
        studio.http.answers.pop("/artist/1/top")
        studio.gd.search_results[("netease", "Coldplay", 1)] = [gd_song(1, "Clocks", "Coldplay", "A Rush of Blood")]
        top = studio.discography.top("Coldplay", 1)
        self.assertEqual((top["source"], [s["title"] for s in top["songs"]]), ("Galaxy", ["Clocks"]))

    def test_get_missing_uses_galaxy_then_bandcamp(self):
        studio = LocalStudio()
        studio.gd.search_results[("netease_album", "X&Y", 1)] = [gd_song(31, "Square One", "Coldplay", "X&Y")]
        studio.gd.search_results[("netease_album", "Viva", 1)] = []
        job = studio.discography.get("Coldplay", albums=[{"title": "Ghost Stories", "status": "missing"},
                                                         {"title": "X&Y", "status": "missing"},
                                                         {"title": "Viva", "status": "missing"}])
        result = wait_job(studio.discography, job)
        log = {e["title"]: e for e in result["log"]}
        self.assertEqual(log["Ghost Stories"]["source"], "Bandcamp")
        self.assertEqual(log["X&Y"]["source"], "Galaxy")
        self.assertIsNone(log["Viva"]["source"])
        sources = {i["title"]: i["source"] for i in studio.download_manager.state["queue"]}
        self.assertEqual(sources, {"Magic": "bandcamp", "Ink": "bandcamp", "Square One": "galaxy"})

    def test_galaxy_tries_the_other_open_source(self):
        # v32: netease has nothing for the album, joox does.
        studio = LocalStudio()
        studio.gd.search_results[("joox_album", "X&Y", 1)] = [gd_song(31, "Square One", "Coldplay", "X&Y", "joox")]
        result = wait_job(studio.discography, studio.discography.get("Coldplay", albums=[{"title": "X&Y", "status": "missing"}]))
        self.assertEqual({e["title"]: e["source"] for e in result["log"]}, {"X&Y": "Galaxy"})
        item = next(i for i in studio.download_manager.state["queue"] if i["title"] == "Square One")
        self.assertEqual(item["payload"]["source"], "joox")

    def test_galaxy_comes_before_soulseek_and_a_failed_soulseek_goes_to_the_wishlist(self):
        studio = LocalStudio(soulseek=True)
        studio.gd.search_results[("netease_album", "X&Y", 1)] = [gd_song(31, "Square One", "Coldplay", "X&Y")]
        studio.gd.search_results[("netease_album", "Rush", 1)] = [gd_song(41, "Clocks", "Coldplay", "Rush")]
        result = wait_job(studio.discography, studio.discography.get("Coldplay", albums=[{"title": "X&Y", "status": "missing"},
                                                                                       {"title": "Rush", "status": "partly"},
                                                                                       {"title": "Parachutes", "status": "partly"},
                                                                                       {"title": "Viva", "status": "missing"}]))
        log = {e["title"]: e["source"] for e in result["log"]}
        # Bandcamp first, then Galaxy; Soulseek only when Galaxy has nothing (and never for a partly owned album).
        self.assertEqual(log, {"X&Y": "Galaxy", "Rush": "Galaxy", "Parachutes": "Bandcamp", "Viva": "Soulseek"})
        item = next(i for i in studio.download_manager.state["queue"] if i["source"] == "soulseek")
        studio.download_manager.update_item(item["id"], state="failed", note="Nobody on Soulseek shares it right now")
        wished = []
        studio.wishlist = type("W", (), {"add": lambda self, *a: wished.append(a)})()
        studio.discography.fallback(studio.download_manager.item(item["id"]))
        self.assertIn("on your wishlist", studio.download_manager.item(item["id"])["note"])
        self.assertEqual([c[2] for c in wished], ["Viva"])
        shown = studio.discography.active()["jobs"][0]
        self.assertEqual((shown["state"], shown["current"]), ("done", ""))
        self.assertEqual(len(shown["log"]), 4)
        self.assertEqual(studio.discography.dismiss(), {"jobs": []})

    def test_galaxy_is_tried_before_bandcamp_even_when_bandcamp_has_the_album(self):
        studio = LocalStudio()
        studio.gd.search_results[("netease_album", "Ghost Stories", 1)] = [gd_song(51, "Magic", "Coldplay", "Ghost Stories")]
        result = wait_job(studio.discography, studio.discography.get("Coldplay", albums=[{"title": "Ghost Stories", "status": "missing"}]))
        self.assertEqual({e["title"]: e["source"] for e in result["log"]}, {"Ghost Stories": "Galaxy"})
        self.assertEqual({i["source"] for i in studio.download_manager.state["queue"]}, {"galaxy"})

    def test_an_album_galaxy_only_lists_as_a_song_is_found_by_title_in_either_chinese_script(self):
        # Fork v54: the album search finds nothing, but Galaxy lists a song with this title (simplified
        # characters, full-width brackets) under another album name.
        studio = LocalStudio()
        wanted = "逆風 (傳說對決 GCS 2026 主題曲)"
        studio.gd.search_results[("netease", f"玖壹壹 {wanted}", 1)] = [
            gd_song(61, "逆风（传说对决 GCS 2026 主题曲）", "玖壹壹", "逆风 单曲")]
        result = wait_job(studio.discography, studio.discography.get("玖壹壹", albums=[{"title": wanted, "status": "missing"}]))
        entry = result["log"][0]
        self.assertEqual(entry["source"], "Galaxy")
        self.assertIn("found by song title", entry["note"])
        self.assertEqual([i["source"] for i in studio.download_manager.state["queue"]], ["galaxy"])

    def test_the_artists_own_songs_are_searched_when_no_title_search_finds_it(self):
        studio = LocalStudio()
        studio.gd.search_results[("netease", "连麻Swimming", 1)] = [
            gd_song(71, "國產專區", "连麻Swimming", "國產專區FT. BOBBYN")]
        result = wait_job(studio.discography, studio.discography.get("连麻Swimming", albums=[{"title": "國產專區", "status": "missing"}]))
        self.assertEqual(result["log"][0]["source"], "Galaxy")

    def test_a_403_firewall_page_is_not_not_found_and_pauses_galaxy(self):
        from tagcast import galaxy as galaxy_mod
        from test_galaxy import FakeResponse
        studio = LocalStudio()
        old = galaxy_mod.BLOCK_PAUSE
        galaxy_mod.BLOCK_PAUSE = 0.01
        try:
            page = FakeResponse(403)
            page.text = '<!doctype html><html><head><title>blocked</title></head></html>'
            calls = []
            studio.galaxy.api.get = lambda params, timeout: (calls.append(params), page)[1]
            result = wait_job(studio.discography, studio.discography.get(
                "Snowgoons", albums=[{"title": "Feels Like I'm Dying", "status": "missing"},
                                     {"title": "Another One", "status": "missing"}]))
        finally:
            galaxy_mod.BLOCK_PAUSE = old
        first, second = result["log"]
        self.assertIsNone(first["source"])
        self.assertIn("blocked", first["note"])
        self.assertNotIn("<html", first["note"].lower())
        self.assertNotIn("Not found on Galaxy", first["note"])
        self.assertEqual(len(calls), 1, "after one block Galaxy is not asked again for a while")
        self.assertIn("paused", second["note"])


if __name__ == "__main__":
    unittest.main()
