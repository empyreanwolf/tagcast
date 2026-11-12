import unittest
from pathlib import Path

from test_discography import LocalStudio
from tagcast.youtube import YouTube, YouTubeError, best_video, music_fit, score_video
from tagcast.artist_match import main_artist


def video(title, channel, seconds=200, vid="abc"):
    return {"id": vid, "title": title, "channel": channel, "duration": seconds}


class ScoringTests(unittest.TestCase):
    def test_the_topic_channel_audio_wins_over_a_video_and_a_cover(self):
        entries = [video("Coldplay - Yellow (Official Video)", "Coldplay", vid="v1"),
                   video("Yellow", "Coldplay - Topic", vid="v2"),
                   video("Yellow cover by Someone", "Someone", vid="v3")]
        self.assertEqual(best_video(entries, "Coldplay", "Yellow")["id"], "v2")

    def test_live_remix_and_another_artist_are_rejected(self):
        self.assertEqual(score_video(video("Coldplay - Yellow (Live)", "Coldplay"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow (Remix)", "Coldplay - Topic"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow", "Random Uploader"), "Coldplay", "Yellow"), 0.0)
        self.assertEqual(score_video(video("Yellow", "Coldplay - Topic", seconds=2000), "Coldplay", "Yellow"), 0.0)

    def test_a_remix_is_fine_when_it_is_what_you_asked_for(self):
        self.assertGreater(score_video(video("Yellow (Remix)", "Coldplay - Topic"), "Coldplay", "Yellow (Remix)"), 0)


class MusicSongTests(unittest.TestCase):
    """Fork v95: YouTube Music's Songs tab first, checked on its own artist/track/length details."""

    def test_main_artist(self):
        self.assertEqual(main_artist("Abstract Rude feat. Busdriver, Myka 9 & Aceyalone"), "Abstract Rude")
        self.assertEqual(main_artist("Analog Dive (feat. Scarub)"), "Analog Dive")
        self.assertEqual(main_artist("Coldplay"), "Coldplay")
        self.assertEqual(main_artist("Featurecast"), "Featurecast")

    def test_music_fit(self):
        info = {"id": "m1", "artists": ["Abstract Rude", "Del The Funky Homosapien"], "track": "From Conception to 2 Cremation",
                "channel": "Abstract Rude - Topic", "duration": 240}
        self.assertEqual(music_fit(info, "Abstract Rude", "From Conception to 2 Cremation")["id"], "m1")
        self.assertIsNone(music_fit(info, "Busdriver", "From Conception to 2 Cremation"))
        self.assertIsNone(music_fit(info, "Abstract Rude", "Another Song"))
        self.assertIsNone(music_fit(info, "Abstract Rude", "From Conception to 2 Cremation", want_seconds=120))
        self.assertIsNone(music_fit({**info, "track": "From Conception to 2 Cremation (Remix)"}, "Abstract Rude", "From Conception to 2 Cremation"))

    def test_find_song_tries_youtube_music_first_with_the_main_artist(self):
        queries, details = [], []

        def search(query, playlists=False):
            queries.append(query)
            if "music.youtube.com" in query:
                return [{"id": "x1", "title": "Something Else"}, {"id": "m1", "title": "The Media"}]
            return []

        def info(url):
            details.append(url)
            return {"id": "m1", "artists": ["Abstract Rude"], "track": "The Media", "channel": "Abstract Rude - Topic", "duration": 230}
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None, info=info)
        found = studio.youtube.find_song("Abstract Rude feat. Busdriver, Myka 9 & Aceyalone", "The Media (feat. Busdriver)")
        self.assertEqual((found["id"], found["duration"]), ("m1", 230))
        self.assertEqual(len(details), 1)  # "Something Else" is not even looked at
        self.assertIn("q=Abstract%20Rude%20The%20Media#songs", queries[0])
        self.assertNotIn("Busdriver", queries[0])

    def test_find_song_falls_back_to_normal_search(self):
        queries = []

        def search(query, playlists=False):
            queries.append(query)
            return [] if "music.youtube.com" in query else [video("The Media", "Abstract Rude - Topic", vid="v5")]
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        self.assertEqual(studio.youtube.find_song("Abstract Rude feat. Busdriver", "The Media")["id"], "v5")
        self.assertEqual(queries[1], "Abstract Rude The Media")


class QueueTests(unittest.TestCase):
    def make(self, entries):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=lambda query, playlists=False: entries, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        return studio

    def test_a_song_is_found_and_queued_first_in_the_chain(self):
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        source, note = studio.discography._song("Coldplay", "Obscure Song", "Parachutes", {})
        self.assertEqual(source, "YouTube")
        item = [i for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual(len(item), 1)
        self.assertEqual(item[0]["payload"]["id"], "v2")

    def test_nothing_found_falls_through_and_switching_off_skips_it(self):
        studio = self.make([video("Something else", "Nobody")])
        self.assertIsNone(studio.discography._youtube_song("Coldplay", "Yellow"))
        with self.assertRaises(YouTubeError):
            studio.youtube.add_song("Coldplay", "Yellow")
        studio.youtube.state["enabled"] = False
        self.assertIsNone(studio.discography._youtube())

    def test_lines_report_each_song(self):
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        out = studio.youtube.add_lines("Coldplay - Obscure Song\nNo dash here")["results"]
        self.assertEqual([r["ok"] for r in out], [True, False])

    def test_an_album_needs_a_matching_playlist(self):
        calls = []

        def search(query, playlists=False):
            calls.append(query)
            if playlists:
                return [{"id": "PL1", "title": "Parachutes", "channel": "Coldplay - Topic"}]
            return [{"id": f"s{i}", "title": f"Song {i}", "duration": 200} for i in range(1, 4)]
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        found = studio.youtube.find_album("Coldplay", "Parachutes")
        self.assertEqual(len(found["tracks"]), 3)
        self.assertIsNone(studio.youtube.find_album("Coldplay", "A Different Album"))

    def test_the_worker_tags_and_saves_the_downloaded_file(self):
        import tempfile
        from tagcast.galaxy import detect_audio_format  # noqa: F401
        studio = self.make([video("Obscure Song", "Coldplay - Topic", vid="v2")])
        saved = {}
        yt = studio.youtube

        def fetch(url, folder, hook):
            folder.mkdir(parents=True, exist_ok=True)
            hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 20})
            path = folder / "audio.m4a"
            path.write_bytes(b"x")
            return path
        yt._fetch_audio = fetch
        yt.check_audio = lambda part, min_seconds=0: "m4a"
        yt.save = lambda job, part, fmt, nbytes, **kw: saved.update(kw, fmt=fmt)
        studio.download_manager.state["folder"] = tempfile.mkdtemp()
        yt.add_song("Coldplay", "Obscure Song", "Parachutes")
        job = studio.download_manager.next_queued("youtube")
        yt.active = job["id"]
        yt.process(job)
        self.assertEqual((saved["title"], saved["album"], saved["fmt"]), ("Obscure Song", "Parachutes", "m4a"))
        self.assertIn("from YouTube, lossy", saved["extras"])



class V61Tests(unittest.TestCase):
    def test_page_meta_cover_choice_and_tags(self):
        from tagcast.youtube import YtdlpWorker, page_meta, pick_thumbnail
        info = {"track": "Yellow", "artist": "Coldplay, Guest", "album": "Parachutes", "release_year": 2000,
                "thumbnails": [{"url": "wide", "width": 1280, "height": 720}, {"url": "sq", "width": 544, "height": 544}]}
        self.assertEqual(page_meta(info)["artists"], ["Coldplay", "Guest"])
        self.assertEqual(page_meta(info)["year"], "2000")
        self.assertEqual(pick_thumbnail(info), "sq")
        self.assertEqual(pick_thumbnail({"thumbnails": [{"url": "a", "width": 120, "height": 90}, {"url": "b", "width": 1280, "height": 720}]}), "b")
        job = {"title": "yellow (official video)", "payload": {"artists": ["X"], "album_artist": "X", "album": "yellow",
                                                               "track_no": 1, "track_total": 1, "page_meta": True, "single": True}}
        tags = YtdlpWorker.tags_for(job, info)
        self.assertEqual((tags["title"], tags["album"], tags["album_artist"], tags["year"]), ("Yellow", "Parachutes", "Coldplay", "2000"))
        job["payload"]["page_meta"] = False
        self.assertEqual(YtdlpWorker.tags_for(job, info)["title"], "yellow (official video)")

    def test_artist_releases_split_albums_and_singles_and_fall_back(self):
        album = [{"id": f"vid{i}xx", "title": f"Song {i}"} for i in range(8)]
        single = [{"id": "vidsingle", "title": "Hit"}]

        def search(query, playlists=False):
            if "music.youtube.com" in query:
                return []
            if "sp=EgIQAg" in query:
                return [{"title": "Coldplay - Topic", "url": "https://www.youtube.com/channel/UC1"}]
            if query.endswith("/releases"):
                return [{"title": "Parachutes", "url": "https://www.youtube.com/playlist?list=PA"},
                        {"title": "Hit", "url": "https://www.youtube.com/playlist?list=PS"}]
            return album if query.endswith("PA") else single if query.endswith("PS") else []

        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        out = studio.youtube.artist_releases("Coldplay", "music")
        self.assertTrue(out["fell_back"])
        self.assertEqual([r["title"] for r in out["albums"]], ["Parachutes"])
        self.assertEqual([r["title"] for r in out["singles"]], ["Hit"])
        studio.youtube.add_releases("Coldplay", [out["albums"][0]])
        q = [i["payload"] for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual([(p["album"], p["track_no"], p["track_total"]) for p in q][:2], [("Parachutes", 1, 8), ("Parachutes", 2, 8)])
        self.assertTrue(all(p["page_meta"] for p in q))
        with self.assertRaises(YouTubeError):
            studio.youtube.artist_releases("  ")


class V63AlbumTests(unittest.TestCase):
    def test_album_search_uses_youtube_music_and_topic_tracks_and_reports_the_query(self):
        songs = [{"id": f"t{i}xxxxxx", "title": f"Track {i}", "channel": "Moka Only - Topic"} for i in range(5)]

        def search(query, playlists=False):
            if "music.youtube.com/search" in query:
                return [{"title": "Airport 6", "url": "https://music.youtube.com/browse/MPREb_6"},
                        {"title": "Airport 2", "url": "https://music.youtube.com/browse/MPREb_2"}]
            if query.endswith("MPREb_2"):
                return songs
            if query.endswith("MPREb_6"):
                return [{"id": "zzzzzzzz", "title": "x", "channel": "Moka Only - Topic"}] * 5
            return []

        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        found = studio.youtube.find_album("Moka Only", "Airport 2")
        self.assertEqual(len(found["tracks"]), 5)
        studio.youtube.add_album("Moka Only", "Airport 2")
        q = [i for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual(q[0]["query"], "Moka Only Airport 2")
        with self.assertRaisesRegex(YouTubeError, "query “Moka Only Airport 9”"):
            studio.youtube.add_album("Moka Only", "Airport 9")

    def test_another_artists_album_with_the_same_name_is_refused(self):
        other = [{"id": f"t{i}xxxxxx", "title": f"T{i}", "channel": "Someone Else - Topic"} for i in range(5)]
        search = lambda q, playlists=False: [{"title": "Airport 2", "url": "u"}] if "music.youtube" in q else other if q == "u" else []
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        self.assertIsNone(studio.youtube.find_album("Moka Only", "Airport 2"))
        why = []
        studio.discography._youtube_album("Moka Only", "Airport 2", why)
        self.assertIn("YouTube · query “Moka Only Airport 2”: no matching album", why)


class V65Tests(unittest.TestCase):
    def test_a_channel_without_a_releases_tab_falls_through_to_playlists(self):
        from tagcast.youtube import plain_error
        songs = [{"id": f"s{i}xxxxxx", "title": f"S{i}", "channel": "Moka Only - Topic"} for i in range(6)]

        def search(query, playlists=False):
            if "music.youtube.com" in query:
                raise YouTubeError("music down")
            if "sp=EgIQAg" in query:
                return [{"title": "Moka Only", "url": "https://www.youtube.com/channel/UC1"}]
            if query.endswith("/releases"):
                raise YouTubeError("\x1b[0;31mERROR:\x1b[0m This channel does not have a releases tab")
            if query.endswith("/playlists"):
                return [{"title": "Bridges", "url": "https://www.youtube.com/playlist?list=PLb"},
                        {"title": "Random faves", "url": "https://www.youtube.com/playlist?list=PLr"}]
            if query.endswith("PLb"):
                return songs
            if query.endswith("PLr"):
                return [{"id": f"r{i}xxxxxx", "title": "x", "channel": "Someone"} for i in range(5)]
            return []

        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        out = studio.youtube.artist_releases("Moka Only", "music")
        self.assertTrue(out["fell_back"])
        self.assertEqual([r["title"] for r in out["albums"]], ["Bridges"])
        self.assertEqual(plain_error("\x1b[0;31mERROR:\x1b[0m [youtube:tab] x: no tab"), "[youtube:tab] x: no tab")

    def test_galaxy_no_link_tries_youtube_and_removes_the_failed_row(self):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=lambda q, playlists=False: [video("Afford the Best", "Moka Only - Topic", vid="v9")],
                                 fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        studio.download_manager.state["queue"].append({"id": "g1", "kind": "song", "title": "Afford the Best", "artist": "Moka Only",
                                                       "album": "Bridges", "source": "galaxy", "state": "failed",
                                                       "note": "No download link (tried 320 and 128 kbps and other sources)"})
        studio.discography.galaxy_fallback(studio.download_manager.item("g1"))
        queue = studio.download_manager.state["queue"]
        self.assertIsNone(studio.download_manager.item("g1"))
        self.assertEqual([(i["source"], i["payload"]["id"]) for i in queue], [("youtube", "v9")])

    def test_try_youtube_on_a_failed_row(self):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=lambda q, playlists=False: [video("Kareem Loves You", "Moka Only - Topic", vid="v8")],
                                 fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        studio.download_manager.state["queue"].append({"id": "g2", "kind": "song", "title": "Kareem Loves You (Explicit)", "artist": "Moka Only",
                                                       "album": "São Paulo", "source": "galaxy", "state": "failed"})
        studio.youtube.try_item("g2")
        self.assertEqual([i["source"] for i in studio.download_manager.state["queue"]], ["youtube"])


class V91Tests(unittest.TestCase):
    """Fork v91: age-restricted videos, album size and length guards, cookies."""

    def yt(self, search):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        return studio

    def test_an_age_restricted_video_fails_the_row_and_starts_the_fallback(self):
        from tagcast.youtube import AgeRestricted
        studio = self.yt(lambda q, playlists=False: [video("Obscure Song", "Coldplay - Topic", vid="v2")])
        yt, called = studio.youtube, []
        yt.on_failed = lambda item: called.append(item)

        def fetch(url, folder, hook):
            raise AgeRestricted("Age-restricted on YouTube (sign-in needed).")
        yt._fetch_audio = fetch
        yt.add_song("Coldplay", "Obscure Song", "Parachutes")
        job = studio.download_manager.next_queued("youtube")
        yt.active = job["id"]
        yt.process(job)
        for _ in range(100):
            if called:
                break
            import time
            time.sleep(0.01)
        item = studio.download_manager.item(job["id"])
        self.assertEqual(item["state"], "failed")
        self.assertIn("Age-restricted", item["note"])
        self.assertEqual(called[0]["id"], job["id"])

    def test_the_age_gate_message_is_recognised(self):
        from tagcast.youtube import AGE_GATE
        self.assertTrue(AGE_GATE.search("[youtube] J3oB6NhQeEg: Sign in to confirm your age. This video may be inappropriate for some users."))
        self.assertFalse(AGE_GATE.search("HTTP Error 403: Forbidden"))

    def test_the_fallback_skips_the_bad_video_and_tries_the_next_upload(self):
        entries = [video("Obscure Song", "Coldplay - Topic", vid="bad"), video("Obscure Song", "Coldplay - Topic", vid="good")]
        studio = self.yt(lambda q, playlists=False: entries)
        studio.discography.youtube_fallback({"id": "x", "kind": "song", "title": "Obscure Song", "artist": "Coldplay",
                                             "album": "Parachutes", "payload": {"id": "bad"}})
        queued = [i for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual([i["payload"]["id"] for i in queued], ["good"])

    def test_a_playlist_with_far_more_videos_than_the_album_has_is_skipped(self):
        big = [{"id": f"t{i}xxxxxx", "title": f"Track {i}", "channel": "Moka Only - Topic"} for i in range(30)]
        small = [{"id": f"s{i}xxxxxx", "title": f"Song {i}", "channel": "Moka Only - Topic"} for i in range(19)]

        def search(query, playlists=False):
            if "music.youtube.com/search" in query:
                return [{"title": "Choose One", "url": "big"}, {"title": "Choose One", "url": "small"}]
            return {"big": big, "small": small}.get(query, [])
        studio = self.yt(search)
        self.assertEqual(len(studio.youtube.find_album("Moka Only", "Choose One")["tracks"]), 30)
        self.assertEqual(len(studio.youtube.find_album("Moka Only", "Choose One", expected=19)["tracks"]), 19)

    def test_videos_over_twenty_minutes_are_not_queued_as_album_songs(self):
        songs = [{"id": f"t{i}xxxxxx", "title": f"Track {i}", "channel": "Moka Only - Topic", "duration": 200} for i in range(4)]
        songs.append({"id": "longxxxxxx", "title": "Unreleased", "channel": "Moka Only - Topic", "duration": 3480})
        studio = self.yt(lambda q, playlists=False: [{"title": "Airport 2", "url": "u"}] if "music.youtube" in q else songs if q == "u" else [])
        result = studio.youtube.add_album("Moka Only", "Airport 2")
        titles = [i["title"] for i in studio.download_manager.state["queue"] if i.get("source") == "youtube"]
        self.assertEqual((len(titles), result["skipped_long"]), (4, 1))
        self.assertNotIn("Unreleased", titles)

    def test_cookie_settings_are_checked_and_used(self):
        import tempfile
        studio = self.yt(lambda q, playlists=False: [])
        yt = studio.youtube
        self.assertEqual(yt._cookie_opts(), {})
        self.assertEqual(yt.save_settings({"cookies_browser": "Firefox"})["cookies_browser"], "firefox")
        self.assertEqual(yt._cookie_opts(), {"cookiesfrombrowser": ("firefox",)})
        with self.assertRaises(YouTubeError):
            yt.save_settings({"cookies_browser": "netscape"})
        with self.assertRaises(YouTubeError):
            yt.save_settings({"cookies_file": "C:\\nope\\cookies.txt"})
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# Netscape HTTP Cookie File\n")
        view = yt.save_settings({"cookies_file": f.name})
        self.assertEqual((yt._cookie_opts(), view["cookies_ok"]), ({"cookiefile": f.name}, True))
        self.assertTrue(yt.save_settings({"enabled": False}) and not yt.state["enabled"])

class V93Tests(unittest.TestCase):
    def test_updating_yt_dlp_does_not_reload_it_in_the_running_program(self):
        import sys
        import types
        from unittest import mock
        studio = LocalStudio()
        yt = YouTube(studio, search=lambda q, playlists=False: [], fetch_audio=lambda *a: None)
        marker = types.ModuleType("yt_dlp.fake_marker")
        sys.modules["yt_dlp.fake_marker"] = marker
        try:
            done = types.SimpleNamespace(returncode=0, stdout="", stderr="")
            with mock.patch("tagcast.youtube.subprocess.run", return_value=done), \
                    mock.patch.object(YouTube, "installed_version", staticmethod(lambda: "9999.1.1")):
                yt.updating = True
                yt._update_ytdlp()
            self.assertIs(sys.modules.get("yt_dlp.fake_marker"), marker)  # nothing was purged
            self.assertIn("Restart Tagcast", yt.update_note)
            self.assertFalse(yt.updating)
        finally:
            sys.modules.pop("yt_dlp.fake_marker", None)


class V94Tests(unittest.TestCase):
    """Fork v94: tidier titles, edition names, Topic queries, 403 fallback, fan clips."""

    def yt(self, search):
        studio = LocalStudio()
        studio.youtube = YouTube(studio, search=search, fetch_audio=lambda *a: None)
        studio.youtube.ensure_worker = lambda: None
        return studio

    def test_tempo_tags_go_and_acapella_is_spelled_one_way(self):
        from tagcast.youtube import strip_edition, tidy_title
        self.assertEqual(tidy_title("Kan of Whoop Ass (Acapella 99.33 BPM)"), "Kan of Whoop Ass (A Cappella)")
        self.assertEqual(tidy_title("Keep It Real (Key: Am)"), "Keep It Real")
        self.assertEqual(tidy_title("Key Chain"), "Key Chain")
        self.assertEqual(strip_edition("South Central Thynk Taynk (2026 Remaster)"), "South Central Thynk Taynk")
        self.assertEqual(strip_edition("Album (Deluxe Edition)"), "Album")
        self.assertEqual(strip_edition("Phoenix (feat. Abstract Rude)"), "Phoenix")
        self.assertEqual(strip_edition("Album - 2011 Remaster"), "Album")
        self.assertEqual(strip_edition("(Deluxe)"), "(Deluxe)")  # never an empty name

    def test_an_acappella_matches_only_an_acappella(self):
        topic = video("Kan of Whoop Ass (A Cappella)", "Abstract Rude - Topic", seconds=241)
        studio_version = video("Kan of Whoop Ass", "Abstract Rude - Topic", seconds=241)
        self.assertGreater(score_video(topic, "Abstract Rude", "Kan of Whoop Ass (Acapella 99.33 BPM)"), 0)
        self.assertEqual(score_video(studio_version, "Abstract Rude", "Kan of Whoop Ass (Acapella 99.33 BPM)"), 0.0)
        self.assertEqual(score_video(topic, "Abstract Rude", "Kan of Whoop Ass"), 0.0)

    def test_a_skate_clip_using_the_song_is_not_the_song(self):
        clip = video("Moka Only - Rolling Along (feat. Abstract Rude) skate", "Maibordeldemerde")
        self.assertEqual(score_video(clip, "Abstract Rude", "Rolling Along"), 0.0)

    def test_the_topic_query_and_the_albums_name_find_a_song_a_plain_search_misses(self):
        topic = video("Phoenix (feat. Abstract Rude)", "AKAdemics - Topic", seconds=261, vid="topic1")
        asked = []

        def search(query, playlists=False):
            asked.append(query)
            return [topic] if "Topic" in query or "Abstract Rude" in query else [video("DJ akademiks teaching", "Other", vid="x")]
        yt = self.yt(search).youtube
        found = yt.find_song("AKAdemics", "Phoenix", album="Phoenix (feat. Abstract Rude)")
        self.assertEqual(found["id"], "topic1")
        self.assertTrue(any("Topic" in q for q in asked))

    def test_an_album_is_searched_again_without_its_edition_tag(self):
        studio = self.yt(lambda q, playlists=False: [])
        yt = studio.youtube
        asked = []
        yt._find_album_once = lambda artist, album, expected=None: asked.append(album) or (
            {"title": album, "artist": artist, "tracks": [{"id": "1", "title": "A"}]} if album == "South Central Thynk Taynk" else None)
        found = yt.find_album("Abstract Rude", "South Central Thynk Taynk (2026 Remaster)", 15)
        self.assertEqual(asked, ["South Central Thynk Taynk (2026 Remaster)", "South Central Thynk Taynk"])
        self.assertEqual(found["title"], "South Central Thynk Taynk (2026 Remaster)")

    def test_a_403_fails_the_row_and_starts_the_fallback(self):
        from tagcast.youtube import DownloadBlocked
        studio = self.yt(lambda q, playlists=False: [video("Obscure Song", "Coldplay - Topic", vid="v2")])
        yt, called = studio.youtube, []
        yt.on_failed = lambda item: called.append(item)

        def fetch(url, folder, hook):
            raise DownloadBlocked("YouTube refused the download (HTTP 403).")
        yt._fetch_audio = fetch
        yt.add_song("Coldplay", "Obscure Song", "Parachutes")
        job = studio.download_manager.next_queued("youtube")
        yt.active = job["id"]
        yt.process(job)
        import time
        for _ in range(100):
            if called:
                break
            time.sleep(0.02)
        self.assertEqual(len(called), 1)
        self.assertEqual(studio.download_manager.item(job["id"])["state"], "failed")

    def test_a_one_song_release_is_searched_as_a_song_before_galaxy(self):
        studio = self.yt(lambda q, playlists=False: [video("Through The Darkness", "Abstract Rude - Topic", vid="s1")]
                         if "Through The Darkness" in q and "album" not in q and "#albums" not in q and not playlists else [])
        source, note = studio.discography._album("Abstract Rude", "Through The Darkness (2026 Remaster)", "new", None,
                                                 {"bandcamp": None, "bandcamp_checked": False}, expected=1)
        self.assertEqual(source, "YouTube")

    def test_add_songs_reports_each_song(self):
        studio = self.yt(lambda q, playlists=False: [video("Found It", "Coldplay - Topic", vid="v9")] if "Found" in q else [])
        answer = studio.youtube.add_songs([{"key": "a", "artist": "Coldplay", "title": "Found It", "album": "X"},
                                           {"key": "b", "artist": "Coldplay", "title": "Missing", "album": "X"}])
        self.assertEqual([(r["key"], r["ok"]) for r in answer["results"]], [("a", True), ("b", False)])


if __name__ == "__main__":
    unittest.main()
