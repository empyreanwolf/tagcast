import tempfile
import unittest
from pathlib import Path

import requests

from tagcast import spotsaver
from tagcast.youtube import FetchTimeout, YouTube
from test_youtube import LocalStudio, video


class FakeReply:
    def __init__(self, status=200, data=None, body=b"", headers=None):
        self.status_code, self._data, self._body, self.headers = status, data or {}, body, headers or {}
        self.ok = status < 400

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, post, get):
        self._post, self._get, self.posted = post, get, []

    def post(self, url, **kw):
        self.posted.append((url, kw.get("json")))
        return self._post

    def get(self, url, **kw):
        return self._get


AUDIO = b"ID3" + b"x" * 200000


class SpotSaverTests(unittest.TestCase):
    def test_video_id_from_the_usual_links(self):
        self.assertEqual(spotsaver.video_id("https://music.youtube.com/watch?v=4pBcZSIF_78"), "4pBcZSIF_78")
        self.assertEqual(spotsaver.video_id("https://www.youtube.com/watch?list=x&v=hMrkKT7HbpQ"), "hMrkKT7HbpQ")
        self.assertEqual(spotsaver.video_id("https://youtu.be/hMrkKT7HbpQ?t=3"), "hMrkKT7HbpQ")
        self.assertEqual(spotsaver.video_id("https://soundcloud.com/a/b"), "")

    def test_fetch_downloads_the_converted_mp3(self):
        session = FakeSession(FakeReply(data={"success": 1, "downloadUrl": "https://c/x"}),
                              FakeReply(body=AUDIO, headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(AUDIO))}))
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            path, info = spotsaver.fetch("https://music.youtube.com/watch?v=4pBcZSIF_78", tmp, seen.append,
                                         "Private Night", "DJ CHARI, Tete", session=session)
            self.assertEqual(path.read_bytes(), AUDIO)
        self.assertEqual(info, {})
        self.assertTrue(seen and seen[-1]["downloaded_bytes"] == len(AUDIO))
        url, body = session.posted[0]
        self.assertTrue(url.endswith("/api/download/"))
        self.assertEqual((body["videoId"], body["format"], body["title"]), ("4pBcZSIF_78", "mp3", "Private Night - DJ CHARI, Tete"))

    def test_bad_answers_are_errors(self):
        good = FakeReply(data={"success": 1, "downloadUrl": "https://c/x"})
        cases = [FakeSession(FakeReply(status=403), None),
                 FakeSession(FakeReply(data={"success": 0}), None),
                 FakeSession(good, FakeReply(body=b"<html>", headers={"Content-Type": "text/html"})),
                 FakeSession(good, FakeReply(body=b"tiny", headers={"Content-Type": "audio/mpeg"})),
                 FakeSession(good, FakeReply(status=500))]
        for session in cases:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(spotsaver.SpotSaverError):
                spotsaver.fetch("https://www.youtube.com/watch?v=4pBcZSIF_78", tmp, lambda d: None, "T", "A", session=session)

    def test_network_errors_are_spotsaver_errors(self):
        class Down:
            def post(self, *a, **k):
                raise requests.ConnectionError("no")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(spotsaver.SpotSaverError):
            spotsaver.fetch("https://www.youtube.com/watch?v=4pBcZSIF_78", tmp, lambda d: None, "T", session=Down())


class SpotSaverSearchTests(unittest.TestCase):
    """fork v105: SpotSaver's own title/artist search (the half of its page this module never used)."""

    def test_a_direct_video_id_is_returned(self):
        session = FakeSession(FakeReply(data={"videoId": "4pBcZSIF_78"}), None)
        self.assertEqual(spotsaver.search("Yellow", "Coldplay", session=session), "4pBcZSIF_78")
        url, body = session.posted[0]
        self.assertTrue(url.endswith("/api/get-id/"))
        self.assertEqual((body["title"], body["artist"]), ("Yellow", "Coldplay"))

    def test_an_id_inside_a_candidates_list_is_also_accepted(self):
        session = FakeSession(FakeReply(data={"candidates": [{"videoId": "abc123"}]}), None)
        self.assertEqual(spotsaver.search("Yellow", "Coldplay", session=session), "abc123")

    def test_no_match_is_a_spotsaver_error(self):
        session = FakeSession(FakeReply(data={}), None)
        with self.assertRaises(spotsaver.SpotSaverError):
            spotsaver.search("Yellow", "Coldplay", session=session)

    def test_network_errors_are_spotsaver_errors(self):
        class Down:
            def post(self, *a, **k):
                raise requests.ConnectionError("no")
        with self.assertRaises(spotsaver.SpotSaverError):
            spotsaver.search("Yellow", "Coldplay", session=Down())


class BackupInWorkerTests(unittest.TestCase):
    def worker(self, fetch_result):
        studio = LocalStudio()
        yt = YouTube(studio, search=lambda q, playlists=False: [video("Obscure Song", "Coldplay - Topic", vid="v2")],
                     fetch_audio=lambda *a: None)
        yt.ensure_worker = lambda: None
        studio.youtube = yt
        yt.add_song("Coldplay", "Obscure Song", "Parachutes")
        job = studio.download_manager.next_queued("youtube")
        job["payload"]["url"] = "https://www.youtube.com/watch?v=4pBcZSIF_78"
        yt.active = job["id"]
        return studio, yt, job

    def test_backup_result_is_returned_and_failure_reraises(self):
        studio, yt, job = self.worker(None)
        error = FetchTimeout("hung")
        import unittest.mock as mock
        with mock.patch("tagcast.spotsaver.fetch", return_value=("file.mp3", {})):
            self.assertEqual(yt._spotsaver_backup(job, "dir", lambda d: None, error), ("file.mp3", {}))
        with mock.patch("tagcast.spotsaver.fetch", side_effect=spotsaver.SpotSaverError("no")):
            with self.assertRaises(FetchTimeout):
                yt._spotsaver_backup(job, "dir", lambda d: None, error)

    def test_the_setting_turns_it_off_and_other_links_are_skipped(self):
        studio, yt, job = self.worker(None)
        import unittest.mock as mock
        yt.save_settings({"spotsaver": False})
        self.assertFalse(yt.settings_view()["spotsaver"])
        with mock.patch("tagcast.spotsaver.fetch") as fetch, self.assertRaises(FetchTimeout):
            yt._spotsaver_backup(job, "dir", lambda d: None, FetchTimeout("hung"))
        fetch.assert_not_called()
        yt.save_settings({"spotsaver": True})
        job["payload"]["url"] = "https://soundcloud.com/a/b"
        with mock.patch("tagcast.spotsaver.fetch") as fetch, self.assertRaises(FetchTimeout):
            yt._spotsaver_backup(job, "dir", lambda d: None, FetchTimeout("hung"))
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
