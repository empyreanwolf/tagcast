"""fork v109: settings backed up to one file, and restored with a preview first."""

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tagcast import settings_backup as sb


def home_with(files):
    home = Path(tempfile.mkdtemp())
    for name, data in files.items():
        (home / name).write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return home


class ExportTests(unittest.TestCase):
    def test_tokens_passwords_logs_and_caches_are_left_out(self):
        home = home_with({"config.json": {"client_id": "x", "lastfm_api_key": "k"}, "tokens.json": {"access": "SECRET"},
                          "soulseek.json": {"username": "me", "password": "SECRET"}, "discography_cache.json": {"big": 1},
                          "wishlist.json": {"items": [{"id": "1"}]}, "tagcast.log": "log"})
        with zipfile.ZipFile(io.BytesIO(sb.export(home, "v109"))) as z:
            names = sorted(z.namelist())
            text = "".join(z.read(n).decode() for n in names)
        self.assertEqual(names, ["manifest.json", "settings/config.json", "settings/soulseek.json", "settings/wishlist.json"])
        self.assertNotIn("SECRET", text)
        self.assertIn('"lastfm_api_key": "k"', text)  # settings you typed stay


class RestoreTests(unittest.TestCase):
    def backup(self, files):
        return base64.b64encode(sb.export(home_with(files), "v109")).decode()

    def test_preview_shows_what_changes_and_writes_nothing(self):
        data = self.backup({"wishlist.json": {"items": [{"id": "1"}, {"id": "2"}]}, "youtube.json": {"on": True},
                            "galaxy.json": {"source": "netease"}})
        home = home_with({"wishlist.json": {"items": [{"id": "1"}]}, "youtube.json": {"on": True}})
        out = sb.preview(home, data)
        rows = {r["file"]: r for r in out["files"]}
        self.assertEqual((rows["wishlist.json"]["status"], rows["youtube.json"]["status"], rows["galaxy.json"]["status"]),
                         ("changed", "same", "new"))
        self.assertEqual((rows["wishlist.json"]["now"], rows["wishlist.json"]["backup"]), ("1 wished for", "2 wished for"))
        self.assertEqual(out["changes"], 2)
        self.assertFalse((home / "galaxy.json").exists())

    def test_restore_keeps_the_passwords_you_have_and_saves_the_old_files(self):
        data = self.backup({"soulseek.json": {"username": "old-me", "password": "BACKED-UP"}})
        home = home_with({"soulseek.json": {"username": "me", "password": "mine"}})
        changes = sb.plan(home, data)
        result = sb.write(home, changes)
        self.assertEqual(json.loads((home / "soulseek.json").read_text()), {"username": "old-me", "password": "mine"})
        self.assertEqual(result["restored"], ["soulseek.json"])
        self.assertEqual(json.loads((home / result["kept_in"] / "soulseek.json").read_text())["username"], "me")

    def test_a_file_that_is_not_a_backup_is_refused(self):
        home = home_with({})
        for junk in ("not base64 !!", base64.b64encode(b"not a zip").decode()):
            with self.assertRaises(sb.BackupFileError):
                sb.preview(home, junk)
        other = io.BytesIO()
        with zipfile.ZipFile(other, "w") as z:
            z.writestr("manifest.json", json.dumps({"application": "something else"}))
        with self.assertRaises(sb.BackupFileError):
            sb.preview(home, base64.b64encode(other.getvalue()).decode())

    def test_odd_names_inside_the_zip_are_never_written(self):
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("manifest.json", json.dumps({"application": "Tagcast settings backup"}))
            z.writestr("settings/../../escape.json", "{}")
            z.writestr("settings/tokens.json", '{"a": 1}')
            z.writestr("settings/ok.json", '{"a": 1}')
        home = home_with({})
        self.assertEqual([n for n, _ in sb.plan(home, evil.getvalue())], ["ok.json"])
