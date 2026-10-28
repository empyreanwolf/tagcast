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
        self.assertEqual((again["tries"], again["note"]), (2, "Not found on Bandcamp, Galaxy or Soulseek"))

    def test_something_you_have_now_is_removed(self):
        studio = LocalStudio()
        studio.wishlist.add("song", "Coldplay", "Yellow")
        view = (studio.wishlist.retry(), wait(studio.wishlist))[1]  # only what is due: nothing yet
        self.assertEqual(len(view["items"]), 1)
        view = (studio.wishlist.retry([view["items"][0]["id"]]), wait(studio.wishlist))[1]
        self.assertEqual((view["items"], view["found"][0]["note"]), ([], "You have it now"))

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


if __name__ == "__main__":
    unittest.main()
