"""Activity log (fork v35): recording, filtering, rotation, secrets, and the hooks."""

import json
import logging
import tempfile
import time
import unittest
from pathlib import Path

from tagcast import activity


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.log = activity.start(self.home)
        self.addCleanup(lambda: setattr(activity, "_current", None))

    def test_entries_are_kept_in_memory_and_on_disk_and_survive_a_restart(self):
        activity.record("downloads", "ok", "Saved Song “Magic” · Coldplay", source="galaxy", ms=1234)
        activity.record("downloads", "error", "Failed Song “Ink”: No download link", source="galaxy")
        lines = (self.home / "activity.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["ms"], 1234)
        again = activity.start(self.home)
        self.assertEqual([e["title"] for e in again.view()["entries"]],
                         ["Failed Song “Ink”: No download link", "Saved Song “Magic” · Coldplay"])

    def test_filters_live_refresh_and_summary(self):
        a = activity.record("downloads", "ok", "Saved A")
        activity.record("metadata", "error", "Save failed B")
        activity.record("downloads", "warn", "Held C")
        view = self.log.view
        self.assertEqual([e["title"] for e in view(status="problems")["entries"]], ["Held C", "Save failed B"])
        self.assertEqual([e["title"] for e in view(cat="downloads")["entries"]], ["Held C", "Saved A"])
        self.assertEqual([e["title"] for e in view(query="failed b")["entries"]], ["Save failed B"])
        self.assertEqual([e["title"] for e in view(after=a["id"])["entries"]], ["Held C", "Save failed B"])
        summary = {row["cat"]: row for row in view()["summary"]}
        self.assertEqual((summary["downloads"]["ok"], summary["downloads"]["warn"]), (1, 1))
        self.assertEqual(summary["metadata"]["last_problem"]["title"], "Save failed B")
        self.assertEqual(view()["summary"][0]["cat"], "metadata")  # categories with errors come first

    def test_the_file_rotates_and_export_reads_every_file(self):
        old_max = activity.MAX_BYTES
        activity.MAX_BYTES = 300
        self.addCleanup(setattr, activity, "MAX_BYTES", old_max)
        for n in range(40):
            activity.record("system", "info", f"line {n:02d}")
        self.assertTrue((self.home / "activity.log.1").exists())
        self.assertFalse((self.home / "activity.log.3").exists())
        text = self.log.export_text()
        self.assertIn("line 39", text)
        self.assertLess(text.index("line 38"), text.index("line 39"))  # oldest first

    def test_request_summaries_never_contain_secrets(self):
        text = activity.summarize({"username": "me", "password": "hunter2", "api_key": "k", "client_id": "c",
                                   "ids": ["a", "b"], "changes": list(range(12)), "paused": True})
        self.assertNotIn("hunter2", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("client_id", text)
        self.assertIn("ids: a, b", text)
        self.assertIn("changes: 12 items", text)

    def test_tagcast_warnings_and_info_become_entries_but_not_terminal_lines(self):
        logging.getLogger("tagcast.galaxy").warning("Galaxy download thread hit an unexpected error")
        logging.getLogger("tagcast.tagclean").info("Tag clean-up could not save artists for x.mp3")
        entries = self.log.view()["entries"]
        self.assertEqual((entries[0]["cat"], entries[0]["status"]), ("cleanup", "info"))
        self.assertEqual((entries[1]["cat"], entries[1]["status"], entries[1]["source"]), ("downloads", "warn", "galaxy"))

    def test_nothing_is_recorded_without_a_started_log(self):
        activity._current = None
        self.assertIsNone(activity.record("downloads", "ok", "x"))

    def test_a_cleanup_run_is_summarised_by_outcome(self):
        task = {"state": "done", "error": "", "items": [{"id": 1, "status": "confirmed"}, {"id": 2, "status": "confirmed"},
                                                        {"id": 3, "status": "skipped"}]}
        entry = activity.record_run("cleanup", "Artist cleanup", task)
        self.assertEqual((entry["status"], entry["title"]), ("ok", "Artist cleanup done: 2 confirmed, 1 skipped"))
        entry = activity.record_run("cleanup", "Artist cleanup", {"state": "error", "error": "iBroadcast refused"})
        self.assertEqual((entry["status"], entry["detail"]), ("error", "iBroadcast refused"))


class QueueHookTests(unittest.TestCase):
    """The download manager writes each item's journey to the Activity log."""

    def test_queue_journey(self):
        from test_galaxy import LocalStudio, wait_idle
        home = Path(tempfile.mkdtemp())
        log = activity.start(home)
        self.addCleanup(lambda: setattr(activity, "_current", None))
        studio = LocalStudio()
        studio.gd.urls[("netease", "1", 320)] = "https://cdn.example/1.mp3"
        tracks = [{"id": str(i), "title": t, "source": "netease", "artists": ["Coldplay"], "album": "Ghost Stories"}
                  for i, t in ((1, "Magic"), (2, "Ink"))]
        studio.galaxy.add(tracks)
        wait_idle(studio)
        titles = [e["title"] for e in reversed(log.view(cat="downloads")["entries"])]
        self.assertTrue(titles[0].startswith("Queued 2 on galaxy"), titles)
        self.assertTrue(any(t.startswith("Started Song “Magic”") for t in titles), titles)
        self.assertTrue(any(t.startswith("Saved Song “Magic”") for t in titles), titles)
        failed = [e for e in log.view(status="error")["entries"] if e["title"].startswith("Failed Song “Ink”")]
        self.assertEqual(len(failed), 1)
        self.assertIn("No download link", failed[0]["title"])


if __name__ == "__main__":
    unittest.main()


class ServerTests(unittest.TestCase):
    """Requests through the real handler are recorded; the log is served to the page."""

    def setUp(self):
        import threading
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from unittest import mock

        from tagcast import app
        from tagcast.library import LibraryError
        self.home = Path(tempfile.mkdtemp())
        self.log = activity.start(self.home)
        self.addCleanup(lambda: setattr(activity, "_current", None))
        studio = mock.Mock()
        studio.galaxy.control.return_value = {"paused": False, "active": None}
        studio.soulseek.add.side_effect = LibraryError("Set the Sockseek program first.")
        studio.bandcamp.add.side_effect = KeyError("boom")
        handler = type("TestHandler", (app.Handler,), {"studio": studio, "port": 0})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        handler.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        def request(method, path, body=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, body=json.dumps(body or {}) if method == "POST" else None,
                                   headers={"X-Tagcast": "1"})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        self.request = request

    def test_actions_failures_and_crashes_are_recorded(self):
        self.assertEqual(self.request("POST", "/api/galaxy/control", {"action": "retry", "ids": ["a1"]})[0], 200)
        self.assertEqual(self.request("POST", "/api/soulseek/add", {"items": [1, 2], "password": "nope"})[0], 400)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.request("POST", "/api/bandcamp/add", {"url": "https://x.bandcamp.com/album/y"})[0], 500)
        entries = {e["source"]: e for e in self.log.view()["entries"] if e.get("cat") == "downloads" and "ms" in e}
        self.assertEqual((entries["galaxy"]["status"], entries["galaxy"]["title"]), ("ok", "galaxy › control · retry"))
        self.assertIn("ids: a1", entries["galaxy"]["detail"])
        self.assertEqual(entries["soulseek"]["status"], "error")
        self.assertIn("Set the Sockseek program first.", entries["soulseek"]["title"])
        self.assertNotIn("nope", json.dumps(self.log.view()["entries"]))
        self.assertEqual(entries["bandcamp"]["status"], "error")
        self.assertIn("KeyError", entries["bandcamp"]["detail"])  # the traceback, for me to read

    def test_the_page_reads_exports_and_reports_its_own_errors(self):
        self.request("POST", "/api/activity/client", {"kind": "script", "message": "x is undefined",
                                                       "where": "album-merge.js:42", "screen": "albums"})
        status, body = self.request("GET", "/api/activity?status=problems")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["entries"][0]["title"], "script: x is undefined")
        self.assertEqual(data["entries"][0]["cat"], "page")
        self.assertEqual(len(data["entries"]), 1)  # reading or reporting is not itself an entry
        status, body = self.request("GET", "/api/activity/export")
        self.assertEqual(status, 200)
        self.assertIn("script: x is undefined", body.decode("utf-8"))
        self.assertIn("album-merge.js:42", body.decode("utf-8"))
