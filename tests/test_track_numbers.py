import unittest

from tagcast.track_numbers import TrackNumberJob, discogs_positions, plan_numbers, positions_of


def listing(*titles, start=1):
    return {"source": "MusicBrainz", "title": "Album", "tracks": [(i, t) for i, t in enumerate(titles, start)]}


class PlanTests(unittest.TestCase):
    def tracks(self, *rows):
        return [{"id": i, "title": t, "number": n} for i, (t, n) in enumerate(rows, 1)]

    def test_exact_titles_get_their_position(self):
        _, wanted, missing = plan_numbers(self.tracks(("Two", 0), ("One", 0)), [listing("One", "Two", "Three")])
        self.assertEqual(wanted, {1: 2, 2: 1})
        self.assertEqual(missing, [])

    def test_unknown_title_is_reported_not_guessed(self):
        _, wanted, missing = plan_numbers(self.tracks(("One", 0), ("Mystery", 0)), [listing("One", "Two")])
        self.assertEqual(wanted, {1: 1})
        self.assertEqual(missing, ["Mystery"])

    def test_listing_that_contradicts_an_existing_number_is_skipped(self):
        found = plan_numbers(self.tracks(("One", 5), ("Two", 0)), [listing("One", "Two")])
        self.assertIsNone(found)

    def test_second_listing_used_when_first_conflicts(self):
        found = plan_numbers(self.tracks(("One", 3), ("Two", 0)), [listing("One", "Two"), listing("X", "Y", "One", "Two")])
        self.assertEqual(found[1], {2: 4})

    def test_duplicate_title_on_listing_is_ambiguous(self):
        self.assertIsNone(plan_numbers(self.tracks(("Intro", 0)), [listing("Intro", "Song", "Intro")]))

    def test_position_already_taken_is_not_reused(self):
        self.assertIsNone(plan_numbers(self.tracks(("One", 0), ("Other", 1)), [listing("One", "Two")]))

    def test_nothing_matches(self):
        self.assertIsNone(plan_numbers(self.tracks(("Zed", 0)), [listing("One")]))
        self.assertEqual(positions_of([], "x"), [])


class DiscogsTests(unittest.TestCase):
    def test_plain_and_disc_positions(self):
        rows = [{"position": "1", "title": "A"}, {"position": "2-2", "title": "B"}, {"position": "", "type_": "heading", "title": "H"}]
        self.assertEqual(discogs_positions(rows), [(1, "A"), (2, "B")])

    def test_vinyl_sides_are_counted(self):
        rows = [{"position": "A1", "title": "A"}, {"position": "A2", "title": "B"}, {"position": "B1", "title": "C"}]
        self.assertEqual(discogs_positions(rows), [(1, "A"), (2, "B"), (3, "C")])


class FakeStudio:
    def __init__(self):
        self.saved = []

    def save(self, changes):
        self.saved.append(changes)
        return {"results": [{"status": "sent"} for _ in changes]}


class JobTests(unittest.TestCase):
    def test_write_sends_track_edits(self):
        studio = FakeStudio()
        job = TrackNumberJob(studio)
        tracks = [{"id": 5, "title": "One", "number": 0}, {"id": 6, "title": "Two", "number": 0}]
        saved = job._write(9, tracks, {6: 2, 5: 1})
        self.assertEqual(saved, 2)
        edit = studio.saved[0][0]
        self.assertEqual(edit, {"kind": "track", "id": "5", "albumId": "9", "label": "One",
                                "fields": {"track": {"before": 0, "after": 1}}})

    def test_listings_fall_back_to_discogs(self):
        job = TrackNumberJob(FakeStudio())
        job._musicbrainz = lambda a, b: []
        job._discogs = lambda a, b: [listing("One")]
        self.assertEqual(len(job.listings("A", "B")), 1)


if __name__ == "__main__":
    unittest.main()
