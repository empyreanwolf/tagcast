import threading
import unittest
from unittest import mock

from tagcast import lyrics_job
from tagcast.lyrics_job import LyricsJob, gd_queries, lrc_from_lines, lrc_plain_preview


class FakeLibrary:
    def __init__(self):
        self.tracks = {1: {}, 2: {}, 3: {}, 4: {"trashed": True}, 5: {}}
        self.albums = {10: {"name": "Album"}}
        self.titles = {1: ("Has Lyrics", "Artist A"), 2: ("Found Song", "Artist B"),
                       3: ("夜曲", "周杰伦"), 5: ("Nothing Here", "Artist C")}

    def track_view(self, track_id):
        title, artist = self.titles[track_id]
        return {"id": str(track_id), "album_id": "10", "title": title, "artist": artist, "length": 200}


class FakeClient:
    def __init__(self):
        self.userdata = {"1": [{"type": "lyrics", "value": "old words"}], "2": [], "3": [], "5": []}
        self.updates = []

    def _jsonrequest(self, action, **kw):
        assert action == "update_track"
        entry = kw["tracks"][0]
        self.userdata[str(entry["file_id"])] = entry["userdata"]
        self.updates.append(entry)
        return {}


class FakeLyrics:
    def __init__(self, client):
        self.client = client

    def _userdata(self, client, track_id):
        rows = list(client.userdata[track_id])
        lyric = [r for r in rows if r.get("type") == "lyrics"]
        return rows, lyric[0]["value"] if lyric else ""

    def _selection(self, body):
        raise RuntimeError("no cache in the fake")


class FakeApi:
    def __init__(self):
        self.searches = []

    def search(self, source, query, count=10, page=1):
        self.searches.append((source, query))
        if source == "netease" and "夜曲" in query:
            return [{"id": "9", "title": "夜曲", "artists": ["周杰倫"], "album": "十一月的蕭邦", "pic_id": "", "lyric_id": "9", "source": "netease"}]
        if source == "netease" and "Nothing" in query:
            return [{"id": "7", "title": "Nothing Here (Remix)", "artists": ["Artist C"], "album": "X", "pic_id": "", "lyric_id": "7", "source": "netease"}]
        return []

    def lyric(self, source, lyric_id, low=True):
        if lyric_id == "9":
            return "[ar:x]\n作词 : 方文山\n[00:10.00]一群嗜血的蚂蚁\n[01:30.50]被腐肉所吸引\n[03:00.00]我面无表情\n"
        return ""


class FakeStudio:
    def __init__(self):
        self.client = FakeClient()
        self.library = FakeLibrary()
        self.lyrics = FakeLyrics(self.client)
        self.premium = True
        self.save_lock = threading.Lock()
        self.lock = threading.RLock()
        self.playback_session = "s"
        self.files = {}
        self.galaxy = mock.Mock()
        self.galaxy.api = FakeApi()
        self.galaxy.limiter.status.return_value = {"used": 1, "quota": 45, "wait": 0}

    def _require_client(self):
        return self.client

    def _library(self):
        return self.library, "7"

    def _read(self, name):
        return self.files.get(name)

    def _write(self, name, data):
        self.files[name] = data


def lrclib_for(track, album, check):
    if track["title"] == "Found Song":
        return {"status": "ready", "source": "lrclib", "timed": True,
                "lines": [{"start": 1.5, "end": 5, "text": "hello"}, {"start": 65.25, "end": 70, "text": "world"}]}
    return {"status": "missing", "source": "lrclib", "timed": False, "lines": []}


class LyricsJobTests(unittest.TestCase):
    def run_job(self, studio=None):
        studio = studio or FakeStudio()
        job = LyricsJob(studio)
        job.running = True
        job.progress = {"done": 0, "total": 0, "name": "", "phase": ""}
        with mock.patch.object(lyrics_job, "fetch_lrclib_display", side_effect=lrclib_for), \
                mock.patch.object(lyrics_job, "SONG_PAUSE", 0), mock.patch.object(lyrics_job, "READ_PAUSE", 0):
            job._run(studio.client, studio.library, "7")
        return studio, job

    def test_lrc_text_and_preview(self):
        timed = {"timed": True, "lines": [{"start": 1.5, "text": "a"}, {"start": 65.25, "text": "b"}]}
        self.assertEqual(lrc_from_lines(timed), "[00:01.50]a\n[01:05.25]b")
        plain = {"timed": False, "lines": [{"start": None, "text": "a"}, {"start": None, "text": "b"}]}
        self.assertEqual(lrc_from_lines(plain), "a\nb")
        self.assertEqual(lrc_plain_preview("[00:01.00]one\n[00:02.00]\n[00:03.00]two"), ["one", "two"])

    def test_gd_queries_try_title_alone_and_simplified(self):
        queries = gd_queries({"title": "夜曲", "artist": "周杰倫 & Someone"})
        self.assertEqual(queries[0], "夜曲 周杰倫")
        self.assertIn("夜曲", queries)
        self.assertTrue(any("周杰伦" in q for q in queries))

    def test_run_saves_sure_matches_only_and_lists_unsure_ones(self):
        studio, job = self.run_job()
        data = studio.files["lyrics-job.json"]
        self.assertEqual(data["checked"], {"1": "has", "2": "saved", "3": "saved", "5": "review"})
        self.assertEqual(studio.client.userdata["1"], [{"type": "lyrics", "value": "old words"}])  # never replaced
        self.assertEqual(studio.client.userdata["2"], [{"type": "lyrics", "value": "[00:01.50]hello\n[01:05.25]world"}])
        saved = studio.client.userdata["3"][0]["value"]
        self.assertTrue(saved.startswith("[00:10.00]一群嗜血的蚂蚁"))
        self.assertNotIn("作词", saved)
        self.assertEqual(data["review"]["5"]["candidates"][0]["title"], "Nothing Here (Remix)")
        self.assertFalse(job.running)

    def test_second_run_continues_and_skips_checked_songs(self):
        studio, _ = self.run_job()
        count = len(studio.client.updates)
        studio2, _ = self.run_job(studio)
        self.assertEqual(len(studio.client.updates), count)

    def test_pick_saves_the_chosen_candidate(self):
        studio, job = self.run_job()
        studio.galaxy.api.lyric = lambda source, lyric_id, low=True: "[00:05.00]one\n[00:20.00]two\n[01:00.00]three"
        answer = job.pick("5", "netease", "7")
        self.assertEqual(answer["status"], "saved")
        self.assertEqual(studio.files["lyrics-job.json"]["checked"]["5"], "saved")
        self.assertNotIn("5", studio.files["lyrics-job.json"]["review"])

    def test_autosave_fills_only_songs_without_lyrics(self):
        studio = FakeStudio()
        job = LyricsJob(studio)
        lines = [{"start": 65.25, "text": "world"}, {"start": 1.5, "text": " hello "}]
        answer = job.autosave({"track_id": "2", "source": "lrclib", "lines": lines})
        self.assertEqual(answer["status"], "saved")
        self.assertTrue(answer["confirmed"])
        self.assertEqual(studio.client.userdata["2"], [{"type": "lyrics", "value": "[00:01.50]hello\n[01:05.25]world"}])
        self.assertEqual(studio.files["lyrics-job.json"]["checked"]["2"], "saved")
        answer = job.autosave({"track_id": "1", "source": "unison", "lines": lines})
        self.assertEqual(answer["status"], "has")
        self.assertEqual(studio.client.userdata["1"], [{"type": "lyrics", "value": "old words"}])  # never replaced

    def test_autosave_refuses_bad_input(self):
        job = LyricsJob(FakeStudio())
        good = [{"start": 1, "text": "a"}, {"start": 2, "text": "b"}]
        for body in ({"track_id": "x", "source": "lrclib", "lines": good},
                     {"track_id": "2", "source": "ibroadcast", "lines": good},
                     {"track_id": "2", "source": "lrclib", "lines": [{"start": "1", "text": "a"}, {"start": 2, "text": "b"}]},
                     {"track_id": "2", "source": "lrclib", "lines": [{"start": 1, "text": ""}, {"start": 2, "text": "b"}]},
                     {"track_id": "2", "source": "lrclib", "lines": [{"start": 1, "text": "a"}, {"start": 400, "text": "b"}]}):
            with self.assertRaises(lyrics_job.LibraryError):
                job.autosave(body)
        job.studio.premium = False
        with self.assertRaises(lyrics_job.LibraryError):
            job.autosave({"track_id": "2", "source": "lrclib", "lines": good})

    def test_dismiss_removes_from_the_list(self):
        studio, job = self.run_job()
        job.dismiss("5")
        self.assertEqual(studio.files["lyrics-job.json"]["checked"]["5"], "skipped")

    def test_pause_holds_the_run_and_stop_ends_it(self):
        studio = FakeStudio()
        job = LyricsJob(studio)
        job.running = job.paused = True
        job.progress = {"done": 0, "total": 0, "name": "", "phase": ""}
        job.pause_flag.set()
        with mock.patch.object(lyrics_job, "fetch_lrclib_display", side_effect=lrclib_for):
            thread = threading.Thread(target=job._run, args=(studio.client, studio.library, "7"))
            thread.start()
            thread.join(.6)
            self.assertTrue(thread.is_alive())
            self.assertEqual(studio.client.updates, [])
            job.stop_flag.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(job.running)
        self.assertEqual(studio.client.updates, [])

    def test_needs_premium(self):
        studio = FakeStudio()
        studio.premium = False
        with self.assertRaises(Exception):
            LyricsJob(studio).start()

    def test_status_counts(self):
        studio, job = self.run_job()
        status = job.status()
        self.assertEqual(status["counts"]["saved"], 2)
        self.assertEqual(status["remaining"], 0)
        self.assertEqual(status["review_count"], 1)


if __name__ == "__main__":
    unittest.main()
