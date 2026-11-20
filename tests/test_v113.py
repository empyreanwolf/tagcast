"""Fork v113: "get" jobs run one at a time, in the order they were started."""

import threading
import time
import unittest
from unittest import mock

from tagcast import discography
from tagcast.discography import Discography


class Studio:
    def _read(self, name):
        return {}


class OneAtATimeTests(unittest.TestCase):
    def test_jobs_never_overlap_and_keep_their_order(self):
        d = Discography(Studio(), http=mock.Mock())
        running, order, overlap = [], [], []

        def fake(job, albums, songs):
            running.append(job["name"])
            if len(running) > 1:
                overlap.append(list(running))
            order.append(job["name"])
            time.sleep(0.05)
            running.remove(job["name"])
            job["state"] = "done"

        with mock.patch.object(d, "_run_all", side_effect=fake), mock.patch.object(discography, "JOB_GAP", 0.01):
            jobs = []
            for n in range(5):
                jobs.append(d.get(f"Artist {n}", [{"title": "X"}]))
                time.sleep(0.01)   # threads are started in order
            for _ in range(200):
                if all(j["state"] == "done" for j in jobs):
                    break
                time.sleep(0.02)
        self.assertEqual(overlap, [])
        self.assertEqual(order, [f"Artist {n}" for n in range(5)])

    def test_a_waiting_job_says_so(self):
        d = Discography(Studio(), http=mock.Mock())
        release = threading.Event()

        def fake(job, albums, songs):
            release.wait(2)
            job["state"] = "done"

        with mock.patch.object(d, "_run_all", side_effect=fake), mock.patch.object(discography, "JOB_GAP", 0.0):
            first = d.get("First", [{"title": "X"}])
            time.sleep(0.05)
            second = d.get("Second", [{"title": "Y"}])
            time.sleep(0.05)
            self.assertIn("waiting for its turn", second.get("current", ""))
            self.assertNotIn("waiting", first.get("current", ""))
            release.set()
            for _ in range(100):
                if second["state"] == "done":
                    break
                time.sleep(0.02)
        self.assertEqual(second["state"], "done")


if __name__ == "__main__":
    unittest.main()
