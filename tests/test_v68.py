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
