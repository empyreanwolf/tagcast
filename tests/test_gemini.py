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
    """requests.Session() stand-in for Gemini's generateContent POST."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])  # queue of raw model text, consumed in order
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "json": json})
        if not self.replies:
            return FakeResponse(status=500)
        text = self.replies.pop(0)
        body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        import json as jsonlib
        return FakeResponse(jsonlib.dumps(body))


class LocalStudio(DiscographyStudio):
    def __init__(self, soulseek=False, replies=None):
        super().__init__(soulseek)
        self.wishlist = Wishlist(self, scheduler=False)
        self.session = FakeSession(replies)
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
        self.assertEqual(out, {"has_key": True, "model": "gemini-2.5-flash"})
        self.assertNotIn("secret", json.dumps(out))

    def test_blank_key_clears_it(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "secret-key"})
        self.assertTrue(studio.gemini.configured())
        studio.gemini.save_settings({"api_key": ""})
        self.assertFalse(studio.gemini.configured())

    def test_key_with_whitespace_is_rejected(self):
        studio = LocalStudio()
        with self.assertRaises(GeminiError):
            studio.gemini.save_settings({"api_key": "bad key with spaces"})

    def test_settings_persist_across_a_restart(self):
        studio = LocalStudio()
        studio.gemini.save_settings({"api_key": "secret-key", "model": "gemini-1.5-flash"})
        again = Gemini(studio, session=studio.session)
        self.assertEqual(again.settings(), {"has_key": True, "model": "gemini-1.5-flash"})


class AskTests(unittest.TestCase):
    def test_plain_reply_with_no_json_block_is_shown_as_is(self):
        studio = LocalStudio(replies=["Coldplay are a British rock band formed in 1996."])
        studio.gemini.save_settings({"api_key": "k"})
        data = studio.gemini.ask("Who are Coldplay?")
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][0], {"role": "user", "text": "Who are Coldplay?"})
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
