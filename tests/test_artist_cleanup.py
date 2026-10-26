"""Artist matching, merges, combined-name splits and featured-artist links."""

import copy
import time
import unittest

from tagcast import artist_match as match
from tagcast.artist_cleanup import ArtistCleanup, transform
from tagcast.library import Library, LibraryError

from test_cleanup import LocalStudio as CleanupStudio


class LocalStudio(CleanupStudio):
    def __init__(self, raw=None, store=None):
        super().__init__(raw, store)
        self.artist_cleanup = ArtistCleanup(self)


def data():
    artists = {1: "Ill Bill", 2: "ILL BILL", 3: "Coldplay/BTS", 4: "Coldplay", 5: "BTS",
               6: "Faye Wong 王菲", 7: "王菲", 8: "Vinnie Paz", 9: "Air", 10: "艾热 AIR",
               11: "Blu & Exile", 12: "Composer"}
    albums = {
        20: {"name": "Gangsta Rap", "artist_id": 2, "disc": 1, "tracks": [100, 101]},
        21: {"name": "Higher Power", "artist_id": 3, "disc": 1, "tracks": [102]},
        22: {"name": "Fable", "artist_id": 6, "disc": 1, "tracks": [103]},
        23: {"name": "Restless", "artist_id": 7, "disc": 1, "tracks": [104]},
        24: {"name": "Moon Safari", "artist_id": 9, "disc": 1, "tracks": [105]},
        25: {"name": "Hip Hop", "artist_id": 10, "disc": 1, "tracks": [106]},
        26: {"name": "Below the Heavens", "artist_id": 11, "disc": 1, "tracks": [107]},
    }
    tracks = {
        100: {"title": "Society (feat. Vinnie Paz & New Guy)", "artist_id": 1, "album_id": 20, "genre": "Hip Hop",
              "artists_additional": [{"artist_id": 12, "type": "composer", "phrase": None}]},
        101: {"title": "Chainsaw", "artist_id": 2, "album_id": 20, "genre": "Hip Hop",
              "artists_additional": [{"artist_id": 1, "type": "artist", "phrase": None}]},
        102: {"title": "Higher Power", "artist_id": 4, "album_id": 21, "genre": "Pop"},
        103: {"title": "Fable", "artist_id": 6, "album_id": 22, "genre": "Mandopop"},
        104: {"title": "Restless", "artist_id": 7, "album_id": 23, "genre": "Mandopop"},
        105: {"title": "La Femme d'Argent", "artist_id": 9, "album_id": 24, "genre": "Electronic"},
        106: {"title": "Song (feat. Blu & Exile, 王菲)", "artist_id": 10, "album_id": 25, "genre": "Mandopop"},
        107: {"title": "Simple Plan", "artist_id": 11, "album_id": 26, "genre": "Hip Hop"},
    }
    return {"library": {
        "artists": {str(i): {"name": n} for i, n in artists.items()},
        "albums": {str(i): a for i, a in albums.items()},
        "tracks": {str(i): t for i, t in tracks.items()}}}


def finished(studio):
    for _ in range(500):
        if not studio.artist_cleanup.running:
            return studio.artist_cleanup.view(report=True)
        time.sleep(.01)
    raise AssertionError("Artist cleanup did not finish")


def credits(record):
    return [(e["artist_id"], e.get("type")) for e in record.get("artists_additional") or []]


class MatchTests(unittest.TestCase):
    def test_clean_and_keys(self):
        self.assertEqual(match.clean("Adventure Club "), "Adventure Club")
        self.assertEqual(match.clean("ＧＭ​仙"), "GM仙")
        self.assertEqual(match.match_key("陳奕迅"), match.match_key("陈奕迅"))
        self.assertEqual(match.match_key("ILL BILL"), match.match_key("Ill Bill"))

    def test_bilingual_parts(self):
        self.assertEqual(match.bilingual("陳冠希Edison Chen"), ("Edison Chen", "陳冠希"))
        self.assertEqual(set(match.bilingual("翁杰_Winjay")), {"翁杰", "Winjay"})
        self.assertEqual(set(match.bilingual("Wiz_H张子豪")), {"Wiz_H", "张子豪"})
        self.assertEqual(set(match.bilingual("派克特（PACT）")), {"派克特", "PACT"})
        self.assertIsNone(match.bilingual("Gm仙"))  # one character: one name
        self.assertIsNone(match.bilingual("伍佰 And China Blue"))
        self.assertIsNone(match.bilingual("Coldplay/BTS"))

    def test_merge_groups_reformat_only_when_parts_are_separable(self):
        artists = {1: "Edison Chen 陳冠希", 2: "陳冠希Edison Chen", 3: "AR刘夫阳", 4: "AR 刘夫阳",
                   5: "Faye Wong 王菲", 6: "王菲", 7: "Air", 8: "艾热 AIR", 9: "艾热AIR"}
        usage = {1: 14, 2: 4, 3: 103, 4: 1, 5: 900, 6: 500, 7: 1, 8: 121, 9: 10}
        genres = {5: match.Counter({"Mandopop": 5}), 6: match.Counter({"Mandopop": 3}),
                  7: match.Counter({"Electronic": 1}), 8: match.Counter({"Chinese Hip Hop": 9})}
        groups = {g["target_name"]: g for g in match.merge_groups(artists, usage, genres)}
        self.assertEqual(groups["Edison Chen 陳冠希"]["target"], {"id": 1})
        self.assertEqual(groups["AR刘夫阳"]["target"], {"id": 3})  # spacing alone: keep his spelling
        faye = groups["Faye Wong 王菲"]
        self.assertEqual([m["ticked"] for m in faye["members"]], [True, True])
        air = groups["艾热 AIR"]
        self.assertEqual({m["name"]: m["ticked"] for m in air["members"]},
                         {"艾热 AIR": True, "艾热AIR": True, "Air": False})

    def test_split_parts_and_suggestions(self):
        self.assertEqual(match.split_parts("Coldplay/BTS/SUGA")[1], ["Coldplay", "BTS", "SUGA"])
        self.assertEqual(match.split_parts("Bastard Feather")[1], ["Bastard Feather"])
        artists = {1: "Coldplay/BTS", 2: "Simon & Garfunkel", 3: "Tyler, The Creator", 4: "Coldplay"}
        items = {s["name"]: s for s in match.split_suggestions(artists, {1: 2, 2: 5, 3: 9, 4: 3},
                                                               {match.match_key(n) for n in artists.values()})}
        self.assertTrue(items["Coldplay/BTS"]["ticked"])
        self.assertFalse(items["Simon & Garfunkel"]["ticked"])
        self.assertIn("one act", items["Simon & Garfunkel"]["note"])
        self.assertNotIn("Tyler, The Creator", items)

    def test_featured_titles(self):
        self.assertEqual(match.featured("Pumpkin Seeds (feat. Lupe Fiasco)"), (["Lupe Fiasco"], None))
        self.assertEqual(match.featured("Ruck (feat.Epcot)"), (["Epcot"], None))
        self.assertEqual(match.featured("創世紀 -FT 李毅杰Pissy"), (["李毅杰Pissy"], None))
        self.assertEqual(match.featured("Only Love (with Brijean)"), (["Brijean"], None))
        self.assertEqual(match.featured("Lunch With Blockhead"), ([], None))
        self.assertEqual(match.featured("Left Behind"), ([], None))
        self.assertEqual(match.featured("Friendship ft"), ([], "no name after feat/ft"))
        self.assertEqual(match.featured("Song (feat. Blu & Exile, Mos Def)", ["Blu & Exile"]),
                         (["Blu & Exile", "Mos Def"], None))
        self.assertEqual(match.featured("Song (feat. Blueprint & Rob Sonic)"), (["Blueprint", "Rob Sonic"], None))

    def test_transform_keeps_roles_and_drops_duplicates(self):
        before = {"artist_id": 3, "artists_additional": [{"artist_id": 4, "type": "artist", "phrase": None},
                                                        {"artist_id": 9, "type": "composer", "phrase": "by"}]}
        after = transform(before, {3: [{"id": 4}, {"id": 5}]})
        self.assertEqual(after["primary"], {"id": 4})
        self.assertEqual(after["credits"], [{"id": 5, "type": "artist", "phrase": None},
                                            {"id": 9, "type": "composer", "phrase": "by"}])


class ArtistCleanupTests(unittest.TestCase):
    def test_suggestions_and_search(self):
        studio = LocalStudio(data())
        groups = {g["target_name"]: g for g in studio.artist_cleanup.suggestions("merge")["groups"]}
        self.assertEqual(groups["Ill Bill"]["kind"], "same")
        self.assertIn("Faye Wong 王菲", groups)
        self.assertFalse(next(m for m in groups["艾热 AIR"]["members"] if m["name"] == "Air")["ticked"])
        split = studio.artist_cleanup.suggestions("split")["items"]
        coldplay = next(s for s in split if s["name"] == "Coldplay/BTS")
        self.assertEqual([r["existing"] for r in coldplay["resolved"]], [True, True])
        self.assertEqual([a["name"] for a in studio.artist_cleanup.search("ill")["artists"]][:2], ["Ill Bill", "ILL BILL"])
        self.assertEqual(studio.writes, [])

    def test_merge_repoints_tracks_albums_and_credits(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}}])
        self.assertEqual(preview["state"], "preview")
        self.assertEqual(studio.writes, [])
        kinds = sorted((i["kind"], i["id"]) for i in studio.artist_cleanup.view(report=True)["items"])
        self.assertEqual(kinds, [("album", 20), ("track", 101)])
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        self.assertEqual(report["counts"], {"confirmed": 2})
        library = Library(studio.raw)
        self.assertEqual(library.albums[20].get("artist_id"), 1)
        self.assertEqual(library.tracks[101].get("artist_id"), 1)
        self.assertEqual(credits(studio.raw["library"]["tracks"]["101"]), [])  # credit equal to primary dropped
        self.assertEqual(credits(studio.raw["library"]["tracks"]["100"]), [(12, "composer")])  # untouched
        self.assertEqual(studio.creations, [])

    def test_merge_into_new_name_creates_it_once(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [10], "target": {"name": "AIR 艾热"}}])
        self.assertEqual(preview["new_artists"], ["AIR 艾热"])
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        self.assertEqual(studio.creations, ["AIR 艾热"])
        new_id = report["artists"]["AIR 艾热"]
        self.assertEqual(Library(studio.raw).albums[25].get("artist_id"), new_id)

    def test_merge_rejects_invalid_groups(self):
        studio = LocalStudio(data())
        with self.assertRaises(LibraryError):
            studio.artist_cleanup.prepare("merge", groups=[{"members": [1], "target": {"id": 1}}])
        with self.assertRaises(LibraryError):
            studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 8}}])
        with self.assertRaises(LibraryError):
            studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}},
                                                           {"members": [2, 8], "target": {"id": 8}}])

    def test_split_combined_album_artist(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("split", ids=[3])
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        album = studio.raw["library"]["albums"]["21"]
        self.assertEqual(album["artist_id"], 4)
        self.assertEqual(credits(album), [(5, "artist")])
        self.assertEqual(studio.creations, [])

    def test_feat_links_existing_and_new_guests(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("feat")
        self.assertEqual(preview["new_artists"], ["New Guy"])
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        new_id = report["artists"]["New Guy"]
        self.assertEqual(credits(studio.raw["library"]["tracks"]["100"]),
                         [(12, "composer"), (8, "artist"), (new_id, "artist")])
        # A kept duo; "王菲" is still an entry in use (merges run first in practice).
        self.assertEqual(credits(studio.raw["library"]["tracks"]["106"]), [(11, "artist"), (7, "artist")])
        self.assertEqual(studio.raw["library"]["tracks"]["100"]["title"], "Society (feat. Vinnie Paz & New Guy)")

    def test_feat_guest_resolves_to_bilingual_entry_after_merge(self):
        raw = data()
        raw["library"]["albums"]["23"]["artist_id"] = 6
        raw["library"]["tracks"]["104"]["artist_id"] = 6  # "王菲" no longer in use
        studio = LocalStudio(raw)
        preview = studio.artist_cleanup.prepare("feat")
        studio.artist_cleanup.start(preview["id"], [])
        finished(studio)
        self.assertEqual(credits(studio.raw["library"]["tracks"]["106"]), [(11, "artist"), (6, "artist")])

    def test_feat_guest_prefers_artist_already_on_the_track_and_never_a_merged_away_entry(self):
        raw = data()
        raw["library"]["artists"].update({"30": {"name": "BooM 黄旭"}, "31": {"name": "黃旭"}})
        raw["library"]["albums"]["27"] = {"name": "Cypher", "artist_id": 8, "disc": 1, "tracks": [108, 109]}
        raw["library"]["tracks"]["108"] = {"title": "Song ft 黃旭", "artist_id": 8, "album_id": 27, "genre": "",
                                           "artists_additional": [{"artist_id": 30, "type": "artist", "phrase": None}]}
        raw["library"]["tracks"]["109"] = {"title": "Other ft 黃旭", "artist_id": 8, "album_id": 27, "genre": "Rock"}
        studio = LocalStudio(raw)
        preview = studio.artist_cleanup.prepare("feat")
        items = {i["id"]: i for i in studio.artist_cleanup.view(report=True)["items"]}
        self.assertNotIn(108, items)  # already credited through "BooM 黄旭"
        self.assertEqual(items[109]["after"]["credits"], [{"id": 30, "type": "artist", "phrase": None}])
        self.assertNotIn("黃旭", preview["new_artists"])

    def test_albums_joined_by_ibroadcast_during_a_merge_are_followed_not_conflicts(self):
        raw = data()
        # The same album uploaded twice, under two spellings of the artist.
        raw["library"]["albums"]["28"] = {"name": "Gangsta Rap", "artist_id": 1, "disc": 1, "tracks": [110]}
        raw["library"]["tracks"]["110"] = {"title": "Chainsaw", "artist_id": 1, "album_id": 28, "genre": "Hip Hop"}
        studio = LocalStudio(raw)

        def join_albums():  # what iBroadcast does once both copies share title and artist
            albums = studio.raw["library"]["albums"]
            if albums["20"]["artist_id"] == albums["28"]["artist_id"] and albums["28"]["tracks"]:
                albums["20"]["tracks"] += albums["28"]["tracks"]
                for track_id in albums["28"]["tracks"]:
                    studio.raw["library"]["tracks"][str(track_id)]["album_id"] = 20
                albums["28"]["tracks"] = []
        studio.on_write = join_albums
        # Write tracks of album 20 in an earlier batch than album 28's own record.
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}}])
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        self.assertNotIn("conflict", report["counts"])
        library = Library(studio.raw)
        self.assertEqual(library.albums[20].get("artist_id"), 1)
        self.assertEqual(library.tracks[101].get("artist_id"), 1)

    def test_resume_after_an_old_conflict_rechecks_and_continues(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}}])
        studio.artist_cleanup.task["items"][0]["status"] = "conflict"
        studio.artist_cleanup.task["state"] = "error"
        studio.artist_cleanup.start(preview["id"])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        self.assertEqual(report["counts"], {"confirmed": 2})

    def test_conflict_since_preview_stops_without_writing(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}}])
        studio.raw["library"]["tracks"]["101"]["artist_id"] = 8
        studio.artist_cleanup.start(preview["id"], [])
        report = finished(studio)
        self.assertEqual(report["state"], "error")
        self.assertIn("changed since the preview", report["error"])
        self.assertEqual(studio.writes, [])

    def test_excluded_groups_are_not_written_and_report_survives_restart(self):
        studio = LocalStudio(data())
        preview = studio.artist_cleanup.prepare("merge", groups=[{"members": [1, 2], "target": {"id": 1}},
                                                                 {"members": [6, 7], "target": {"id": 6}}])
        faye = next(g["id"] for g in preview["groups"] if "王菲" in g["title"])
        studio.artist_cleanup.start(preview["id"], [faye])
        report = finished(studio)
        self.assertEqual(report["state"], "done", report["error"])
        self.assertEqual(studio.raw["library"]["albums"]["23"]["artist_id"], 7)
        restarted = LocalStudio(copy.deepcopy(studio.raw), studio.store)
        self.assertEqual(restarted.artist_cleanup.view(report=True)["items"], report["items"])


if __name__ == "__main__":
    unittest.main()
