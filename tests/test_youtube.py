import unittest
from pathlib import Path

from test_discography import LocalStudio
from tagcast.youtube import YouTube, YouTubeError, best_video, score_video


def video(title, channel, seconds=200, vid="abc"):
    return {"id": vid, "title": title, "channel": channel, "duration": seconds}


class ScoringTests(unittest.TestCase):
    def test_the_topic_channel_audio_wins_over_a_video_and_a_cover(self):
        entries = [video("Coldplay - Yellow (Official Video)", "Coldplay", vid="v1"),
                   video("Yellow", "Coldplay - Topic", vid="v2"),
                   video("Yellow cover by Someone", "Someone", vid="v3")]
        self.assertEqual(best_video(entries, "Coldplay", "Yellow")["id"], "v2")

    def test_live_remix_and_another_artist_are_rejected(self):
        self.assertEqual(score_video(video("Coldplay - Yellow (Live)", "Coldplay"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow (Remix)", "Coldplay - Topic"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow", "Random Uploader"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow", "Coldplay - Topic", seconds=2000), "Coldplay", "Yellow"), 0.0)

    def test_a_remix_is_fine_when_it_is_what_you_asked_for(self):
        self.assertGreater(score_video(video("Yellow (Remix)", "Coldplay - Topic"), "Coldplay", "Yellow (Remix)"), 0)


class QueueTests(unittest.TestCase):
    def make(self, entries):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=lambda query, playlists=False: entries, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        return studio

    def test_a_song_is_found_and_queued_first_in_the_chain(self):
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        source, note = studio.discography._song("Coldplay", "Obscure Song", "Parachutes", {})
        self.assertEqual(source, "YouTube")
        item = [i for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual(len(item), 1)
        self.assertEqual(item[0]["payload"]["id"], "v2")

    def test_nothing_found_falls_through_and_switching_off_skips_it(self):
        studio = self.make([video("Something else", "Nobody")])
        self.assertIsNone(studio.discography._youtube_song("Coldplay", "Yellow"))
        with self.assertRaises(YouTubeError):
            studio.youtube.add_song("Coldplay", "Yellow")
        studio.youtube.state["enabled"] = False
        self.assertIsNone(studio.discography._youtube())

    def test_lines_report_each_song(self):
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        out = studio.youtube.add_lines("Coldplay - Obscure Song\nNo dash here")["results"]
        self.assertEqual([r["ok"] for r in out], [True, False])

    def test_an_album_needs_a_matching_playlist(self):
        calls = []

        def search(query, playlists=False):
            calls.append(query)
            if playlists:
                return [{"id": "PL1", "title": "Parachutes", "channel": "Coldplay - Topic"}]
            return [{"id": f"s{i}", "title": f"Song {i}", "duration": 200} for i in range(1, 4)]
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        found = studio.youtube.find_album("Coldplay", "Parachutes")
        self.assertEqual(len(found["tracks"]), 3)
        self.assertIsNone(studio.youtube.find_album("Coldplay", "A Different Album"))

    def test_the_worker_tags_and_saves_the_downloaded_file(self):
        import tempfile
        from tagcast.galaxy import detect_audio_format  # noqa: F401
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        saved = {}
        yt = studio.youtube

        def fetch(url, folder, hook):
            folder.mkdir(parents=True, exist_ok=True)
            hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 20})
            path = folder / "audio.m4a"
            path.write_bytes(b"x")
            return path
        yt._fetch_audio = fetch
        yt.check_audio = lambda part, min_seconds=0: "m4a"
        yt.save = lambda job, part, fmt, nbytes, **kw: saved.update(kw, fmt=fmt)
        studio.download_manager.state["folder"] = tempfile.mkdtemp()
        yt.add_song("Coldplay", "Obscure Song", "Parachutes")
        job = studio.download_manager.next_queued("youtube")
        yt.active = job["id"]
        yt.process(job)
        self.assertEqual((saved["title"], saved["album"], saved["fmt"]), ("Obscure Song", "Parachutes", "m4a"))
        self.assertIn("from YouTube, lossy", saved["extras"])


if __name__ == "__main__":
    unittest.main()
