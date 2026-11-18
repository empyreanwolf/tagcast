"""Fork v96: SpotSaver (spotsaver.net) as a back-up when yt-dlp cannot get a song from YouTube.
Fork v105: also ``search()``, SpotSaver's own title/artist search, used when YouTube's own search
(YouTube Music, then YouTube) finds nothing - not only once a video is already known.

SpotSaver has no public API. Its web page makes two calls and this module repeats them:
``POST /api/get-id/`` (title, artist, duration -> a YouTube video id) and
``POST /api/download/`` (video id -> a short-lived MP3 link on its converter). Their server fetches the video from YouTube,
so it works when this PC's yt-dlp is stuck or refused. It can change or stop at any time: every failure here is an
ordinary error and the caller falls back to the other sources.
"""

import re
import time
from pathlib import Path

import requests

BASE = "https://spotsaver.net"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Origin": BASE, "Referer": BASE + "/", "Accept": "application/json, text/plain, */*"}
MIN_BYTES = 100 * 1024   # a real song; anything smaller is an error page
_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})")


class SpotSaverError(Exception):
    pass


def video_id(url):
    found = _ID.search(str(url or ""))
    return found.group(1) if found else ""


def search(title, artist="", duration=0, session=None):
    """fork v105: ask SpotSaver's own search for the YouTube video it would use for this title/artist
    (the half of its page this module never called before). Returns a video id, or raises
    SpotSaverError when SpotSaver has no match - an ordinary miss, same as any other source."""
    http = session or requests.Session()
    body = {"title": str(title or ""), "artist": str(artist or ""), "query": f"{artist} {title}".strip()}
    if duration:
        body["duration"] = int(duration)
    try:
        reply = http.post(f"{BASE}/api/get-id/", headers=HEADERS, timeout=(15, 30), json=body)
        data = reply.json() if reply.ok else {}
    except (requests.RequestException, ValueError) as error:
        raise SpotSaverError(f"SpotSaver search did not answer ({type(error).__name__})") from None
    candidates = data.get("candidates") or data.get("results") or []
    vid = data.get("videoId") or data.get("id") or next(
        (c.get("videoId") or c.get("id") for c in candidates if isinstance(c, dict) and (c.get("videoId") or c.get("id"))), None)
    if not vid:
        raise SpotSaverError(f"SpotSaver found no match (HTTP {reply.status_code})")
    return str(vid)


def fetch(url, folder, hook, title, artist="", session=None):
    """Download the song behind a YouTube link through SpotSaver into ``folder`` and return (path, {})."""
    vid = video_id(url)
    if not vid:
        raise SpotSaverError("not a YouTube link")
    http = session or requests.Session()
    label = f"{title} - {artist}".strip(" -") if artist else str(title or "")
    try:
        reply = http.post(f"{BASE}/api/download/", headers=HEADERS, timeout=(15, 60),
                          json={"videoId": vid, "candidateIds": [], "format": "mp3", "title": label, "licenseKey": None})
        data = reply.json() if reply.ok else {}
    except (requests.RequestException, ValueError) as error:
        raise SpotSaverError(f"SpotSaver did not answer ({type(error).__name__})") from None
    link = data.get("downloadUrl") or data.get("url") or ""
    if not link or not data.get("success"):
        raise SpotSaverError(f"SpotSaver gave no download link (HTTP {reply.status_code})")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "spotsaver.mp3"
    done, total = 0, 0
    try:
        with http.get(link, headers={**HEADERS, "Accept": "*/*"}, stream=True, timeout=(15, 60)) as stream:
            if not stream.ok:
                raise SpotSaverError(f"SpotSaver's converter answered HTTP {stream.status_code}")
            kind = str(stream.headers.get("Content-Type") or "").lower()
            if "text/html" in kind or "json" in kind:
                raise SpotSaverError("SpotSaver's converter did not send audio")
            total = int(stream.headers.get("Content-Length") or 0)
            with open(target, "wb") as out:
                for chunk in stream.iter_content(65536):
                    if not chunk:
                        continue
                    out.write(chunk)
                    done += len(chunk)
                    hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": total})
    except requests.RequestException as error:
        raise SpotSaverError(f"SpotSaver download broke off ({type(error).__name__})") from None
    if done < MIN_BYTES or (total and done < total):
        target.unlink(missing_ok=True)
        raise SpotSaverError("SpotSaver sent an incomplete file")
    return target, {}
