"""Fork v88: English translation of Chinese (and other CJK) lyrics while a song plays.

Uses Google Translate's free web endpoint (no key). One request per song, remembered on this PC in
~/.tagcast/lyrics_translations.json. It is an unofficial endpoint: when it refuses (too many requests) or
changes, the lyrics simply stay untranslated for a while; nothing else is affected."""
import bisect
import hashlib
import re
import threading
import time

import requests

from .library import LibraryError

CACHE_FILE = "lyrics_translations.json"
CACHE_MAX = 300           # songs
MAX_LINES = 300
MAX_LINE = 300
MAX_TOTAL = 24000
ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MIN_GAP = 2.0             # seconds between web requests
COOLDOWN = 600.0          # after the service said "too many requests"
LINE_FALLBACK_MAX = 100
CJK = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")


def has_cjk(lines):
    """True when at least a third of the lines with text contain Chinese, Japanese or Korean."""
    texts = [t for t in lines if t.strip()]
    return bool(texts) and sum(1 for t in texts if CJK.search(t)) * 3 >= len(texts)


def assign_segments(segments, lines):
    """Maps Google's [translated, original, ...] segments back onto the input lines by the character
    offset of each original segment. Returns one string per line, or None when the offsets do not add up."""
    query = "\n".join(lines)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    out = [[] for _ in lines]
    pos = 0
    for seg in segments:
        if not isinstance(seg, list) or len(seg) < 2 or not isinstance(seg[0], str) or not isinstance(seg[1], str):
            continue
        first = bisect.bisect_right(starts, pos) - 1
        last = bisect.bisect_right(starts, max(pos, pos + len(seg[1].rstrip("\n")) - 1)) - 1
        pieces = seg[0].rstrip("\n").split("\n")
        if last > first and len(pieces) == last - first + 1:
            for i, piece in enumerate(pieces):  # one segment that covers several lines, kept apart by Google
                out[first + i].append(piece.strip())
        elif 0 <= first < len(out):
            out[first].append(seg[0].strip())
        pos += len(seg[1])
    if pos != len(query) and pos != len(query) + 1:
        return None
    return [" ".join(p for p in parts if p) for parts in out]


class LyricsTranslator:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.blocked_until = 0.0
        self.cache = None

    # -- cache -------------------------------------------------------------------------------------------------
    def _cache(self):
        if self.cache is None:
            data = self.studio._read(CACHE_FILE)
            self.cache = data if isinstance(data, dict) else {}
        return self.cache

    @staticmethod
    def _key(lines):
        return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()

    # -- web ---------------------------------------------------------------------------------------------------
    def _ask(self, text):
        wait = self.last_request + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        try:
            response = requests.get(ENDPOINT, params={"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text},
                                    headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        except requests.RequestException:
            raise LibraryError("The translator could not be reached.")
        if response.status_code == 429 or "text/html" in response.headers.get("Content-Type", ""):
            self.blocked_until = time.monotonic() + COOLDOWN
            raise LibraryError("The translator says too many requests. Trying again in a few minutes.")
        if response.status_code != 200:
            raise LibraryError(f"The translator answered {response.status_code}.")
        try:
            data = response.json()
            segments = data[0] or []
        except (ValueError, TypeError, IndexError):
            raise LibraryError("The translator gave an answer Tagcast cannot read.")
        return segments

    def _translate(self, lines):
        segments = self._ask("\n".join(lines))
        out = assign_segments(segments, lines)
        if out is not None:
            return out
        # The offsets did not match: translate line by line (slower, rarely needed).
        out = []
        for line in lines[:LINE_FALLBACK_MAX]:
            if not line.strip():
                out.append("")
                continue
            parts = self._ask(line)
            out.append(" ".join(seg[0].strip() for seg in parts if isinstance(seg, list) and seg and isinstance(seg[0], str)))
        return out + [""] * (len(lines) - len(out))

    # -- route -------------------------------------------------------------------------------------------------
    def view(self, body):
        lines = body.get("lines")
        if (not isinstance(lines, list) or not 0 < len(lines) <= MAX_LINES
                or any(not isinstance(t, str) or len(t) > MAX_LINE or "\x00" in t for t in lines)
                or sum(len(t) for t in lines) > MAX_TOTAL):
            raise LibraryError("These lyrics cannot be translated.")
        lines = [" ".join(t.split()) for t in lines]
        if not has_cjk(lines):
            return {"status": "skipped", "tr": []}
        key = self._key(lines)
        with self.lock:
            cached = self._cache().get(key)
            if isinstance(cached, list) and len(cached) == len(lines):
                return {"status": "ready", "tr": cached}
            if time.monotonic() < self.blocked_until:
                raise LibraryError("The translator says too many requests. Trying again in a few minutes.")
            result = self._translate(lines)
            # Lines that need no translation (English words, music notes) stay empty.
            result = ["" if not tr or tr.strip().lower() == src.strip().lower() else tr[:MAX_LINE * 2]
                      for src, tr in zip(lines, result)]
            cache = self._cache()
            cache.pop(key, None)
            cache[key] = result
            while len(cache) > CACHE_MAX:
                cache.pop(next(iter(cache)))
            self.studio._write(CACHE_FILE, cache)
        return {"status": "ready", "tr": result}
