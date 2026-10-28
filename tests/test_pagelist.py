import unittest
from pathlib import Path
from unittest import mock

from tagcast import pagelist
from tagcast.pagelist import PageList, candidates_from_lines, dedupe, html_to_lines, ld_candidates, split_artist_album

BLOG = (Path(__file__).parent / "fixtures" / "pagelist_blog.html").read_text(encoding="utf-8")


class ReadingTests(unittest.TestCase):
    def test_line_shapes(self):
        self.assertEqual(split_artist_album("1. Kendrick Lamar – GNX (2024)"), ("Kendrick Lamar", "GNX", "2024"))
        self.assertEqual(split_artist_album("Row Your Boat by Griselda"), ("Griselda", "Row Your Boat", ""))
        self.assertEqual(split_artist_album('Billy Woods, "Golliwog"'), ("Billy Woods", "Golliwog", ""))
        self.assertEqual(split_artist_album("#3 MIKE - Showbiz!"), ("MIKE", "Showbiz!", ""))
        self.assertIsNone(split_artist_album("Here we go, it was a great year"))
        self.assertIsNone(split_artist_album("Artist - Album"))

    def test_blog_page(self):
        found = dedupe(ld_candidates(BLOG) + candidates_from_lines(html_to_lines(BLOG)))
        pairs = [(c["artist"], c["album"]) for c in found]
        self.assertEqual(pairs, [("Tyler, The Creator", "Chromakopia"), ("Kendrick Lamar", "GNX"), ("Griselda", "Row Your Boat"),
                                 ("Some Artist", "Album Two"), ("Billy Woods", "Golliwog"), ("Armand Hammer", "We Buy Diabetic Test Strips"),
                                 ("MIKE", "Showbiz!")])

    def test_prose_only_page_falls_back_to_plain_lines(self):
        lines = ["Intro text.", "Madvillain - Madvillainy", "Some other prose"]
        self.assertEqual([c["album"] for c in candidates_from_lines(lines)], ["Madvillainy"])

    def test_private_addresses_are_refused(self):
        with self.assertRaises(pagelist.PageError):
            pagelist.check_link("http://127.0.0.1:8913/")
        with self.assertRaises(pagelist.PageError):
            pagelist.check_link("ftp://example.com/x")


class FakeHttp:
    def json(self, url, params=None, **kw):
        q = ((params or {}).get("query") or (params or {}).get("q") or "").lower()
        if "musicbrainz" in url and "showbiz" in q:
            return {"release-groups": [{"score": 100, "title": "Showbiz!", "first-release-date": "2024-06-01",
                                        "artist-credit": [{"name": "MIKE"}]}]}
        if "api.deezer.com/search/album" in url and "showbiz" in q:
            return {"data": [{"title": "Showbiz!", "artist": {"name": "MIKE"}, "cover_medium": "c.jpg", "id": 5}]}
        return {"data": [], "release-groups": []}


class Studio:
    class discography:
        http = FakeHttp()
        jobs = {}
        got = []

        @classmethod
        def get(cls, name, albums):
            cls.got.append((name, albums))
            return {"id": "j1"}

    class download_manager:
        @staticmethod
        def check(rows):
            return {"results": [{"status": "have" if r["title"] == "Showbiz!" else "new", "you_have": []} for r in rows]}


class FlowTests(unittest.TestCase):
    def test_read_verify_and_get(self):
        pl = PageList(Studio)
        with mock.patch.object(pl, "collect", return_value=[
                {"artist": "mike", "album": "showbiz!", "year": "", "raw": "mike - showbiz!", "song": False},
                {"artist": "Nobody", "album": "Nothing", "year": "2024", "raw": "Nobody - Nothing (2024)", "song": False}]):
            first = pl.read("https://example.com/x")
        job = pl.jobs[first["id"]]
        for _ in range(100):
            if job["state"] == "done":
                break
            import time; time.sleep(0.05)
        items = pl.view(first["id"])["items"]
        self.assertEqual([(i["artist"], i["album"], i["year"], i["state"], i["status"]) for i in items],
                         [("MIKE", "Showbiz!", "2024", "ok", "have"), ("Nobody", "Nothing", "2024", "unsure", "new")])
        self.assertEqual(items[0]["cover"], "c.jpg")
        result = pl.get(first["id"], ["1"])
        self.assertEqual(result["queued"], 1)
        self.assertEqual(Studio.discography.got, [("Nobody", [{"title": "Nothing", "status": "missing", "deezer_id": None}])])
        with self.assertRaises(pagelist.PageError):
            pl.get(first["id"], [])

    def test_nothing_found_explains_paste_box(self):
        pl = PageList(Studio)
        with mock.patch.object(pl, "collect", return_value=[]):
            with self.assertRaises(pagelist.PageError) as cm:
                pl.read("https://example.com/x")
        self.assertIn("Paste the text instead", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
