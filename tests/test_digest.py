"""Daily Digest (fork v24): aggregates new releases, wishlist finds, chart movement and
recommendations, each section failing on its own without taking down the rest of the page."""

import json
import time
import unittest

from tagcast.charts import Charts
from tagcast.digest import Digest
from tagcast.recommendations import Recommendations
from tagcast.releases import NewReleases
from tagcast.wishlist import Wishlist

from test_discography import LocalStudio as DiscographyStudio
from test_discography import FakeHttp


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
    """Charts' requests.Session() stand-in. A list of answers for one key is consumed in
    order (last one repeats), so a test can simulate a chart changing between two fetches."""

    def __init__(self, answers):
        self.answers = {k: (list(v) if isinstance(v, list) else [v]) for k, v in (answers or {}).items()}
        self.headers, self.calls = {}, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for key, values in self.answers.items():
            if key in url:
                return values.pop(0) if len(values) > 1 else values[0]
        return FakeResponse(status=404)


class LocalStudio(DiscographyStudio):
    def __init__(self, http_answers=None, session_answers=None, seeds=()):
        super().__init__()
        self.wishlist = Wishlist(self, scheduler=False)
        self.new_releases = NewReleases(self, scheduler=False)
        self.new_releases.state["artists"] = list(seeds)
        self.new_releases.state["auto"] = False
        self.http = FakeHttp(dict(http_answers or {}))
        self.discography.http = self.http
        self.session = FakeSession(dict(session_answers or {}))
        self.charts = Charts(self, http=self.http, session=self.session)
        self.recommendations = Recommendations(self, http=self.http)
        self.digest = Digest(self)


def netease_json(songs):
    return json.dumps({"result": {"tracks": [
        {"id": i, "name": title, "artists": [{"name": artist}], "album": {"name": ""}}
        for i, (title, artist) in enumerate(songs, 1)]}})


DEEZER_SEED = {"data": [{"id": 1, "name": "Seed Artist", "nb_fan": 500}]}
DEEZER_RELATED = {"data": [{"id": 2, "name": "Related Act"}]}
DEEZER_TOP = {"data": [{"title": "Related Song"}]}


class NewReleasesSectionTests(unittest.TestCase):
    def test_lists_undismissed_results_and_unseen_count(self):
        studio = LocalStudio()
        studio.new_releases.state["results"] = [
            {"id": "r1", "artist": "New Artist", "title": "New Album", "status": "missing", "dismissed": False}]
        studio.new_releases.state["unseen"] = 1
        data = studio.digest.view()
        section = data["new_releases"]
        self.assertTrue(section["ok"])
        self.assertEqual(section["total"], 1)
        self.assertEqual(section["unseen"], 1)
        self.assertEqual(section["results"][0]["artist"], "New Artist")

    def test_dismissed_results_are_left_out(self):
        studio = LocalStudio()
        studio.new_releases.state["results"] = [
            {"id": "r1", "artist": "A", "title": "B", "status": "missing", "dismissed": True}]
        data = studio.digest.view()
        self.assertEqual(data["new_releases"]["total"], 0)


class WishlistFoundSectionTests(unittest.TestCase):
    def test_items_found_before_the_last_mark_seen_are_not_new(self):
        studio = LocalStudio()
        studio.wishlist.state["found"] = [{"kind": "song", "artist": "A", "title": "B", "source": "Bandcamp",
                                           "note": "", "when": time.time()}]
        data = studio.digest.view()
        self.assertEqual(data["wishlist_found"]["new_count"], 1)
        self.assertTrue(data["wishlist_found"]["items"][0]["is_new"])
        studio.digest.mark_found_seen()
        data = studio.digest.view()
        self.assertEqual(data["wishlist_found"]["new_count"], 0)
        self.assertFalse(data["wishlist_found"]["items"][0]["is_new"])

    def test_marking_seen_never_mutates_wishlists_own_state(self):
        studio = LocalStudio()
        studio.wishlist.state["found"] = [{"kind": "song", "artist": "A", "title": "B", "source": "Bandcamp",
                                           "note": "", "when": time.time()}]
        studio.digest.view()
        self.assertNotIn("is_new", studio.wishlist.state["found"][0])


class ChartMovementSectionTests(unittest.TestCase):
    def test_first_scan_has_no_movement_but_saves_a_snapshot(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(netease_json([("Song A", "Artist A")]))})
        data = studio.digest.view()
        section = data["charts"]["netease_cn_rap"]
        self.assertTrue(section["ok"])
        self.assertTrue(section["first_scan"])
        self.assertEqual(section["new_entries"], [])
        self.assertIn("netease_cn_rap", studio.digest.state["chart_snapshots"])

    def test_a_changed_chart_reports_only_the_genuinely_new_entries(self):
        studio = LocalStudio(session_answers={"music.163.com": [
            FakeResponse(netease_json([("Song A", "Artist A"), ("Song B", "Artist B")])),
            FakeResponse(netease_json([("Song B", "Artist B"), ("Song C", "Artist C")])),
        ]})
        studio.digest.view()  # first scan: snapshot = {A, B}
        studio.charts.view("netease_cn_rap", refresh=True)  # chart now reads {B, C}
        data = studio.digest.view()
        section = data["charts"]["netease_cn_rap"]
        self.assertFalse(section["first_scan"])
        self.assertEqual([(e["title"], e["artist"]) for e in section["new_entries"]], [("Song C", "Artist C")])

    def test_a_failing_chart_errors_on_its_own_without_breaking_other_sections(self):
        studio = LocalStudio(session_answers={"music.163.com": FakeResponse(netease_json([("Song A", "Artist A")]))})
        data = studio.digest.view()
        self.assertTrue(data["charts"]["netease_cn_rap"]["ok"])
        self.assertFalse(data["charts"]["hotnewhiphop_top100"]["ok"])
        self.assertIn("error", data["charts"]["hotnewhiphop_top100"])


class RecommendationsSectionTests(unittest.TestCase):
    def test_each_recommendation_source_is_isolated(self):
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": DEEZER_RELATED, "/artist/2/top": DEEZER_TOP})
        data = studio.digest.view()
        self.assertTrue(data["recommendations"]["deezer"]["ok"])
        self.assertEqual(data["recommendations"]["deezer"]["rows"][0]["artist"], "Related Act")
        self.assertEqual(list(data["recommendations"]), ["deezer"])  # the Gemini list is gone (v44)


if __name__ == "__main__":
    unittest.main()
