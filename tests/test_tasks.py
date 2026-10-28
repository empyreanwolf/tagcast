"""Background task queue (fork v36): writes to iBroadcast wait their turn instead of holding the page."""

import json
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

from tagcast import app, tasks

from tagcast.library import ConflictError
from tagcast.tasks import TaskQueue


REAL_SLEEP = time.sleep


def classify(error):
    return app.classify_error(error)


def wait_for(check, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(.01)
    return False


class QueueTests(unittest.TestCase):
    def test_tasks_run_one_at_a_time_in_the_order_they_were_queued(self):
        queue, order, running, overlap = TaskQueue(), [], [0], []

        def job(name):
            def run():
                running[0] += 1
                overlap.append(running[0])
                time.sleep(.01)
                order.append(name)
                running[0] -= 1
                return {"name": name}
            return run

        for name in "abcde":
            queue.submit("/api/save", name, job(name), classify)
        self.assertTrue(queue.wait_idle())
        self.assertEqual(order, list("abcde"))
        self.assertEqual(max(overlap), 1)

    def test_submit_returns_at_once_while_the_task_is_still_going(self):
        queue, release = TaskQueue(), threading.Event()
        started = time.time()
        queue.submit("/api/artwork", "cover", release.wait, classify)
        self.assertLess(time.time() - started, .5)
        self.assertEqual(queue.active(), 1)
        release.set()
        self.assertTrue(queue.wait_idle())

    def test_the_answer_of_a_finished_task_is_collected_by_id(self):
        queue = TaskQueue()
        task = queue.submit("/api/save", "save", lambda: {"results": [1, 2]}, classify)
        self.assertTrue(queue.wait_idle())
        self.assertEqual(queue.result(task["id"]), {"state": "done", "result": {"results": [1, 2]}})

    def test_a_waiting_task_reports_that_it_is_waiting(self):
        queue, release = TaskQueue(), threading.Event()
        first = queue.submit("/api/save", "first", release.wait, classify)
        self.assertTrue(wait_for(lambda: queue.result(first["id"])["state"] == "running"))
        second = queue.submit("/api/save", "second", lambda: {}, classify)
        view = queue.view()
        self.assertEqual((view["active"], view["waiting"]), (2, 1))
        self.assertEqual([t["state"] for t in view["tasks"][:2]], ["running", "queued"])
        release.set()
        self.assertTrue(queue.wait_idle())

    def test_a_failure_keeps_the_status_and_message_a_direct_request_would_have_had(self):
        queue = TaskQueue()

        def conflict():
            raise ConflictError("The cover changed in iBroadcast since you loaded it.")

        task = queue.submit("/api/artwork", "cover", conflict, classify)
        self.assertTrue(queue.wait_idle())
        answer = queue.result(task["id"])
        self.assertEqual(answer["state"], "error")
        self.assertEqual(answer["status"], 409)
        self.assertEqual(answer["data"]["error"], "The cover changed in iBroadcast since you loaded it.")
        self.assertTrue(answer["data"]["conflict"])
        listed = [t for t in queue.view()["tasks"] if t["id"] == task["id"]][0]
        self.assertEqual((listed["state"], listed["status"]), ("error", 409))

    def test_one_failing_task_does_not_stop_the_ones_behind_it(self):
        queue = TaskQueue()

        def boom():
            raise RuntimeError("unexpected")

        with self.assertLogs(level="ERROR"):
            first = queue.submit("/api/save", "bad", boom, classify)
            second = queue.submit("/api/save", "good", lambda: {"ok": True}, classify)
            self.assertTrue(queue.wait_idle())
        self.assertEqual(queue.result(first["id"])["status"], 500)
        self.assertEqual(queue.result(second["id"]), {"state": "done", "result": {"ok": True}})

    def test_an_unknown_task_is_a_key_error(self):
        with self.assertRaises(KeyError):
            TaskQueue().result("nope")

    def test_a_busy_save_lock_is_waited_for_not_reported_as_a_failure(self):
        queue, calls = TaskQueue(), []

        def run():
            calls.append(1)
            if len(calls) < 3:
                raise app.ApiError("Another save is still running.")
            return {"saved": True}

        with mock.patch.object(tasks.time, "sleep", lambda s: REAL_SLEEP(.001)):
            task = queue.submit("/api/save", "save", run, classify)
            self.assertTrue(queue.wait_idle())
        self.assertEqual(len(calls), 3)
        self.assertEqual(queue.result(task["id"]), {"state": "done", "result": {"saved": True}})

    def test_a_save_lock_that_never_frees_up_eventually_fails(self):
        queue = TaskQueue()

        def run():
            raise app.ApiError("Another save is still running.")

        with mock.patch.object(tasks, "BUSY_RETRY_SECONDS", -1):
            task = queue.submit("/api/save", "save", run, classify)
            self.assertTrue(queue.wait_idle())
        self.assertEqual(queue.result(task["id"])["status"], 400)

    def test_other_errors_are_never_retried(self):
        queue, calls = TaskQueue(), []

        def run():
            calls.append(1)
            raise app.ApiError("Choose an album or an artist.")

        queue.submit("/api/artwork", "cover", run, classify)
        self.assertTrue(queue.wait_idle())
        self.assertEqual(len(calls), 1)

    def test_on_finish_hears_about_every_task_even_when_it_raises(self):
        seen = []

        def on_finish(task):
            seen.append((task["label"], task["state"]))
            raise RuntimeError("logging broke")

        queue = TaskQueue(on_finish=on_finish)
        queue.submit("/api/save", "one", lambda: {}, classify)
        queue.submit("/api/save", "two", lambda: {}, classify)
        self.assertTrue(queue.wait_idle())
        self.assertTrue(wait_for(lambda: len(seen) == 2))
        self.assertEqual(seen, [("one", "done"), ("two", "done")])

    def test_only_the_recent_finished_tasks_are_kept(self):
        queue = TaskQueue()
        with mock.patch.object(tasks, "KEEP_FINISHED", 3):
            ids = [queue.submit("/api/save", str(i), lambda: {}, classify)["id"] for i in range(6)]
            self.assertTrue(queue.wait_idle())
        with self.assertRaises(KeyError):
            queue.result(ids[0])
        self.assertEqual(queue.result(ids[-1])["state"], "done")


class CongestionTests(unittest.TestCase):
    def _blocked(self, waiting):
        queue, release = TaskQueue(), threading.Event()
        queue.submit("/api/save", "running", release.wait, classify)
        for i in range(waiting):
            queue.submit("/api/save", f"waiting {i}", lambda: {}, classify)
        self.addCleanup(release.set)
        self.assertTrue(wait_for(lambda: queue.view()["waiting"] == waiting))
        return queue

    def test_a_short_queue_is_not_congested_and_says_nothing(self):
        view = self._blocked(2).view()
        self.assertFalse(view["congested"])
        self.assertEqual(view["message"], "")

    def test_many_waiting_is_congested_and_says_how_long_it_will_take(self):
        view = self._blocked(tasks.MANY_WAITING).view()
        self.assertTrue(view["congested"])
        self.assertIn(f"{tasks.MANY_WAITING + 1} changes are waiting", view["message"])
        self.assertIn("keep using Tagcast", view["message"])
        self.assertGreater(view["eta_seconds"], 0)

    def test_the_estimate_uses_how_long_recent_tasks_really_took(self):
        queue = TaskQueue()
        queue.durations.extend([20.0, 20.0, 20.0])
        release = threading.Event()
        queue.submit("/api/save", "running", release.wait, classify)
        for i in range(tasks.MANY_WAITING):
            queue.submit("/api/save", f"w{i}", lambda: {}, classify)
        self.addCleanup(release.set)
        self.assertTrue(wait_for(lambda: queue.view()["waiting"] == tasks.MANY_WAITING))
        self.assertEqual(queue.view()["eta_seconds"], (tasks.MANY_WAITING + 1) * 20)
        self.assertIn("minutes", queue.view()["message"])

    def test_a_task_that_has_waited_a_long_time_makes_it_congested_even_with_few_waiting(self):
        queue = self._blocked(1)
        with queue.cond:
            queue.waiting[0]["created"] -= tasks.SLOW_WAIT + 1
        self.assertTrue(queue.view()["congested"])


class RouteTests(unittest.TestCase):
    """The page talks to the queue through /api/queue, /api/tasks and /api/tasks/<id>."""

    def setUp(self):
        self.studio = mock.Mock()
        self.studio.tasks = TaskQueue()
        self.gate = threading.Event()
        self.gate.set()

        def change_artwork(body):
            self.gate.wait(5)
            if body.get("fail"):
                raise ConflictError("The cover changed in iBroadcast since you loaded it.")
            return {"artwork_id": 7, "image": "x"}

        self.studio.change_artwork.side_effect = change_artwork
        self.studio.save.return_value = {"results": []}
        handler = type("TestHandler", (app.Handler,), {"studio": self.studio, "port": 0})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        handler.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            data = json.dumps(body) if body is not None else None
            sent = {"X-Tagcast": "1"} if method == "POST" else {}
            connection.request(method, path, body=data, headers={**sent, **(headers or {})})
            response = connection.getresponse()
            raw = response.read()
            try:
                return response.status, json.loads(raw or b"{}")
            except ValueError:  # a refused Host gets a plain error page
                return response.status, {}
        finally:
            connection.close()

    def finish(self, task_id):
        self.assertTrue(wait_for(lambda: self.request("GET", f"/api/tasks/{task_id}")[1]["state"] not in ("queued", "running")))
        return self.request("GET", f"/api/tasks/{task_id}")[1]

    def test_a_queued_action_answers_at_once_and_its_result_is_collected_later(self):
        self.gate.clear()
        status, body = self.request("POST", "/api/queue", {"path": "/api/artwork", "label": "Cover of X", "body": {"id": "1"}})
        self.assertEqual(status, 200)
        task = body["task"]
        self.assertEqual(task["label"], "Cover of X")
        self.assertIn(self.request("GET", f"/api/tasks/{task['id']}")[1]["state"], ("queued", "running"))
        self.gate.set()
        self.assertEqual(self.finish(task["id"]), {"state": "done", "result": {"artwork_id": 7, "image": "x"}})
        self.studio.change_artwork.assert_called_once_with({"id": "1"})

    def test_a_failure_arrives_with_the_status_the_direct_request_would_have_had(self):
        _, body = self.request("POST", "/api/queue", {"path": "/api/artwork", "body": {"fail": True}})
        answer = self.finish(body["task"]["id"])
        self.assertEqual((answer["state"], answer["status"]), ("error", 409))
        self.assertTrue(answer["data"]["conflict"])

    def test_only_writes_to_ibroadcast_can_be_queued(self):
        for path in ("/api/library", "/api/galaxy/add", "/api/queue", "/api/nothing"):
            status, body = self.request("POST", "/api/queue", {"path": path, "body": {}})
            self.assertEqual(status, 400, path)
            self.assertIn("can't be queued", body["error"])
        status, _ = self.request("POST", "/api/queue", {"path": "/api/save", "body": "not an object"})
        self.assertEqual(status, 400)
        self.studio.save.assert_not_called()

    def test_every_queueable_path_is_a_real_route(self):
        for path in app.QUEUED_PATHS:
            status, body = self.request("POST", "/api/queue", {"path": path, "body": {}})
            self.assertEqual(status, 200, f"{path}: {body}")
        self.assertTrue(self.studio.tasks.wait_idle())

    def test_the_queue_has_a_ceiling(self):
        self.gate.clear()
        with mock.patch.object(app, "MAX_QUEUED", 3):
            for _ in range(3):
                self.assertEqual(self.request("POST", "/api/queue", {"path": "/api/artwork", "body": {}})[0], 200)
            status, body = self.request("POST", "/api/queue", {"path": "/api/artwork", "body": {}})
        self.assertEqual(status, 400)
        self.assertIn("3 changes are already waiting", body["error"])
        self.gate.set()

    def test_the_task_list_and_results_follow_the_usual_host_and_site_checks(self):
        status, _ = self.request("POST", "/api/queue", {"path": "/api/save", "body": {}}, {"X-Tagcast": ""})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/api/tasks", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("GET", "/api/tasks", headers={"Host": "elsewhere.test"})[0], 403)
        self.assertEqual(self.request("GET", "/api/tasks/abc", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("GET", "/api/tasks")[0], 200)

    def test_an_unknown_task_says_so(self):
        status, body = self.request("GET", "/api/tasks/not-a-task")
        self.assertEqual(status, 400)
        self.assertIn("no longer available", body["error"])

    def test_the_task_list_shows_what_is_running_and_waiting(self):
        self.gate.clear()
        self.request("POST", "/api/queue", {"path": "/api/artwork", "label": "first", "body": {}})
        self.request("POST", "/api/queue", {"path": "/api/save", "label": "second", "body": {}})
        self.assertTrue(wait_for(lambda: self.request("GET", "/api/tasks")[1]["waiting"] == 1))
        view = self.request("GET", "/api/tasks")[1]
        self.assertEqual([t["label"] for t in view["tasks"]], ["first", "second"])
        self.assertEqual(view["active"], 2)
        self.gate.set()


class ActivityTests(unittest.TestCase):
    def test_a_finished_task_is_logged_like_the_request_it_replaces_with_a_long_wait_noted(self):
        task = {"path": "/api/artwork", "label": "Cover of X", "state": "done", "created": 100.0, "started": 130.0,
                "ended": 132.0, "status": 200, "result": {"artwork_id": 7}, "payload": None, "crash": "",
                "body": {"id": "1"}}
        with mock.patch.object(app.activity, "record") as record:
            app.record_task(task)
        first, second = record.call_args_list
        self.assertEqual(first.args[:2], ("metadata", "ok"))
        self.assertIn("artwork", first.args[2])
        self.assertEqual(first.kwargs["ms"], 2000.0)
        self.assertIn("waited 30 s", second.args[2])

    def test_a_failed_task_is_logged_as_an_error_with_its_message(self):
        task = {"path": "/api/save", "label": "save", "state": "error", "created": 1.0, "started": 1.0, "ended": 2.0,
                "status": 409, "result": None, "payload": {"error": "Changed in iBroadcast."}, "crash": "", "body": {}}
        with mock.patch.object(app.activity, "record") as record:
            app.record_task(task)
        record.assert_called_once()
        self.assertEqual(record.call_args.args[1], "error")
        self.assertIn("Changed in iBroadcast.", record.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
