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

    def test_heading_then_artist_line(self):
        # Fork v95: realjapanesehiphop.com style: number, date, album heading, artist line, review, song picks.
        review = "<p>" + "A long review paragraph about this record and why it matters this year. " * 3 + "</p>"
        entry = lambda n, date, album, artist: (f"<div>0{n}</div><a>Stream</a><p>{date}</p><h3>{album}</h3><p>{artist}</p>{review}"
                                                f"<h4>🎧 Essential Listens</h4><ul><li>“Some Song” feat. LEX</li></ul>")
        html = ("<h1>Best Japanese Hip Hop Albums of 2026 – Vol. 1</h1><p>By Team RJHH</p>"
                + entry(1, "January 1, 2026", "The One", "DJ CHARI &amp; DJ TATSUKI") + entry(2, "January 10, 2026", "Genius", "Kaneee")
                + entry(3, "March 6, 2026 — Deluxe Edition", "Vertex 2", "Gottz &amp; MUD")
                + "<ul><li>Listening picks coming soon</li></ul><p>Keep Exploring</p><h3>RJHH MIX — Japanese Rap Hits</h3>")
        found = candidates_from_lines(html_to_lines(html))
        self.assertEqual([(c["artist"], c["album"], c["year"]) for c in found],
                         [("DJ CHARI & DJ TATSUKI", "The One", "2026"), ("Kaneee", "Genius", "2026"), ("Gottz & MUD", "Vertex 2", "2026")])
        # Two such pairs are not a pattern yet.
        self.assertEqual(pagelist.heading_pairs(["• A", "B", "• C", "D"]), [])

    def test_japanese_brackets(self):
        self.assertEqual(split_artist_album("ISSUGI『GEMZ』")[:2], ("ISSUGI", "GEMZ"))
        self.assertEqual(split_artist_album("KOHH 「DIRT」")[:2], ("KOHH", "DIRT"))

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
        def get(cls, name, albums, songs=None):
            cls.got.append((name, albums) if songs is None else (name, albums, songs))
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
        self.assertEqual(Studio.discography.got, [("Nobody", [{"title": "Nothing", "status": "missing", "deezer_id": None}], [])])
        with self.assertRaises(pagelist.PageError):
            pl.get(first["id"], [])

    def test_a_song_row_stays_one_song_and_is_not_swapped_for_its_album(self):
        # fork v116: "Bills Paid" by DJ Khaled on a game-soundtrack page was turned into the whole GOD DID album
        class SongHttp(FakeHttp):
            def json(self, url, params=None, **kw):
                if "api.deezer.com/search" in url and "album" not in url.rsplit("/", 1)[-1] and "bills paid" in str(params).lower():
                    return {"data": [{"artist": {"name": "DJ Khaled"}, "album": {"title": "GOD DID"}}]}
                return super().json(url, params, **kw)

        class SongStudio(Studio):
            class discography(Studio.discography):
                http = SongHttp()
                got = []

            class download_manager:
                rows = []

                @classmethod
                def check(cls, rows):
                    cls.rows = rows
                    return {"results": [{"status": "new", "you_have": []} for _ in rows]}

        pl = PageList(SongStudio)
        with mock.patch.object(pl, "collect", return_value=[
                {"artist": "DJ Khaled", "album": "Bills Paid", "year": "", "raw": "Bills Paid - DJ Khaled", "song": True}]):
            first = pl.read("https://example.com/gta")
        job = pl.jobs[first["id"]]
        for _ in range(100):
            if job["state"] == "done":
                break
            import time; time.sleep(0.05)
        item = pl.view(first["id"])["items"][0]
        self.assertEqual((item["artist"], item["album"], item["song"], item["state"]), ("DJ Khaled", "Bills Paid", True, "ok"))
        self.assertEqual(item["song_album"], "GOD DID")
        self.assertEqual(SongStudio.download_manager.rows[0]["kind"], "song")
        pl.get(first["id"], ["0"])
        name, albums, songs = SongStudio.discography.got[0]
        self.assertEqual((name, albums), ("DJ Khaled", []))
        self.assertEqual([(s["title"], s["album"]) for s in songs], [("Bills Paid", "GOD DID")])

    def test_nothing_found_explains_paste_box(self):
        pl = PageList(Studio)
        with mock.patch.object(pl, "collect", return_value=[]):
            with self.assertRaises(pagelist.PageError) as cm:
                pl.read("https://example.com/x")
        self.assertIn("Paste the text instead", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
