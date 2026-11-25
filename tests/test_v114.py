"""Fork v114: Reset a jammed song, search pause / stop / skip, and a search line that survives a restart."""

import copy
import threading
import time
import unittest
from unittest import mock

from test_downloads import LocalStudio

from tagcast import discography
from tagcast.discography import LINE_FILE, Discography
from tagcast.galaxy import Abandoned, JobCancelled
from tagcast.source_worker import FileWorker


def wait_for(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


class FakeWorker:
    """Obeys a cancel at once, like a worker that is not jammed."""

    def __init__(self, manager, obey=True):
        self.manager, self.obey, self.active, self.abandoned = manager, obey, [], []
        self.thread = mock.Mock(is_alive=lambda: True)   # a worker with a running thread

    def active_ids(self):
        return list(self.active)

    def cancel(self, item_id):
        if self.obey:
            self.active.remove(item_id)
            self.manager.cancelled(item_id)

    def abandon(self, item_id):
        self.abandoned.append(item_id)
        self.active.remove(item_id)

    def ensure_worker(self):
        pass


def add_active(studio, worker, item_id="a1", source="fake", **extra):
    m = studio.download_manager
    m.workers[source] = worker
    with m.lock:
        m.state["queue"].append({"id": item_id, "state": "active", "source": source, "payload": {"id": item_id},
                                 "kind": "song", "title": "Song", "artists": ["Artist"], "started_at": time.time(), **extra})
    worker.active.append(item_id)
    return m


class ResetTests(unittest.TestCase):
    def test_reset_puts_the_same_song_back_in_its_place(self):
        studio = LocalStudio()
        m = add_active(studio, FakeWorker(studio.download_manager), step=1, progress=40, speed=0.0)
        self.assertTrue(m.reset("a1"))
        item = m.item("a1")
        self.assertEqual((item["state"], item["resets"], item["progress"]), ("queued", 1, 0))
        self.assertEqual([i["id"] for i in m.state["queue"]], ["a1"])   # kept, not removed and re-added

    def test_a_worker_that_cannot_let_go_has_its_thread_given_up(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager, obey=False)
        m = add_active(studio, worker)
        m._resetting["a1"] = "requeue"
        m._finish_reset("a1", worker, wait=0.3)
        self.assertEqual(worker.abandoned, ["a1"])
        self.assertEqual(m.item("a1")["state"], "queued")

    def test_a_reset_the_worker_already_handled_is_not_applied_twice(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager)
        m = add_active(studio, worker)
        m.reset("a1")
        m._finish_reset("a1", worker, wait=0.3)
        self.assertEqual(m.item("a1")["state"], "queued")
        self.assertEqual(worker.abandoned, [])

    def test_the_fourth_automatic_reset_gives_up(self):
        studio = LocalStudio()
        m = add_active(studio, FakeWorker(studio.download_manager), resets=3)
        m.reset("a1", auto=True)
        item = m.item("a1")
        self.assertEqual(item["state"], "failed")
        self.assertIn("given up", item["note"])

    def test_reset_by_hand_never_gives_up(self):
        studio = LocalStudio()
        m = add_active(studio, FakeWorker(studio.download_manager), resets=9)
        m.reset("a1")
        self.assertEqual(m.item("a1")["state"], "queued")

    def test_stuck_songs_are_found_after_120_seconds_without_progress(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager)
        m = add_active(studio, worker, "slow")
        add_active(studio, worker, "waiting", step_detail="Waiting for the API limit (45 per 5 min) - 90 s")
        add_active(studio, worker, "moving")
        seek = FakeWorker(studio.download_manager)
        add_active(studio, seek, "ss1", source="soulseek", idle=130)
        add_active(studio, seek, "ss2", source="soulseek", idle=20)
        now = time.time()
        m._moved.update({"slow": now - 130, "waiting": now - 500, "moving": now - 5})
        self.assertEqual(sorted(m.reset_stuck(now=now)), ["slow", "ss1"])
        self.assertEqual(m.item("moving")["state"], "active")
        self.assertEqual(m.item("waiting")["state"], "active")

    def test_progress_keeps_a_song_from_counting_as_stuck(self):
        studio = LocalStudio()
        m = add_active(studio, FakeWorker(studio.download_manager))
        m._moved["a1"] = time.time() - 500
        m.update_item("a1", progress=55)
        self.assertEqual(m.reset_stuck(), [])

    def test_control_reset_resets_the_song_given_or_every_active_one(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager)
        m = add_active(studio, worker, "a1")
        add_active(studio, worker, "a2")
        m.control("reset", ["a1"])
        self.assertEqual((m.item("a1")["state"], m.item("a2")["state"]), ("queued", "active"))
        m.control("reset")
        self.assertEqual(m.item("a2")["state"], "queued")

    def test_cancel_without_a_reset_still_removes(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager)
        m = add_active(studio, worker)
        worker.cancel("a1")
        self.assertIsNone(m.item("a1"))


class JammedThread(FileWorker):
    source = "jam"
    track_delay = 0.0

    def __init__(self, studio):
        self.calls, self.release = [], threading.Event()
        super().__init__(studio, fetch=None)

    def process(self, job):
        self.calls.append(job["id"])
        if len(self.calls) == 1:
            self.release.wait(10)      # a jam: not looking at the cancel flag
            self._check_cancel()       # when it wakes up
            raise AssertionError("an abandoned thread went on working")
        self.manager.update_item(job["id"], persist=True, state="done")


class AbandonTests(unittest.TestCase):
    def test_a_jammed_worker_thread_is_replaced_and_the_old_one_ends_quietly(self):
        studio = LocalStudio()
        worker = JammedThread(studio)
        m = studio.download_manager
        with m.lock:
            m.state["queue"].append({"id": "j1", "state": "queued", "source": "jam", "payload": {"id": "j1"},
                                     "kind": "song", "title": "Song", "artists": ["A"]})
        worker.ensure_worker()
        self.assertTrue(wait_for(lambda: m.item("j1") and m.item("j1")["state"] == "active"))
        old = worker.thread
        m._resetting["j1"] = "requeue"
        worker.cancel("j1")
        m._finish_reset("j1", worker, wait=0.3)                      # still jammed: the thread is given up
        self.assertTrue(wait_for(lambda: len(worker.calls) == 2))    # the new thread took the same song again
        self.assertTrue(wait_for(lambda: m.item("j1") is None or m.item("j1")["state"] != "active"))
        worker.release.set()                                         # the old thread wakes up
        old.join(3)
        self.assertFalse(old.is_alive())
        self.assertEqual(worker.calls, ["j1", "j1"])

    def test_abandoned_and_cancelled_are_different_things(self):
        self.assertTrue(issubclass(Abandoned, BaseException) and not issubclass(Abandoned, Exception))
        self.assertTrue(issubclass(JobCancelled, Exception))


class SearchStudio:
    def __init__(self, store=None):
        self.store = {} if store is None else store

    def _read(self, name):
        return copy.deepcopy(self.store.get(name))

    def _write(self, name, value):
        self.store[name] = copy.deepcopy(value)


class SearchControlTests(unittest.TestCase):
    def test_a_skipped_source_is_not_searched_and_stays_skipped_after_a_restart(self):
        studio = SearchStudio()
        d = Discography(studio, http=mock.Mock())
        d.search_control("skip", "galaxy", True)
        d.search_control("skip", "bandcamp", True)
        self.assertEqual(d._galaxy_album("A", "B"), (None, "Galaxy is switched off"))
        self.assertEqual(d._galaxy_song("A", "B"), (None, "Galaxy is switched off"))
        self.assertEqual(d._bandcamp_album("A", "B", {}), "Bandcamp is switched off")
        self.assertIsNone(d._bandcamp_base("A", {"bandcamp_checked": False}))
        self.assertEqual(Discography(studio, http=mock.Mock()).skipped, {"galaxy", "bandcamp"})
        d.search_control("skip", "galaxy", False)
        self.assertEqual(d.active()["search"]["skipped"], ["bandcamp"])
        with self.assertRaises(discography.DiscographyError):
            d.search_control("skip", "napster", True)

    def test_pause_holds_a_job_until_resume(self):
        d = Discography(SearchStudio(), http=mock.Mock())
        job = {"name": "A", "gen": 0}
        d.search_control("pause")
        passed = threading.Event()
        threading.Thread(target=lambda: (d._hold(job), passed.set()), daemon=True).start()
        self.assertFalse(passed.wait(0.5))
        self.assertIn("paused", job["current"])
        d.search_control("resume")
        self.assertTrue(passed.wait(2))

    def test_stop_ends_running_and_waiting_jobs_and_keeps_what_was_found(self):
        d = Discography(SearchStudio(), http=mock.Mock())
        entered = threading.Event()

        def slow(job, albums, songs):
            job["log"].append({"title": "first", "source": "Galaxy", "note": ""})
            job["done"] = 1
            entered.set()
            while True:
                d._hold(job)
                time.sleep(0.05)

        with mock.patch.object(d, "_run_all", side_effect=slow), mock.patch.object(discography, "JOB_GAP", 0.0):
            first = d.get("A", [{"title": "1"}, {"title": "2"}])
            self.assertTrue(entered.wait(2))
            second = d.get("B", [{"title": "3"}])
            time.sleep(0.1)
            self.assertIn("waiting for its turn", second["current"])
            d.search_control("stop")
            self.assertTrue(wait_for(lambda: first["state"] == "done" and second["state"] == "done"))
        self.assertEqual(first["log"][0]["source"], "Galaxy")      # what was found stays
        self.assertEqual(first["log"][-1]["source"], "Stopped")
        self.assertIn("1 not searched", first["log"][-1]["note"])
        self.assertEqual(second["log"][-1]["source"], "Stopped")
        self.assertEqual(studio_rows(d), [])


def studio_rows(d):
    return d.studio.store[LINE_FILE]["jobs"]


class SearchLineTests(unittest.TestCase):
    def test_the_waiting_searches_are_saved_and_come_back_paused_after_a_restart(self):
        studio = SearchStudio()
        d = Discography(studio, http=mock.Mock())
        gate = threading.Event()

        def blocked(job, albums, songs):
            job["done"] = 1
            d._save_line()
            gate.wait(5)
            job["state"] = "done"

        with mock.patch.object(d, "_run_all", side_effect=blocked):
            d.get("Artist One", [{"title": "A1"}, {"title": "A2"}, {"title": "A3"}], [{"title": "S1", "album": "x"}])
            self.assertTrue(wait_for(lambda: studio.store[LINE_FILE]["jobs"] and
                                     len(studio.store[LINE_FILE]["jobs"][0]["albums"]) == 2))
        saved = studio.store[LINE_FILE]["jobs"][0]
        self.assertEqual([a["title"] for a in saved["albums"]], ["A2", "A3"])   # the finished one is crossed off
        self.assertEqual([s["title"] for s in saved["songs"]], ["S1"])
        gate.set()

        ran = []
        with mock.patch.object(Discography, "_run_all", autospec=True,
                               side_effect=lambda self_, job, albums, songs: (ran.append(job["name"]), job.update(state="done"))):
            again = Discography(SearchStudio(copy.deepcopy(studio.store)), http=mock.Mock())
            self.assertTrue(again.search_paused)
            self.assertEqual(again.restored, 3)
            self.assertEqual(again.active()["search"]["restored"], 3)
            (job,) = again.jobs.values()
            self.assertEqual(job["name"], "Artist One")
            self.assertIn("paused", job["current"])
            self.assertEqual(ran, [])                     # nothing ran: they wait for Resume
            again.search_control("resume")
            self.assertTrue(wait_for(lambda: ran == ["Artist One"]))
        self.assertEqual(again.restored, 0)

    def test_a_finished_search_is_not_saved_again(self):
        studio = SearchStudio()
        d = Discography(studio, http=mock.Mock())
        with mock.patch.object(d, "_run_all", side_effect=lambda job, a, s: job.update(state="done")), \
                mock.patch.object(discography, "JOB_GAP", 0.0):
            job = d.get("A", [{"title": "1"}])
            self.assertTrue(wait_for(lambda: job["state"] == "done"))
        d._save_line()
        self.assertEqual(studio.store[LINE_FILE]["jobs"], [])
        self.assertEqual(Discography(studio, http=mock.Mock()).restored, 0)


if __name__ == "__main__":
    unittest.main()


class WatcherManners(unittest.TestCase):
    def test_a_song_nobody_is_running_is_left_to_the_watchdog(self):
        studio = LocalStudio()
        worker = FakeWorker(studio.download_manager)
        worker.thread = None
        m = add_active(studio, worker)
        m._moved["a1"] = time.time() - 999
        self.assertEqual(m.reset_stuck(), [])
        self.assertEqual(m.item("a1")["state"], "active")


class V125ClearRestoredLineTests(unittest.TestCase):
    def test_stop_on_a_restored_line_empties_the_saved_file(self):
        studio = SearchStudio()
        studio.store[LINE_FILE] = {"jobs": [{"id": "a", "name": "Old Artist", "albums": [{"title": "X"}], "songs": []},
                                            {"id": "b", "name": "Other", "albums": [{"title": "Y"}], "songs": []}], "skipped": []}
        d = Discography(studio, http=mock.Mock())
        self.assertEqual(d.restored, 2)
        d.search_control("stop")
        self.assertTrue(wait_for(lambda: studio.store[LINE_FILE]["jobs"] == []))
        self.assertEqual(Discography(SearchStudio(copy.deepcopy(studio.store)), http=mock.Mock()).restored, 0)
