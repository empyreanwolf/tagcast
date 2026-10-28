"""Ask Gemini (fork v21): chat, JSON-suggestion parsing, and "Get it" routing."""

import json
import time
import unittest

from tagcast.gemini import DEFAULT_MODEL, Gemini, GeminiError

from test_discography import LocalStudio as DiscographyStudio
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
    """requests.Session() stand-in for both Gemini's generateContent and Tavily's search POST."""

    def __init__(self, replies=None, tavily_results=None, tavily_status=200):
        self.replies = list(replies or [])  # queue of raw model text, consumed in order
        self.tavily_results = tavily_results if tavily_results is not None else []
        self.tavily_status = tavily_status
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "json": json})
        import json as jsonlib
        if "tavily.com" in url:
            if self.tavily_status >= 400:
                return FakeResponse(status=self.tavily_status)
            return FakeResponse(jsonlib.dumps({"results": self.tavily_results}))
        if not self.replies:
            return FakeResponse(status=500)
        text = self.replies.pop(0)
        body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return FakeResponse(jsonlib.dumps(body))


class LocalStudio(DiscographyStudio):
    def __init__(self, soulseek=False, replies=None, tavily_results=None, tavily_status=200):
        super().__init__(soulseek)
        self.wishlist = Wishlist(self, scheduler=False)
        self.session = FakeSession(replies, tavily_results, tavily_status)
        self.gemini = Gemini(self, session=self.session)


def wait_progress(gemini):
    for _ in range(200):
        if not gemini.progress:
            return gemini.jobs_view()
        time.sleep(0.02)
    raise AssertionError("gemini get did not finish")


def reply(text, songs=None, similar=None):
    block = json.dumps({"songs": songs or [], "similar_artists": similar or []})
    return f"{text}\n```json\n{block}\n```"


class SettingsTests(unittest.TestCase):
    def test_default_model_is_a_current_free_tier_one(self):
        studio = LocalStudio()
        self.assertEqual(studio.gemini.settings()["model"], DEFAULT_MODEL)
        self.assertNotIn(DEFAULT_MODEL, ("gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash"))

    def test_no_key_refuses_to_ask(self):
        studio = LocalStudio()
        with self.assertRaises(GeminiError):
            studio.gemini.ask("Tell me about Coldplay")

    def test_save_and_read_settings_never_echoes_the_key(self):
        studio = LocalStudio()
        out = studio.gemini.save_settings({"api_key": "secret-key", "model": "gemini-2.5-flash"})
        self.assertEqual(out, {"has_key": True, "model": "gemini-2.5-flash", "has_tavily_key": False})
        self.assertNotIn("secret", json.dumps(out))

    def test_blank_key_clears_it(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "secret-key"})
        self.assertTrue(studio.gemini.configured())
        studio.gemini.save_settings({"api_key": ""})
        self.assertFalse(studio.gemini.configured())

    def test_whitespace_in_a_pasted_key_is_removed_and_an_absurd_key_is_rejected(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "good key\nwith spaces"})
        self.assertEqual(studio.gemini.state["api_key"], "goodkeywithspaces")
        with self.assertRaises(GeminiError):
            studio.gemini.save_settings({"api_key": "x" * 201})

    def test_settings_persist_across_a_restart(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "secret-key", "model": "gemini-1.5-flash"})
        again = Gemini(studio, session=studio.session)
        self.assertEqual(again.settings(), {"has_key": True, "model": "gemini-1.5-flash", "has_tavily_key": False})


class AskTests(unittest.TestCase):
    def test_plain_reply_with_no_json_block_is_shown_as_is(self):
        studio = LocalStudio(replies=["Coldplay are a British rock band formed in 1996."])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("Who are Coldplay?")
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][0], {"role": "user", "text": "Who are Coldplay?", "searched": False})
        self.assertEqual(data["history"][1]["text"], "Coldplay are a British rock band formed in 1996.")
        self.assertEqual(data["history"][1]["songs"], [])
        self.assertEqual(data["history"][1]["similar_artists"], [])

    def test_json_block_is_parsed_into_songs_and_similar_artists(self):
        text = reply("Here's a start.", songs=[{"artist": "Coldplay", "title": "Yellow", "album": "Parachutes"},
                                                {"artist": "Coldplay", "title": "Clocks"}],
                      similar=["Snow Patrol", "Snow Patrol", "Keane"])
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("Recommend some Coldplay songs")
        turn = data["history"][1]
        self.assertEqual(turn["text"], "Here's a start.")
        self.assertEqual([(s["title"], s["artist"], s["album"]) for s in turn["songs"]],
                          [("Yellow", "Coldplay", "Parachutes"), ("Clocks", "Coldplay", "")])
        self.assertEqual(turn["similar_artists"], ["Snow Patrol", "Keane"])  # de-duplicated, order kept

    def test_songs_are_checked_against_the_library(self):
        text = reply("", songs=[{"artist": "Coldplay", "title": "Yellow"}])
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("Coldplay")
        self.assertEqual(data["history"][1]["songs"][0]["status"], "have")  # library already has it

    def test_song_ids_are_unique_and_increasing_across_turns(self):
        first = reply("", songs=[{"artist": "A", "title": "One"}])
        second = reply("", songs=[{"artist": "B", "title": "Two"}])
        studio = LocalStudio(replies=[first, second])
        studio.gemini.save_settings({"api_key": "k"})
        studio.gemini.ask("first")
        data = studio.gemini.ask("second")
        ids = [s["id"] for turn in data["history"] for s in turn.get("songs", [])]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))

    def test_malformed_json_block_falls_back_to_plain_text(self):
        text = "Some thoughts.\n```json\n{not valid json\n```"
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("anything")
        self.assertEqual(data["history"][1]["text"], "Some thoughts.")
        self.assertEqual(data["history"][1]["songs"], [])

    def test_blocked_reply_raises_a_clear_error(self):
        body = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}})
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "k"})
        studio.session.replies = []

        def post(url, params=None, json=None, timeout=None):
            return FakeResponse(body)
        studio.session.post = post
        with self.assertRaises(GeminiError):
            studio.gemini.ask("anything")

    def test_http_error_is_a_clean_error_not_a_crash(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "k"})

        def post(url, params=None, json=None, timeout=None):
            return FakeResponse(status=404)
        studio.session.post = post
        with self.assertRaises(GeminiError):
            studio.gemini.ask("anything")

    def test_empty_message_is_rejected(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "k"})
        with self.assertRaises(GeminiError):
            studio.gemini.ask("   ")

    def test_api_key_is_sent_as_a_query_param_not_logged_in_the_url(self):
        studio = LocalStudio(replies=["hi"])
        studio.gemini.save_settings({"api_key": "super-secret"})
        studio.gemini.ask("hi")
        call = studio.session.calls[0]
        self.assertEqual(call["params"], {"key": "super-secret"})
        self.assertNotIn("super-secret", call["url"])

    def test_reset_clears_the_conversation(self):
        studio = LocalStudio(replies=["hi"])
        studio.gemini.save_settings({"api_key": "k"})
        studio.gemini.ask("hi")
        self.assertTrue(studio.gemini.view()["history"])
        data = studio.gemini.reset()
        self.assertEqual(data["history"], [])


class SearchTests(unittest.TestCase):
    def test_search_without_tavily_key_raises(self):
        studio = LocalStudio(replies=["hi"])
        studio.gemini.save_settings({"api_key": "k"})
        with self.assertRaises(GeminiError):
            studio.gemini.ask("Thailand top hip hop songs", search=True)

    def test_not_searching_never_calls_tavily(self):
        studio = LocalStudio(replies=["hi"])
        studio.gemini.save_settings({"api_key": "k", "tavily_api_key": "t"})
        studio.gemini.ask("hi", search=False)
        self.assertFalse(any("tavily" in c["url"] for c in studio.session.calls))

    def test_search_results_are_sent_to_gemini_as_context_and_shown_as_sources(self):
        results = [{"title": "Thai Hip Hop Chart", "url": "https://example.com/thai-chart", "content": "1. Rapper X - Song Y"}]
        studio = LocalStudio(replies=[reply("Here's what's current.")], tavily_results=results)
        studio.gemini.save_settings({"api_key": "k", "tavily_api_key": "t"})
        data = studio.gemini.ask("Thailand top hip hop songs", search=True)
        gemini_call = next(c for c in studio.session.calls if "generativelanguage" in c["url"])
        sent_text = gemini_call["json"]["contents"][-1]["parts"][0]["text"]
        self.assertIn("Thai Hip Hop Chart", sent_text)
        self.assertIn("Rapper X - Song Y", sent_text)
        turn = data["history"][0]
        self.assertTrue(turn["searched"])
        model_turn = data["history"][1]
        self.assertEqual(model_turn["sources"], [{"title": "Thai Hip Hop Chart", "url": "https://example.com/thai-chart"}])

    def test_tavily_http_error_is_a_clean_error_not_a_crash(self):
        studio = LocalStudio(replies=["hi"], tavily_status=401)
        studio.gemini.save_settings({"api_key": "k", "tavily_api_key": "bad"})
        with self.assertRaises(GeminiError):
            studio.gemini.ask("Thailand top hip hop songs", search=True)

    def test_search_configured_reflects_tavily_key(self):
        studio = LocalStudio()
        self.assertFalse(studio.gemini.search_configured())
        studio.gemini.save_settings({"tavily_api_key": "t"})
        self.assertTrue(studio.gemini.search_configured())
        self.assertTrue(studio.gemini.settings()["has_tavily_key"])


class GetSongsTests(unittest.TestCase):
    def test_get_runs_as_a_background_job_and_lands_on_wishlist_when_not_found(self):
        text = reply("", songs=[{"artist": "French Montana", "title": "Live and Die"},
                                 {"artist": "Some Rapper", "title": "Another Song"}])
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("suggest some rap songs")
        ids = [s["id"] for s in data["history"][1]["songs"]]
        result = studio.gemini.get(ids)
        self.assertEqual(result, {"jobs": 2})
        jobs = wait_progress(studio.gemini)
        self.assertEqual(len(jobs["jobs"]), 2)
        self.assertTrue(all(not j["source"] for j in jobs["jobs"]))
        self.assertEqual({i["title"] for i in studio.wishlist.view()["items"]}, {"Live and Die", "Another Song"})

    def test_get_finds_a_song_through_galaxy_fallback(self):
        text = reply("", songs=[{"artist": "Apple Rapper", "title": "Apple Song"}])
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("anything")
        ids = [s["id"] for s in data["history"][1]["songs"]]
        studio.gd.search_results[("netease", "Apple Rapper Apple Song", 1)] = [
            gd_song(77, "Apple Song", "Apple Rapper", "Some Album")]
        studio.gemini.get(ids)
        jobs = wait_progress(studio.gemini)
        self.assertEqual(jobs["jobs"][0]["source"], "Galaxy")
        self.assertEqual({i["title"] for i in studio.download_manager.state["queue"]}, {"Apple Song"})

    def test_get_with_no_ids_raises(self):
        studio = LocalStudio()
        with self.assertRaises(GeminiError):
            studio.gemini.get([])

    def test_get_with_unknown_ids_raises(self):
        text = reply("", songs=[{"artist": "A", "title": "One"}])
        studio = LocalStudio(replies=[text])
        studio.gemini.save_settings({"api_key": "k"})
        studio.gemini.ask("x")
        with self.assertRaises(GeminiError):
            studio.gemini.get([99999])


if __name__ == "__main__":
    unittest.main()


class PastedKeyTests(unittest.TestCase):
    def test_a_pasted_key_loses_spaces_line_breaks_and_invisible_characters(self):
        from tagcast.gemini import clean_key
        self.assertEqual(clean_key(" AQ.Ab8RN6\n KOc3u​jzeQ Boo3F-T4 "), "AQ.Ab8RN6KOc3ujzeQBoo3F-T4")
        self.assertEqual(clean_key(None), "")
