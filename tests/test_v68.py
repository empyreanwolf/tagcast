import subprocess
import unittest
from unittest.mock import patch

from test_discography import LocalStudio
from tagcast.spotify import SpotDL, clean_error


class SpotdlFfmpegTests(unittest.TestCase):
    def test_save_passes_tagcasts_ffmpeg_and_cleans_the_error(self):
        spot = SpotDL(LocalStudio())
        seen = {}

        def run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 1, "", "╭── Traceback ──╮\n│ x │\n╰───────╯\nFFmpegError: FFmpeg is not installed.\n")

        with patch.object(SpotDL, "installed", return_value=True), patch.object(SpotDL, "_ffmpeg", return_value="C:/ff/ffmpeg.exe"), patch.object(SpotDL, "python", return_value="py"), \
                patch("tagcast.spotify.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(Exception, "could not find FFmpeg"):
                spot.songs("https://open.spotify.com/album/1AUlCLObprriMIs0lwS7a8")
        self.assertEqual(seen["cmd"][seen["cmd"].index("--ffmpeg") + 1], "C:/ff/ffmpeg.exe")

    def test_clean_error_keeps_the_error_line(self):
        self.assertEqual(clean_error("\x1b[31m╭──╮\n│ boom │\n╰──╯\nValueError: bad link\n"), "ValueError: bad link")


if __name__ == "__main__":
    unittest.main()


class KeepSeparateTests(unittest.TestCase):
    """v70: names kept apart in Artist cleanup › Duplicates stop coming back."""

    def setUp(self):
        from tagcast.artist_cleanup import ArtistCleanup

        class Studio:
            files = {}

            def _read(self, name):
                return self.files.get(name)

            def _write(self, name, data):
                self.files[name] = data
        Studio.files = {}
        self.ac = ArtistCleanup(Studio())

    def card(self, *names):
        return {"id": "g", "kind": "bilingual", "target": {"id": 1, "name": names[0]},
                "members": [{"id": i + 1, "name": n, "tracks": 1, "reason": "", "ticked": True} for i, n in enumerate(names)]}

    def test_a_kept_apart_name_leaves_its_card_and_a_two_name_card_disappears(self):
        self.ac.keep_separate({"add": [["艾热 AIR", "Air"]]})
        groups, hidden = self.ac._without_separate([self.card("艾热 AIR", "Air"), self.card("SHarK 卫彬月", "SHarK")])
        self.assertEqual(([g["members"][0]["name"] for g in groups], hidden), (["SHarK 卫彬月"], 1))

    def test_a_new_name_still_shows_and_undo_brings_it_back(self):
        self.ac.keep_separate({"add": [["梦徐MX", "MX"]]})
        groups, _ = self.ac._without_separate([self.card("梦徐MX", "MX", "Mx Dream")])
        self.assertEqual([m["name"] for m in groups[0]["members"]], ["梦徐MX", "MX", "Mx Dream"])
        self.ac.keep_separate({"remove": [["MX", "梦徐MX"]]})
        self.assertEqual(self.ac.separate_pairs(), [])


class YouTubeMatchingTests(unittest.TestCase):
    """v70: the same words in any order count; other versions still never do."""

    def test_swapped_remix_credits_match(self):
        from tagcast.youtube import score_video
        entry = {"title": "Neroche - Powdered Water (Antiheroes Remix)", "channel": "András Hajdu", "duration": 183}
        self.assertGreater(score_video(entry, "Neroche", "Antiheroes - Powdered Water (Neroche Remix)"), 0)
        self.assertEqual(score_video(entry, "Neroche", "Powdered Water"), 0, "a remix never stands in for the original")
        other = {"title": "Neroche - Since You Went Away", "channel": "Kengdarot", "duration": 134}
        self.assertEqual(score_video(other, "Neroche", "Antiheroes - Powdered Water (Neroche Remix)"), 0)

    def test_galaxy_preview_only_starts_the_other_sources(self):
        import inspect
        from tagcast import galaxy
        self.assertIn('"Preview only" in str(e)', inspect.getsource(galaxy))


class TryYouTubeReasonTests(unittest.TestCase):
    def test_a_miss_keeps_the_reason_and_the_nearest_video_and_use_this_one_queues_it(self):
        from tagcast.youtube import YouTube, YouTubeError

        class Manager:
            def __init__(self):
                self.items = {"x": {"id": "x", "kind": "song", "title": "Other Song", "artist": "Neroche",
                                    "album": "Blue Tomorrows", "note": "Preview only"}}
                self.removed = []

            def item(self, item_id):
                return self.items.get(item_id)

            def update_item(self, item_id, persist=False, **fields):
                self.items[item_id].update(fields)

            def remove(self, ids):
                self.removed += ids
        yt = YouTube.__new__(YouTube)
        manager = Manager()
        yt.studio = type("S", (), {"download_manager": manager})()
        yt._search = lambda query, *a: [{"id": "TYJlkUDKP9o", "title": "Neroche - Since You Went Away", "channel": "K", "duration": 134}]
        submitted = []
        yt._submit = lambda items: submitted.extend(items) or {"queued": len(items)}
        with self.assertRaisesRegex(YouTubeError, "closest was"):
            yt.try_item("x")
        item = manager.items["x"]
        self.assertEqual(item["youtube_near"]["id"], "TYJlkUDKP9o")
        self.assertIn("Preview only · YouTube: closest was", item["note"])
        yt.use_near("x")
        self.assertEqual((submitted[0]["payload"]["id"], manager.removed), ("TYJlkUDKP9o", ["x"]))
