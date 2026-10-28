import unittest

from test_library import raw
from tagcast.library import Library


def uploaded():
    data = raw()
    rows = data["library"]["tracks"]
    rows["map"].update({"uploaded_on": 8, "uploaded_time": 9, "length": 10})
    for key in rows:
        if key != "map":
            rows[key].extend([None] * (8 - len(rows[key])))
    stamps = {"100": ("2026-10-02", "09:00:00", 200), "101": ("2026-10-02", "09:05:00", 100),
              "102": ("2026-10-01", "12:00:00", 300), "103": ("2026-10-02", "10:00:00", 50),  # trashed
              "104": ("2026-10-01", "13:00:00", 400)}
    for key, (day, clock, length) in stamps.items():
        rows[key][8:11] = [day, clock, length]
    return Library(data)


class UploadViewTests(unittest.TestCase):
    def test_days_are_newest_first_with_counts_and_duration_and_ignore_trashed_tracks(self):
        days = uploaded().upload_days()
        self.assertEqual([d["date"] for d in days], ["2026-10-02", "2026-10-01"])
        first, second = days
        self.assertEqual((first["albums"], first["tracks"], first["seconds"]), (1, 2, 300))
        self.assertEqual((second["albums"], second["tracks"], second["seconds"]), (2, 2, 700))
        self.assertEqual(first["covers"], ["https://art.example/artwork/77-150"])

    def test_tracks_without_an_upload_date_are_not_listed(self):
        data = raw()
        self.assertEqual(Library(data).upload_days(), [])
        self.assertEqual(Library(data).upload_recent()["total"], 0)

    def test_a_day_lists_albums_and_tracks_and_flags_what_looks_wrong(self):
        day = uploaded().upload_day("2026-10-02")
        self.assertEqual((day["tracks"], len(day["albums"])), (2, 1))
        album = day["albums"][0]
        self.assertEqual((album["album"], album["album_artist"], album["year"]), ("Original", "Artist A", 1982))
        one, two = album["tracks"]
        self.assertEqual((one["title"], one["genres"]), ("Song One", []))
        self.assertIn("no_genre", one["problems"])
        self.assertIn("no_cover", one["problems"])
        self.assertEqual(two["problems"], [])
        self.assertEqual(day["problems"], 1)

    def test_odd_missing_and_combined_values_are_flagged(self):
        data = raw()
        rows = data["library"]["tracks"]
        rows["map"].update({"uploaded_on": 8, "uploaded_time": 9})
        rows["100"][4] = "Unknown"
        rows["101"][4] = "Pop;Rock"
        rows["102"][2] = 0
        for key in ("100", "101", "102"):
            rows[key][7] = rows[key][7] or 5
            rows[key].extend(["2026-10-02", "09:00:00"])
        library = Library(data)
        found = {t["title"]: t["problems"] for a in library.upload_day("2026-10-02")["albums"] for t in a["tracks"]}
        self.assertEqual(found["Song One"], ["odd_genre"])
        self.assertEqual(found["Song Two"], ["combined_genre"])
        self.assertEqual(found["Elsewhere"], [])  # artist 0 is "Various Artists": a real value

    def test_recent_is_one_flat_newest_first_list_with_a_limit(self):
        recent = uploaded().upload_recent(limit=3)
        self.assertEqual([t["title"] for t in recent["tracks"]], ["Song Two", "Song One", "Later"])
        self.assertEqual(recent["total"], 4)


if __name__ == "__main__":
    unittest.main()


class StudioMarkTests(unittest.TestCase):
    def studio(self):
        import threading
        from unittest import mock
        from tagcast.client import Studio
        studio = Studio.__new__(Studio)
        studio.lock = threading.RLock()
        studio.config = {}
        studio._write = mock.Mock()
        return studio

    def test_a_day_can_be_marked_checked_and_unmarked_and_is_saved(self):
        from tagcast.library import LibraryError  # noqa: F401
        from tagcast.client import ApiError
        studio = self.studio()
        studio.uploads_checked({"day": "2026-10-02", "checked": True})
        studio.uploads_checked({"day": "2026-10-01", "checked": True})
        self.assertEqual(studio.config["uploads_checked"], ["2026-10-01", "2026-10-02"])
        studio.uploads_checked({"day": "2026-10-01", "checked": False})
        self.assertEqual(studio.config["uploads_checked"], ["2026-10-02"])
        self.assertEqual(studio._write.call_count, 3)
        for bad in ({"day": "yesterday", "checked": True}, {"day": "2026-10-02", "checked": "yes"}):
            with self.assertRaises(ApiError):
                studio.uploads_checked(bad)
