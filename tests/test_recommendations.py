"""Recommendations (fork v23): Deezer related-artist list + Gemini-based list."""

import json
import time
import unittest

from tagcast.gemini import Gemini
from tagcast.recommendations import Recommendations, RecommendationsError
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


class FakeGeminiSession:
    def __init__(self, replies=None):
        self.replies, self.calls = list(replies or []), []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "json": json})
        import json as jsonlib
        if not self.replies:
            return FakeResponse(status=500)
        text = self.replies.pop(0)
        return FakeResponse(jsonlib.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}))


def gemini_reply(songs):
    body = json.dumps({"songs": songs, "similar_artists": []})
    return f"Here you go.\n```json\n{body}\n```"


class LocalStudio(DiscographyStudio):
    def __init__(self, seeds=("Seed Artist",), http_answers=None, gemini_replies=None, gemini_key="k"):
        super().__init__()
        self.wishlist = Wishlist(self, scheduler=False)
        self.new_releases = NewReleases(self, scheduler=False)
        self.new_releases.state["artists"] = list(seeds)
        self.new_releases.state["auto"] = False
        self.http = FakeHttp(dict(http_answers or {}))
        self.discography.http = self.http
        self.gemini = Gemini(self, session=FakeGeminiSession(gemini_replies))
        if gemini_key:
            self.gemini.state["api_key"] = gemini_key
        self.recommendations = Recommendations(self, http=self.http)


def wait_progress(recs, source):
    for _ in range(200):
        if not recs.progress.get(source):
            return recs.jobs_view()
        time.sleep(0.02)
    raise AssertionError("recommendations get did not finish")


DEEZER_SEED = {"data": [{"id": 1, "name": "Seed Artist", "nb_fan": 500}]}
DEEZER_RELATED = {"data": [{"id": 2, "name": "Related Act"}, {"id": 3, "name": "Also Related"}]}
DEEZER_TOP = {"data": [{"title": "Related Song"}]}


class SeedTests(unittest.TestCase):
    def test_no_seed_artists_is_a_clean_error(self):
        studio = LocalStudio(seeds=())
        data = studio.recommendations.view("deezer")
        self.assertEqual(data["rows"], [])
        self.assertIn("No artists", data["error"])


class DeezerRecommendationsTests(unittest.TestCase):
    def test_related_artists_are_tallied_and_given_a_top_track(self):
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": DEEZER_RELATED, "/artist/2/top": DEEZER_TOP,
            "/artist/3/top": {"data": []},  # no top track -- dropped
        })
        data = studio.recommendations.view("deezer")
        self.assertEqual([(r["artist"], r["title"]) for r in data["rows"]], [("Related Act", "Related Song")])
        self.assertEqual(data["seeds"], ["Seed Artist"])

    def test_never_suggests_an_artist_already_in_the_seed_list(self):
        related = {"data": [{"id": 2, "name": "Seed Artist"}]}  # Deezer relates it to itself-ish case
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": related})
        data = studio.recommendations.view("deezer")
        self.assertEqual(data["rows"], [])
        self.assertIn("didn't suggest anyone new", data["error"])

    def test_seed_not_found_on_deezer_is_a_clean_error(self):
        studio = LocalStudio(seeds=("Nobody",), http_answers={"/search/artist": {"data": []}})
        data = studio.recommendations.view("deezer")
        self.assertEqual(data["rows"], [])
        self.assertIn("doesn't recognize", data["error"])

    def test_view_caches_until_refresh(self):
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": DEEZER_RELATED, "/artist/2/top": DEEZER_TOP,
            "/artist/3/top": {"data": []}})
        first = studio.recommendations.view("deezer")
        calls = len(studio.http.calls)
        again = studio.recommendations.view("deezer")
        self.assertEqual(len(studio.http.calls), calls)
        self.assertEqual(again["when"], first["when"])
        studio.recommendations.view("deezer", refresh=True)
        self.assertGreater(len(studio.http.calls), calls)


class GeminiRecommendationsTests(unittest.TestCase):
    def test_no_key_is_a_clean_error(self):
        studio = LocalStudio(seeds=("Seed Artist",), gemini_key="")
        data = studio.recommendations.view("gemini")
        self.assertEqual(data["rows"], [])
        self.assertIn("Add your free Gemini API key", data["error"])

    def test_parses_the_same_json_block_ask_gemini_uses(self):
        reply = gemini_reply([{"artist": "New Act", "title": "New Song", "album": "New Album"}])
        studio = LocalStudio(seeds=("Seed Artist",), gemini_replies=[reply])
        data = studio.recommendations.view("gemini")
        self.assertEqual([(r["artist"], r["title"], r["album"]) for r in data["rows"]],
                          [("New Act", "New Song", "New Album")])

    def test_drops_any_artist_gemini_echoes_back_from_the_seed_list(self):
        reply = gemini_reply([{"artist": "Seed Artist", "title": "Old Song", "album": ""},
                               {"artist": "New Act", "title": "New Song", "album": ""}])
        studio = LocalStudio(seeds=("Seed Artist",), gemini_replies=[reply])
        data = studio.recommendations.view("gemini")
        self.assertEqual([r["artist"] for r in data["rows"]], ["New Act"])

    def test_gemini_http_error_is_a_clean_error_not_a_crash(self):
        studio = LocalStudio(seeds=("Seed Artist",), gemini_replies=[])  # FakeGeminiSession returns 500
        data = studio.recommendations.view("gemini")
        self.assertEqual(data["rows"], [])
        self.assertTrue(data["error"])


class GetTests(unittest.TestCase):
    def test_get_runs_bandcamp_soulseek_galaxy_same_as_charts(self):
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": DEEZER_RELATED, "/artist/2/top": DEEZER_TOP,
            "/artist/3/top": {"data": []}})
        studio.recommendations.view("deezer")
        result = studio.recommendations.get("deezer", [1])
        self.assertEqual(result, {"jobs": 1})
        jobs = wait_progress(studio.recommendations, "deezer")
        self.assertEqual(len(jobs["jobs"]), 1)
        self.assertEqual(jobs["jobs"][0]["artist"], "Related Act")

    def test_get_with_nothing_scanned_yet_raises(self):
        studio = LocalStudio()
        with self.assertRaises(Exception):
            studio.recommendations.get("deezer", [1])

    def test_get_with_no_matching_ranks_raises(self):
        studio = LocalStudio(seeds=("Seed Artist",), http_answers={
            "/search/artist": DEEZER_SEED, "/artist/1/related": DEEZER_RELATED, "/artist/2/top": DEEZER_TOP,
            "/artist/3/top": {"data": []}})
        studio.recommendations.view("deezer")
        with self.assertRaises(Exception):
            studio.recommendations.get("deezer", [99])


if __name__ == "__main__":
    unittest.main()
