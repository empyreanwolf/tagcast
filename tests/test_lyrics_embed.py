"""fork v109: lyrics go into downloads from every source, not only Galaxy's."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_discography import LocalStudio

from tagcast.moresources import MoreSources

FIXTURE_MP3 = Path(__file__).parent / "fixtures" / "galaxy-silence.mp3"
TIMED = "[00:01.00] First line\n[00:02.00] Second line"


def answer(body, status=200):
    return mock.Mock(status_code=status, json=lambda: body)


class LyricsEmbedTests(unittest.TestCase):
    def save(self, fetch):
        studio = LocalStudio()
        worker = MoreSources(studio, fetch_audio=lambda *a: None, info=lambda url: {})
        worker.lyrics_fetch = fetch
        part = Path(tempfile.mkdtemp()) / "x.part"
        self.addCleanup(shutil.rmtree, part.parent, True)
        shutil.copy(FIXTURE_MP3, part)
        studio.download_manager.state["queue"].append({"id": "j1", "state": "active", "title": "Song"})
        final = worker.save({"id": "j1", "payload": {}}, part, "mp3", 1000, title="Song", artists=["Jay"],
                            album_artist="Jay", album="Album", track_no=1, track_total=1)
        from mutagen.id3 import ID3
        lyrics = [f.text for f in ID3(str(final)).getall("USLT")]
        return final, lyrics, studio.download_manager.item("j1")

    def test_timed_lyrics_are_embedded_and_saved_as_an_lrc_file(self):
        final, lyrics, _ = self.save(lambda url, **kw: answer({"syncedLyrics": TIMED}))
        self.assertEqual(lyrics, [TIMED + "\n"])
        self.assertEqual(final.with_suffix(".lrc").read_text(encoding="utf-8"), TIMED + "\n")

    def test_plain_lyrics_are_used_when_there_are_no_timed_ones(self):
        final, lyrics, _ = self.save(lambda url, **kw: answer({"plainLyrics": "Just words\nMore words"}))
        self.assertEqual(lyrics, ["Just words\nMore words\n"])
        self.assertFalse(final.with_suffix(".lrc").exists())  # untimed: no .lrc

    def test_nothing_found_or_lrclib_down_still_saves_the_song(self):
        final, lyrics, item = self.save(lambda url, **kw: answer({}, 404))
        self.assertTrue(final.exists())
        self.assertEqual(lyrics, [])
        self.assertIn("no lyrics found", item["note"])

        def down(url, **kw):
            raise ConnectionError("offline")
        final, lyrics, _ = self.save(down)
        self.assertTrue(final.exists())
        self.assertEqual(lyrics, [])

    def test_an_instrumental_gets_no_lyrics(self):
        _, lyrics, _ = self.save(lambda url, **kw: answer({"instrumental": True, "plainLyrics": ""}))
        self.assertEqual(lyrics, [])
