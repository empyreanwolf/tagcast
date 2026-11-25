"""Log sync (fork v125): after an error, the Claude digest is pushed to a separate private GitHub repo.

Off until a repo (``owner/name``) and a fine-grained token (Contents read/write on that repo only) are set in Settings.
At most one push per 10 minutes; "Send now" skips the wait. Nothing here may break the app, and the token is never
returned, logged or put in the digest.
"""

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request

FILE = "logsync.json"
INTERVAL = 600
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_current = None


def urllib_http(method, url, headers, body):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        return error.code, {}


class LogSync:
    threaded = True

    def __init__(self, studio, http=None, clock=time.monotonic):
        self.studio, self.http, self.clock = studio, http or urllib_http, clock
        data = studio._read(FILE)
        data = data if isinstance(data, dict) else {}
        self.repo, self.token = str(data.get("repo") or ""), str(data.get("token") or "")
        self.last, self.error, self._attempt = "", "", None
        self._lock = threading.Lock()

    def digest(self):
        from .claude_digest import build_digest
        return build_digest(self.studio)

    def configure(self, repo, token=""):
        repo = str(repo or "").strip()
        if repo and not REPO.fullmatch(repo):
            raise ValueError("Repository must look like owner/name.")
        self.repo = repo
        if token:
            self.token = str(token).strip()
        if not repo:
            self.token = ""
        self.studio._write(FILE, {"repo": self.repo, "token": self.token})
        self.error = ""
        return self.status()

    def status(self):
        return {"on": bool(self.repo and self.token), "repo": self.repo, "has_token": bool(self.token),
                "last": self.last, "error": self.error}

    def notify_error(self):
        if not (self.repo and self.token):
            return
        with self._lock:
            now = self.clock()
            if self._attempt is not None and now - self._attempt < INTERVAL:
                return
            self._attempt = now
        if self.threaded:
            threading.Thread(target=self._push, daemon=True, name="logsync").start()
        else:
            self._push()

    def send_now(self):
        if not (self.repo and self.token):
            self.error = "Set the repository and token first."
            return self.status()
        with self._lock:
            self._attempt = self.clock()
        self._push()
        return self.status()

    def _push(self):
        try:
            url = f"https://api.github.com/repos/{self.repo}/contents/latest.md"
            headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                       "User-Agent": "tagcast-fork"}
            content = base64.b64encode(self.digest().encode("utf-8")).decode()
            code = 0
            for _ in range(2):
                got, info = self.http("GET", url, headers, None)
                body = {"message": "Tagcast digest " + time.strftime("%Y-%m-%d %H:%M:%S"), "content": content}
                if got == 200 and isinstance(info, dict) and info.get("sha"):
                    body["sha"] = info["sha"]
                code, _info = self.http("PUT", url, headers, body)
                if code != 409:
                    break
            if code in (200, 201):
                self.last, self.error = time.strftime("%Y-%m-%d %H:%M:%S"), ""
            else:
                self.error = f"GitHub answered {code}. Check the repository name and that the token can write its contents."
        except Exception as error:  # never break the app
            self.error = f"{type(error).__name__}: {str(error)[:120]}".replace(self.token or "\0", "<hidden>")


def start(studio):
    global _current
    _current = LogSync(studio)
    return _current


def notify_error():
    sync = _current
    if sync is not None:
        try:
            sync.notify_error()
        except Exception:
            pass


def handle(sync, body):
    """The /api/logsync action: save the repo and token, send now, or just report the status (never the token)."""
    from .sources import SourceError
    action = (body or {}).get("action")
    if action == "save":
        try:
            return sync.configure(body.get("repo"), body.get("token"))
        except ValueError as error:
            raise SourceError(str(error)) from None
    if action == "send":
        return sync.send_now()
    return sync.status()
