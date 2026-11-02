"""Spotify links through spotDL (fork v66): github.com/spotDL/spotify-downloader (MIT).

spotDL reads a Spotify song, album, playlist or artist link, takes the exact tags from Spotify
(title, artists, album, album artist, track number and count, year, genre, cover, ISRC) and finds the
matching audio on YouTube Music. Tagcast uses only that first half: ``spotdl save <link> --preload``
writes the song list with Spotify's tags and the YouTube Music link of each song. Tagcast then
downloads that link with its own yt-dlp worker (the "more" source) and writes Spotify's tags and
cover, so albums come out complete and numbered. Every song is checked against your library first.

spotDL pins its own versions of yt-dlp and other packages, so it lives in its own small Python
environment in the settings folder (``spotdl-env``), installed once, on first use, in the background.
Spotify limits its API; when spotDL's built-in key is busy, add your own free Spotify app key
(developer.spotify.com) under More sources > Spotify. The audio is YouTube audio, so it is lossy.
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import artist_match as match
from .library import LibraryError

log = logging.getLogger("tagcast")
STATE_FILE = "spotify.json"
ENV_DIR = "spotdl-env"
MAX_SONGS = 200
SAVE_TIMEOUT = 15 * 60
LINK_RE = re.compile(r"^https?://(?:open\.)?spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/([A-Za-z0-9]{10,40})")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes on Windows


class SpotifyError(LibraryError):
    pass


def spotify_link(url):
    """(kind, id) of a Spotify link, else None."""
    m = LINK_RE.match(str(url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def song_row(song):
    """A spotDL song (its ``save`` JSON) as a More-sources row with Spotify's tags."""
    if not isinstance(song, dict):
        return None
    title = match.clean(song.get("name"))
    artists = [match.clean(a) for a in song.get("artists") or [] if match.clean(a)] or [match.clean(song.get("artist"))]
    artists = [a for a in artists if a]
    url = str(song.get("download_url") or "")
    if not title or not artists or not url.startswith("http"):
        return None
    year = str(song.get("year") or song.get("date") or "")[:4]
    genres = [g for g in song.get("genres") or [] if isinstance(g, str) and g.strip()]
    album = match.clean(song.get("album_name"))
    return {"url": url, "title": title, "artist": artists[0], "site": "YouTube", "duration": song.get("duration") or 0,
            "album": album, "album_artist": match.clean(song.get("album_artist")) or artists[0],
            "track_no": int(song.get("track_number") or 1), "track_total": int(song.get("tracks_count") or 1),
            "spotify": {"artists": artists, "year": year if year.isdigit() else "", "genre": genres[0].title() if genres else "",
                        "disc": int(song.get("disc_number") or 1), "cover_url": str(song.get("cover_url") or ""),
                        "isrc": str(song.get("isrc") or ""), "spotify_url": str(song.get("url") or "")}}


class SpotDL:
    def __init__(self, studio, runner=None):
        self.studio = studio
        data = studio._read(STATE_FILE)
        self.state = data if isinstance(data, dict) else {}
        self.state.setdefault("client_id", "")
        self.state.setdefault("client_secret", "")
        self.lock = threading.Lock()
        self.installing = False
        self.install_error = ""
        self._run = runner  # tests: (link) -> list of spotDL songs

    # -- the private environment ----------------------------------------------------------

    @property
    def env(self):
        return Path(self.studio.home) / ENV_DIR

    def python(self):
        return self.env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def installed(self):
        return self._run is not None or (self.python().exists() and (self.env / "spotdl-ok").exists())

    def status(self):
        return {"installed": self.installed(), "installing": self.installing, "error": self.install_error,
                "own_key": bool(self.state.get("client_id") and self.state.get("client_secret"))}

    def install(self):
        """Create the private environment and install spotDL into it, in the background (about a minute)."""
        with self.lock:
            if self.installing or self.installed():
                return self.status()
            self.installing, self.install_error = True, ""
        threading.Thread(target=self._install, daemon=True, name="spotdl-install").start()
        return self.status()

    def _install(self):
        try:
            if not self.python().exists():
                subprocess.run([sys.executable, "-m", "venv", str(self.env)], check=True, capture_output=True,
                               timeout=300, creationflags=NO_WINDOW)
            out = subprocess.run([str(self.python()), "-m", "pip", "install", "--quiet", "--upgrade", "spotdl"],
                                 capture_output=True, text=True, timeout=900, creationflags=NO_WINDOW)
            if out.returncode != 0:
                raise SpotifyError((out.stderr or out.stdout or "pip failed").strip()[-300:])
            (self.env / "spotdl-ok").write_text(time.strftime("%Y-%m-%d"), encoding="utf-8")
        except (OSError, subprocess.SubprocessError, SpotifyError) as error:
            self.install_error = f"Could not install spotDL: {error}"
            log.warning(self.install_error)
        finally:
            self.installing = False

    def save_settings(self, body):
        if not isinstance(body, dict):
            raise SpotifyError("Choose the Spotify settings.")
        for key in ("client_id", "client_secret"):
            if key in body:
                value = str(body[key] or "").strip()
                if len(value) > 100 or not re.fullmatch(r"[A-Za-z0-9]*", value):
                    raise SpotifyError("A Spotify app key is letters and numbers only.")
                self.state[key] = value
        self.studio._write(STATE_FILE, self.state)
        return self.status()

    # -- reading a link -------------------------------------------------------------------

    def songs(self, link):
        """Every song behind one Spotify link, with Spotify's tags and the YouTube Music link spotDL picked."""
        if not spotify_link(link):
            raise SpotifyError("That is not a Spotify song, album, playlist or artist link.")
        if self._run is not None:
            raw = self._run(link)
        else:
            if not self.installed():
                self.install()
                raise SpotifyError("spotDL is being installed (one time, about a minute). Paste the link again after that.")
            raw = self._save(link)
        rows = [r for r in (song_row(s) for s in raw[:MAX_SONGS]) if r]
        if not rows:
            raise SpotifyError("spotDL found no songs it could match on YouTube Music for this link.")
        return rows

    def _save(self, link):
        with tempfile.TemporaryDirectory(prefix="tagcast-spotdl-") as folder:
            out = Path(folder) / "songs.spotdl"
            cmd = [str(self.python()), "-m", "spotdl", "save", link, "--save-file", str(out), "--preload", "--threads", "6"]
            if self.state.get("client_id") and self.state.get("client_secret"):
                cmd += ["--client-id", self.state["client_id"], "--client-secret", self.state["client_secret"]]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=SAVE_TIMEOUT, cwd=folder,
                                        creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                raise SpotifyError("spotDL took too long on this link (more than 15 minutes).") from None
            except OSError as error:
                raise SpotifyError(f"spotDL could not start ({error}).") from None
            if not out.exists():
                tail = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", (result.stderr or result.stdout or "").strip())[-300:]
                if re.search(r"rate|429|too many", tail, re.I):
                    raise SpotifyError("Spotify is limiting spotDL's built-in key right now. Add your own free Spotify app key "
                                       "under More sources > Spotify, or try again later.")
                raise SpotifyError(f"spotDL could not read this link ({tail or 'no answer'}).")
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
            except ValueError:
                raise SpotifyError("spotDL wrote an unreadable song list.") from None
        return data if isinstance(data, list) else []
