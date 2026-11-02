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


class YouTubeLinkTests(unittest.TestCase):
    def test_a_youtube_playlist_is_one_album_in_order(self):
        studio = LocalStudio()
        flat = [{"id": "aaaaaaa1", "url": "aaaaaaa1", "title": "Jay - One (Official Video)", "channel": "Jay", "playlist_title": "Mix"},
                {"id": "aaaaaaa2", "url": "aaaaaaa2", "title": "Two", "channel": "Kay - Topic"}]
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=lambda url: flat)
        studio.moresources.ensure_worker = lambda: None
        out = studio.moresources.add_links("https://www.youtube.com/playlist?list=PL1")["results"]
        self.assertTrue(out[0]["choose"])
        self.assertEqual((out[0]["kind"], out[0]["count"], out[0]["has_song"]), ("playlist", 2, False))
        self.assertEqual(studio.download_manager.state["queue"], [])
        out = studio.moresources.add_links("https://www.youtube.com/playlist?list=PL1", "list")["results"]
        self.assertTrue(out[0]["ok"])
        q = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(p["album"], p["album_artist"], p["track_no"], p["url"]) for p in q],
                         [("Mix", "Various Artists", 1, "https://www.youtube.com/watch?v=aaaaaaa1"),
                          ("Mix", "Various Artists", 2, "https://www.youtube.com/watch?v=aaaaaaa2")])
        self.assertEqual([i["title"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"], ["One", "Two"])


class V63LinkTests(unittest.TestCase):
    def make(self, extract):
        studio = LocalStudio()
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=extract)
        studio.moresources.ensure_worker = lambda: None
        return studio

    def queued(self, studio):
        return [i for i in studio.download_manager.state["queue"] if i.get("source") == "more"]

    def test_song_with_a_mix_asks_then_this_song_takes_only_the_video(self):
        seen = []
        mix = [{"id": f"v{i}xxxxxx", "title": f"Song {i}", "channel": f"Artist {i}"} for i in range(5)]

        def extract(url):
            seen.append(url)
            return mix if "list=" in url else [{"id": "BXa5y6q8wHg", "title": "French 79 – Hometown", "channel": "French 79",
                                                "webpage_url": "https://www.youtube.com/watch?v=BXa5y6q8wHg"}]
        studio = self.make(extract)
        link = "https://www.youtube.com/watch?v=BXa5y6q8wHg&list=RDBXa5y6q8wHg&start_radio=1"
        ask = studio.moresources.add_links(link)["results"][0]
        self.assertEqual((ask["choose"], ask["kind"], ask["count"], ask["has_song"]), (True, "mix", 5, True))
        self.assertEqual(self.queued(studio), [])
        done = studio.moresources.add_links(link, "song")["results"][0]
        self.assertTrue(done["ok"])
        self.assertEqual(seen[-1], "https://www.youtube.com/watch?v=BXa5y6q8wHg")
        q = self.queued(studio)
        self.assertEqual([(i["artist"], i["title"], i["link"]) for i in q], [("French 79", "Hometown", link)])
        self.assertEqual(q[0]["payload"]["site"], "YouTube")

    def test_a_whole_mix_is_singles_and_an_olak_album_is_one_album(self):
        mix = [{"id": f"v{i}xxxxxx", "title": f"Song {i}", "channel": f"Artist {i}", "playlist_title": "Mix - X"} for i in range(3)]
        studio = self.make(lambda url: mix)
        studio.moresources.add_links("https://www.youtube.com/watch?v=abc&list=RDabc", "list")
        self.assertEqual({i["payload"]["album"] for i in self.queued(studio)}, {"Song 0", "Song 1", "Song 2"})
        album = [{"id": f"a{i}xxxxxx", "title": f"Track {i}", "playlist_title": "Album - Airport 2",
                  "playlist_uploader": "Moka Only - Topic"} for i in range(3)]
        studio = self.make(lambda url: album)
        link = "https://www.youtube.com/watch?v=OiogCwVUekQ&list=OLAK5uy_kgt9MyqHFUu4D2x_CN3ciVxc_fga47Vyo"
        ask = studio.moresources.add_links(link)["results"][0]
        self.assertEqual((ask["kind"], ask["title"]), ("album", "Airport 2"))
        studio.moresources.add_links(link, "list")
        q = [i["payload"] for i in self.queued(studio)]
        self.assertEqual([(p["album"], p["album_artist"], p["track_no"]) for p in q],
                         [("Airport 2", "Moka Only", 1), ("Airport 2", "Moka Only", 2), ("Airport 2", "Moka Only", 3)])

    def test_an_unread_redirect_is_reported_not_downloaded(self):
        studio = self.make(lambda url: [{"_type": "url", "url": "https://www.youtube.com/playlist?list=OLAK5uy_x", "title": "Airport 2"}])
        out = studio.moresources.add_links("https://www.youtube.com/watch?v=abc", "song")["results"][0]
        self.assertFalse(out["ok"])
        self.assertIn("Couldn't read", out["note"])
        self.assertEqual(self.queued(studio), [])

    def test_dashes_split(self):
        self.assertEqual(split_title("Metronomy – The Bay (Official Video)", "x"), ("Metronomy", "The Bay"))
        self.assertEqual(split_title("Poolside — Harvest Moon", "x"), ("Poolside", "Harvest Moon"))
