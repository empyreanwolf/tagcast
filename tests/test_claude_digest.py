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

    def test_more_secret_shapes_are_hidden(self):
        for secret_text in ("Authorization: Bearer ghp_abc123", '{"token": "ghp_abc123"}', "{'password': 'ghp_abc123'}",
                            "--client-secret ghp_abc123", "api_key=ghp_abc123"):
            with self.subTest(secret_text):
                activity.record("system", "error", "boom", detail=secret_text)
                self.assertNotIn("ghp_abc123", claude_digest.build_digest(self.studio))

    def test_a_malformed_search_line_does_not_break_the_digest(self):
        self.studio.files["search_line.json"] = {"jobs": [], "skipped": [1, None]}
        self.assertIn("Search line", claude_digest.build_digest(self.studio))

    def test_each_download_thread_keeps_its_own_ytdlp_lines(self):
        import threading
        got = {}

        def work(name):
            own = claude_digest.capture_start()
            claude_digest.ytdlp_logger().info("line of " + name)
            got[name] = claude_digest.ytdlp_tail(own)
        threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(got, {"a": "line of a", "b": "line of b"})


if __name__ == "__main__":
    unittest.main()
