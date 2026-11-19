"""Fork v110: looser title matching, wish release types, split singles, guest credits, search."""

import unittest

from test_discography import LocalStudio as DiscographyStudio

from tagcast.downloads import (
    HistoryIndex,
    LibraryIndex,
    album_key,
    artist_keys,
    check_item,
    close_title,
    fold_accents,
    loose_key,
    song_key,
)
from tagcast.wishlist import Wishlist, split_titles


class LocalStudio(DiscographyStudio):
    def __init__(self, soulseek=False):
        super().__init__(soulseek)
        self.wishlist = Wishlist(self, scheduler=False)


class LooseKeyTests(unittest.TestCase):
    def test_numbers_as_digits_or_words_are_the_same(self):
        self.assertEqual(album_key("Thirteen"), album_key("13"))
        self.assertEqual(song_key("Part Two"), song_key("Pt. 2"))
        self.assertEqual(song_key("Volume 2"), song_key("Vol. 2"))

    def test_accents_punctuation_and_and_do_not_count(self):
        self.assertEqual(song_key("Björk's Song!"), song_key("Bjork's Song"))
        self.assertEqual(album_key("Black & Blue"), album_key("Black and Blue"))
        self.assertEqual(song_key("Don't Stop"), song_key("Dont Stop"))

    def test_chinese_and_kana_are_left_alone(self):
        self.assertEqual(fold_accents("がぎ"), "がぎ")
        self.assertNotEqual(song_key("か"), song_key("が"))

    def test_a_title_made_only_of_symbols_keeps_a_key(self):
        self.assertTrue(song_key("???"))
        self.assertNotEqual(song_key("???"), song_key("!!!"))

    def test_roman_numerals_only_count_as_a_close_match(self):
        self.assertNotEqual(song_key("Part II"), song_key("Part 2"))
        self.assertTrue(close_title("Part II", "Part 2"))
        self.assertNotEqual(loose_key("Vi"), loose_key("6"))

    def test_artist_keys_ignore_accents_and_a_leading_the(self):
        self.assertTrue(artist_keys("Björk") & artist_keys("Bjork"))
        self.assertTrue(artist_keys("The Weeknd") & artist_keys("Weeknd"))

    def test_the_library_check_finds_a_single_under_another_spelling_of_its_number(self):
        index = LibraryIndex.__new__(LibraryIndex)
        entry = {"title": "13", "artist": "Blue Sky Black Death", "keys": artist_keys("Blue Sky Black Death"),
                 "named": True, "tracks": 1, "album_id": 5}
        index.songs, index.album_bases, index.albums_loose = {}, {}, {}
        index.albums = {album_key(entry["title"]): [entry]}
        result = check_item({"kind": "album", "title": "Thirteen", "artist": "Blue Sky Black Death"}, index, HistoryIndex([]))
        self.assertEqual((result["status"], result["you_have"][0]["title"]), ("have", "13"))


class WishTypeTests(unittest.TestCase):
    def test_a_wish_keeps_its_release_type_and_a_featured_guest(self):
        w = LocalStudio().wishlist
        a = w.add("album", "Abstract Rude", "Cali Soul", release="single")
        b = w.add("ep", "X", "Some EP")
        c = w.add("song", "X", "Some Song")
        d = w.add("album", "Madecipha & 5:am", "Completely Disrespectful", release="album", featured="Jus Allah")
        e = w.add("album", "Y", "No Type")
        self.assertEqual([(i["kind"], i.get("release")) for i in (a, b, c, d, e)],
                         [("album", "single"), ("album", "ep"), ("song", "song"), ("album", "album"), ("album", None)])
        self.assertEqual(d["featured"], "Jus Allah")

    def test_old_wish_keys_follow_the_looser_matching_on_load(self):
        studio = LocalStudio()
        studio.wishlist.add("album", "A", "Thirteen")
        studio.wishlist.state["items"][0]["key"] = "album|a|thirteen"  # a key written before v110
        studio.wishlist.state["items"].append(dict(studio.wishlist.state["items"][0], id="dup", title="13"))
        studio._write("wishlist.json", studio.wishlist.state)
        again = Wishlist(studio, scheduler=False)
        self.assertEqual(len(again.state["items"]), 1)  # "13" and "Thirteen" are one wish now

    def test_split_titles(self):
        self.assertEqual(split_titles("The Ocean / No Image"), ["The Ocean", "No Image"])
        self.assertEqual(split_titles("AC/DC"), [])
        self.assertEqual(split_titles("Plain"), [])


class SplitSingleTests(unittest.TestCase):
    def _studio(self, have):
        studio = LocalStudio()

        def check(items=None, **_):
            out = []
            for i in items:
                if i["kind"] == "song" and i["title"] in have:
                    out.append({**i, "status": "have", "you_have": [{"where": "ibroadcast", "title": i["title"],
                                "artist": i["artist"], "album": "The Holocaust"}]})
                else:
                    out.append({**i, "status": "new", "you_have": []})
            return {"results": out}
        studio.download_manager.check = check
        studio.discography.album_credit = lambda *a: ""
        return studio

    def test_half_of_a_split_single_is_reported_and_can_be_narrowed_to_the_missing_song(self):
        studio = self._studio({"No Image"})
        w = studio.wishlist
        item = w.add("album", "Blue Sky Black Death", "The Ocean / No Image")
        view = w.check_library()
        row = view["items"][0]
        self.assertFalse(row.get("owned"))
        self.assertEqual((row["partial"]["total"], row["partial"]["missing"], row["release"]), (2, ["The Ocean"], "single"))
        view = w.split([item["id"]])
        self.assertEqual([(i["kind"], i["title"], i["album"]) for i in view["items"]],
                         [("song", "The Ocean", "The Ocean / No Image")])

    def test_a_split_single_you_fully_have_waits_for_your_confirmation(self):
        studio = self._studio({"No Image", "The Ocean"})
        item = studio.wishlist.add("album", "Blue Sky Black Death", "The Ocean / No Image")
        row = studio.wishlist.check_library()["items"][0]
        self.assertTrue(row["owned"]["all_songs"])
        self.assertEqual(len(studio.wishlist.view()["items"]), 1)  # still listed until you remove it
        self.assertEqual(row["id"], item["id"])

    def test_discogs_tracklist_is_used_when_the_title_is_not_split(self):
        studio = self._studio({"Hello", "World"})
        studio.discography.tracklist = lambda artist, title: (["Hello", "World", "Again"], "single")
        studio.wishlist.add("album", "Someone", "Big Single")
        row = studio.wishlist.check_library()["items"][0]
        self.assertEqual((row["partial"]["missing"], row["release"]), (["Again"], "single"))

    def test_a_song_only_in_the_download_history_is_a_maybe(self):
        studio = LocalStudio()
        studio.download_manager.check = lambda items=None, **_: {"results": [
            {**items[0], "status": "downloaded", "you_have": [{"where": "history", "title": "Walk With Me", "artist": "BSBD",
                                                                "album": "Lost"}]}]}
        studio.wishlist.add("song", "BSBD", "Walk With Me")
        row = studio.wishlist.check_library()["items"][0]
        self.assertEqual((row["owned"]["sure"], row["owned"]["you_have"][0]["downloaded"]), (False, True))


class GuestCreditTests(unittest.TestCase):
    def test_album_credit_names_the_real_main_artist(self):
        studio = LocalStudio()
        studio.http.answers["/search/album"] = {"data": [{"title": "Completely Disrespectful", "artist": {"name": "Madecipha & 5:am"}}]}
        self.assertEqual(studio.discography.album_credit("Jus Allah", "Completely Disrespectful"), "Madecipha & 5:am")

    def test_album_credit_is_empty_when_the_artist_is_one_of_the_credited_names(self):
        studio = LocalStudio()
        studio.http.answers["/search/album"] = {"data": [{"title": "Duo Album", "artist": {"name": "Braille & Symbolyc One"}}]}
        self.assertEqual(studio.discography.album_credit("Braille", "Duo Album"), "")

    def test_a_wish_for_a_guest_release_is_saved_under_the_main_artist(self):
        studio = LocalStudio()
        from test_discography import wait_job
        job = wait_job(studio.discography, studio.discography.get("Coldplay", albums=[
            {"title": "Viva", "status": "missing", "appearance": True, "full_album": True,
             "main_artist": "Madecipha & 5:am", "type": "compile"}]))
        item = studio.wishlist.view()["items"][0]
        self.assertEqual((item["artist"], item.get("featured"), item.get("release")), ("Madecipha & 5:am", "Coldplay", "album"))
        self.assertIn("on your wishlist", job["log"][0]["note"])


class SearchTests(unittest.TestCase):
    def test_search_finds_library_albums_and_links_the_matching_wish(self):
        studio = LocalStudio()
        studio.wishlist.add("album", "Coldplay", "Parachutes")
        found = studio.wishlist.search("parachutes")
        self.assertTrue(any(a["title"] == "Parachutes" and a["wishes"] for a in found["albums"]))
        self.assertEqual([w["title"] for w in found["wished"]], ["Parachutes"])

    def test_search_ignores_accents_and_punctuation(self):
        studio = LocalStudio()
        self.assertTrue(studio.download_manager.library_search("parachùtes!")["albums"])
        self.assertEqual(studio.download_manager.library_search("")["songs"], [])


if __name__ == "__main__":
    unittest.main()
