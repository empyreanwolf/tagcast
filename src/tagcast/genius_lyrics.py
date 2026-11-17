"""Genius - a last-resort plain-lyrics source (fork v101).

LyricsJob tries this only after LRCLIB and GD Studio have both found nothing: a search on
Genius's own public search endpoint (the one genius.com's website itself calls, no key
needed), then the matching song's own page for the lyrics text. Genius gives plain, untimed
text only - it carries no line timing. A result is used only when the page's own title and
artist pass the same title/artist match GD Studio results already go through, so a
same-named cover or remix on Genius is not saved as if it were the song.
"""

import html as htmllib
import logging
import re

import requests

from .playback_lyrics import _gdstudio_match, lyrics_strip_artist, lyrics_title

log = logging.getLogger("tagcast")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tagcast-Lyrics/1.0"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
MAX_LINES = 2000
_SPLIT_ARTIST = re.compile(r"\s*[,;&/×·、，]\s*|\s+(?:and|x|feat\.?|ft\.?|featuring|with)\s+", re.I)
_DIV_TAG = re.compile(r"<div\b[^>]*>|</div>", re.I)


def query_title(track):
    return lyrics_title(lyrics_strip_artist(track["title"], track["artist"])) or track["title"]


def primary_artist(track):
    return _SPLIT_ARTIST.split(track["artist"], maxsplit=1)[0].strip()


def _html_to_text(html):
    """A lyrics container's inner HTML to plain lines."""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p>|</div>", "\n", html)
    html = re.sub(r"(?i)<[^>]+>", "", html)
    lines = [ln.strip() for ln in htmllib.unescape(html).splitlines()]
    out, blank = [], True
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _balanced_divs(html, marker):
    """Every top-level ``<div ...>...</div>`` whose opening tag contains ``marker``, with
    whatever is nested inside kept (Genius nests links and annotations inside the lyrics)."""
    out, pos = [], 0
    while True:
        start = html.find("<div", pos)
        if start == -1:
            return out
        end_open = html.find(">", start)
        if end_open == -1:
            return out
        if marker not in html[start:end_open + 1]:
            pos = end_open + 1
            continue
        depth, cursor = 1, end_open + 1
        for m in _DIV_TAG.finditer(html, cursor):
            depth += -1 if m.group().startswith("</") else 1
            if depth == 0:
                out.append(html[end_open + 1:m.start()])
                cursor = m.end()
                break
        else:
            cursor = len(html)
        pos = cursor


class Genius:
    """``genius.com``'s own public search, no API key - the same calls its website makes."""

    def __init__(self, get=None):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self.get = get or (lambda url, **kw: self._session.get(url, timeout=15, **kw))

    def find(self, track):
        """Plain lyric text for ``track``, or ``None``. A near-miss is never returned."""
        query = f"{primary_artist(track)} {query_title(track)}"
        try:
            r = self.get("https://genius.com/api/search/song", params={"q": query})
        except requests.RequestException as error:
            log.info("Genius search failed for %r: %s", query, error)
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        sections = (data.get("response") or {}).get("sections") or []
        hits = sections[0].get("hits") if sections else []
        for hit in (hits or [])[:5]:
            result = hit.get("result") or {}
            url = result.get("url") or ""
            name = result.get("title") or result.get("title_with_featured") or ""
            by = (result.get("primary_artist") or {}).get("name") or ""
            if url and _gdstudio_match({"name": name, "artist": by}, track):
                text = self._page_lyrics(url)
                if text:
                    return text
        return None

    def _page_lyrics(self, url):
        try:
            r = self.get(url)
        except requests.RequestException as error:
            log.info("Genius page failed for %s: %s", url, error)
            return None
        if r.status_code != 200:
            return None
        blocks = _balanced_divs(r.text, 'data-lyrics-container="true"')
        if not blocks:
            return None
        text = "\n".join(_html_to_text(block) for block in blocks).strip()
        return text if text and len(text.splitlines()) <= MAX_LINES else None
