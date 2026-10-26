"""Hip-hop charts (fork v20): fetchers, caching, and "Get it" routing."""

import json
import time
import unittest

from tagcast.charts import Charts, ChartsError

from test_discography import LocalStudio as DiscographyStudio
from test_discography import FakeHttp
from test_discography import wait_job
from test_galaxy import song as gd_song
from tagcast.wishlist import Wishlist


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """requests.Session() stand-in: matches by URL, keyed loosely like FakeHttp."""

    def __init__(self, answers):
        self.answers, self.headers, self.calls = answers, {}, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for key, value in self.answers.items():
            if key in url:
                return value() if callable(value) else value
        return FakeResponse(status=404)


class LocalStudio(DiscographyStudio):
    def __init__(self, soulseek=False, http_answers=None, session_answers=None):
        super().__init__(soulseek)
        self.wishlist = Wishlist(self, scheduler=False)
        self.http = FakeHttp(dict(http_answers or {}))
        self.discography.http = self.http
        self.session = FakeSession(dict(session_answers or {}))
        self.charts = Charts(self, http=self.http, session=self.session)


def wait_progress(charts, chart_id):
    for _ in range(200):
        if not charts.progress.get(chart_id):
            return charts.jobs_view()
        time.sleep(0.02)
    raise AssertionError("charts get-others did not finish")


NETEASE_JSON = json.dumps({"result": {"tracks": [
    {"id": 501, "name": "Song One", "artists": [{"name": "Rapper A"}], "album": {"name": "Album A", "picId": 999}},
    {"id": 502, "name": "Song Two", "artists": [{"name": "Rapper B"}], "album": {"name": "Album B", "picId": 998}},
]}})

HNHH_HTML = """
<a href="/1014105-live-and-die-french-montana"><img src="x.jpg">Live and Die</a>
<a href="/artist/french-montana">French Montana</a>
<a href="/1014106-another-song-x"><img src="y.jpg">Another Song</a>
<a href="/artist/some-rapper">Some Rapper</a>
"""

DEEZER_GENRES = {"data": [{"id": 0, "name": "All"}, {"id": 116, "name": "Rap/Hip Hop"}]}
DEEZER_CHART = {"tracks": {"data": [
    {"title": "Deezer Song", "artist": {"name": "Deezer Rapper"}, "album": {"title": "Deezer Album"}},
]}}

APPLE_FEED = {"feed": {"entry": [
    {"im:name": {"label": "Apple Song"}, "im:artist": {"label": "Apple Rapper"}},
    {"im:name": {"label": ""}, "im:artist": {"label": "Skip Me"}},  # dropped: no title
]}}

NEXT_DATA_HTML = """<html><script id="__NEXT_DATA__" type="application/json">{json}</script></html>"""


def next_data_html(entries):
    return NEXT_DATA_HTML.format(json=json.dumps({"props": {"pageProps": {"chart": entries}}}))


class ChartsListViewTests(unittest.TestCase):
    def test_list_has_all_six_charts_grouped(self):
        studio = LocalStudio()
        charts = studio.charts.list()
        self.assertEqual(set(charts), {"netease_cn_rap", "hotnewhiphop_top100", "deezer_hiphop",
                                        "apple_hiphop", "shazam_hiphop_us", "billboard_rap"})
        self.assertEqual(charts["netease_cn_rap"]["group"], "chinese")
        self.assertEqual(charts["hotnewhiphop_top100"]["group"], "mainstream")

    def test_view_unknown_chart_raises(self):
        studio = LocalStudio()
        with self.assertRaises(Exception):
            studio.charts.view("not_a_chart")

    def test_view_caches_until_refresh(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(NETEASE_JSON)})
        first = studio.charts.view("netease_cn_rap")
        self.assertEqual(len(first["rows"]), 2)
        calls = len(studio.session.calls)
        again = studio.charts.view("netease_cn_rap")
        self.assertEqual(len(studio.session.calls), calls)  # cached, no new fetch
        self.assertEqual(again["when"], first["when"])
        studio.charts.view("netease_cn_rap", refresh=True)
        self.assertEqual(len(studio.session.calls), calls + 1)


class NeteaseFetchTests(unittest.TestCase):
    def test_parses_tracks_and_carries_a_real_track_id(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(NETEASE_JSON)})
        data = studio.charts.view("netease_cn_rap")
        self.assertEqual([r["title"] for r in data["rows"]], ["Song One", "Song Two"])
        row = data["rows"][0]
        self.assertEqual((row["source"], row["source_id"], row["artist"], row["rank"]), ("netease", "501", "Rapper A", 1))

    def test_empty_tracks_is_a_clean_error(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(json.dumps({"result": {"tracks": []}}))})
        data = studio.charts.view("netease_cn_rap")
        self.assertEqual(data["rows"], [])
        self.assertTrue(data["error"])

    def test_bad_json_is_a_clean_error_not_a_crash(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse("not json")})
        data = studio.charts.view("netease_cn_rap")
        self.assertEqual(data["rows"], [])
        self.assertIn("NetEase", data["error"])

    def test_http_error_is_a_clean_error(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse("", status=500)})
        data = studio.charts.view("netease_cn_rap")
        self.assertEqual(data["rows"], [])
        self.assertTrue(data["error"])


class HotNewHipHopFetchTests(unittest.TestCase):
    def test_parses_title_artist_pairs_from_link_patterns(self):
        studio = LocalStudio(session_answers={"hotnewhiphop.com": FakeResponse(HNHH_HTML)})
        data = studio.charts.view("hotnewhiphop_top100")
        self.assertEqual([(r["title"], r["artist"]) for r in data["rows"]],
                          [("Live and Die", "French Montana"), ("Another Song", "Some Rapper")])
        self.assertEqual(data["rows"][0]["rank"], 1)

    def test_no_matches_is_a_clean_error(self):
        studio = LocalStudio(session_answers={"hotnewhiphop.com": FakeResponse("<html>nothing here</html>")})
        data = studio.charts.view("hotnewhiphop_top100")
        self.assertEqual(data["rows"], [])
        self.assertIn("HotNewHipHop", data["error"])


class DeezerFetchTests(unittest.TestCase):
    def test_discovers_genre_id_by_name_and_reads_its_chart(self):
        studio = LocalStudio(http_answers={"/genre": DEEZER_GENRES, "/chart/116": DEEZER_CHART})
        data = studio.charts.view("deezer_hiphop")
        self.assertEqual([(r["title"], r["artist"], r["album"]) for r in data["rows"]],
                          [("Deezer Song", "Deezer Rapper", "Deezer Album")])

    def test_no_hiphop_genre_is_a_clean_error(self):
        studio = LocalStudio(http_answers={"/genre": {"data": [{"id": 0, "name": "All"}]}})
        data = studio.charts.view("deezer_hiphop")
        self.assertEqual(data["rows"], [])
        self.assertIn("Deezer", data["error"])


class AppleFetchTests(unittest.TestCase):
    def test_parses_feed_and_skips_incomplete_entries(self):
        studio = LocalStudio(http_answers={"/topsongs/limit=100/genre=18/json": APPLE_FEED})
        data = studio.charts.view("apple_hiphop")
        self.assertEqual([(r["title"], r["artist"]) for r in data["rows"]], [("Apple Song", "Apple Rapper")])

    def test_empty_feed_is_a_clean_error(self):
        studio = LocalStudio(http_answers={"/topsongs/limit=100/genre=18/json": {"feed": {"entry": []}}})
        data = studio.charts.view("apple_hiphop")
        self.assertEqual(data["rows"], [])
        self.assertTrue(data["error"])


class ShazamBillboardFetchTests(unittest.TestCase):
    def test_shazam_reads_next_data_blob(self):
        html = next_data_html([{"title": "Shaz Song", "artist": "Shaz Rapper"}])
        studio = LocalStudio(session_answers={"shazam.com": FakeResponse(html)})
        data = studio.charts.view("shazam_hiphop_us")
        self.assertEqual([(r["title"], r["artist"]) for r in data["rows"]], [("Shaz Song", "Shaz Rapper")])

    def test_billboard_reads_next_data_blob_with_artists_list(self):
        html = next_data_html([{"name": "Bbd Song", "artists": [{"name": "Bbd Rapper"}]}])
        studio = LocalStudio(session_answers={"billboard.com": FakeResponse(html)})
        data = studio.charts.view("billboard_rap")
        self.assertEqual([(r["title"], r["artist"]) for r in data["rows"]], [("Bbd Song", "Bbd Rapper")])

    def test_missing_next_data_is_a_clean_error(self):
        studio = LocalStudio(session_answers={"shazam.com": FakeResponse("<html>no data here</html>")})
        data = studio.charts.view("shazam_hiphop_us")
        self.assertEqual(data["rows"], [])
        self.assertIn("Shazam", data["error"])


class StatusAndGetTests(unittest.TestCase):
    def test_rows_are_checked_against_the_library(self):
        netease = json.dumps({"result": {"tracks": [
            {"id": 9, "name": "Yellow", "artists": [{"name": "Coldplay"}], "album": {"name": "Parachutes"}},
        ]}})
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(netease)})
        data = studio.charts.view("netease_cn_rap")
        self.assertEqual(data["rows"][0]["status"], "have")  # library already has Coldplay - Yellow

    def test_get_netease_rows_goes_straight_through_galaxy(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(NETEASE_JSON)})
        studio.charts.view("netease_cn_rap")
        result = studio.charts.get("netease_cn_rap", [1, 2])
        self.assertEqual(result, {"queued": 2, "jobs": 0})
        queued_titles = {i["title"] for i in studio.download_manager.state["queue"]}
        self.assertEqual(queued_titles, {"Song One", "Song Two"})
        # explicitly tagged "netease" regardless of whatever Galaxy's own UI source is set to
        payloads = [i["payload"]["source"] for i in studio.download_manager.state["queue"]]
        self.assertEqual(set(payloads), {"netease"})

    def test_get_other_rows_runs_as_a_background_job_and_lands_on_wishlist_when_not_found(self):
        studio = LocalStudio(session_answers={"hotnewhiphop.com": FakeResponse(HNHH_HTML)})
        studio.charts.view("hotnewhiphop_top100")
        result = studio.charts.get("hotnewhiphop_top100", [1, 2])
        self.assertEqual(result, {"queued": 0, "jobs": 2})
        jobs = wait_progress(studio.charts, "hotnewhiphop_top100")
        self.assertEqual(len(jobs["jobs"]), 2)
        self.assertTrue(all(not j["source"] for j in jobs["jobs"]))
        self.assertTrue(all("on your wishlist" in j["note"] for j in jobs["jobs"]))
        self.assertEqual({i["title"] for i in studio.wishlist.view()["items"]}, {"Live and Die", "Another Song"})

    def test_get_other_rows_finds_a_song_through_galaxy_fallback(self):
        studio = LocalStudio(http_answers={"/topsongs/limit=100/genre=18/json": APPLE_FEED})
        studio.charts.view("apple_hiphop")
        studio.gd.search_results[("netease", "Apple Rapper Apple Song", 1)] = [
            gd_song(77, "Apple Song", "Apple Rapper", "Some Album")]
        studio.charts.get("apple_hiphop", [1])
        jobs = wait_progress(studio.charts, "apple_hiphop")
        self.assertEqual(jobs["jobs"][0]["source"], "Galaxy")
        self.assertEqual({i["title"] for i in studio.download_manager.state["queue"]}, {"Apple Song"})

    def test_get_with_no_rows_cached_raises(self):
        studio = LocalStudio()
        with self.assertRaises(Exception):
            studio.charts.get("netease_cn_rap", [1])

    def test_get_with_no_matching_ranks_raises(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(NETEASE_JSON)})
        studio.charts.view("netease_cn_rap")
        with self.assertRaises(Exception):
            studio.charts.get("netease_cn_rap", [99])

    def test_jobs_view_keeps_most_recent_first(self):
        studio = LocalStudio(session_answers={"hotnewhiphop.com": FakeResponse(HNHH_HTML)})
        studio.charts.view("hotnewhiphop_top100")
        studio.charts.get("hotnewhiphop_top100", [1, 2])
        wait_progress(studio.charts, "hotnewhiphop_top100")
        jobs = studio.charts.jobs_view()["jobs"]
        self.assertEqual(jobs[0]["title"], "Another Song")  # rank 2 finishes after rank 1, so it's most recent


if __name__ == "__main__":
    unittest.main()
