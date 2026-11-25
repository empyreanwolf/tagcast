"""Log sync (fork v125): the digest goes to a separate private GitHub repo, rarely, and never breaks the app."""

import base64
import tempfile
import unittest

from tagcast import logsync


class Studio:
    def __init__(self):
        self.home = tempfile.mkdtemp()
        self.files = {}

    def _read(self, name):
        return self.files.get(name)

    def _write(self, name, data):
        self.files[name] = data


class Fake:
    def __init__(self, put_codes=(200,)):
        self.calls, self.put_codes = [], list(put_codes)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "GET":
            return 200, {"sha": f"sha{len(self.calls)}"}
        return self.put_codes.pop(0) if self.put_codes else 200, {}


def make(fake, clock=None):
    studio = Studio()
    sync = logsync.LogSync(studio, http=fake, clock=clock or (lambda: 0.0))
    sync.threaded = False
    sync.digest = lambda: "digest text"
    return studio, sync


class LogSyncTests(unittest.TestCase):
    def test_no_token_no_push(self):
        fake = Fake()
        _, sync = make(fake)
        sync.configure("me/tagcast-logs", "")
        sync.notify_error()
        self.assertEqual(fake.calls, [])
        self.assertFalse(sync.status()["on"])

    def test_error_pushes_once_in_ten_minutes(self):
        now = [1000.0]
        fake = Fake()
        _, sync = make(fake, clock=lambda: now[0])
        sync.configure("me/tagcast-logs", "tok")
        sync.notify_error()
        sync.notify_error()
        self.assertEqual([c[0] for c in fake.calls], ["GET", "PUT"])
        now[0] += 601
        sync.notify_error()
        self.assertEqual([c[0] for c in fake.calls], ["GET", "PUT", "GET", "PUT"])
        put = fake.calls[1]
        self.assertTrue(put[1].endswith("/repos/me/tagcast-logs/contents/latest.md"))
        self.assertEqual(base64.b64decode(put[3]["content"]).decode(), "digest text")
        self.assertEqual(put[3]["sha"], "sha1")
        self.assertEqual(put[2]["Authorization"], "Bearer tok")

    def test_409_retried_once(self):
        fake = Fake(put_codes=(409, 200))
        _, sync = make(fake)
        sync.configure("me/tagcast-logs", "tok")
        status = sync.send_now()
        self.assertEqual([c[0] for c in fake.calls], ["GET", "PUT", "GET", "PUT"])
        self.assertEqual(status["error"], "")

    def test_network_error_is_swallowed(self):
        def broken(*a):
            raise OSError("offline")
        _, sync = make(broken)
        sync.configure("me/tagcast-logs", "tok")
        sync.notify_error()
        self.assertIn("offline", sync.status()["error"])

    def test_bad_token_is_reported_without_the_token(self):
        fake = Fake(put_codes=(401,))
        _, sync = make(fake)
        sync.configure("me/tagcast-logs", "supersecret")
        status = sync.send_now()
        self.assertIn("401", status["error"])
        self.assertNotIn("supersecret", str(status))

    def test_bad_repo_name_is_refused(self):
        _, sync = make(Fake())
        with self.assertRaises(ValueError):
            sync.configure("not a repo", "tok")


if __name__ == "__main__":
    unittest.main()


class BackupTests(unittest.TestCase):
    def test_token_file_is_left_out_of_settings_backups(self):
        from tagcast import settings_backup
        self.assertIn("logsync.json", settings_backup.LEFT_OUT)
