import unittest

from test_discography import LocalStudio
from tagcast.moresources import MoreError, MoreSources, site_of, split_title


def entry(title, uploader="Someone", url=None):
    return {"title": title, "uploader": uploader, "webpage_url": url or f"https://soundcloud.com/x/{title.replace(' ', '-')}", "duration": 180}


class HelperTests(unittest.TestCase):
    def test_sites_and_title_splitting(self):
        self.assertEqual(site_of("https://m.soundcloud.com/a/b"), "SoundCloud")
        self.assertEqual(site_of("https://audiomack.com/a/song/b"), "Audiomack")
        self.assertEqual(site_of("https://evilsoundcloud.com/a"), "")
        self.assertEqual(split_title("Jay - Song", "Up"), ("Jay", "Song"))
        self.assertEqual(split_title("Song", "Uploader"), ("Uploader", "Song"))


class MoreTests(unittest.TestCase):
    def make(self, **kw):
        studio = LocalStudio()
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, **kw)
        studio.moresources.ensure_worker = lambda: None
        return studio

    def test_search_lists_rows_with_library_status(self):
        studio = self.make(search=lambda q: [entry("Jay - Brand New Track"), {"title": "", "webpage_url": ""}])
        out = studio.moresources.search("jay")["results"]
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["artist"], out[0]["title"], out[0]["status"]), ("Jay", "Brand New Track", "new"))
        with self.assertRaises(MoreError):
            studio.moresources.search("  ")

    def test_add_queues_under_the_more_source_and_rejects_other_sites(self):
        studio = self.make()
        studio.moresources.add([{"url": "https://soundcloud.com/a/b", "title": "Brand New Track", "artist": "Jay"}])
        queued = [i for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["payload"]["url"], "https://soundcloud.com/a/b")
        with self.assertRaises(MoreError):
            studio.moresources.add([{"url": "https://example.com/a", "title": "X", "artist": "Y"}])

    def test_links_report_each_line_and_number_album_tracks(self):
        album = [entry("One", url="https://audiomack.com/a/song/one"), entry("Two", url="https://audiomack.com/a/song/two")]
        studio = self.make(extract=lambda url: album if "album" in url else [])
        out = studio.moresources.add_links("https://audiomack.com/a/album/x\nhttps://example.com/no\nhttps://soundcloud.com/a/empty")["results"]
        self.assertEqual([r["ok"] for r in out], [True, False, False])
        queued = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(p["track_no"], p["track_total"]) for p in queued], [(1, 2), (2, 2)])

    def test_chart_rows_and_getting_them(self):
        collection = [{"track": {"title": "Jay - Hot One", "permalink_url": "https://soundcloud.com/j/hot", "user": {"username": "jay"}}},
                      {"track": {"title": "", "permalink_url": ""}}]
        studio = self.make(chart_fetch=lambda: collection)
        rows = studio.moresources.soundcloud_rows()
        self.assertEqual([(r["rank"], r["artist"], r["title"], r["source"]) for r in rows], [(1, "Jay", "Hot One", "soundcloud")])
        from tagcast.charts import CHARTS, Charts
        self.assertIn("soundcloud_hiphop", CHARTS)
        charts = Charts(studio)
        charts.cache["soundcloud_hiphop"] = {"rows": rows, "when": 9e12, "error": ""}
        self.assertEqual(charts.get("soundcloud_hiphop", [1])["queued"], 1)
        self.assertEqual(len([i for i in studio.download_manager.state["queue"] if i.get("source") == "more"]), 1)
