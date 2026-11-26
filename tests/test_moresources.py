import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_discography import LocalStudio
from tagcast.moresources import MoreError, MoreSources, chapters_from_description, site_of, split_title

FIXTURE_MP3 = Path(__file__).parent / "fixtures" / "galaxy-silence.mp3"


def wait_links(job, timeout=2.0):
    """fork v107: add_links() returns a background job right away - wait for it to finish
    reading before a test looks at job["results"]. The tests' mocked extract/search/info callables
    do no real I/O, so this always resolves almost immediately."""
    deadline = time.monotonic() + timeout
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert job["state"] != "running", "add_links job did not finish in time"
    return job


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
        kw.setdefault("info", lambda url: {})  # fork v108: never a real yt-dlp call from a test
        kw.setdefault("lookup", lambda *a: None)
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
        out = wait_links(studio.moresources.add_links("https://audiomack.com/a/album/x\nhttps://example.com/no\nhttps://soundcloud.com/a/empty"))["results"]
        self.assertEqual([r["ok"] for r in out], [True, False, False])
        queued = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(p["track_no"], p["track_total"]) for p in queued], [(1, 2), (2, 2)])

    def test_links_run_in_the_background_and_each_error_names_its_own_line(self):
        gate = threading.Event()  # fork v107: prove the call returns before the line is actually read

        def extract(url):
            gate.wait(2)
            return [entry("One")] if "audiomack" in url else []

        studio = self.make(extract=extract)
        job = studio.moresources.add_links("https://audiomack.com/a/song/one\nhttps://example.com/not-a-site")
        self.assertEqual(job["state"], "running")  # add_links returned before either line was read
        self.assertEqual(studio.moresources.active_links()["jobs"][0]["id"], job["id"])
        gate.set()
        done = wait_links(job)
        self.assertEqual(done["state"], "done")
        self.assertEqual([(r["line"], r["ok"]) for r in done["results"]],
                         [("https://audiomack.com/a/song/one", True), ("https://example.com/not-a-site", False)])
        self.assertIn("Only youtube.com", done["results"][1]["note"])

    def test_v108_a_finished_job_survives_the_next_one_starting(self):
        studio = self.make(extract=lambda url: [entry("One")])
        first = wait_links(studio.moresources.add_links("https://audiomack.com/a/song/one"))
        second = wait_links(studio.moresources.add_links("https://audiomack.com/a/song/two"))
        ids = {j["id"] for j in studio.moresources.active_links()["jobs"]}
        self.assertEqual(ids, {first["id"], second["id"]})

    def test_an_unexpected_error_on_one_line_still_names_that_line_and_does_not_crash_the_job(self):
        def extract(url):
            if "boom" in url:
                raise RuntimeError("kaboom")
            return [entry("One")]

        studio = self.make(extract=extract)
        done = wait_links(studio.moresources.add_links("https://soundcloud.com/a/boom\nhttps://soundcloud.com/a/fine"))
        self.assertEqual(done["results"][0]["ok"], False)
        self.assertEqual(done["results"][0]["line"], "https://soundcloud.com/a/boom")
        self.assertEqual(done["results"][1]["ok"], True)

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
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=lambda url: flat, info=lambda url: {})
        studio.moresources.ensure_worker = lambda: None
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/playlist?list=PL1"))["results"]
        self.assertTrue(out[0]["choose"])
        self.assertEqual((out[0]["kind"], out[0]["count"], out[0]["has_song"]), ("playlist", 2, False))
        self.assertEqual(studio.download_manager.state["queue"], [])
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/playlist?list=PL1", "list"))["results"]
        self.assertTrue(out[0]["ok"])
        q = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(p["album"], p["album_artist"], p["track_no"], p["url"]) for p in q],
                         [("Mix", "Various Artists", 1, "https://www.youtube.com/watch?v=aaaaaaa1"),
                          ("Mix", "Various Artists", 2, "https://www.youtube.com/watch?v=aaaaaaa2")])
        self.assertEqual([i["title"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"], ["One", "Two"])


class V63LinkTests(unittest.TestCase):
    def make(self, extract):
        studio = LocalStudio()
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=extract, info=lambda url: {})
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
        ask = wait_links(studio.moresources.add_links(link))["results"][0]
        self.assertEqual((ask["choose"], ask["kind"], ask["count"], ask["has_song"]), (True, "mix", 5, True))
        self.assertEqual(self.queued(studio), [])
        done = wait_links(studio.moresources.add_links(link, "song"))["results"][0]
        self.assertTrue(done["ok"])
        self.assertEqual(seen[-1], "https://www.youtube.com/watch?v=BXa5y6q8wHg")
        q = self.queued(studio)
        self.assertEqual([(i["artist"], i["title"], i["link"]) for i in q], [("French 79", "Hometown", link)])
        self.assertEqual(q[0]["payload"]["site"], "YouTube")

    def test_a_whole_mix_is_singles_and_an_olak_album_is_one_album(self):
        mix = [{"id": f"v{i}xxxxxx", "title": f"Song {i}", "channel": f"Artist {i}", "playlist_title": "Mix - X"} for i in range(3)]
        studio = self.make(lambda url: mix)
        wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=abc&list=RDabc", "list"))
        self.assertEqual({i["payload"]["album"] for i in self.queued(studio)}, {"Song 0", "Song 1", "Song 2"})
        album = [{"id": f"a{i}xxxxxx", "title": f"Track {i}", "playlist_title": "Album - Airport 2",
                  "playlist_uploader": "Moka Only - Topic"} for i in range(3)]
        studio = self.make(lambda url: album)
        link = "https://www.youtube.com/watch?v=OiogCwVUekQ&list=OLAK5uy_kgt9MyqHFUu4D2x_CN3ciVxc_fga47Vyo"
        ask = wait_links(studio.moresources.add_links(link))["results"][0]
        self.assertEqual((ask["kind"], ask["title"]), ("album", "Airport 2"))
        wait_links(studio.moresources.add_links(link, "list"))
        q = [i["payload"] for i in self.queued(studio)]
        self.assertEqual([(p["album"], p["album_artist"], p["track_no"]) for p in q],
                         [("Airport 2", "Moka Only", 1), ("Airport 2", "Moka Only", 2), ("Airport 2", "Moka Only", 3)])

    def test_an_unread_redirect_is_reported_not_downloaded(self):
        studio = self.make(lambda url: [{"_type": "url", "url": "https://www.youtube.com/playlist?list=OLAK5uy_x", "title": "Airport 2"}])
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=abc", "song"))["results"][0]
        self.assertFalse(out["ok"])
        self.assertIn("Couldn't read", out["note"])
        self.assertEqual(self.queued(studio), [])

    def test_dashes_split(self):
        self.assertEqual(split_title("Metronomy – The Bay (Official Video)", "x"), ("Metronomy", "The Bay"))
        self.assertEqual(split_title("Poolside — Harvest Moon", "x"), ("Poolside", "Harvest Moon"))


class V105SplitDescriptionTests(unittest.TestCase):
    def test_timestamp_first_lines_are_read_in_order(self):
        rows = chapters_from_description("00:00 - Intro\n01:30 - Second Song\n1:03:00 - Finale")
        self.assertEqual([(r["title"], r["start_time"], r["end_time"]) for r in rows],
                         [("Intro", 0.0, 90.0), ("Second Song", 90.0, 3780.0), ("Finale", 3780.0, None)])

    def test_title_first_and_trailing_timestamp_formats_are_read(self):
        rows = chapters_from_description("Opening Track - 00:00\nClosing Track 02:15")
        self.assertEqual([(r["title"], r["start_time"]) for r in rows], [("Opening Track", 0.0), ("Closing Track", 135.0)])

    def test_a_line_with_no_timestamp_or_a_duplicate_time_is_skipped(self):
        rows = chapters_from_description("Tracklist:\n00:00 - One\n00:00 - Also One (dup)\nJust some text")
        self.assertEqual([r["title"] for r in rows], ["One"])

    def test_v108_track_lengths_are_added_up_into_start_times(self):
        rows = chapters_from_description("1. One - 3:45\n2. Two - 4:10\n3. Three - 2:30")
        self.assertEqual([r["start_time"] for r in rows], [0.0, 225.0, 475.0])
        rows = chapters_from_description("One 3:00\nTwo 3:30\nThree 4:00")  # counts up, but can't be start times
        self.assertEqual([r["start_time"] for r in rows], [0.0, 180.0, 390.0])

    def test_v108_bracketed_timestamps_are_read(self):
        rows = chapters_from_description("[00:00] One\n(03:45) Two")
        self.assertEqual([(r["title"], r["start_time"]) for r in rows], [("One", 0.0), ("Two", 225.0)])

    def test_v108_album_and_track_names_lose_the_full_album_and_numbering_decoration(self):
        from tagcast.moresources import split_album_name, split_track_title
        self.assertEqual(split_album_name("Shakespeare Science (Full Album Stream)"), "Shakespeare Science")
        self.assertEqual(split_album_name("Greatest Hits | FULL ALBUM"), "Greatest Hits")
        self.assertEqual(split_album_name("Full Album"), "Full Album")
        self.assertEqual([split_track_title(t, "Jay") for t in ("01. Intro", "03 - Song", "Jay - Song", "Other - Song")],
                         ["Intro", "Song", "Song", "Other - Song"])
        self.assertEqual([split_track_title(t) for t in ("7 Rings", "1.5 Degrees", "24-7", "10:00 PM")],
                         ["7 Rings", "1.5 Degrees", "24-7", "10:00 PM"])

    def test_no_description_is_no_chapters(self):
        self.assertEqual(chapters_from_description(None), [])
        self.assertEqual(chapters_from_description("just a normal description, no tracklist here"), [])


class V105SplitAlbumVideoTests(unittest.TestCase):
    def make(self, info=None, **kw):
        studio = LocalStudio()
        kw.setdefault("lookup", lambda *a: None)
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, info=info, **kw)
        studio.moresources.ensure_worker = lambda: None
        return studio

    def queued(self, studio):
        return [i for i in studio.download_manager.state["queue"] if i.get("source") == "more"]

    def test_youtube_chapters_are_preferred_over_the_description(self):
        studio = self.make(info=lambda url: {"title": "Jay - Greatest Hits", "uploader": "Jay",
                                             "chapters": [{"title": "One", "start_time": 0, "end_time": 120},
                                                         {"title": "Two", "start_time": 120, "end_time": 240}],
                                             "description": "00:00 - Different One\n02:00 - Different Two"})
        chapters, artist, album = studio.moresources._chapters("https://www.youtube.com/watch?v=abc")
        self.assertEqual([c["title"] for c in chapters], ["One", "Two"])
        self.assertEqual((artist, album), ("Jay", "Greatest Hits"))

    def test_a_single_chapter_falls_back_to_the_description(self):
        studio = self.make(info=lambda url: {"title": "Full Album", "uploader": "Jay", "chapters": [{"title": "Whole thing"}],
                                             "description": "00:00 - One\n02:00 - Two\n04:00 - Three"})
        chapters, artist, album = studio.moresources._chapters("https://www.youtube.com/watch?v=abc")
        self.assertEqual([c["title"] for c in chapters], ["One", "Two", "Three"])

    def test_pasting_a_full_album_video_offers_the_split_choice(self):
        studio = self.make(info=lambda url: {"title": "Jay - Greatest Hits", "uploader": "Jay",
                                             "chapters": [{"title": "One", "start_time": 0, "end_time": 90},
                                                         {"title": "Two", "start_time": 90}]})
        ask = wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=aaaaaaaaaaa"))["results"][0]
        self.assertEqual((ask["choose"], ask["kind"], ask["count"], ask["has_song"]), (True, "split", 2, True))
        self.assertEqual(self.queued(studio), [])

    def test_a_video_with_no_tracklist_is_not_offered_a_split(self):
        # fork v108: extract is mocked too - this used to fall through to a real yt-dlp call (flaky, slow)
        studio = self.make(info=lambda url: {"title": "Jay - One Song", "uploader": "Jay", "chapters": [], "description": "no timestamps here"},
                           extract=lambda url: [{"title": "Jay - One Song", "uploader": "Jay", "url": url, "duration": 200}])
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=bbbbbbbbbbb"))["results"][0]
        self.assertFalse(out.get("choose"))
        self.assertTrue(out["ok"])

    def test_clicking_split_queues_one_album_job_with_the_chapters(self):
        studio = self.make(info=lambda url: {"title": "Jay - Greatest Hits", "uploader": "Jay",
                                             "chapters": [{"title": "One", "start_time": 0, "end_time": 90},
                                                         {"title": "Two", "start_time": 90, "end_time": None}]})
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=aaaaaaaaaaa", "split"))["results"][0]
        self.assertTrue(out["ok"])
        self.assertIn("Split into 2 tracks", out["note"])
        q = self.queued(studio)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["kind"], "album")
        self.assertEqual(q[0]["tracks"], 2)
        self.assertEqual([c["title"] for c in q[0]["payload"]["chapters"]], ["One", "Two"])
        self.assertEqual((q[0]["payload"]["album_artist"], q[0]["payload"]["album"]), ("Jay", "Greatest Hits"))

    def test_v108_a_full_album_video_opened_from_a_mix_is_offered_a_split_too(self):
        two = {"title": "Jay - Greatest Hits", "uploader": "Jay",
               "chapters": [{"title": "One", "start_time": 0, "end_time": 90}, {"title": "Two", "start_time": 90}]}
        studio = self.make(info=lambda url: two, extract=lambda url: [{"title": "x", "playlist_title": "Mix"}] * 3)
        link = "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=RDaaaaaaaaaaa&start_radio=1"
        ask = wait_links(studio.moresources.add_links(link))["results"][0]
        self.assertEqual((ask["choose"], ask["kind"], ask["split"]), (True, "mix", 2))
        out = wait_links(studio.moresources.add_links(link, "split"))["results"][0]
        self.assertTrue(out["ok"])
        q = self.queued(studio)
        self.assertEqual(q[0]["payload"]["url"], "https://www.youtube.com/watch?v=aaaaaaaaaaa")  # the video, not the Mix

    def test_add_split_rejects_a_bad_tracklist(self):
        studio = self.make()
        with self.assertRaises(MoreError):
            studio.moresources.add_split("https://x", [{"title": "Only one", "start_time": 0}], "Jay", "Album")
        with self.assertRaises(MoreError):
            studio.moresources.add_split("https://x", [{"title": "No start time"}, {"title": "Two", "start_time": 5}], "Jay", "Album")

    def test_process_routes_a_chaptered_job_to_the_splitter_not_the_normal_download(self):
        studio = self.make()
        called = []
        studio.moresources._process_split = lambda job: called.append(job["id"])
        studio.moresources.process({"id": "x1", "payload": {"chapters": [{"title": "A", "start_time": 0}]}})
        self.assertEqual(called, ["x1"])

    def test_process_split_downloads_once_cuts_tags_and_records_each_track(self):
        studio = self.make()
        more = studio.moresources
        chapters = [{"title": "One", "start_time": 0, "end_time": 1}, {"title": "Two", "start_time": 1, "end_time": None}]
        item = studio.moresources.add_split("https://www.youtube.com/watch?v=ccccccccccc", chapters, "Jay", "Greatest Hits",
                                            link="https://www.youtube.com/watch?v=ccccccccccc")
        job = self.queued(studio)[0]

        def fetch(url, folder, hook):
            # a copy, not FIXTURE_MP3 itself - _process_split moves (Path.replace) whatever this returns
            folder.mkdir(parents=True, exist_ok=True)
            copy = folder / "whole.mp3"
            copy.write_bytes(FIXTURE_MP3.read_bytes())
            return str(copy), {"title": "Jay - Greatest Hits"}

        more._fetch_watched = fetch

        def fake_cut(src, dst, start, end):
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(Path(src).read_bytes())

        more._ffmpeg_cut = fake_cut
        more.process(job)
        done = studio.download_manager.item(job["id"])
        self.assertEqual(done["state"], "done")
        self.assertIn("Split into 2 tracks", done["note"])
        history = [h for h in studio.download_manager.state["history"] if h.get("album") == "Greatest Hits"]
        self.assertEqual(sorted(h["title"] for h in history), ["One", "Two"])
        for h in history:
            self.assertTrue(Path(h["path"]).exists())
            self.assertEqual(Path(h["path"]).read_bytes()[:3], Path(h["path"]).read_bytes()[:3])  # sanity: file is readable


class V129UntitledSetTests(unittest.TestCase):
    """SoundCloud sets come back from yt-dlp's quick read with links but no titles."""

    SET = "https://soundcloud.com/bonobo/sets/the-north-borders-album"

    def _flat(self):
        return [{"title": None, "url": f"https://soundcloud.com/bonobo/{s}", "playlist_title": "The North Borders (Album Sampler)",
                 "playlist_uploader": "bonobo"} for s in ("first-fires", "cirrus", "jets")]

    def test_untitled_set_entries_are_resolved_one_by_one(self):
        studio = LocalStudio()
        names = {"first-fires": "First Fires (feat. Grey Reverend)", "cirrus": "Cirrus", "jets": "Jets"}
        seen = []

        def info(url):
            seen.append(url)
            return {"title": names[url.rsplit("/", 1)[1]], "uploader": "bonobo", "duration": 200}

        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=lambda url: self._flat(), info=info)
        studio.moresources.ensure_worker = lambda: None
        out = wait_links(studio.moresources.add_links(self.SET))["results"]
        self.assertTrue(out[0]["ok"], out[0])
        q = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        titles = [i["title"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([(t, p["track_no"], p["album"]) for t, p in zip(titles, q)],
                         [("First Fires (feat. Grey Reverend)", 1, "The North Borders (Album Sampler)"),
                          ("Cirrus", 2, "The North Borders (Album Sampler)"), ("Jets", 3, "The North Borders (Album Sampler)")])
        self.assertEqual(len(seen), 3)

    def test_untitled_entry_that_cannot_be_read_falls_back_to_its_link_name(self):
        studio = LocalStudio()

        def info(url):
            raise MoreError("boom")

        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, extract=lambda url: self._flat(), info=info)
        studio.moresources.ensure_worker = lambda: None
        out = wait_links(studio.moresources.add_links(self.SET))["results"]
        self.assertTrue(out[0]["ok"], out[0])
        q = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"]
        self.assertEqual([i["title"] for i in studio.download_manager.state["queue"] if i.get("source") == "more"],
                         ["First Fires", "Cirrus", "Jets"])
