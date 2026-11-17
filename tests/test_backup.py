import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from tagcast.backup import Backup


class Studio:
    """A minimal real-filesystem studio double: Backup zips ``self.home`` for real."""

    def __init__(self):
        self.home = Path(tempfile.mkdtemp(prefix="tagcast-home-"))

    def _read(self, name):
        try:
            return json.loads((self.home / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, name, data):
        (self.home / name).write_text(json.dumps(data), encoding="utf-8")


def wait_done(backup, seconds=5):
    end = time.monotonic() + seconds
    while backup.running and time.monotonic() < end:
        time.sleep(0.02)
    if backup.running:
        raise AssertionError("backup did not finish")


class BackupTests(unittest.TestCase):
    def test_no_folder_set_is_a_clear_error(self):
        backup = Backup(Studio(), scheduler=False)
        backup.run_now()  # may finish (it's a fast failure) before this call even returns
        wait_done(backup)
        view = backup.view()
        self.assertFalse(view["running"])
        self.assertEqual(view["last_ok"], False)
        self.assertIn("Set a Google Drive folder", view["last_error"])

    def test_an_unreachable_folder_is_a_clear_error(self):
        backup = Backup(Studio(), scheduler=False)
        backup.save_settings("/does/not/exist/at/all")
        backup.run_now()
        wait_done(backup)
        view = backup.view()
        self.assertFalse(view["last_ok"])
        self.assertIn("not a folder", view["last_error"])

    def test_zips_the_settings_folder_into_the_drive_folder(self):
        studio = Studio()
        (studio.home / "wishlist.json").write_text(json.dumps({"items": ["x"]}))
        backup = Backup(studio, scheduler=False)
        drive = tempfile.mkdtemp(prefix="tagcast-drive-")
        backup.save_settings(drive)
        backup.run_now()
        wait_done(backup)
        view = backup.view()
        self.assertTrue(view["last_ok"], view["last_error"])
        self.assertGreater(view["last_bytes"], 0)
        zip_path = Path(drive) / "tagcast-backup.zip"
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            self.assertIn("wishlist.json", names)
            # backup.json itself (the state from before this run) is in the folder too
            self.assertIn("backup.json", names)

    def test_a_second_run_overwrites_rather_than_keeping_both(self):
        studio = Studio()
        backup = Backup(studio, scheduler=False)
        drive = tempfile.mkdtemp(prefix="tagcast-drive-")
        backup.save_settings(drive)
        backup.run_now()
        wait_done(backup)
        backup.run_now()
        wait_done(backup)
        self.assertEqual(sorted(Path(drive).iterdir()), [Path(drive) / "tagcast-backup.zip"])

    def test_a_run_already_in_progress_is_not_started_twice(self):
        backup = Backup(Studio(), scheduler=False)
        backup.running = True  # simulate a run already under way
        result = backup.run_now()
        self.assertTrue(result["running"])
        backup.running = False  # cleanup so other assertions aren't confused
