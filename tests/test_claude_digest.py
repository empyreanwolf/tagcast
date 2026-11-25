"""Claude digest (fork v125): a short, secret-free summary of recent problems."""

import tempfile
import unittest
from pathlib import Path

from tagcast import activity, claude_digest


class Studio:
    def __init__(self, files=None):
        self.home = tempfile.mkdtemp()
        self.files = files or {}

    def _read(self, name):
        return self.files.get(name)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.studio = Studio()
        activity.start(self.studio.home)
        self.addCleanup(lambda: setattr(activity, "_current", None))

    def test_digest_lists_recent_errors(self):
        activity.record("downloads", "error", "Failed Song “Ink”", detail="No link")
        text = claude_digest.build_digest(self.studio)
        self.assertIn("Failed Song “Ink”", text)
        self.assertIn("No link", text)

    def test_digest_hides_secrets(self):
        self.studio.files["config.json"] = {"api_token": "abc123"}
        self.studio.files["downloads.json"] = {"queue": [{"state": "failed", "title": "T", "note": "ok"}]}
        activity.record("system", "error", "boom", detail="x")
        text = claude_digest.build_digest(self.studio)
        self.assertNotIn("abc123", text)

    def test_digest_is_capped(self):
        for n in range(200):
            activity.record("downloads", "error", f"Failed song number {n}", detail="d" * 300)
        text = claude_digest.build_digest(self.studio)
        self.assertLessEqual(len(text.encode("utf-8")), 8000)

    def test_ytdlp_context_is_included(self):
        claude_digest.note_ytdlp("https://youtu.be/x", "line1\nline2")
        self.assertEqual(claude_digest.youtube_context()["url"], "https://youtu.be/x")
        self.assertIn("https://youtu.be/x", claude_digest.build_digest(self.studio))


if __name__ == "__main__":
    unittest.main()
