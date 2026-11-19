"""Fork v111: flip title and artist on "From a page"; table headers decide the column order."""

import time
import unittest
from unittest import mock

from test_pagelist import Studio

from tagcast.pagelist import PageList, candidates_from_lines, dedupe, html_to_lines

TABLE = """<table><tr><th>Artist</th><th>Song</th><th>Radio station / source</th></tr>
<tr><td>!!!</td><td>All U Writers</td><td>Stockyard FM</td></tr>
<tr><td>112</td><td>Dance With Me</td><td>Honey FM</td></tr>
<tr><td>2Pac feat. Trick Daddy</td><td>Still Ballin' (Nitty Remix)</td><td>Dirty South Classics</td></tr>
<tr><td>ABC</td><td>Vanity Kills</td><td>Vice City FM</td></tr></table>"""


def pairs(html):
    return [(c["artist"], c["album"]) for c in dedupe(candidates_from_lines(html_to_lines(html)))]


class TableHeaderTests(unittest.TestCase):
    def test_a_header_with_artist_then_song_keeps_a_number_as_the_artist(self):
        self.assertEqual(pairs(TABLE), [("!!!", "All U Writers"), ("112", "Dance With Me"),
                                        ("2Pac feat. Trick Daddy", "Still Ballin' (Nitty Remix)"), ("ABC", "Vanity Kills")])

    def test_a_header_with_song_before_artist_is_read_that_way(self):
        html = TABLE.replace("<th>Artist</th><th>Song</th>", "<th>Song title</th><th>Artist</th>")
        html = html.replace("<td>112</td><td>Dance With Me</td>", "<td>Dance With Me</td><td>112</td>")
        self.assertIn(("112", "Dance With Me"), pairs(html))

    def test_a_table_without_a_header_is_read_as_before(self):
        html = "<table><tr><td>1</td><td>MIKE</td><td>Showbiz!</td></tr><tr><td>2</td><td>Armand Hammer</td><td>Haram</td></tr></table>"
        self.assertEqual(pairs(html), [("MIKE", "Showbiz!"), ("Armand Hammer", "Haram")])


class FlipTests(unittest.TestCase):
    def _job(self):
        pl = PageList(Studio)
        with mock.patch.object(pl, "collect", return_value=[
                {"artist": "Showbiz!", "album": "mike", "year": "", "raw": "x", "song": False},
                {"artist": "Dance With Me", "album": "Honey FM", "year": "", "raw": "y", "song": False},
                {"artist": "Nobody", "album": "Nothing", "year": "", "raw": "z", "song": False}]):
            first = pl.read("https://example.com/x")
        self._wait(pl, first["id"])
        return pl, first["id"]

    @staticmethod
    def _wait(pl, job_id):
        for _ in range(200):
            if pl.jobs[job_id]["state"] == "done":
                return
            time.sleep(0.02)
        raise AssertionError("page list did not finish")

    def test_flip_swaps_the_ticked_rows_only_and_checks_them_again(self):
        pl, job_id = self._job()
        view = pl.flip(job_id, ["0"])
        self.assertEqual(view["state"], "running")
        self._wait(pl, job_id)
        items = {i["id"]: i for i in pl.view(job_id)["items"]}
        self.assertEqual((items["0"]["artist"], items["0"]["album"], items["0"]["state"]), ("MIKE", "Showbiz!", "ok"))  # spelling fixed online
        self.assertEqual((items["1"]["artist"], items["1"]["album"]), ("Dance With Me", "Honey FM"))  # not ticked: left alone

    def test_flip_with_no_ids_flips_every_row(self):
        pl, job_id = self._job()
        pl.flip(job_id, [])
        self._wait(pl, job_id)
        items = {i["id"]: i for i in pl.view(job_id)["items"]}
        self.assertEqual((items["1"]["artist"], items["1"]["album"]), ("Honey FM", "Dance With Me"))
        self.assertEqual((items["2"]["artist"], items["2"]["album"]), ("Nothing", "Nobody"))

    def test_flip_twice_returns_to_the_start(self):
        pl, job_id = self._job()
        for _ in range(2):
            pl.flip(job_id, ["1"])
            self._wait(pl, job_id)
        item = pl.view(job_id)["items"][1]
        self.assertEqual((item["artist"], item["album"]), ("Dance With Me", "Honey FM"))


if __name__ == "__main__":
    unittest.main()
