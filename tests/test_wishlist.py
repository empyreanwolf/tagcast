"""Wishlist with retry (fork v17)."""

import time
import unittest

from tagcast.library import LibraryError
from tagcast.wishlist import Wishlist

from test_discography import LocalStudio as DiscographyStudio
from test_discography import wait_job
from test_galaxy import song as gd_song


class LocalStudio(DiscographyStudio):
    def __init__(self, soulseek=False):
        super().__init__(soulseek)
        self.wishlist = Wishlist(self, scheduler=False)


def wait(w):
    for _ in range(200):
        if not w.progress:
            return w.view()
        time.sleep(0.02)
    raise AssertionError("retry did not finish")


class WishlistTests(unittest.TestCase):
    def test_not_found_goes_on_the_wishlist_and_is_found_later(self):
        studio = LocalStudio()
        job = wait_job(studio.discography, studio.discography.get("Coldplay", albums=[{"title": "Viva", "status": "missing"}]))
        self.assertIn("on your wishlist", job["log"][0]["note"])
        items = studio.wishlist.view()["items"]
        self.assertEqual([(i["kind"], i["title"]) for i in items], [("album", "Viva")])
        # Asking again doesn't add it twice.
        wait_job(studio.discography, studio.discography.get("Coldplay", albums=[{"title": "viva", "status": "missing"}]))
        self.assertEqual(len(studio.wishlist.view()["items"]), 1)
        # Not due yet; then Galaxy gets it and the retry finds it.
        self.assertEqual(studio.wishlist.due(), [])
        self.assertEqual(studio.wishlist.due(time.time() + 4 * 86400), [items[0]["id"]])
        studio.gd.search_results[("netease_album", "Viva", 1)] = [gd_song(51, "Viva la Vida", "Coldplay", "Viva")]
        view = (studio.wishlist.retry([items[0]["id"]]), wait(studio.wishlist))[1]
        self.assertEqual(view["items"], [])
        self.assertEqual((view["found"][0]["title"], view["found"][0]["source"]), ("Viva", "Galaxy"))
        self.assertIn("Viva la Vida", {i["title"] for i in studio.download_manager.state["queue"]})

    def test_still_missing_counts_tries(self):
        studio = LocalStudio()
        item = studio.wishlist.add("song", "Coldplay", "Unreleased Demo")
        (studio.wishlist.retry([item["id"]]), wait(studio.wishlist))[1]
        again = studio.wishlist.view()["items"][0]
        self.assertEqual((again["tries"], again["note"]), (2, "Not found on Galaxy, Bandcamp or Soulseek (Galaxy · query “Coldplay Unreleased Demo”: not found)"))

    def test_something_you_have_now_waits_for_you(self):
        # Fork v95: a wish you meanwhile have is tagged, not searched, and leaves only when you confirm.
        studio = LocalStudio()
        studio.wishlist.add("song", "Coldplay", "Yellow")
        view = (studio.wishlist.retry(), wait(studio.wishlist))[1]  # only what is due: nothing yet
        self.assertEqual(len(view["items"]), 1)
        item_id = view["items"][0]["id"]
        view = (studio.wishlist.retry([item_id]), wait(studio.wishlist))[1]
        self.assertEqual(len(view["items"]), 1)
        self.assertTrue(view["items"][0]["owned"]["sure"])
        self.assertEqual(view["items"][0]["owned"]["you_have"][0]["title"], "Yellow")
        self.assertEqual(view["found"], [])
        # "Not the same, keep": the check leaves it alone from now on.
        view = studio.wishlist.keep([item_id])
        self.assertNotIn("owned", view["items"][0])
        self.assertEqual(studio.wishlist.check_library()["checked"], {"total": 0, "found": 0})

    def test_check_my_library(self):
        studio = LocalStudio()
        sure = studio.wishlist.add("song", "Coldplay feat. Somebody Else", "Yellow (feat. Somebody Else)")
        guest = studio.wishlist.add("song", "Somebody Else feat. Coldplay", "Yellow")
        missing = studio.wishlist.add("song", "Coldplay", "Unreleased Demo")
        view = studio.wishlist.check_library()
        self.assertEqual(view["checked"], {"total": 3, "found": 2})
        owned = {i["id"]: i.get("owned") for i in view["items"]}
        self.assertTrue(owned[sure["id"]]["sure"])
        self.assertFalse(owned[guest["id"]]["sure"])  # only a guest matches: you tick it yourself
        self.assertIsNone(owned[missing["id"]])
        self.assertNotIn(sure["id"], [i["id"] for i in studio.wishlist.remove([sure["id"]])["items"]])

    def test_owned_from(self):
        from tagcast.wishlist import owned_from
        item = {"kind": "album", "artist": "Coldplay", "title": "Viva"}
        self.assertIsNone(owned_from(item, {"status": "partly", "you_have": [{"where": "ibroadcast", "artist": "Coldplay"}]}))
        self.assertIsNone(owned_from(item, {"status": "have", "you_have": [{"where": "history", "artist": "Coldplay"}]}))
        edition = owned_from(item, {"status": "have", "other_edition": True, "you_have": [{"where": "ibroadcast", "title": "Viva (Deluxe)", "artist": "Coldplay", "edition": True}]})
        self.assertFalse(edition["sure"])
        close = owned_from({"kind": "song", "artist": "Coldplay", "title": "Yelow"}, {"status": "have", "you_have": [{"where": "ibroadcast", "title": "Yellow", "artist": "Coldplay", "close": True}]})
        self.assertFalse(close["sure"])

    def test_soulseek_failure_with_nothing_on_galaxy(self):
        studio = LocalStudio(soulseek=True)
        studio.soulseek.add([{"kind": "album", "artist": "Coldplay", "album": "Viva", "fallback": True}])
        item = studio.download_manager.state["queue"][0]
        studio.download_manager.update_item(item["id"], state="failed", note="Nobody on Soulseek shares it right now")
        studio.discography.fallback(studio.download_manager.item(item["id"]))
        self.assertEqual([i["title"] for i in studio.wishlist.view()["items"]], ["Viva"])
        self.assertIn("wishlist", studio.download_manager.item(item["id"])["note"])

    def test_settings_and_remove(self):
        studio = LocalStudio()
        with self.assertRaises(LibraryError):
            studio.wishlist.save_settings({"every_days": 31})
        self.assertEqual(studio.wishlist.save_settings({"every_days": 7})["every_days"], 7)
        item = studio.wishlist.add("album", "Coldplay", "Viva")
        self.assertEqual(studio.wishlist.remove([item["id"]])["items"], [])
        with self.assertRaises(LibraryError):
            studio.wishlist.add("album", "", "Viva")


class V91SearchAsTests(unittest.TestCase):
    """Fork v91: aliases, a credit suggestion and "Search as..." for a wish."""

    def make(self):
        studio = LocalStudio()
        studio.discography.aliases = lambda name: []
        studio.discography.credited_as = lambda name, title: ""
        return studio

    def test_an_alias_is_tried_after_your_own_spelling(self):
        studio = self.make()
        studio.discography.aliases = lambda name: ["Poetic Death"]
        item = studio.wishlist.add("album", "Jus Allah", "Shakespeare Science")
        studio.gd.search_results[("netease_album", "Shakespeare Science", 1)] = [gd_song(61, "Hamlet", "Poetic Death", "Shakespeare Science")]
        view = (studio.wishlist.retry([item["id"]]), wait(studio.wishlist))[1]
        self.assertEqual(view["items"], [])
        self.assertIn("searched as “Poetic Death”", view["found"][0]["note"])

    def test_a_credit_on_musicbrainz_becomes_a_one_click_suggestion(self):
        studio = self.make()
        studio.discography.credited_as = lambda name, title: "Poetic Death"
        item = studio.wishlist.add("album", "Jus Allah", "Shakespeare Science")
        view = (studio.wishlist.retry([item["id"]]), wait(studio.wishlist))[1]
        self.assertEqual(view["items"][0]["suggest"], "Poetic Death")
        self.assertIn("MusicBrainz credits an album with this title to “Poetic Death”", view["items"][0]["note"])

    def test_search_as_searches_now_under_the_new_names_and_drops_the_suggestion(self):
        studio = self.make()
        item = studio.wishlist.add("album", "Jus Allah", "Shakespeare Science")
        studio.wishlist._update(item["id"], suggest="Poetic Death")
        studio.gd.search_results[("netease_album", "Shakespeare Science", 1)] = [gd_song(61, "Hamlet", "Poetic Death", "Shakespeare Science")]
        view = (studio.wishlist.search_as({"id": item["id"], "artist": "Poetic Death", "title": "Shakespeare Science"}), wait(studio.wishlist))[1]
        self.assertEqual((view["items"], view["found"][0]["title"]), ([], "Shakespeare Science"))

    def test_search_as_needs_both_names_and_a_known_wish(self):
        studio = self.make()
        item = studio.wishlist.add("album", "Jus Allah", "Shakespeare Science")
        with self.assertRaises(LibraryError):
            studio.wishlist.search_as({"id": item["id"], "artist": "", "title": "x"})
        with self.assertRaises(LibraryError):
            studio.wishlist.search_as({"id": "nope", "artist": "a", "title": "b"})


if __name__ == "__main__":
    unittest.main()
