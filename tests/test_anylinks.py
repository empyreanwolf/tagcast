"""fork v120: one box for every link."""

import time
import unittest
from unittest import mock

from tagcast.anylinks import AnyLinks, kind_of, split_links, youtube_mode
from tagcast.library import LibraryError


class SplitTests(unittest.TestCase):
    def test_lines_and_glued_links_are_split_without_repeats(self):
        text = "https://youtu.be/abc\nhttps://open.spotify.com/album/1https://x.bandcamp.com/album/y  https://youtu.be/abc"
        self.assertEqual(split_links(text), ["https://youtu.be/abc", "https://open.spotify.com/album/1", "https://x.bandcamp.com/album/y"])

    def test_each_site_goes_to_its_source(self):
        self.assertEqual(kind_of("https://www.youtube.com/watch?v=1"), "more")
        self.assertEqual(kind_of("https://music.youtube.com/playlist?list=OLAK"), "more")
        self.assertEqual(kind_of("https://open.spotify.com/playlist/1"), "more")
        self.assertEqual(kind_of("https://soundcloud.com/a/b"), "more")
        self.assertEqual(kind_of("https://artist.bandcamp.com"), "bandcamp")
        self.assertEqual(kind_of("https://www.rockstargames.com/VI/music"), "page")

    def test_a_video_from_a_playlist_is_one_song(self):
        self.assertEqual(youtube_mode("https://www.youtube.com/watch?v=1&list=PL2"), "song")
        self.assertEqual(youtube_mode("https://www.youtube.com/playlist?list=PL2"), "list")


class PasteTests(unittest.TestCase):
    def setUp(self):
        self.studio = mock.Mock()
        self.studio.bandcamp.discography.return_value = {"releases": ["https://a.bandcamp.com/album/1", "https://a.bandcamp.com/album/2"]}
        self.studio.pagelist.read.return_value = {"id": "j"}
        self.studio.pagelist.view.return_value = {"state": "done", "items": [
            {"id": "0", "album": "A", "status": "new"}, {"id": "1", "album": "B", "status": "have"}, {"id": "2", "album": "C", "status": "partly"}]}

    def _paste(self, text):
        with mock.patch("tagcast.anylinks.threading.Thread") as thread:
            r = AnyLinks(self.studio).paste(text)
            target, args = thread.call_args.kwargs["target"], thread.call_args.kwargs["args"]
        with mock.patch("tagcast.anylinks.activity"):
            target(*args)
        return r

    def test_everything_is_sent_at_once(self):
        r = self._paste("https://www.youtube.com/watch?v=1&list=P\nhttps://open.spotify.com/album/9\n"
                        "https://a.bandcamp.com\nhttps://b.bandcamp.com/track/t\nhttps://example.com/list")
        self.assertEqual(r["counts"], {"more": 2, "bandcamp": 2, "page": 1})
        calls = {c.args[1]: c.args[0] for c in self.studio.moresources.add_links.call_args_list}
        self.assertEqual(calls, {"song": "https://www.youtube.com/watch?v=1&list=P", "list": "https://open.spotify.com/album/9"})
        added = [c.args[0] for c in self.studio.bandcamp.add.call_args_list]
        self.assertEqual(added, ["https://a.bandcamp.com/album/1", "https://a.bandcamp.com/album/2", "https://b.bandcamp.com/track/t"])
        self.studio.pagelist.get.assert_called_once_with("j", ["0", "2"])

    def test_one_bad_link_does_not_stop_the_rest(self):
        self.studio.bandcamp.add.side_effect = [ValueError("gone"), None]
        self._paste("https://a.bandcamp.com/album/1\nhttps://a.bandcamp.com/album/2")
        self.assertEqual(self.studio.bandcamp.add.call_count, 2)

    def test_no_link_is_a_clean_error(self):
        with self.assertRaises(LibraryError):
            AnyLinks(self.studio).paste("Coldplay - Yellow")


if __name__ == "__main__":
    unittest.main()


class V125BandcampOutcomeTests(unittest.TestCase):
    def test_all_releases_failing_is_not_reported_as_queued(self):
        import tempfile
        import types
        from tagcast import activity
        activity.start(tempfile.mkdtemp())
        self.addCleanup(lambda: setattr(activity, "_current", None))

        def fail(url):
            raise RuntimeError("no stream")
        bc = types.SimpleNamespace(add=fail, discography=lambda u: {"releases": ["https://a.bandcamp.com/album/x", "https://a.bandcamp.com/album/y"]})
        AnyLinks(types.SimpleNamespace(bandcamp=bc))._bandcamp("https://a.bandcamp.com")
        last = [e for e in activity.current().entries if "Bandcamp link" in e["title"]][-1]
        self.assertEqual(last["status"], "warn")
        self.assertIn("0 of 2", last["detail"])
