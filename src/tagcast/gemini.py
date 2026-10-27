"""Ask Gemini (fork v21): a chat page backed by your own free Gemini API key.

Not the gemini.google.com consumer chat -- that page has no API, so an app can't talk to
it directly without automating your actual logged-in browser (fragile, and against
Google's terms for unattended/repeated use). Instead this calls the Gemini Developer API
(generativelanguage.googleapis.com) with a personal API key from Google AI Studio
(aistudio.google.com/apikey), which is free to create and has its own free quota.

You type anything -- an artist, a mood, "what should I listen to after X" -- and Gemini
replies conversationally. It's asked (via a system instruction) to end any reply that
names concrete songs or other artists with a small fenced JSON block; that block is
parsed out and shown as a "Get it" list (checked against your library/history, same as
Charts) and a row of similar-artist chips, while the rest of the reply is shown as plain
chat text. Songs you pick go through the same Bandcamp -> Soulseek -> Galaxy order as
Discography/Charts, one at a time; not found anywhere lands on the wishlist.

This calls the plain ``generateContent`` method -- no Google Search grounding. Grounding
would let it answer "what's current" questions (e.g. "top hip-hop albums this month")
accurately, but it is paid-tier only even though a small monthly quota is bundled in free
once billing is enabled; since the brief for this feature was to stay on a free key, it
is left out, and the system prompt instead asks the model to flag when a question is
time-sensitive rather than guess from training data. A later "Get it" can still find a
song it names wrong; the library/history check on each title is the actual safety net.

The exact request/response shape of Gemini's REST API could not be exercised against the
real service from this environment (no network path to generativelanguage.googleapis.com
here), so this follows Google's long-documented ``models.{model}:generateContent`` REST
method and fails with a clear, actionable error (check the key/model in Settings) rather
than a crash if a future API change breaks it. The model name is a Settings field, not
hardcoded, so a renamed/retired model can be fixed without a code change.

State: ``gemini.json`` (API key, model name, the conversation). The conversation persists
across restarts; "New conversation" clears it.
"""

import json
import logging
import re
import threading

import requests

from . import artist_match as match
from .library import LibraryError, text

log = logging.getLogger("tagcast")
STATE_FILE = "gemini.json"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"  # Settings can override; raise if Google retires this
MAX_TURNS = 40          # user+model pairs kept for context and shown in the page
MAX_MESSAGE = 4000
MAX_SONGS_PER_REPLY = 20
MAX_SIMILAR_PER_REPLY = 12
JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```\s*$", re.S)

SYSTEM_PROMPT = (
    "You are a music-discovery assistant inside a personal music app called Tagcast. "
    "Talk naturally and helpfully about whatever artist, song or music question the user "
    "raises. You have no live internet access and only know what was in your training "
    "data, so for anything time-sensitive (this week's/month's/year's charts, a release "
    "date, whether something has come out yet) say plainly that you can't check current "
    "data and give your best training-data answer labelled as possibly outdated, rather "
    "than stating it as current fact. Whenever you can name concrete songs worth "
    "downloading, or other artists worth exploring, end your reply with exactly one "
    "fenced ```json code block, and "
    "nothing after it, shaped exactly like this (use empty arrays if you have nothing to "
    "suggest yet, and never invent an artist or song you are not reasonably sure exists): "
    '```json\n{"songs": [{"artist": "", "title": "", "album": ""}], "similar_artists": [""]}\n```'
    " Never put that JSON block anywhere except at the very end of the reply, and never "
    "wrap it in anything else."
)


class GeminiError(LibraryError):
    pass


class Gemini:
    def __init__(self, studio, session=None):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("api_key", "")
        self.state.setdefault("model", DEFAULT_MODEL)
        self.state.setdefault("history", [])
        self.state.setdefault("next_id", 1)
        self.session = session or requests.Session()
        self.lock = threading.RLock()
        self.jobs = []       # recent per-song "Get it" outcomes, most recent first
        self.progress = None  # {"done", "total", "title"} while a "Get it" batch runs

    def _persist(self):
        self.studio._write(STATE_FILE, self.state)

    # -- settings -----------------------------------------------------------------------

    def settings(self):
        return {"has_key": bool(self.state["api_key"]), "model": self.state["model"]}

    def configured(self):
        return bool(self.state["api_key"])

    def save_settings(self, body):
        if not isinstance(body, dict):
            raise GeminiError("Choose the Gemini settings.")
        with self.lock:
            if "api_key" in body:
                key = str(body["api_key"] or "").strip()
                if key:
                    if len(key) > 200 or any(c.isspace() for c in key):
                        raise GeminiError("That Gemini API key doesn't look right.")
                    self.state["api_key"] = key
                else:
                    self.state["api_key"] = ""
            if body.get("model"):
                model = str(body["model"]).strip()
                if len(model) > 100 or any(c.isspace() for c in model):
                    raise GeminiError("That model name doesn't look right.")
                self.state["model"] = model
            self._persist()
        return self.settings()

    # -- reading --------------------------------------------------------------------------

    def view(self):
        with self.lock:
            return {"history": list(self.state["history"]), "progress": self.progress, **self.settings()}

    def jobs_view(self):
        with self.lock:
            return {"jobs": list(self.jobs[:50]), "progress": self.progress}

    def reset(self):
        with self.lock:
            self.state["history"] = []
            self._persist()
        return self.view()

    # -- asking ---------------------------------------------------------------------------

    def ask(self, message):
        message = " ".join(str(message or "").split())
        if not message:
            raise GeminiError("Type a message.")
        if len(message) > MAX_MESSAGE:
            raise GeminiError(f"Keep it under {MAX_MESSAGE} characters.")
        if not self.configured():
            raise GeminiError("Add your free Gemini API key in Settings first (aistudio.google.com/apikey).")
        with self.lock:
            model, api_key = self.state["model"], self.state["api_key"]
            history = list(self.state["history"])[-MAX_TURNS * 2:]
        contents = [{"role": "user", "parts": [{"text": SYSTEM_PROMPT}]},
                    {"role": "model", "parts": [{"text": "Understood."}]}]
        for turn in history:
            contents.append({"role": "model" if turn["role"] == "model" else "user",
                              "parts": [{"text": turn["text"]}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        reply = self._generate(model, api_key, contents)
        shown, songs_raw, similar = self._parse_reply(reply)
        with self.lock:
            songs = []
            for s in songs_raw:
                songs.append({"id": self.state["next_id"], **s})
                self.state["next_id"] += 1
            songs = self._with_status(songs)
            self.state["history"].append({"role": "user", "text": message})
            self.state["history"].append({"role": "model", "text": shown, "songs": songs, "similar_artists": similar})
            del self.state["history"][:-MAX_TURNS * 2]
            self._persist()
        return self.view()

    def _generate(self, model, api_key, contents):
        url = f"{API_BASE}/{model}:generateContent"
        try:
            r = self.session.post(url, params={"key": api_key}, json={"contents": contents}, timeout=30)
            r.raise_for_status()
            data = r.json()
        except requests.HTTPError as error:
            raise GeminiError(f"Gemini didn't answer -- check the API key and model name in Settings ({error}).") from None
        except (requests.RequestException, ValueError) as error:
            raise GeminiError(f"Couldn't reach Gemini: {error}") from None
        try:
            return text(data["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError):
            block = (data.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise GeminiError(f"Gemini declined to answer this ({block}).") from None
            raise GeminiError("Gemini's reply came back empty -- try again, or check the model name in Settings.") from None

    @staticmethod
    def _parse_reply(reply):
        m = JSON_BLOCK_RE.search(reply or "")
        if not m:
            return (text(reply).strip() or "(Gemini didn't say anything.)"), [], []
        shown = text(reply[:m.start()]).strip() or "(Gemini didn't say anything else.)"
        try:
            parsed = json.loads(m.group(1))
        except ValueError:
            return shown, [], []
        songs, similar, seen_similar = [], [], set()
        if isinstance(parsed, dict):
            for s in (parsed.get("songs") or [])[:MAX_SONGS_PER_REPLY]:
                if isinstance(s, dict) and text(s.get("artist")) and text(s.get("title")):
                    songs.append({"artist": text(s.get("artist")), "title": text(s.get("title")),
                                  "album": text(s.get("album"))})
            for a in parsed.get("similar_artists") or []:
                name = text(a)
                key = match.match_key(name)
                if name and key not in seen_similar:
                    seen_similar.add(key)
                    similar.append(name)
                if len(similar) >= MAX_SIMILAR_PER_REPLY:
                    break
        return shown, songs, similar

    def _with_status(self, rows):
        if not rows:
            return rows
        manager = self.studio.download_manager
        try:
            self.studio._require_client()
            results = manager.check([{"kind": "song", "artist": r["artist"], "title": r["title"]} for r in rows])["results"]
        except LibraryError:
            results = [{"status": "new"}] * len(rows)
        for row, result in zip(rows, results):
            row["status"] = result["status"]
        return rows

    # -- getting songs --------------------------------------------------------------------

    def get(self, ids):
        wanted = {str(i) for i in (ids or [])}
        if not wanted:
            raise GeminiError("Choose at least one song.")
        rows = []
        with self.lock:
            for turn in self.state["history"]:
                for song in turn.get("songs") or []:
                    if str(song["id"]) in wanted:
                        rows.append(dict(song))
            if not rows:
                raise GeminiError("Choose at least one song.")
            if self.progress:
                raise GeminiError("Still getting songs from this conversation.")
            self.progress = {"done": 0, "total": len(rows), "title": ""}
        threading.Thread(target=self._get_songs, args=(rows,), name="gemini-get", daemon=True).start()
        return {"jobs": len(rows)}

    def _get_songs(self, rows):
        ctx = {}
        discography = self.studio.discography
        try:
            for row in rows:
                self.progress["title"] = row["title"]
                key = match.match_key(row["artist"])
                artist_ctx = ctx.setdefault(key, {"bandcamp": None, "bandcamp_checked": False})
                try:
                    source, note = discography._song(row["artist"], row["title"], row.get("album", ""), artist_ctx)
                except Exception as error:
                    source, note = "", str(error)
                if not source:
                    note = discography._wish("song", row["artist"], row["title"], row.get("album", ""), "missing", note)
                with self.lock:
                    self.jobs.insert(0, {"title": row["title"], "artist": row["artist"], "source": source, "note": note})
                    del self.jobs[200:]
                self.progress["done"] += 1
        finally:
            with self.lock:
                self.progress = None
