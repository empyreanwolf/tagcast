"""Bandcamp (fork v13): page parsing, discography statuses, queueing and downloads."""

import html
import json
import tempfile
import time
import unittest
from pathlib import Path

from tagcast import bandcamp as bc
from tagcast.bandcamp import Bandcamp, BandcampError

from test_downloads import LocalStudio as DownloadsStudio
from test_galaxy import JPEG, MP3, FakeResponse, wait_idle


def album_page(title, artist, tracks, art_id=123, date="16 May 2014 00:00:00 GMT"):
    data = {"artist": artist, "art_id": art_id, "album_release_date": date, "item_type": "album",
            "current": {"title": title},
            "trackinfo": [{"track_num": n, "title": t, "duration": 40.0,
                           "file": {"mp3-128": f"https://t4.bcbits.com/stream/{n}?token=x"} if stream else None}
                          for n, (t, stream) in enumerate(tracks, 1)]}
    return f'<html><div id="x" data-tralbum="{html.escape(json.dumps(data))}"></div></html>'


PAGES = {
    "https://coldplay.bandcamp.com/music": '<a href="/album/ghost-stories">x</a><a href="/album/parachutes">y</a>'
                                           '<a href="/track/single?x=1">z</a>',
    "https://coldplay.bandcamp.com/album/ghost-stories": album_page("Ghost Stories", "Coldplay",
                                                                    [("Magic", True), ("Ink", True), ("Paid Only", False)]),
    "https://coldplay.bandcamp.com/album/parachutes": album_page("Parachutes", "Coldplay", [("Yellow", True)]),
    "https://bandcamp.com/search?q=Coldplay&item_type=b": '<li><div class="heading"> <a href="https://coldplay.bandcamp.com?from=sr">Coldplay</a></div>'
                                                         '<div class="heading"><a href="https://other.bandcamp.com">Coldplay Tribute</a></div></li>',
}


class FakeGet:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url in PAGES:
            return type("R", (), {"status_code": 200, "text": PAGES[url]})()
        return type("R", (), {"status_code": 404, "text": ""})()


def fetch(url, **kw):
    if "/stream/" in url:
        return FakeResponse(content=MP3, headers={"Content-Length": str(len(MP3))})
    if "bcbits.com/img" in url:
        return FakeResponse(content=JPEG)
    return FakeResponse(404)


class LocalStudio(DownloadsStudio):
    def __init__(self):
        super().__init__()
        self.folder = Path(tempfile.mkdtemp())
        self.download_manager.set_folder(str(self.folder))
        self.get = FakeGet()
        self.bandcamp = Bandcamp(self, get=self.get, fetch=fetch)
        self.bandcamp.track_delay = 0
        self.bandcamp.pace = 0


class UnreadablePageTests(unittest.TestCase):
    """fork v33: Bandcamp answering with something other than the album page."""

    def setUp(self):
        self.studio = LocalStudio()
        self.b = self.studio.bandcamp
        self.url = "https://coldplay.bandcamp.com/album/ghost-stories"
        self.real = PAGES[self.url]
        PAGES[self.url] = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
        self.addCleanup(PAGES.__setitem__, self.url, self.real)

    def test_a_check_page_is_explained_logged_and_not_cached(self):
        with self.assertLogs("tagcast.bandcamp", "WARNING") as logged:
            with self.assertRaises(bc.BandcampError) as caught:
                self.b.album(self.url)
        self.assertIn("check you're a person", str(caught.exception))
        self.assertIn("tagcast.log", str(caught.exception))
        self.assertIn("Just a moment", logged.output[0])
        PAGES[self.url] = self.real  # Bandcamp sends the real page on the next try
        self.assertEqual(self.b.album(self.url)["title"], "Ghost Stories")

    def test_a_page_without_album_names_its_title(self):
        PAGES[self.url] = "<html><head><title>Something else</title></head></html>"
        with self.assertLogs("tagcast.bandcamp", "WARNING"), self.assertRaises(bc.BandcampError) as caught:
            self.b.album(self.url)
        self.assertIn("without the album on it (“Something else”)", str(caught.exception))

    def test_browser_headers_are_sent(self):
        headers = Bandcamp(self.studio)._session.headers
        self.assertIn("text/html", headers["Accept"])
        self.assertIn("en", headers["Accept-Language"])


class ParseTests(unittest.TestCase):
    def test_album_page(self):
        r = bc.parse_release(PAGES["https://coldplay.bandcamp.com/album/ghost-stories"], "u")
        self.assertEqual((r["title"], r["artist"], r["year"]), ("Ghost Stories", "Coldplay", "2014"))
        self.assertEqual([t["title"] for t in r["tracks"]], ["Magic", "Ink", "Paid Only"])
        self.assertEqual(r["tracks"][2]["stream"], "")
        self.assertEqual(r["art"], "https://f4.bcbits.com/img/a0000000123_10.jpg")
        with self.assertRaises(BandcampError):
            bc.parse_release("<html></html>", "u")

    def test_music_page_and_links(self):
        self.assertEqual(bc.music_page_releases(PAGES["https://coldplay.bandcamp.com/music"]),
                         ["/album/ghost-stories", "/album/parachutes", "/track/single"])
        single = '<div id="discography"><a href="/album/only">a</a><a href="/album/only">b</a></div>'
        self.assertEqual(bc.music_page_releases(single), ["/album/only"])
        self.assertEqual(bc.base_url("coldplay.bandcamp.com/album/x"), ("https://coldplay.bandcamp.com", "/album/x"))
        with self.assertRaises(BandcampError):
            bc.base_url("not a link")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.b = self.studio.bandcamp

    def test_discography_and_statuses(self):
        d = self.b.discography("https://coldplay.bandcamp.com/album/ghost-stories")
        self.assertEqual(len(d["releases"]), 3)
        self.assertEqual(d["selected"], "https://coldplay.bandcamp.com/album/ghost-stories")
        ghost = self.b.album("https://coldplay.bandcamp.com/album/ghost-stories")
        self.assertEqual((ghost["status"], ghost["unavailable"]), ("new", 1))
        self.assertEqual(self.b.album("https://coldplay.bandcamp.com/album/parachutes")["status"], "have")
        # Pages are kept: no second request for the same page.
        before = len(self.studio.get.calls)
        self.b.album("https://coldplay.bandcamp.com/album/ghost-stories")
        self.assertEqual(len(self.studio.get.calls), before)

    def test_download_album_tags_and_holds_owned(self):
        self.assertEqual(self.b.add("https://coldplay.bandcamp.com/album/parachutes")["held"], 1)
        result = self.b.add("https://coldplay.bandcamp.com/album/ghost-stories")
        self.assertEqual(result["queued"], 2)
        wait_idle(self.studio)
        items = {i["title"]: i for i in self.studio.download_manager.state["queue"]}
        self.assertEqual(items["Ink"]["state"], "done", items["Ink"].get("note"))
        path = Path(items["Ink"]["path"])
        self.assertEqual(path.relative_to(self.studio.folder).as_posix(), "Coldplay/Ghost Stories/02 - Ink.mp3")
        from mutagen.id3 import ID3
        tags = ID3(str(path))
        self.assertEqual((str(tags["TRCK"]), str(tags["TDRC"]), tags["APIC:Cover"].mime), ("2/3", "2014", "image/jpeg"))
        self.assertEqual(self.b.album("https://coldplay.bandcamp.com/album/ghost-stories")["status"], "have")

    def test_only_paid_songs(self):
        PAGES["https://x.bandcamp.com/album/paid"] = album_page("Paid", "X", [("A", False)])
        with self.assertRaises(BandcampError):
            self.b.add("https://x.bandcamp.com/album/paid")

    def test_find_artist_album_and_track(self):
        base = self.b.find_artist("Coldplay", ["Ghost Stories"])
        self.assertEqual(base, "https://coldplay.bandcamp.com")
        release = self.b.find_album(base, "ghost stories")
        self.assertEqual(release["title"], "Ghost Stories")
        self.assertEqual(self.b.find_track(release, "magic (feat. Someone)")["num"], 1)
        self.assertIsNone(self.b.find_album(base, "Viva la Vida"))

    def test_shared_pause_and_cancel(self):
        self.studio.download_manager.control("pause")
        self.b.add("https://coldplay.bandcamp.com/album/ghost-stories")
        time.sleep(0.3)
        self.assertTrue(all(i["state"] == "queued" for i in self.studio.download_manager.state["queue"]))
        self.studio.download_manager.control("resume")
        wait_idle(self.studio)
        self.assertTrue(all(i["state"] == "done" for i in self.studio.download_manager.state["queue"]))


if __name__ == "__main__":
    unittest.main()


class AlbumSearchTests(unittest.TestCase):
    def test_album_search_results_are_read_with_their_artist(self):
        from tagcast.bandcamp import album_search_results
        page = ('<li class="searchresult data-search"><div class="result-info"><div class="itemtype">ALBUM</div>'
                '<div class="heading"><a href="https://pr.bandcamp.com/album/soul-survivor?from=search">Soul Survivor</a></div>'
                '<div class="subhead">by Pete Rock</div></div></li>'
                '<li class="searchresult data-search"><div class="heading"><a href="https://x.bandcamp.com/album/y">Y &amp; Z</a></div>'
                '<div class="subhead">from Some Label\n by Other</div></li>')
        self.assertEqual(album_search_results(page), [("https://pr.bandcamp.com/album/soul-survivor", "Soul Survivor", "Pete Rock"),
                                                      ("https://x.bandcamp.com/album/y", "Y & Z", "Other")])
