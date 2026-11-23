"""Fork v117: wishlist retries respect the search line, pause and stop; the search trail; no second copy of a running song."""

import threading
import time
import unittest
from unittest import mock

from tagcast.discography import Discography, SearchStopped


class Studio:
    def _read(self, name):
        return {}


class PausePointTests(unittest.TestCase):
    def setUp(self):
        self.d = Discography(Studio(), http=mock.Mock())

    def test_returns_at_once_when_nothing_is_searching(self):
        started = time.time()
        self.d.pause_point(self.d._stop_gen)
        self.assertLess(time.time() - started, 0.3)

    def test_waits_while_paused_and_reports_why(self):
        self.d.search_paused = True
        reasons, done = [], threading.Event()

        def run():
            self.d.pause_point(self.d._stop_gen, reasons.append)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        time.sleep(0.7)
        self.assertFalse(done.is_set())
        self.assertIn("search paused", reasons)
        self.d.search_paused = False
        self.assertTrue(done.wait(2))

    def test_waits_for_a_running_search_job(self):
        with self.d._turns_lock:
            self.d._turns.append({"name": "Someone"})
        done = threading.Event()
        threading.Thread(target=lambda: (self.d.pause_point(self.d._stop_gen), done.set()), daemon=True).start()
        time.sleep(0.7)
        self.assertFalse(done.is_set())
        with self.d._turns_lock:
            self.d._turns.clear()
        self.assertTrue(done.wait(2))

    def test_stop_search_ends_the_wait(self):
        self.d.search_paused = True
        gen, seen = self.d._stop_gen, []

        def run():
            try:
                self.d.pause_point(gen)
            except SearchStopped:
                seen.append("stopped")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.2)
        self.d.search_control("stop")
        thread.join(2)
        self.assertEqual(seen, ["stopped"])


if __name__ == "__main__":
    unittest.main()


class WholeWordArtistTests(unittest.TestCase):
    def test_a_release_by_impact_is_not_by_pact(self):
        from tagcast.youtube import YouTube
        from test_discography import LocalStudio

        entries = [{"url": "https://music.youtube.com/playlist?list=A", "title": "Album - Night", "channel": "ImPact"},
                   {"url": "https://music.youtube.com/playlist?list=B", "title": "Album - Day", "channel": "Pact"}]
        yt = YouTube(LocalStudio(), search=lambda q, playlists=False: entries, fetch_audio=lambda *a: None)
        found = yt._music_releases("Pact")
        self.assertEqual([r["title"] for r in found], ["Day"])


class GalaxyBlockedTests(unittest.TestCase):
    def test_a_firewall_block_is_not_reported_as_no_link(self):
        from tagcast.galaxy import Galaxy, GalaxyBlocked
        from test_galaxy import LocalStudio as GStudio
        studio = GStudio()
        galaxy = studio.galaxy

        class Api:
            calls = 0

            def url(self, *a):
                Api.calls += 1
                raise GalaxyBlocked("blocked")

            def search(self, *a, **k):
                raise AssertionError("must not search other sources while blocked")
        galaxy.api = Api()
        job = {"id": "x", "title": "Song", "payload": {"source": "netease", "id": "1", "artists": ["A"], "album": "B"}}
        with self.assertRaises(GalaxyBlocked):
            galaxy.get_link(job)
        self.assertEqual(Api.calls, 1)
        self.assertEqual(galaxy._dead, {})
