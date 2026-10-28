"""Background task queue (fork v36).

Anything that writes to iBroadcast can take a while: Tagcast checks that nothing changed
there first (a library download can take half a minute), uploads an image, writes, and
reads the result back. These actions are queued here instead of holding the page: the
page gets a task id straight away, keeps working, and asks how the task is doing.

One worker runs the tasks one at a time, in the order they were queued. That is on
purpose: every save has to see the library the previous save left behind, and two saves
at once used to be refused with "Another save is still running".

``view()`` is what the Tasks indicator shows: what is running, what is waiting, and when
the queue is congested (many waiting, or the oldest has waited a long time) a message
saying how long it is likely to take.

Tasks live in memory only: closing Tagcast drops whatever was still waiting, which is why
the page warns before it is closed while tasks are active.
"""

import secrets
import threading
import time
from collections import deque
from statistics import mean

MANY_WAITING = 6          # this many waiting counts as congested
SLOW_WAIT = 45            # ...and so does the oldest one having waited this many seconds
KEEP_FINISHED = 60        # finished tasks (and their answers) kept for the page to collect
GUESS_SECONDS = 8.0       # per task, until some have finished and we know better
TIMING_SAMPLES = 12
BUSY = "Another save is still running"  # what an action says when something else holds the save lock
BUSY_RETRY_SECONDS = 120  # how long a task keeps waiting for that (a cleanup run, say) before it gives up


class TaskQueue:
    def __init__(self, on_finish=None):
        self.cond = threading.Condition()
        self.waiting = deque()      # task dicts, oldest first
        self.running = None
        self.finished = deque()     # task dicts, newest first
        self.durations = deque(maxlen=TIMING_SAMPLES)
        self.thread = None
        self.on_finish = on_finish  # called with the finished task, from the worker

    # -- submitting -----------------------------------------------------------------

    def submit(self, path, label, run, classify, body=None):
        """Queue run() and return the task as the page sees it.

        run() returns the answer (a dict). If it raises, classify(error) -> (status,
        payload, crash) says how the failure is reported, the same way a direct request
        would have reported it. body is only kept for the Activity log.
        """
        task = {"id": secrets.token_urlsafe(9), "path": path, "label": str(label or path)[:200], "body": body,
                "state": "queued", "created": time.time(), "started": None, "ended": None,
                "status": None, "result": None, "error": "", "payload": None, "crash": "",
                "_run": run, "_classify": classify}
        with self.cond:
            self.waiting.append(task)
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._work, name="tagcast-tasks", daemon=True)
                self.thread.start()
            self.cond.notify()
            return self._public(task)

    # -- the worker -----------------------------------------------------------------

    def _work(self):
        while True:
            with self.cond:
                while not self.waiting:
                    self.cond.wait()
                task = self.waiting.popleft()
                task["state"], task["started"] = "running", time.time()
                self.running = task
            try:
                task["result"], task["status"] = self._run_when_free(task), 200
                task["state"] = "done"
            except Exception as error:  # reported to whoever asked, never allowed to end the worker
                try:
                    task["status"], task["payload"], task["crash"] = task["_classify"](error)
                except Exception:
                    task["status"], task["payload"] = 500, {"error": "Something went wrong in Tagcast."}
                task["error"] = str((task["payload"] or {}).get("error") or "The action failed.")
                task["state"] = "error"
            task["ended"] = time.time()
            with self.cond:
                self.running = None
                self.durations.append(task["ended"] - task["started"])
                self.finished.appendleft(task)
                while len(self.finished) > KEEP_FINISHED:
                    self.finished.pop()
            if self.on_finish:
                try:
                    self.on_finish(task)
                except Exception:
                    pass  # logging the task must never stop the next one

    @staticmethod
    def _run_when_free(task):
        """Run the task; if something outside the queue (a cleanup run) holds the save lock,
        wait for it instead of failing. The lock is checked before anything is written, so
        trying again never repeats a write."""
        give_up = time.monotonic() + BUSY_RETRY_SECONDS
        while True:
            try:
                return task["_run"]()
            except Exception as error:
                if not str(error).startswith(BUSY) or time.monotonic() > give_up:
                    raise
                task["note"] = "Waiting for another save in Tagcast to finish…"
                time.sleep(1)

    # -- reading --------------------------------------------------------------------

    def _public(self, task):
        now = time.time()
        started, ended = task["started"], task["ended"]
        view = {"id": task["id"], "path": task["path"], "label": task["label"], "state": task["state"],
                "waited": round((started or now) - task["created"], 1),
                "took": round(ended - started, 1) if started and ended else None,
                "running_for": round(now - started, 1) if started and not ended else None}
        if task["state"] == "error":
            view["error"] = task["error"]
            view["status"] = task["status"]
        if task.get("note") and task["state"] == "running":
            view["note"] = task["note"]
        return view

    def view(self):
        """What the Tasks indicator shows."""
        now = time.time()
        with self.cond:
            waiting, running = list(self.waiting), self.running
            recent = list(self.finished)[:15]
            typical = mean(self.durations) if self.durations else GUESS_SECONDS
            oldest = now - waiting[0]["created"] if waiting else 0
        pending = len(waiting) + (1 if running else 0)
        congested = len(waiting) >= MANY_WAITING or oldest >= SLOW_WAIT
        eta = round(pending * typical)
        message = ""
        if congested:
            message = (f"{pending} changes are waiting to be saved to iBroadcast, one after the other. "
                       f"That will take about {_duration(eta)}. You can keep using Tagcast; "
                       "please keep it open until this is done.")
        return {"active": pending, "waiting": len(waiting), "congested": congested, "eta_seconds": eta,
                "message": message,
                "tasks": [self._public(t) for t in ([running] if running else []) + waiting + recent]}

    def result(self, task_id):
        """How one task is doing, as the page reads it: {"state": "queued" | "running"},
        {"state": "done", "result": ...} or {"state": "error", "status": 409, "data":
        <what the direct request would have answered>}. KeyError for an unknown task."""
        with self.cond:
            for task in [self.running, *self.waiting, *self.finished]:
                if task and task["id"] == task_id:
                    break
            else:
                raise KeyError(task_id)
            if task["state"] == "done":
                return {"state": "done", "result": task["result"]}
            if task["state"] == "error":
                return {"state": "error", "status": task["status"], "data": task["payload"]}
            return {"state": task["state"]}

    def active(self):
        with self.cond:
            return len(self.waiting) + (1 if self.running else 0)

    def wait_idle(self, timeout=10):
        """Block until nothing is queued or running (tests, shutdown)."""
        end = time.time() + timeout
        while time.time() < end:
            if not self.active():
                return True
            time.sleep(.01)
        return False


def _duration(seconds):
    if seconds < 90:
        return "a minute" if seconds > 45 else f"{max(seconds, 5)} seconds"
    return f"{round(seconds / 60)} minutes"
