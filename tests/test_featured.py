"""fork v109: songs downloaded as "by A" are credited to A (Artist A first, Album Artist A, no feat.)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from tagcast import featured
from tagcast.discography import _only_theirs
from tagcast.downloads import clean_item
from tagcast.galaxy import detect_audio_format, write_tags
from tagcast.tagclean import read_tags

FIXTURE_MP3 = Path(__file__).parent / "fixtures" / "galaxy-silence.mp3"


class CreditTests(unittest.TestCase):
    def test_a_featured_artist_comes_first_and_becomes_the_album_artist(self):
        self.assertEqual(featured.credit_for("Jay", "Song (feat. Jay)", ["Bee"], "Bee"), ("Song", ["Jay", "Bee"], "Jay"))
        self.assertEqual(featured.credit_for("Jay", "Song [ft. Jay & Kay]", ["Bee"], "Bee"), ("Song", ["Jay", "Bee", "Kay"], "Jay"))
        self.assertEqual(featured.credit_for("Jay", "Song featuring Jay", ["Bee"], "Bee"), ("Song", ["Jay", "Bee"], "Jay"))

    def test_a_second_credited_artist_is_moved_to_the_front(self):
        self.assertEqual(featured.credit_for("Jay", "Song", ["Bee", "Jay"], "Bee"), ("Song", ["Jay", "Bee"], "Jay"))
        self.assertEqual(featured.credit_for("Jay", "Song", ["Bee & Jay"], "Bee & Jay"), ("Song", ["Jay", "Bee"], "Jay"))

    def test_their_own_song_just_loses_the_feat_with_nobody_dropped(self):
        self.assertEqual(featured.credit_for("Jay", "Song (feat. Kay)", ["Jay"], "Jay"), ("Song", ["Jay", "Kay"], "Jay"))

    def test_accents_and_case_still_match(self):
        self.assertEqual(featured.credit_for("JAŸ", "Song (feat. jay)", ["Bee"], "Bee")[1][0], "jay")

    def test_a_song_the_artist_is_not_on_is_left_alone(self):
        self.assertEqual(featured.credit_for("Jay", "Song (feat. Kay)", ["Bee"], "Bee"), ("Song (feat. Kay)", ["Bee"], "Bee"))
        self.assertEqual(featured.credit_for("", "Song (feat. Jay)", ["Bee"], "Bee"), ("Song (feat. Jay)", ["Bee"], "Bee"))

    def test_with_in_a_title_is_only_a_credit_when_it_names_the_artist(self):
        self.assertEqual(featured.credit_for("Jay", "Song (With You)", ["Jay"], "Jay"), ("Song (With You)", ["Jay"], "Jay"))
        self.assertEqual(featured.credit_for("Jay", "Song (with Jay)", ["Bee"], "Bee"), ("Song", ["Jay", "Bee"], "Jay"))

    def test_a_duo_name_is_not_split(self):
        self.assertEqual(featured.credit_for("Simon & Garfunkel", "Song", ["Simon & Garfunkel"], "Simon & Garfunkel"),
                         ("Song", ["Simon & Garfunkel"], "Simon & Garfunkel"))


class QueueTests(unittest.TestCase):
    def test_items_queued_inside_for_artist_carry_it_and_others_do_not(self):
        with featured.for_artist("Jay"):
            self.assertEqual(clean_item({"title": "Song", "artist": "Bee"})["for_artist"], "Jay")
            with featured.for_artist(""):  # a whole album asked for on purpose
                self.assertNotIn("for_artist", clean_item({"title": "Song", "artist": "Bee"}))
        self.assertNotIn("for_artist", clean_item({"title": "Song", "artist": "Bee"}))

    def test_only_the_artists_own_songs_are_kept_from_another_artists_album(self):
        tracks = [{"title": "One", "artists": ["Bee"]}, {"title": "Two (feat. Jay)", "artists": ["Bee"]},
                  {"title": "Three", "artists": ["Bee", "Jay"]}]
        kept, note = _only_theirs(tracks, "Jay", "Bee")
        self.assertEqual([t["title"] for t in kept], ["Two (feat. Jay)", "Three"])
        self.assertIn("1 skipped", note)
        self.assertEqual(_only_theirs(tracks, "Bee", "Bee"), (tracks, ""))  # their own album: everything
        self.assertEqual(_only_theirs(tracks[:1], "Jay", "Bee")[0], tracks[:1])  # can't tell: nothing dropped


class RetagTests(unittest.TestCase):
    def test_a_file_that_came_tagged_is_credited_too(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / "song.mp3"
        shutil.copy(FIXTURE_MP3, path)
        write_tags(path, "mp3", title="Song (feat. Jay)", artists=["Bee"], album_artist="Bee", album="Bee's Album",
                   track_no=3, track_total=10)
        self.assertTrue(featured.retag_file(path, "Jay"))
        tags = read_tags(path, detect_audio_format(path))
        self.assertEqual((tags["title"], tags["artists"], tags["album_artist"], tags["album"], tags["track_no"]),
                         ("Song", ["Jay", "Bee"], "Jay", "Bee's Album", 3))
        self.assertFalse(featured.retag_file(path, "Jay"))  # already right: left alone


class SaveTests(unittest.TestCase):
    def test_a_download_asked_for_as_by_jay_is_saved_under_jay(self):
        from test_discography import LocalStudio
        from tagcast.moresources import MoreSources
        studio = LocalStudio()
        worker = MoreSources(studio, fetch_audio=lambda *a: None, info=lambda url: {})
        part = Path(tempfile.mkdtemp()) / "x.part"
        self.addCleanup(shutil.rmtree, part.parent, True)
        shutil.copy(FIXTURE_MP3, part)
        job = {"id": "j1", "for_artist": "Jay", "payload": {}}
        studio.download_manager.state["queue"].append({"id": "j1", "state": "active", "title": "Song"})
        final = worker.save(job, part, "mp3", 1000, title="Song (feat. Jay)", artists=["Bee"], album_artist="Bee",
                            album="Bee's Album", track_no=2, track_total=9)
        self.assertEqual(final.parent.parent.name, "Jay")  # Jay\Bee's Album\02 - Song.mp3
        self.assertEqual(final.name, "02 - Song.mp3")
        tags = read_tags(final, "mp3")
        self.assertEqual((tags["artists"], tags["album_artist"], tags["album"]), (["Jay", "Bee"], "Jay", "Bee's Album"))


class RetagLyricsTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        self.path = folder / "song.mp3"
        shutil.copy(FIXTURE_MP3, self.path)

    def test_lyrics_already_in_the_file_survive_the_retag(self):
        write_tags(self.path, "mp3", title="Song (feat. Jay)", artists=["Bee"], album_artist="Bee", album="A",
                   track_no=1, track_total=1, lyrics="Old words\n")
        featured.retag_file(self.path, "Jay", lambda *a: "New words\n")
        self.assertEqual(featured.read_lyrics(self.path, "mp3"), "Old words\n")

    def test_missing_lyrics_are_added_even_without_an_artist_to_credit(self):
        write_tags(self.path, "mp3", title="Song", artists=["Bee"], album_artist="Bee", album="A", track_no=1, track_total=1)
        self.assertTrue(featured.retag_file(self.path, "", lambda *a: "Found words\n"))
        self.assertEqual(featured.read_lyrics(self.path, "mp3"), "Found words\n")
        self.assertEqual(read_tags(self.path, "mp3")["artists"], ["Bee"])
