"""fork v109: same song under a different name, found by its sound (Chromaprint fingerprints)."""

import hashlib
import io
import random
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tagcast import fingerprint as fpm
from tagcast.library import LibraryError

RNG = random.Random(7)
SONG_A = [RNG.getrandbits(32) for _ in range(240)]
SONG_B = [RNG.getrandbits(32) for _ in range(240)]


def noisy(fp, bits=3):
    """The same recording re-encoded: a few bits flip here and there."""
    rng = random.Random(1)
    return [x ^ (1 << rng.randrange(32)) if rng.random() < bits / 32 else x for x in fp]


class FakeLibrary:
    def __init__(self, songs):
        self.songs = songs  # id -> (title, artist, length)
        self.tracks = {i: {"title": t, "length": n, "trashed": False} for i, (t, _, n) in songs.items()}
        self.albums = {1: {"name": "Album"}}

    def track_view(self, track_id):
        if track_id not in self.songs:
            raise LibraryError("gone")
        title, artist, length = self.songs[track_id]
        return {"title": title, "artist": artist, "album_id": "1", "length": length}


class FakeStudio:
    def __init__(self, library=None):
        self.home = Path(tempfile.mkdtemp())
        self.library = library


class CompareTests(unittest.TestCase):
    def test_the_same_recording_matches_and_a_different_one_does_not(self):
        self.assertGreater(fpm.similarity(SONG_A, noisy(SONG_A)), fpm.SAME)
        self.assertLess(fpm.similarity(SONG_A, SONG_B), 0.7)

    def test_a_small_shift_at_the_start_is_still_the_same_song(self):
        self.assertGreater(fpm.similarity(SONG_A, SONG_A[5:]), fpm.SAME)

    def test_silence_alone_never_matches(self):
        silence = [12345] * 200
        self.assertEqual(fpm.similarity(silence, silence), 0.0)
        self.assertGreater(fpm.similarity([7] * 30 + SONG_A, SONG_A), fpm.SAME)  # a quiet intro is skipped

    def test_pack_round_trip(self):
        self.assertEqual(fpm.unpack(fpm.pack(SONG_A)), SONG_A)


class InstallTests(unittest.TestCase):
    def test_a_download_with_the_wrong_checksum_is_not_installed(self):
        home = Path(tempfile.mkdtemp())
        def fetch(url, **kw):
            return mock.Mock(status_code=200, content=b"not the real thing")
        with mock.patch.object(fpm.platform, "system", return_value="Windows"), \
                mock.patch.object(fpm.platform, "machine", return_value="AMD64"):
            with self.assertRaisesRegex(fpm.FingerprintError, "checksum"):
                fpm.install_fpcalc(home, fetch)
        self.assertFalse((home / "tools").exists())

    def test_the_checked_tool_is_unpacked_into_the_settings_folder(self):
        home = Path(tempfile.mkdtemp())
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("chromaprint-fpcalc-1.5.1-windows-x86_64/fpcalc.exe", b"MZ fake exe")
        body = archive.getvalue()
        table = {"windows": ("x.zip", hashlib.sha256(body).hexdigest(), "fpcalc.exe")}
        with mock.patch.object(fpm.platform, "system", return_value="Windows"), \
                mock.patch.object(fpm.platform, "machine", return_value="AMD64"), \
                mock.patch.dict(fpm.FPCALC, table, clear=True):
            path = fpm.install_fpcalc(home, lambda url, **kw: mock.Mock(status_code=200, content=body))
        self.assertEqual(Path(path).read_bytes(), b"MZ fake exe")
        with mock.patch.object(fpm.shutil, "which", return_value=None):
            self.assertEqual(fpm.fpcalc_path(home), path)


class LibraryIndexTests(unittest.TestCase):
    def make(self):
        library = FakeLibrary({1: ("Song", "Jay", 200), 2: ("Other", "Kay", 180), 3: ("Broken", "Kay", 100)})
        studio = FakeStudio(library)
        audio = {1: SONG_A, 2: SONG_B}

        def fake_fpcalc(exe, path=None, data=None, seconds=30):
            if data is not None:
                if data == b"3":
                    raise fpm.FingerprintError("could not read")
                return audio[int(data)], 0.0
            return noisy(SONG_A), 201.0  # the file on disk: song 1, re-encoded
        patcher = mock.patch.object(fpm, "run_fpcalc", side_effect=fake_fpcalc)
        patcher.start()
        self.addCleanup(patcher.stop)
        index = fpm.Fingerprints(studio, stream=lambda track_id: str(track_id).encode(), exe="fpcalc")
        return studio, index

    def wait(self, index):
        for _ in range(200):
            if not index.running:
                return
            time.sleep(0.01)
        self.fail("fingerprinting did not finish")

    def test_the_library_is_fingerprinted_once_and_kept(self):
        studio, index = self.make()
        index.start()
        self.wait(index)
        view = index.view()
        self.assertEqual((view["done"], view["failed"], view["library_total"]), (2, 1, 3))
        again = fpm.Fingerprints(studio, stream=lambda t: b"", exe="fpcalc")  # reloaded from fingerprints.json.gz
        self.assertEqual(set(again.state["tracks"]), {"1", "2"})

    def test_a_new_file_with_the_same_audio_is_matched_to_the_library_song(self):
        studio, index = self.make()
        index.start()
        self.wait(index)
        path = studio.home / "Song (Official Audio).mp3"
        path.write_bytes(b"audio")
        hit = index.match_file(path)
        self.assertEqual((hit["track_id"], hit["title"], hit["artist"], hit["album"]), ("1", "Song", "Jay", "Album"))
        self.assertTrue(index.is_cached(path))

    def test_a_song_of_a_very_different_length_is_not_compared(self):
        studio, index = self.make()
        index.start()
        self.wait(index)
        self.assertIsNone(index.match(noisy(SONG_A), 400))

    def test_nothing_to_compare_against_yet_means_no_work(self):
        _, index = self.make()
        self.assertIsNone(index.match_file("/nowhere.mp3"))

    def test_a_download_with_the_same_audio_gets_a_note_on_its_queue_row(self):
        studio, index = self.make()
        index.start()
        self.wait(index)
        updated = threading.Event()

        class Manager:
            def item(self, item_id):
                return {"id": item_id, "note": "Saved"}

            def update_item(self, item_id, **fields):
                self.fields = fields
                updated.set()
        studio.download_manager = Manager()
        path = studio.home / "x.mp3"
        path.write_bytes(b"audio")
        index.check_download(path, "j1")
        self.assertTrue(updated.wait(2))
        self.assertIn("same audio as “Song” by Jay", studio.download_manager.fields["note"])


@unittest.skipUnless(shutil.which("fpcalc") and shutil.which("ffmpeg"), "needs fpcalc and ffmpeg installed")
class RealToolTests(unittest.TestCase):
    def test_a_re_encoded_copy_matches_and_another_song_does_not(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)

        def song(seed, path, bitrate="192k"):
            rng = random.Random(seed)
            notes = [rng.choice([220, 262, 294, 330, 392, 440, 523]) for _ in range(60)]
            expr = "+".join(f"0.3*sin(2*PI*{f}*t)*between(t,{i * 0.5},{i * 0.5 + 0.5})" for i, f in enumerate(notes))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':d=30",
                            "-b:a", bitrate, str(path)], check=True)
        song(1, folder / "a.mp3")
        song(1, folder / "a-low.mp3", "96k")
        song(2, folder / "b.mp3")
        exe = shutil.which("fpcalc")
        a, b = fpm.run_fpcalc(exe, folder / "a.mp3")[0], fpm.run_fpcalc(exe, folder / "b.mp3")[0]
        low = fpm.run_fpcalc(exe, data=(folder / "a-low.mp3").read_bytes())[0]
        self.assertGreater(fpm.similarity(a, low), fpm.SAME)
        self.assertLess(fpm.similarity(a, b), fpm.SAME)
