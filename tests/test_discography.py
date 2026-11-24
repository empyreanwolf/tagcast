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

    def test_no_discogs_token_downgrades_a_various_artists_album_by_track_count(self):
        # Fork v99: Deezer calls "Parachutes" an "album" of ours, but most of its 14 tracks are
        # really credited to someone else - a various-artists compilation Deezer still files
        # under us. Without a Discogs token (which would say so directly via its "Appearance"
        # role) we fetch the tracklist once and downgrade it ourselves.
        studio = LocalStudio()
        studio.http.answers.update({"/album/11/tracks": {"data":
            [{"artist": {"name": "Coldplay"}}] * 2 + [{"artist": {"name": "Someone Else"}}] * 12}})
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertEqual(rel["Parachutes"]["type"], "compile")
        self.assertEqual(rel["Ghost Stories"]["type"], "album")  # no tracklist answer configured - stays as-is
        calls = len(studio.http.calls)
        studio.discography.view("Coldplay")  # cached: no new tracklist fetch
        self.assertEqual(len(studio.http.calls), calls)

    def test_a_discogs_token_does_not_skip_the_track_count_check(self):
        # fork v110: Discogs' "Appearance" role misses releases (Jus' Allah on Madecipha & 5:am's album), so the
        # tracklist / credited-artist check now runs with a token too.
        studio = LocalStudio()
        studio.source_keys = lambda: {"discogs_token": "tok"}
        studio.http.answers.update({"/album/11/tracks": {"data": [{"artist": {"name": "Someone Else"}}] * 13
                                                         + [{"artist": {"name": "Coldplay"}}]},
                                    "/database/search": {"results": []}})
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertEqual(rel["Parachutes"]["type"], "compile")
        self.assertTrue(rel["Parachutes"]["appearance"])
        self.assertTrue(any(url.endswith("/album/11/tracks") for url in studio.http.calls))

    def test_a_release_credited_to_someone_else_is_a_guest_appearance_with_that_main_artist(self):
        # fork v110: "Completely Disrespectful" is Madecipha & 5:am's album; Jus' Allah is only a guest on it.
        studio = LocalStudio()
        studio.http.answers.update({"/album/11/tracks": {"data": [{"artist": {"name": "Coldplay"}}] * 2},
                                    "/album/11": {"artist": {"name": "Madecipha & 5:am"}, "contributors": [], "nb_tracks": 2}})
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertTrue(rel["Parachutes"]["appearance"])
        self.assertEqual(rel["Parachutes"]["main_artist"], "Madecipha & 5:am")

    def test_feat_in_title_is_flagged_as_an_appearance_with_no_extra_fetch(self):
        # fork v102: "Title (feat. <this artist>)" means they're a guest on someone else's song,
        # whatever Deezer or Discogs say about the release - catches a release neither source
        # correctly tags (the bug report: Deezer just calls it an album, no Discogs token check
        # runs for it since a token is set, and it isn't on Discogs at all).
        studio = LocalStudio()
        studio.http.answers["/artist/1/albums"] = {"data": [
            *ANSWERS["/artist/1/albums"]["data"],
            {"id": 14, "title": "True Power (feat. Coldplay)", "release_date": "2020-01-01", "record_type": "album"}]}
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertTrue(rel["True Power (feat. Coldplay)"]["appearance"])
        self.assertEqual(rel["True Power (feat. Coldplay)"]["type"], "compile")
        self.assertFalse(any(url.endswith("/album/14/tracks") for url in studio.http.calls))  # no extra fetch needed

    def test_feat_in_title_does_not_flag_someone_elses_feature(self):
        studio = LocalStudio()
        studio.http.answers["/artist/1/albums"] = {"data": [
            *ANSWERS["/artist/1/albums"]["data"],
            {"id": 15, "title": "Clocks (feat. Someone Else)", "release_date": "2020-01-01", "record_type": "album"}]}
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertFalse(rel["Clocks (feat. Someone Else)"].get("appearance"))
        self.assertEqual(rel["Clocks (feat. Someone Else)"]["type"], "album")

    def test_discogs_appearance_role_is_carried_onto_a_release_already_from_deezer(self):
        # A release Deezer already added (so it has no "appearance" flag yet) still needs
        # Discogs' more accurate "Appearance" role applied when Discogs also lists it.
        studio = LocalStudio()
        studio.source_keys = lambda: {"discogs_token": "tok"}
        studio.http.answers.pop("/search", None)  # would otherwise shadow "/database/search" (both end with "/search")
        studio.http.answers.update({
            "/database/search": {"results": [{"title": "Coldplay", "id": 99}]},
            "/artists/99/releases": {"releases": [{"title": "Parachutes", "year": 2000, "role": "Appearance",
                                                    "format": "Album"}], "pagination": {"pages": 1}},
        })
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertTrue(rel["Parachutes"]["appearance"])
        self.assertEqual(rel["Parachutes"]["type"], "compile")

    def test_deezer_guest_detection_also_sets_the_appearance_flag(self):
        studio = LocalStudio()
        studio.http.answers.update({"/album/11/tracks": {"data":
            [{"artist": {"name": "Coldplay"}}] * 2 + [{"artist": {"name": "Someone Else"}}] * 12}})
        data = studio.discography.view("Coldplay")
        rel = {r["title"]: r for r in data["releases"]}
        self.assertTrue(rel["Parachutes"]["appearance"])

    def test_musicbrainz_only_on_request(self):
        studio = LocalStudio()
        studio.http.answers.update({"/ws/2/artist": {"artists": [{"id": "mb", "name": "Coldplay"}]},
                                    "/ws/2/release-group": {"release-groups": [{"title": "Safety", "primary-type": "EP", "first-release-date": "1998"}]}})
        self.assertNotIn("Safety", {r["title"] for r in studio.discography.view("Coldplay")["releases"]})
        self.assertIn("Safety", {r["title"] for r in studio.discography.view("Coldplay", musicbrainz=True)["releases"]})

    def test_alias_has_album_true_when_deezer_lists_the_title(self):
        studio = LocalStudio()
        self.assertTrue(studio.discography.alias_has_album("Coldplay", "Parachutes"))

    def test_alias_has_album_false_when_deezer_catalog_lacks_the_title(self):
        studio = LocalStudio()
        self.assertFalse(studio.discography.alias_has_album("Coldplay", "Shakespeare Science"))

    def test_alias_has_album_true_when_no_deezer_artist_to_check_against(self):
        studio = LocalStudio()
        self.assertTrue(studio.discography.alias_has_album("Some Unknown Act", "Anything"))

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

    def test_get_missing_leaves_out_instrumental_and_backing_track_bonuses(self):
        # v103: an automatic "Get missing album" used to grab every track GD Studio listed,
        # bonus 伴奏/instrumental versions included - the manual "Download album" button already
        # left these out (galaxyWanted() in galaxy.js); now this path does too.
        studio = LocalStudio()
        studio.gd.search_results[("netease_album", "烽烟四起", 1)] = [
            gd_song(31, "烽烟四起", "胡彦斌", "烽烟四起"),
            gd_song(32, "烽烟四起 (伴奏)", "胡彦斌", "烽烟四起"),
            gd_song(33, "对月 (和声伴奏)", "胡彦斌", "烽烟四起"),
        ]
        job = studio.discography.get("胡彦斌", albums=[{"title": "烽烟四起", "status": "missing"}])
        wait_job(studio.discography, job)
        titles = {i["title"] for i in studio.download_manager.state["queue"]}
        self.assertEqual(titles, {"烽烟四起"})

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
        self.assertEqual(studio.discography.dismiss()["jobs"], [])

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

    def test_a_single_is_never_widened_into_the_album_its_song_is_on(self):   # fork v117
        studio = LocalStudio()
        studio.gd.search_results[("netease", "DJ Khaled Bills Paid", 1)] = [gd_song(71, "Bills Paid", "DJ Khaled", "GOD DID")]
        for key in (("netease_album", "GOD DID", 1), ("netease", "GOD DID", 1), ("netease", "DJ Khaled GOD DID", 1)):
            studio.gd.search_results[key] = [gd_song(70 + n, f"Track {n}", "DJ Khaled", "GOD DID") for n in range(1, 6)] + \
                                            [gd_song(71, "Bills Paid", "DJ Khaled", "GOD DID")]
        result = wait_job(studio.discography, studio.discography.get(
            "DJ Khaled", albums=[{"title": "Bills Paid", "status": "missing", "tracks": 1}]))
        self.assertEqual(result["log"][0]["source"], "Galaxy")
        queued = [i["title"] for i in studio.download_manager.state["queue"] if i["source"] == "galaxy"]
        self.assertEqual(queued, ["Bills Paid"])

    def test_an_unknown_size_release_is_not_widened_either(self):   # fork v121
        studio = LocalStudio()
        studio.gd.search_results[("netease", "DJ Khaled Bills Paid", 1)] = [gd_song(71, "Bills Paid", "DJ Khaled", "GOD DID")]
        for key in (("netease_album", "GOD DID", 1), ("netease", "GOD DID", 1), ("netease", "DJ Khaled GOD DID", 1)):
            studio.gd.search_results[key] = [gd_song(70 + n, f"Track {n}", "DJ Khaled", "GOD DID") for n in range(1, 6)] + \
                                            [gd_song(71, "Bills Paid", "DJ Khaled", "GOD DID")]
        result = wait_job(studio.discography, studio.discography.get(
            "DJ Khaled", albums=[{"title": "Bills Paid", "status": "missing"}]))
        self.assertEqual(result["log"][0]["source"], "Galaxy")
        queued = [i["title"] for i in studio.download_manager.state["queue"] if i["source"] == "galaxy"]
        self.assertEqual(queued, ["Bills Paid"])

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


class V105SpotifyFallbackTests(unittest.TestCase):
    """fork v105: spotDL's own Spotify search (v99) tried automatically for a wishlist song miss."""

    def test_a_close_match_is_queued_through_more_sources(self):
        studio = LocalStudio()
        row = {"url": "https://youtu.be/x", "title": "Yellow", "artist": "Coldplay", "album": "Parachutes"}
        studio.spotify = type("S", (), {"songs": staticmethod(lambda q: [row])})()
        added = []
        studio.moresources = type("M", (), {
            "add": lambda self, rows, query="": added.append(rows) or {"queued": 1, "held": 0},
            "_spotify_note": staticmethod(lambda rows, result: f"{result['queued']} queued with Spotify's tags")})()
        source, note = studio.discography._spotify_song("Coldplay", "Yellow")
        self.assertEqual(source, "Spotify")
        self.assertEqual(added, [[row]])
        self.assertIn("queued", note)

    def test_a_distant_match_is_not_trusted(self):
        studio = LocalStudio()
        row = {"title": "Something Completely Different", "artist": "Nobody"}
        studio.spotify = type("S", (), {"songs": staticmethod(lambda q: [row])})()

        def boom(self, rows, query=""):
            raise AssertionError("should not have queued a distant match")
        studio.moresources = type("M", (), {"add": boom})()
        self.assertIsNone(studio.discography._spotify_song("Coldplay", "Yellow"))

    def test_no_spotify_or_moresources_attribute_is_a_quiet_miss(self):
        studio = LocalStudio()
        self.assertIsNone(studio.discography._spotify_song("Coldplay", "Yellow"))

    def test_v108_an_empty_spotify_answer_is_a_quiet_miss_not_a_crash(self):
        studio = LocalStudio()
        studio.spotify = type("S", (), {"songs": staticmethod(lambda q: [])})()
        studio.moresources = type("M", (), {"add": lambda self, rows, query="": None})()
        self.assertIsNone(studio.discography._spotify_song("Coldplay", "Yellow"))

    def test_a_spotify_search_error_is_a_quiet_miss(self):
        from tagcast.library import LibraryError
        studio = LocalStudio()

        def boom(q):
            raise LibraryError("spotDL found nothing")
        studio.spotify = type("S", (), {"songs": staticmethod(boom)})()
        studio.moresources = type("M", (), {})()
        self.assertIsNone(studio.discography._spotify_song("Coldplay", "Yellow"))


if __name__ == "__main__":
    unittest.main()


class SearchTrailTests(unittest.TestCase):
    def test_each_searched_item_logs_its_term_origin_and_result(self):
        from unittest import mock
        from tagcast.discography import Discography
        with mock.patch("tagcast.discography.activity.record") as record:
            Discography._trail("song", "DJ Khaled", "Bills Paid", "page https://x.test · row “Bills Paid - DJ Khaled”", "Galaxy", "1 queued")
            Discography._trail("album", "Nobody", "Nothing", None, None, "not found")
        first, second = record.call_args_list
        self.assertEqual(first.args[:3], ("downloads", "ok", "Searched song “DJ Khaled Bills Paid” → Galaxy"))
        self.assertIn("Search term: DJ Khaled Bills Paid", first.kwargs["detail"])
        self.assertIn("Asked from: page https://x.test · row “Bills Paid - DJ Khaled”", first.kwargs["detail"])
        self.assertEqual(second.args[:3], ("downloads", "info", "Searched album “Nobody Nothing” → not found"))
        self.assertIn("Asked from: Discography", second.kwargs["detail"])


class LooksLikeASongTests(unittest.TestCase):   # fork v121
    def test_article_song_titles_are_songs(self):
        from tagcast.discography import _looks_like_a_song
        self.assertTrue(_looks_like_a_song("GASS” (feat. Travis Scott)"))
        self.assertTrue(_looks_like_a_song("Bad and Boujee (feat. Lil Uzi Vert)"))
        self.assertFalse(_looks_like_a_song("Dreams and Nightmares"))
        self.assertFalse(_looks_like_a_song("PROJECT X"))
