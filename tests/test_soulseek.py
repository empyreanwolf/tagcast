"""Soulseek through Sockseek (fork v14): settings, starting the daemon, jobs and results."""

import tempfile
import unittest
from pathlib import Path

from tagcast.soulseek import Soulseek, SoulseekError

from test_downloads import LocalStudio as DownloadsStudio


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.body, self.text = status, body, ""

    def json(self):
        if self.body is None:
            raise ValueError
        return self.body


class FakeDaemon:
    """Sockseek's HTTP API as far as Tagcast uses it."""

    def __init__(self):
        self.up, self.calls, self.jobs, self.next = True, [], {}, 1

    def request(self, method, url, json=None, timeout=None):
        import requests
        if not self.up:
            raise requests.ConnectionError("down")
        path = url.split("5030", 1)[1]
        self.calls.append((method, path, json))
        if path == "/api/server/info":
            return Resp(body={"name": "Sockseek", "version": "3.0.4"})
        if path == "/api/server/status":
            return Resp(body={"soulseekClient": {"state": "Connected, LoggedIn", "isReady": True}})
        if method == "POST" and path.startswith("/api/jobs/downloads/"):
            job_id = f"job-{self.next}"
            self.next += 1
            self.jobs[job_id] = {"kind": path.rsplit("/", 1)[1], "body": json, "state": "Running", "outcome": "None"}
            return Resp(202, {"jobId": job_id, "lifecycleState": "Pending"})
        if method == "POST" and path.endswith("/cancel"):
            self.jobs[path.split("/")[3]]["state"] = "Terminal"
            return Resp(body={})
        if method == "GET" and path.startswith("/api/jobs/"):
            job = self.jobs[path.rsplit("/", 1)[1]]
            payload = {"kind": "album", "selectedFolderFileCount": 2, "selectedFolderCompletedFileCount": 1,
                       "resolvedFolderUsername": "someone",
                       "tracks": [{"downloadPath": "/m/Coldplay/X&Y/01 - Square One.mp3", "query": {"artist": "Coldplay", "title": "Square One"}},
                                  {"downloadPath": "/m/Coldplay/X&Y/02 - What If.mp3", "query": {}}]} if job["kind"] == "album" \
                else {"kind": "song", "downloadPath": "/m/Coldplay - Clocks.mp3", "query": {"artist": "Coldplay", "title": "Clocks"}}
            return Resp(body={"summary": {"lifecycleState": job["state"], "terminalOutcome": job["outcome"],
                                          "activityPhase": "Downloading", "failureReason": job.get("reason")},
                              "payload": payload, "children": []})
        return Resp(404, {})


class LocalStudio(DownloadsStudio):
    def __init__(self):
        super().__init__()
        self.home = tempfile.mkdtemp()
        self.daemon = FakeDaemon()
        self.started = []
        self.soulseek = Soulseek(self, http=self.daemon, popen=lambda *a, **k: self.started.append(a) or Proc())

    def _write_bytes(self, name, data):
        (Path(self.home) / name).write_bytes(data)


class Proc:
    def poll(self):
        return None

    def terminate(self):
        pass


def configure(studio):
    exe = Path(studio.home) / "sockseek.exe"
    exe.write_text("")
    return studio.soulseek.save_settings({"exe": str(exe), "username": "me", "password": "secret", "shared_files": 120})


class SoulseekTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.s = self.studio.soulseek
        self.s.ensure_worker = lambda: None  # the tests run the rounds themselves

    def test_settings_never_show_the_password(self):
        with self.assertRaises(SoulseekError):
            self.s.add([{"kind": "album", "artist": "Coldplay", "album": "X&Y"}])
        status = configure(self.studio)
        self.assertEqual(status["settings"]["has_password"], True)
        self.assertNotIn("secret", repr(status))
        self.s.save_settings({"password": ""})  # empty keeps it
        self.assertEqual(self.s.state["password"], "secret")
        with self.assertRaises(SoulseekError):
            self.s.save_settings({"exe": "/no/such/sockseek.exe"})
        with self.assertRaises(SoulseekError):
            self.s.save_settings({"port": 80})

    def test_start_writes_the_config_and_launches_the_daemon(self):
        configure(self.studio)
        self.studio.daemon.up = False
        self.s.ping = lambda: bool(self.studio.started)  # "up" once launched
        self.s.status = lambda: {"running": True}
        self.s.start()
        args = self.studio.started[0][0]
        self.assertEqual(args[1:3], ["daemon", "--config"])
        conf = (Path(self.studio.home) / "sockseek.conf").read_text()
        self.assertIn("password = secret", conf)
        self.assertIn("shared-files = 120", conf)
        self.assertIn(f"path = {self.studio.download_manager.folder()}", conf)

    def test_album_job_from_start_to_history(self):
        configure(self.studio)
        result = self.s.add([{"kind": "album", "artist": "Coldplay", "album": "X&Y"},
                             {"kind": "album", "artist": "Coldplay", "album": "Parachutes"}])  # owned: held
        self.assertEqual((result["queued"], result["held"]), (1, 1))
        self.assertTrue(self.s.tick())
        item = self.studio.download_manager.state["queue"][0]
        self.assertEqual(item["state"], "active")
        job = self.studio.daemon.jobs[item["job_id"]]
        self.assertEqual(job["body"]["albumQuery"], {"artist": "Coldplay", "album": "X&Y"})
        self.s.tick()
        self.assertEqual(self.studio.download_manager.item(item["id"])["step_detail"], "1 of 2 files from someone")
        job.update(state="Terminal", outcome="Succeeded")
        self.assertFalse(self.s.tick())
        done = self.studio.download_manager.item(item["id"])
        self.assertEqual((done["state"], done["note"]), ("done", "Saved 2 files"))
        history = {e["title"]: e for e in self.studio.download_manager.history_view(source="soulseek")["entries"]}
        self.assertEqual(set(history), {"Square One", "What If"})
        self.assertEqual(history["What If"]["artists"], ["Coldplay"])

    def test_failure_tries_the_next_source(self):
        configure(self.studio)
        tried = []
        self.s.on_failed = tried.append
        self.s.add([{"kind": "song", "artist": "Coldplay", "title": "Clocks", "fallback": True}])
        self.s.tick()
        item = self.studio.download_manager.state["queue"][0]
        self.studio.daemon.jobs[item["job_id"]].update(state="Terminal", outcome="Failed", reason="NoSearchResults")
        self.s.tick()
        failed = self.studio.download_manager.item(item["id"])
        self.assertEqual((failed["state"], failed["note"]), ("failed", "Nobody on Soulseek shares it right now"))
        self.assertEqual(tried[0]["title"], "Clocks")

    def test_cancel_and_pause(self):
        configure(self.studio)
        self.studio.download_manager.control("pause")
        self.s.add([{"kind": "song", "artist": "Coldplay", "title": "Clocks"}])
        self.assertTrue(self.s.tick())  # waits while paused
        self.assertEqual(self.studio.download_manager.state["queue"][0]["state"], "queued")
        self.studio.download_manager.control("resume")
        self.s.tick()
        item = self.studio.download_manager.state["queue"][0]
        self.studio.download_manager.control("cancel")
        self.assertEqual(self.studio.download_manager.state["queue"], [])
        self.assertIn(("POST", f"/api/jobs/{item['job_id']}/cancel", None), self.studio.daemon.calls)


if __name__ == "__main__":
    unittest.main()
