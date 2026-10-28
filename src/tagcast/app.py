"""Local web server for Tagcast. Listens on 127.0.0.1 only.

Remote access (fork v25): the socket itself never leaves loopback, and `_host_ok()` still
rejects any Host header it doesn't recognize by default -- that pair is what keeps a
same-network device, or a malicious page's background request, from reaching your
iBroadcast session. ``TAGCAST_EXTRA_HOSTS`` (comma-separated hostnames, no scheme/port) is
the one opt-in escape hatch, meant for a `tailscale serve 127.0.0.1:<port>` setup: Tailscale
forwards from its own private, authenticated network into this same loopback socket, and
the env var just tells `_host_ok()` to also trust the hostname that arrives with it. Off by
default, so a normal install is exactly as closed as before this existed.
"""

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
import traceback
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import urlopen

from . import __version__, activity, artist_match
from .album_info import AlbumInformationRetryError
from .artist_bios import BiographyRetryError
from .artwork import ArtworkError
from .client import ApiError, NotConnected, Studio
from .downloads import album_key
from .library import ConflictError, LibraryError
from .sources import SourceError

STATIC = Path(__file__).parent / "static"
MAX_BODY = 24 * 1024 * 1024  # an uploaded image arrives base64-encoded
MAX_QUEUED = 100             # changes that may wait in the background queue at once (fork v36)
# Extra hostnames this server answers to, beyond 127.0.0.1/localhost -- e.g. a Tailscale
# MagicDNS name, once you're reaching it through `tailscale serve` (fork v25). Off by
# default: a wrong guess here just means another device on your tailnet can reach your
# iBroadcast session and downloads, so it's opt-in, one env var, never auto-detected.
# Comma-separated hostnames only, no scheme or port -- `tailscale serve` forwards whatever
# Host your browser used, which is the tailnet name, not "127.0.0.1".
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("TAGCAST_EXTRA_HOSTS", "").split(",") if h.strip()}


class Handler(SimpleHTTPRequestHandler):
    studio = None
    port = 8912
    # Fixed types: on Windows the registry can map .js to text/plain, and with nosniff
    # the browser would then refuse to run the page's scripts.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                      ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                      ".png": "image/png", ".json": "application/json"}

    def log_message(self, fmt, *args):
        if os.environ.get("TAGCAST_DEBUG"):
            super().log_message(fmt, *args)

    # -- helpers -------------------------------------------------------------

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        if host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}:
            return True
        hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host  # strip a :port, keep IPv6 literals intact
        return hostname in EXTRA_HOSTS

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser closed the request; another response cannot reach it either.
            self.close_connection = True
            logging.debug("Browser disconnected before receiving the JSON response")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("Request too large.")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError("Invalid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError("Invalid request.")
        return data

    def _redirect_uri(self):
        return f"http://127.0.0.1:{self.port}/callback"

    def _run(self, action):
        started, crash = time.monotonic(), ""
        try:
            status, payload = 200, action()
        except Exception as error:
            status, payload, crash = classify_error(error)
        self._json(status, payload)
        self._record_request(status, payload, (time.monotonic() - started) * 1000, crash)

    def _record_request(self, status, payload, ms, crash=""):
        """One Activity log entry per action (fork v35); see activity.py."""
        info = getattr(self, "_activity", None)
        if not info:
            return
        record_route(info["path"], info["method"], getattr(self, "_activity_body", None), status, payload, ms, crash)

    def end_headers(self):
        if self._frontend_asset():
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _frontend_asset(self):
        path = urlparse(getattr(self, "path", "")).path
        return not path.startswith("/api/") and (
            path == "/" or Path(path).suffix in (".html", ".js", ".css"))

    def send_head(self):
        # Local source updates can retain the same release number or file timestamp.
        # Always serve the current UI together instead of accepting an older cached script.
        if self._frontend_asset():
            for header in ("If-Modified-Since", "If-None-Match"):
                if header in self.headers:
                    del self.headers[header]
        return super().send_head()

    # -- routes --------------------------------------------------------------

    def _same_site(self):
        # Browsers say where a request comes from; other websites may not read or embed /api/.
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def do_GET(self):
        if not self._host_ok():
            host = self.headers.get("Host") or "(no Host header)"
            self.send_error(403, f'Open Tagcast via http://127.0.0.1, or add "{host}" to TAGCAST_EXTRA_HOSTS.')
            return
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path.startswith("/api/") and not self._same_site():
            self._json(403, {"error": "Forbidden."})
            return
        self._activity, self._activity_body = {"path": url.path, "method": "GET"}, None
        if url.path == "/api/activity":
            log = activity.current()
            self._activity = None  # reading the log is not itself logged
            self._run(lambda: log.view(query.get("cat", ""), query.get("status", ""), query.get("q", ""),
                                       int(query.get("after") or 0), int(query.get("limit") or 300))
                      if log else {"entries": [], "latest": 0, "summary": [], "categories": activity.CATEGORIES})
            return
        if url.path == "/api/activity/export":
            self._activity = None
            log = activity.current()
            body = (log.export_text() if log else "No activity recorded yet.\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            stamp = time.strftime("%Y%m%d-%H%M")
            self.send_header("Content-Disposition", f'attachment; filename="tagcast-activity-{stamp}.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/tasks":
            self._activity = None  # polled every second or so while something is queued
            self._run(self.studio.tasks.view)
            return
        if url.path.startswith("/api/tasks/"):
            self._activity = None

            def task_state():
                try:
                    return self.studio.tasks.result(url.path.rsplit("/", 1)[1])
                except KeyError:
                    raise ApiError("This task is no longer available. Reload the library to see where things stand.") from None
            self._run(task_state)
            return
        if url.path.startswith("/api/stream/"):
            self._stream(url.path.rsplit("/", 1)[1], query.get("quality"))
            return
        if url.path == "/api/app":
            self._json(200, {"application": "Tagcast", "version": __version__,
                             "desktop_protocol": 1})
        elif url.path == "/api/status":
            self._run(self.studio.status)
        elif url.path == "/api/library":
            refresh = parse_qs(url.query).get("refresh") == ["1"]
            self._run(lambda: self.studio.load_library(refresh))
        elif url.path == "/api/overview":
            self._run(self.studio.overview)
        elif url.path == "/api/account-settings":
            self._run(self.studio.account_settings)
        elif url.path == "/api/service-status":
            self._run(lambda: self.studio.service_status.get(refresh=query.get("refresh") == "1"))
        elif url.path == "/api/settings":
            self._run(self.studio.settings)
        elif url.path == "/api/lyrics":
            self._run(lambda: self.studio.lyrics.view({
                **{k: query.get(k) for k in ("track_id", "title", "artist", "session")},
                "refresh": query.get("refresh") == "1"}))
        elif url.path == "/api/lyrics/cache":
            self._run(lambda: self.studio.lyrics.cached_album({
                "album_ids": query.get("album_ids", "").split(","), "session": query.get("session")}))
        elif url.path == "/api/metadata-export/status":
            self._run(lambda: self.studio.metadata_export.status(query.get("id"), query.get("session")))
        elif url.path == "/api/metadata-export/download":
            self._metadata_download(query.get("id"), query.get("session"))
        elif url.path == "/api/album-guide":
            self._run(self.studio.album_guide.view)
        elif url.path == "/api/album-guide/album":
            self._run(lambda: self.studio.album_guide.notes_for_album(
                query.get("album_id"), query.get("session"), query.get("album"), query.get("artist")))
        elif url.path == "/api/updates":
            self._run(self.studio.check_updates)
        elif url.path == "/api/lookup/album":
            self._run(lambda: self.studio.lookup_album(query.get("source"), query.get("album_id"),
                                                       query.get("artist"), query.get("album")))
        elif url.path == "/api/lookup/artist":
            self._run(lambda: self.studio.lookup_artist(query.get("source"), query.get("name")))
        elif url.path == "/api/artwork/related":
            self._run(lambda: self.studio.related_artwork(query.get("album_id"), query.get("artist_id")))
        elif url.path.startswith("/api/jobs/"):
            self._run(lambda: self.studio.job(url.path.rsplit("/", 1)[1]))
        elif url.path == "/api/albums":
            ids = [i for i in (parse_qs(url.query).get("ids") or [""])[0].split(",") if i]
            self._run(lambda: {"albums": self.studio.album_details(ids)})
        elif url.path == "/api/tracks":
            self._run(lambda: self.studio.search_tracks(query.get("q")))
        elif url.path == "/api/queue-sync":
            self._run(self.studio.queue_sync.view)
        elif url.path == "/api/playlists":
            self._run(lambda: self.studio.collections.playlists(query.get("q"), query.get("id"), query.get("offset", "0"), query.get("browse") == "1"))
        elif url.path == "/api/playlist/order":
            self._run(lambda: self.studio.collections.playlist_order_view({
                **{k: query.get(k) for k in ("playlist", "before_token", "before_name", "session")},
                "positions": query.get("positions", "").split(","), "query": query.get("q")}))
        elif url.path == "/api/playlist-folder/choices":
            self._run(lambda: self.studio.collections.folder_choices(query.get("q"), query.get("offset", "0")))
        elif url.path == "/api/album-favourites":
            self._run(self.studio.collections.album_favourites)
        elif url.path == "/api/artist-favourites":
            self._run(self.studio.collections.artist_favourites)
        elif url.path == "/api/tags":
            self._run(lambda: self.studio.tags.catalogue(query.get("q"), query.get("offset", "0"), query.get("id")))
        elif url.path == "/api/tags/selection":
            self._run(lambda: self.studio.tags.selection((query.get("albums") or "").split(","), query.get("track"),
                                                        {"0": False, "1": True}.get(query.get("multiple", "0"))))
        elif url.path == "/api/uploads":
            self._run(lambda: self.studio.uploads(query.get("view", "days"), query.get("day", ""), query.get("refresh") == "1"))
        elif url.path == "/api/favourites":
            self._run(lambda: self.studio.favourites(query.get("q"), query.get("offset", "0"),
                                                   query.get("limit", "50")))
        elif url.path == "/api/recently-played":
            self._run(lambda: self.studio.recently_played(query.get("q"), query.get("offset", "0"),
                                                        query.get("limit", "50"), query.get("refresh") == "1"))
        elif url.path == "/api/cleanup":
            self._run(lambda: self.studio.cleanup.view(query.get("offset", "0"), query.get("limit", "25")))
        elif url.path == "/api/cleanup/report":
            self._run(lambda: self.studio.cleanup.view(report=True))
        elif url.path == "/api/duplicate-cleanup":
            self._run(lambda: self.studio.duplicate_cleanup.view(query.get("offset", "0"), query.get("limit", "10")))
        elif url.path == "/api/duplicate-cleanup/report":
            self._run(lambda: self.studio.duplicate_cleanup.view(report=True))
        elif url.path == "/api/artist-cleanup":
            self._run(lambda: self.studio.artist_cleanup.view(query.get("offset", "0"), query.get("limit", "25")))
        elif url.path == "/api/artist-cleanup/report":
            self._run(lambda: self.studio.artist_cleanup.view(report=True))
        elif url.path == "/api/artist-cleanup/suggestions":
            self._run(lambda: self.studio.artist_cleanup.suggestions(query.get("kind")))
        elif url.path == "/api/artist-cleanup/search":
            self._run(lambda: self.studio.artist_cleanup.search(query.get("q", "")))
        elif url.path == "/api/artist-pictures":
            self._run(self.studio.artist_pictures.status)
        elif url.path == "/api/downloads":
            self._run(self.studio.download_manager.view)
        elif url.path == "/api/downloads/queue":
            self._run(self.studio.download_manager.queue_view)
        elif url.path == "/api/downloads/history":
            self._run(lambda: self.studio.download_manager.history_view(
                query.get("q", ""), query.get("source", ""), query.get("offset", "0"), query.get("limit", "50")))
        elif url.path == "/api/galaxy/cover":
            self._cover(query)
        elif url.path == "/api/complete":
            self._run(self.studio.completion.view)
        elif url.path == "/api/tagclean":
            self._run(self.studio.tagclean.view)
        elif url.path == "/api/charts":
            self._run(self.studio.charts.list)
        elif url.path == "/api/charts/view":
            self._run(lambda: self.studio.charts.view(query.get("chart"), query.get("refresh") == "1"))
        elif url.path == "/api/charts/jobs":
            self._run(self.studio.charts.jobs_view)
        elif url.path == "/api/charts/browse/options":
            self._run(self.studio.charts.browse_options)
        elif url.path == "/api/charts/browse/view":
            self._run(lambda: self.studio.charts.browse_view(
                query.get("source"), query.get("country", ""), query.get("genre", ""), query.get("refresh") == "1"))
        elif url.path == "/api/recommendations":
            self._run(lambda: self.studio.recommendations.view(query.get("source"), query.get("refresh") == "1"))
        elif url.path == "/api/recommendations/jobs":
            self._run(self.studio.recommendations.jobs_view)
        elif url.path == "/api/digest":
            self._run(self.studio.digest.view)
        elif url.path == "/api/wishlist":
            self._run(self.studio.wishlist.view)
        elif url.path == "/api/releases":
            self._run(self.studio.new_releases.view)
        elif url.path == "/api/releases/summary":
            self._run(self.studio.new_releases.summary)
        elif url.path == "/api/discography/get-status":
            self._run(lambda: self.studio.discography.status(query.get("id")))
        elif url.path == "/api/soulseek/status":
            self._run(self.studio.soulseek.status)
        elif url.path == "/api/galaxy/status":
            self._run(self.studio.galaxy.status)
        elif url.path == "/api/downloads/find-galaxy":
            self._run(self.studio.download_manager.find_galaxy)
        elif url.path == "/api/cleanup/labels":
            self._run(lambda: self.studio.cleanup.labels(query.get("q"), query.get("offset", "0"),
                                                         query.get("limit", "50")))
        elif url.path == "/api/browse":
            self._run(lambda: self.studio.browse(query.get("kind"), query.get("key"), query.get("q"),
                                               query.get("offset", "0"), query.get("limit", "50"),
                                               query.get("sort", "az")))
        elif url.path == "/callback":
            self._callback(parse_qs(url.query))
        elif url.path.startswith("/api/"):
            self._json(404, {"error": "Unknown endpoint."})
        else:
            if url.path in ("/", "/index.html"):
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        # The custom header forces a CORS preflight, which this server never approves,
        # so other websites cannot post to it.
        if not self._host_ok() or self.headers.get("X-Tagcast") != "1":
            self._json(403, {"error": "Forbidden."})
            return
        path = urlparse(self.path).path
        routes = {
            "/api/config": lambda b: (self.studio.set_client_id(b.get("client_id")),
                                      self.studio.status())[1],
            "/api/config/default": lambda b: (self.studio.use_default_client(),
                                              self.studio.status())[1],
            "/api/auth/device": lambda b: {"device": self.studio.start_device()},
            "/api/auth/cancel": lambda b: (self.studio.cancel_device(), {"ok": True})[1],
            "/api/auth/browser": lambda b: {"url": self.studio.browser_url(self._redirect_uri())},
            "/api/auth/logout": lambda b: (self.studio.logout(), {"ok": True})[1],
            "/api/save": lambda b: self.studio.save(b.get("changes")),
            "/api/cleanup/preview": lambda b: self.studio.cleanup.prepare(
                b.get("category"), b.get("label"), b.get("replacements"), b.get("remove", False)),
            "/api/cleanup/start": lambda b: self.studio.cleanup.start(b.get("id"), b.get("excluded")),
            "/api/cleanup/stop": lambda b: self.studio.cleanup.cancel(b.get("id")),
            "/api/artist-cleanup/preview": lambda b: self.studio.artist_cleanup.prepare(
                b.get("mode"), b.get("groups"), b.get("ids")),
            "/api/artist-cleanup/start": lambda b: self.studio.artist_cleanup.start(b.get("id"), b.get("excluded")),
            "/api/artist-cleanup/stop": lambda b: self.studio.artist_cleanup.cancel(b.get("id")),
            "/api/artist-pictures/start": lambda b: self.studio.artist_pictures.start(),
            "/api/artist-pictures/stop": lambda b: self.studio.artist_pictures.stop(),
            "/api/downloads/check": lambda b: self.studio.download_manager.check(
                b.get("items"), b.get("lines"), b.get("kind", "song")),
            "/api/downloads/submit": lambda b: self.studio.download_manager.submit(b.get("items"), "manual"),
            "/api/downloads/decide": lambda b: self.studio.download_manager.decide(b.get("ids"), b.get("action")),
            "/api/downloads/remove": lambda b: self.studio.download_manager.remove(b.get("ids")),
            "/api/downloads/folder": lambda b: self.studio.download_manager.set_folder(b.get("folder")),
            "/api/downloads/import": lambda b: self.studio.download_manager.import_history(b.get("kind"), b.get("path")),
            "/api/galaxy/search": lambda b: self.studio.galaxy.search(b.get("mode"), b.get("q"), b.get("source")),
            "/api/galaxy/album": lambda b: self.studio.galaxy.album(b.get("album"), b.get("artist"), b.get("source")),
            "/api/galaxy/add": lambda b: self.studio.galaxy.add(b.get("tracks"), b.get("album_artist"), b.get("total")),
            "/api/bandcamp/discography": lambda b: self.studio.bandcamp.discography(b.get("url")),
            "/api/bandcamp/album": lambda b: self.studio.bandcamp.album(b.get("url")),
            "/api/bandcamp/add": lambda b: self.studio.bandcamp.add(b.get("url"), b.get("nums")),
            "/api/complete/scan": lambda b: self.studio.completion.scan(),
            "/api/complete/tracks": lambda b: self.studio.completion.missing_tracks(b.get("key")),
            "/api/tagclean/scan": lambda b: self.studio.tagclean.scan(b.get("scope"), b.get("path") or ""),
            "/api/tagclean/settings": lambda b: self.studio.tagclean.save_settings(b),
            "/api/tagclean/apply-artists": lambda b: self.studio.tagclean.apply_artists(b.get("ids")),
            "/api/tagclean/apply-genre": lambda b: self.studio.tagclean.apply_genre(b.get("ids")),
            "/api/tagclean/assign-genre": lambda b: self.studio.tagclean.assign_genre(b.get("ids"), b.get("genre")),
            "/api/charts/get": lambda b: self.studio.charts.get(b.get("chart"), b.get("ranks")),
            "/api/charts/browse/get": lambda b: self.studio.charts.browse_get(
                b.get("source"), b.get("country") or "", b.get("genre") or "", b.get("ranks")),
            "/api/recommendations/get": lambda b: self.studio.recommendations.get(b.get("source"), b.get("ranks")),
            "/api/digest/mark-found-seen": lambda b: self.studio.digest.mark_found_seen(),
            "/api/wishlist/retry": lambda b: self.studio.wishlist.retry(b.get("ids")),
            "/api/wishlist/remove": lambda b: self.studio.wishlist.remove(b.get("ids")),
            "/api/wishlist/settings": lambda b: self.studio.wishlist.save_settings(b),
            "/api/wishlist/add": lambda b: self.studio.wishlist.add(b.get("kind"), b.get("artist"), b.get("title"), b.get("album") or ""),
            "/api/uploads/checked": lambda b: self.studio.uploads_checked(b),
            "/api/releases/settings": lambda b: self.studio.new_releases.save_settings(b),
            "/api/releases/check": lambda b: self.studio.new_releases.check(),
            "/api/releases/seen": lambda b: self.studio.new_releases.mark_seen(),
            "/api/releases/dismiss": lambda b: self.studio.new_releases.dismiss(b.get("ids")),
            "/api/discography": lambda b: self.studio.discography.view(
                b.get("name"), b.get("deezer_id"), b.get("musicbrainz") is True, b.get("refresh") is True),
            "/api/discography/top": lambda b: self.studio.discography.top(b.get("name"), b.get("deezer_id")),
            "/api/discography/get": lambda b: self.studio.discography.get(b.get("name"), b.get("albums"), b.get("songs")),
            "/api/soulseek/settings": lambda b: self.studio.soulseek.save_settings(b),
            "/api/soulseek/start": lambda b: self.studio.soulseek.start(),
            "/api/soulseek/stop": lambda b: self.studio.soulseek.stop(),
            "/api/soulseek/add": lambda b: self.studio.soulseek.add(b.get("items")),
            "/api/downloads/control": lambda b: self.studio.download_manager.control(b.get("action"), b.get("ids")),
            "/api/galaxy/settings": lambda b: self.studio.galaxy.save_settings(b),
            "/api/galaxy/album-years": lambda b: self.studio.galaxy.album_years(b.get("artist"), b.get("albums")),
            "/api/galaxy/control": lambda b: self.studio.galaxy.control(b.get("action"), b.get("ids")),
            "/api/galaxy/cards": lambda b: self.studio.artist_cards.cards_for(
                str(b.get("name") or "")[:200], [str(a)[:500] for a in (b.get("albums") or [])[:200]],
                self.studio.galaxy.state["picks"]),
            "/api/galaxy/pick": lambda b: self.studio.galaxy.pick(
                artist_match.match_key(b.get("name")), album_key(b.get("album")), b.get("card")),
            "/api/settings": self.studio.save_settings,
            "/api/lyrics": self.studio.lyrics.view,
            "/api/lyrics/review": self.studio.lyrics.prepare,
            "/api/lyrics/save": self.studio.lyrics.save,
            "/api/metadata-export": self.studio.metadata_export.start,
            "/api/metadata-export/cancel": self.studio.metadata_export.cancel,
            "/api/album-guide/analyse": lambda b: self.studio.album_guide.analyse(),
            "/api/album-guide/link": self.studio.album_guide.link,
            "/api/updates/check": lambda b: self.studio.check_updates(manual=True),
            "/api/artwork": self.studio.change_artwork,
            "/api/artwork/undo": self.studio.undo_artwork,
            "/api/trash": self.studio.trash_tracks,
            "/api/trash/albums": self.studio.trash_albums,
            "/api/tags/save": self.studio.tags.save,
            "/api/duplicate-cleanup/preview": lambda b: self.studio.duplicate_cleanup.prepare(),
            "/api/duplicate-cleanup/start": lambda b: self.studio.duplicate_cleanup.start(b.get("id"), b.get("excluded"), b.get("approved")),
            "/api/duplicate-cleanup/stop": lambda b: self.studio.duplicate_cleanup.cancel(b.get("id")),
            "/api/favourite": self.studio.favourite,
            "/api/play": self.studio.record_play,
            "/api/album-rating": self.studio.collections.rate_album,
            "/api/artist-rating": self.studio.collections.rate_artist,
            "/api/album-favourite": self.studio.collections.favourite_album,
            "/api/artist-favourite": self.studio.collections.favourite_artist,
            "/api/artist-biography": self.studio.artist_bios.view,
            "/api/album-information": self.studio.album_info.view,
            "/api/playlist": self.studio.collections.save_playlist,
            "/api/playlist-folder": self.studio.collections.save_folder,
            "/api/playlist/artwork": self.studio.collections.playlist_artwork,
            "/api/playlist-import/read": self.studio.playlist_import.read,
            "/api/playlist-import/url": self.studio.playlist_import.read_url,
            "/api/playlist-import/match": self.studio.playlist_import.match,
            "/api/playlist-import/view": self.studio.playlist_import.view,
            "/api/playlist-import/export": self.studio.playlist_import.export,
            "/api/playlist-import/search": self.studio.playlist_import.search,
            "/api/playlist-import/choose": self.studio.playlist_import.choose,
            "/api/playlist-import/review": self.studio.playlist_import.review,
            "/api/playlist-import/revise": self.studio.playlist_import.revise,
            "/api/playlist-import/save": self.studio.playlist_import.save,
            "/api/playlist-import/discard": self.studio.playlist_import.discard,
            "/api/playlist-import/paused": self.studio.playlist_import.paused,
            "/api/playlist-import/pause": self.studio.playlist_import.pause,
            "/api/playlist-import/resume": self.studio.playlist_import.resume,
            "/api/playlist-import/remove-paused": self.studio.playlist_import.remove_paused,
            "/api/queue-tracks": self.studio.collections.queue_tracks,
            "/api/activity/client": record_page_event,
            "/api/queue-sync": self.studio.queue_sync.action,
        }
        def enqueue(b):
            target, inner = b.get("path"), b.get("body")
            if target not in QUEUED_PATHS or target not in routes or not isinstance(inner, dict):
                raise ApiError("This action can't be queued.")
            if self.studio.tasks.active() >= MAX_QUEUED:
                raise ApiError(f"{MAX_QUEUED} changes are already waiting. Wait for some of them to finish.")
            action = routes[target]
            task = self.studio.tasks.submit(target, b.get("label") or target, lambda: action(inner),
                                            classify_error, inner)
            return {"task": task}
        routes["/api/queue"] = enqueue
        if path not in routes:
            self._json(404, {"error": "Unknown endpoint."})
            return
        self._activity, self._activity_body = {"path": path, "method": "POST"}, None
        if path in ("/api/activity/client", "/api/queue"):
            self._activity = None  # the first records itself as a page error; a queued task is recorded when it finishes

        def call():
            body = self._body()
            self._activity_body = body
            return routes[path](body)
        self._run(call)

    def _cover(self, query):
        """A Galaxy cover picture for <img>; 404 when there is none (the page shows ♪)."""
        try:
            data = self.studio.galaxy.thumbnail(query.get("source"), query.get("pic_id"), query.get("size", "300"))
        except LibraryError:
            data = None
        except Exception:
            logging.exception("Galaxy cover failed")
            data = None
        try:
            if not data:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if data[:4] == b"\x89PNG" else "image/jpeg")
            self.send_header("Cache-Control", "private, max-age=604800")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _metadata_download(self, job_id, session):
        """Stream the prepared private file without buffering it in a JSON response."""
        started = False
        try:
            with self.studio.metadata_export.download(job_id, session) as job:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8" if job["format"] == "json" else "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{job["filename"]}"')
                self.send_header("Content-Length", str(job["size"]))
                self.send_header("Cache-Control", "no-store")
                started = True
                self.end_headers()
                while chunk := job["file"].read(64 * 1024):
                    self.studio.metadata_export._check(job)
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as error:
            if started:
                self.close_connection = True
                logging.debug("Metadata download stopped: %s", type(error).__name__)
            else:
                # Reuse the API's sanitized error/status handling before any headers.
                def failed(failure=error):
                    raise failure
                self._run(failed)

    def _stream(self, track_id, quality=None):
        """Pass a track's audio through, so the access token never reaches the page."""
        try:
            upstream, mime = self.studio.stream(track_id, self.headers.get("Range"), quality)
        except NotConnected as error:
            self._json(401, {"error": str(error)})
            return
        except (ApiError, LibraryError) as error:
            self._json(404 if isinstance(error, LibraryError) else 502, {"error": str(error)})
            return
        with upstream:
            self.send_response(upstream.status_code)
            self.send_header("Content-Type", upstream.headers.get("Content-Type") or mime or "audio/mpeg")
            for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if upstream.headers.get(name):
                    self.send_header(name, upstream.headers[name])
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for chunk in upstream.iter_content(65536):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player skipped or stopped

    def _callback(self, query):
        error = (query.get("error_description") or query.get("error") or [""])[0]
        if not error:
            try:
                self.studio.finish_browser((query.get("code") or [""])[0],
                                           (query.get("state") or [""])[0])
            except ApiError as failure:
                error = str(failure)
        target = "/?connected=1" if not error else "/?auth_error=" + quote(error[:300])
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()


def classify_error(error):
    """How a failed action is reported to the page: (HTTP status, payload, crash details).

    Call it from inside the ``except`` block: an unexpected failure is logged with its
    traceback. Used for direct requests and for queued tasks alike, so a task fails with
    exactly the message and status the same request would have had.
    """
    if isinstance(error, ConflictError):
        return 409, {"error": str(error), "conflict": True}, ""
    if isinstance(error, NotConnected):
        return 401, {"error": str(error), "connected": False}, ""
    if isinstance(error, (BiographyRetryError, AlbumInformationRetryError)):
        return 429, {"error": str(error), "retry_after": error.retry_after}, ""
    if isinstance(error, (ApiError, LibraryError, ArtworkError, SourceError)):
        return 400, {"error": str(error)}, ""
    try:
        raise error  # so the log call below sees it as the exception being handled
    except Exception:
        logging.exception("Request failed")
    return 500, {"error": "Something went wrong in Tagcast. See Overview → Activity log."}, \
        "".join(traceback.format_exception(type(error), error, error.__traceback__))


def record_route(path, method, body, status, payload, ms, crash=""):
    """One Activity log entry per action (fork v35); see activity.py."""
    if method == "GET" and status in (200, 401):
        return  # reading pages and polls succeed constantly; only failures are worth a line
    if status == 200 and path in QUIET_ROUTES:
        return
    name = path[len("/api/"):]
    action = body.get("action") if isinstance(body, dict) and isinstance(body.get("action"), str) else ""
    title = name.replace("/", " › ") + (f" · {action}" if action else "")
    lines = []
    if body:
        lines.append("Sent: " + (activity.summarize(body) or "(nothing)"))
    if status == 200:
        if isinstance(payload, dict) and activity.summarize(payload):
            lines.append("Answer: " + activity.summarize(payload))
    else:
        lines.append(f"HTTP {status}: {payload.get('error', '') if isinstance(payload, dict) else payload}")
    if crash:
        lines.append(crash)
    level = "ok" if status == 200 else "warn" if status in (401, 429) else "error"
    if status != 200:
        title += f" failed: {payload.get('error', '') if isinstance(payload, dict) else ''}"
    activity.record(route_category(path), level, title, "\n".join(lines), source=name.split("/", 1)[0], ms=ms)


def record_task(task):
    """A finished queued task goes in the Activity log like the direct request it replaces,
    with how long it waited in the queue and how long it ran."""
    waited = (task["started"] or task["created"]) - task["created"]
    payload = task["result"] if task["state"] == "done" else task["payload"]
    record_route(task["path"], "POST", task.get("body"), task["status"] or 500, payload,
                 ((task["ended"] or time.time()) - (task["started"] or task["created"])) * 1000, task["crash"])
    if waited >= 5:
        activity.record(route_category(task["path"]), "info",
                        f"{task['label']}: waited {waited:.0f} s in the queue", source="queue")


# Writes to iBroadcast that run through the background queue (fork v36): one at a time, in
# order, so each one starts from the library the previous one left behind. Quick actions
# that don't write to iBroadcast, and everything that reads, never wait in it.
QUEUED_PATHS = {"/api/save", "/api/artwork", "/api/artwork/undo", "/api/trash", "/api/trash/albums",
                "/api/tags/save", "/api/favourite", "/api/album-rating", "/api/artist-rating",
                "/api/album-favourite", "/api/artist-favourite", "/api/playlist", "/api/playlist/artwork",
                "/api/lyrics/save"}

# Successful requests that happen on their own (lookups while browsing, queue sync) are not
# worth a line each; their failures still are.
QUIET_ROUTES = {"/api/queue-sync", "/api/galaxy/cards", "/api/galaxy/album-years", "/api/artist-biography",
                "/api/album-information", "/api/playlist-import/view", "/api/lyrics/review"}
_ROUTE_CATEGORIES = [
    (("/api/galaxy", "/api/bandcamp", "/api/soulseek", "/api/downloads", "/api/discography", "/api/wishlist",
      "/api/complete", "/api/charts", "/api/recommendations", "/api/releases", "/api/digest"), "downloads"),
    (("/api/save", "/api/artwork", "/api/tags", "/api/lyrics", "/api/tagclean", "/api/trash", "/api/album-rating",
      "/api/artist-rating", "/api/favourite", "/api/album-favourite", "/api/artist-favourite", "/api/playlist",
      "/api/metadata-export"), "metadata"),
    (("/api/cleanup", "/api/artist-cleanup", "/api/duplicate-cleanup"), "cleanup"),
    (("/api/artist-pictures",), "pictures"),
    (("/api/play", "/api/queue-tracks", "/api/queue-sync", "/api/stream"), "playback"),
    (("/api/config", "/api/settings", "/api/auth", "/api/updates"), "settings"),
    (("/api/library", "/api/albums", "/api/overview", "/api/status", "/api/browse", "/api/tracks"), "library"),
]


def route_category(path):
    for prefixes, cat in _ROUTE_CATEGORIES:
        if any(path == p or path.startswith(p + "/") or path.startswith(p + "-") or path.startswith(p + "?")
               for p in prefixes):
            return cat
    return "app"


def record_page_event(body):
    """A problem the page itself ran into (a script error, a failed request): POSTed by
    the browser so it lands in the same Activity log as everything else."""
    kind = str(body.get("kind") or "error")[:40]
    message = str(body.get("message") or "")[:500]
    detail = "\n".join(str(body.get(k))[:2000] for k in ("where", "stack") if body.get(k))
    activity.record("page", "warn" if kind == "request" else "error", f"{kind}: {message}", detail,
                    source=f"{str(body.get('screen'))[:30]} page" if body.get("screen") else "")
    return {"ok": True}


def _log_to_file(home):
    """Problems also go to <settings folder>/tagcast.log (kept small)."""
    try:
        home.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(home / "tagcast.log", encoding="utf-8",
                                                       maxBytes=1_000_000, backupCount=2)
    except OSError:
        return
    try:
        os.chmod(home / "tagcast.log", 0o600)  # it can contain titles from your library
    except OSError:
        pass
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.setLevel(logging.WARNING)  # INFO lines go to the Activity log only (fork v35)
    logging.getLogger().addHandler(handler)


class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second program listen on a port that is in use,
    # so a second Tagcast would start next to the first instead of noticing it.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        # The interface is numeric loopback only. HTTPServer's reverse DNS lookup
        # is unnecessary here and can stall startup on hosts with slow DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def server_close(self):
        handler = self.RequestHandlerClass
        studio = getattr(getattr(handler, "func", handler), "studio", None)
        if studio:
            studio.queue_sync.disconnect()
            studio.metadata_export.close()
        super().server_close()


def _already_running(port):
    """True when the program on this port is Tagcast (started earlier, e.g. by a double-click)."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as response:
            return "configured" in json.loads(response.read() or b"{}")
    except (OSError, ValueError):
        return False


def open_server(port):
    """Bind before loading settings; callers own this server's lifecycle."""
    handler = type("BoundHandler", (Handler,), {"studio": None, "port": port})
    server = Server(("127.0.0.1", port), partial(handler, directory=str(STATIC)))
    handler.port = server.server_port
    try:
        _log_to_file(Studio.default_home())
        handler.studio = Studio()
        handler.studio.tasks.on_finish = record_task  # queued saves land in the Activity log
    except Exception:
        server.server_close()
        raise
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tagcast", description="Tagcast for iBroadcast")
    parser.add_argument("--version", action="version", version=f"Tagcast {__version__}")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TAGCAST_PORT", 8912)))
    parser.add_argument("--open", action="store_true", help="open the browser after starting")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:  # the terminal keeps showing warnings only
        handler.setLevel(logging.WARNING)
    url = f"http://127.0.0.1:{args.port}"

    try:
        server = open_server(args.port)
    except OSError as error:
        if _already_running(args.port):
            print(f"Tagcast is already running: {url}", flush=True)
            if args.open:
                webbrowser.open(url)
            return 0
        print(f"Port {args.port} is in use by another program ({error.strerror}). "
              "Start Tagcast with --port 8913, for example.", flush=True)
        return 1

    with server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Tagcast: {url}", flush=True)
        print(f"Settings and sign-in tokens: {server.RequestHandlerClass.func.studio.home}", flush=True)
        print("Stop Tagcast with Ctrl+C, or by closing this window.", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
