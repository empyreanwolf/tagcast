"""fork v109: a split full-album video gets the official track names when MusicBrainz/Deezer agree."""

import unittest

from test_discography import LocalStudio
from test_moresources import wait_links

from tagcast.moresources import MoreSources
from tagcast.sources import SourceError
from tagcast.split_lookup import album_info, lengths_match

CHAPTERS = [{"title": "Track 1", "start_time": 0, "end_time": 200},
            {"title": "Track 2", "start_time": 200, "end_time": 380},
            {"title": "Track 3", "start_time": 380, "end_time": 620}]


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def json(self, url, params=None, **kw):
        self.asked.append(url)
        for key, value in self.answers.items():
            if url.endswith(key):
                if isinstance(value, Exception):
                    raise value
                return value
        return None


MB_SEARCH = {"releases": [{"id": "r1", "title": "Greatest Hits", "track-count": 3}]}
MB_RELEASE = {"title": "Greatest Hits", "date": "1999-05-01", "artist-credit": [{"name": "Jay", "joinphrase": ""}],
              "genres": [{"name": "hip hop", "count": 3}],
              "media": [{"tracks": [{"title": "Intro", "length": 201000}, {"title": "Middle", "length": 178000},
                                    {"title": "Outro", "length": 241000}]}]}


class LookupTests(unittest.TestCase):
    def test_a_release_with_matching_lengths_gives_the_real_names(self):
        info = album_info(FakeHttp({"/release": MB_SEARCH, "/release/r1": MB_RELEASE}), "Jay", "Greatest Hits", CHAPTERS)
        self.assertEqual((info["source"], info["titles"], info["year"], info["genre"]),
                         ("MusicBrainz", ["Intro", "Middle", "Outro"], "1999", "Hip Hop"))
        self.assertTrue(info["cover_url"].endswith("/r1/front-500"))

    def test_different_lengths_are_not_trusted_and_deezer_is_tried_next(self):
        wrong = {**MB_RELEASE, "media": [{"tracks": [{"title": "A", "length": 60000}, {"title": "B", "length": 60000},
                                                     {"title": "C", "length": 60000}]}]}
        deezer = {"title": "Greatest Hits", "release_date": "2001-01-01", "cover_xl": "https://x/c.jpg",
                  "genres": {"data": [{"name": "Rap"}]},
                  "tracks": {"data": [{"title": "One", "duration": 199}, {"title": "Two", "duration": 181},
                                      {"title": "Three", "duration": 238}]}}
        http = FakeHttp({"/release": MB_SEARCH, "/release/r1": wrong,
                         "/search/album": {"data": [{"id": 9, "title": "Greatest Hits", "nb_tracks": 3}]}, "/album/9": deezer})
        info = album_info(http, "Jay", "Greatest Hits", CHAPTERS)
        self.assertEqual((info["source"], info["titles"], info["year"]), ("Deezer", ["One", "Two", "Three"], "2001"))

    def test_nothing_matching_or_both_down_is_no_match(self):
        self.assertIsNone(album_info(FakeHttp({}), "Jay", "Greatest Hits", CHAPTERS))
        down = SourceError("could not be reached")
        self.assertIsNone(album_info(FakeHttp({"/release": down, "/search/album": down}), "Jay", "Greatest Hits", CHAPTERS))
        self.assertIsNone(album_info(None, "Jay", "Greatest Hits", CHAPTERS))

    def test_track_count_must_be_the_same(self):
        self.assertFalse(lengths_match(CHAPTERS, [200, 180]))
        self.assertTrue(lengths_match(CHAPTERS, [205, 175, 240]))
        self.assertFalse(lengths_match(CHAPTERS, [100, 100, 100]))


class SplitUsesLookupTests(unittest.TestCase):
    def test_clicking_split_uses_the_matched_names_year_and_cover(self):
        studio = LocalStudio()
        info = {"title": "Jay - Greatest Hits (Full Album)", "uploader": "Jay", "duration": 620, "chapters": CHAPTERS}
        found = {"source": "MusicBrainz", "album": "Greatest Hits", "titles": ["Intro", "Middle", "Outro"],
                 "year": "1999", "genre": "Hip Hop", "cover_url": "https://coverartarchive.org/release/r1/front-500"}
        studio.moresources = MoreSources(studio, fetch_audio=lambda *a: None, info=lambda url: info, lookup=lambda *a: found)
        studio.moresources.ensure_worker = lambda: None
        out = wait_links(studio.moresources.add_links("https://www.youtube.com/watch?v=aaaaaaaaaaa", "split"))["results"][0]
        self.assertIn("track names from MusicBrainz", out["note"])
        job = [i for i in studio.download_manager.state["queue"] if i.get("source") == "more"][0]
        self.assertEqual([c["title"] for c in job["payload"]["chapters"]], ["Intro", "Middle", "Outro"])
        self.assertEqual((job["payload"]["year"], job["payload"]["genre"], job["payload"]["names_from"]),
                         ("1999", "Hip Hop", "MusicBrainz"))
