"""v73: one-click update, PC sync merging, debug report."""
import io
import json
import tempfile
import unittest
import zipfile
import zipfile as _z
from pathlib import Path

from tagcast import debug_report, selfupdate, settings_sync as ss


def make_zip(version, files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("tagcast-main/src/tagcast/static/artist-cleanup.js", f'const artistCleanupVersion = "fork v{version}";')
        for name, text in files.items():
            z.writestr(f"tagcast-main/{name}", text)
    return out.getvalue()


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src/tagcast/static").mkdir(parents=True)
        (self.root / "pyproject.toml").write_text("x")
        (self.root / ".tagcast").mkdir()
        (self.root / ".tagcast/keep.txt").write_text("mine")
        (self.root / "src/tagcast/old.py").write_text("old")

    def test_newer_zip_replaces_files_and_keeps_everything_else(self):
        data = make_zip(80, {"src/tagcast/new.py": "new", ".tagcast/keep.txt": "hacked", ".venv/x.py": "no", "../evil.py": "no"})
        number, written = selfupdate.apply_zip(data, self.root, current=79)
        self.assertEqual(number, 80)
        self.assertEqual((self.root / "src/tagcast/new.py").read_text(), "new")
        self.assertEqual((self.root / ".tagcast/keep.txt").read_text(), "mine")
        self.assertFalse((self.root / ".venv").exists())
        self.assertEqual((self.root / "src/tagcast/old.py").read_text(), "old")  # nothing is deleted
        self.assertFalse(list(self.root.rglob("*.new")))
        self.assertFalse((self.root.parent / "evil.py").exists())

    def test_same_or_older_version_changes_nothing(self):
        with self.assertRaisesRegex(selfupdate.UpdateError, "up to date"):
            selfupdate.apply_zip(make_zip(79, {"src/tagcast/new.py": "n"}), self.root, current=79)
        self.assertFalse((self.root / "src/tagcast/new.py").exists())

    def test_a_zip_that_is_not_this_fork_is_refused(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("other/readme.txt", "hi")
        with self.assertRaisesRegex(selfupdate.UpdateError, "does not look like"):
            selfupdate.apply_zip(out.getvalue(), self.root, current=1)
        with self.assertRaisesRegex(selfupdate.UpdateError, "not a valid zip"):
            selfupdate.apply_zip(b"junk", self.root, current=1)

    def test_a_folder_without_sources_is_not_updated(self):
        with self.assertRaisesRegex(selfupdate.UpdateError, "source folder"):
            selfupdate.apply_zip(make_zip(80, {}), Path(tempfile.mkdtemp()), current=1)


class SyncMergeTests(unittest.TestCase):
    def test_pairs_union_ignores_order(self):
        self.assertEqual(ss.merge_pairs([["A", "B"]], [["B", "A"], ["C", "D"]]), [["A", "B"], ["C", "D"]])

    def test_wishlist_adds_new_items_once(self):
        mine = {"items": [{"id": "1", "kind": "album", "artist": "Air", "title": "Moon Safari"}], "found": []}
        theirs = {"items": [{"id": "9", "kind": "album", "artist": "AIR", "title": "moon safari"}, {"id": "8", "kind": "song", "artist": "X", "title": "Y"}]}
        items, _ = ss.merge_wishlist(mine, theirs)
        self.assertEqual([i["id"] for i in items], ["1", "8"])

    def test_dict_and_list_merge_keep_this_pcs_values_first(self):
        self.assertEqual(ss.merge_dict({"a": 1}, {"a": 2, "b": 3}), {"a": 1, "b": 3})
        self.assertEqual(ss.merge_list(["Chinese Hip Hop"], ["chinese hip hop", "Rock"]), ["Chinese Hip Hop", "Rock"])


class DebugReportTests(unittest.TestCase):
    def test_secrets_are_hidden(self):
        out = debug_report.scrub({"discogs_token": "abc", "name": "x", "nested": {"Password": "p", "ok": 1}})
        self.assertEqual(out, {"discogs_token": "<hidden>", "name": "x", "nested": {"Password": "<hidden>", "ok": 1}})

    def test_report_is_a_zip_without_token_files(self):
        home = Path(tempfile.mkdtemp())
        (home / "tokens.json").write_text('{"access_token": "SECRET"}')
        (home / "tagcast.log").write_text("WARNING something")

        class Studio:
            def __init__(self):
                self.home, self.activity = home, None

            def _read(self, name):
                return {"discogs_token": "SECRET", "x": 1} if name == "config.json" else None
        data = debug_report.build(Studio())
        with _z.ZipFile(io.BytesIO(data)) as z:
            text = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
            self.assertIn("WARNING something", text)
            self.assertNotIn("SECRET", text)
            self.assertIn("info.json", z.namelist())


if __name__ == "__main__":
    unittest.main()
