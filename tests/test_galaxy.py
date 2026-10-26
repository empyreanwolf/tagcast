"""Galaxy (fork v11): GD Studio search, exact albums, queue, downloads, tags and artist cards."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from tagcast import galaxy as gx
from tagcast.artist_cards import ArtistCards
from tagcast.galaxy import Galaxy, GalaxyError, QuotaLimiter

from test_downloads import LocalStudio as DownloadsStudio

MP3 = (Path(__file__).parent / "fixtures" / "galaxy-silence.mp3").read_bytes()
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def song(i, title, artist, album, source="netease"):
    return {"id": str(i), "name": title, "artist": [artist], "album": album, "pic_id": "109951", "source": source}


class FakeResponse:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def iter_content(self, size):
        for n in range(0, len(self.content), size):
            yield self.content[n:n + size]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGD:
    """GD Studio API answers by request type."""

    def __init__(self):
        self.calls, self.urls = [], {}
        self.search_results = {}

    def __call__(self, params, timeout):
        self.calls.append(dict(params))
        kind = params["types"]
        if kind == "search":
            key = (params["source"], params["name"], params.get("pages", 1))
            return FakeResponse(body=self.search_results.get(key, []))
        if kind == "url":
            url = self.urls.get((params["source"], params["id"], params["br"]), "")
            return FakeResponse(body={"url": url, "br": params["br"], "size": 0})
        if kind == "pic":
            return FakeResponse(body={"url": "https://img.example/cover.jpg"})
        return FakeResponse(400, {"error": "?"})


def fetch(url, **kw):
    if url.endswith(".mp3"):
        return FakeResponse(content=MP3, headers={"Content-Length": str(len(MP3))})
    if "126.net" in url or "img.example" in url:
        return FakeResponse(content=JPEG)
    return FakeResponse(404)


class LocalStudio(DownloadsStudio):
    def __init__(self):
        super().__init__()
        self.folder = Path(tempfile.mkdtemp())
        self.download_manager.set_folder(str(self.folder))
        self.gd = FakeGD()
        limiter = QuotaLimiter(interval=0)
        self.galaxy = Galaxy(self, api=gx.GDApi(limiter, self.gd), fetch=fetch, track_delay=0)
        self.galaxy.limiter = limiter


def wait_idle(studio, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        states = [i.get("state") for i in studio.download_manager.state["queue"]]
        if "queued" not in states and "active" not in states:
            return
        time.sleep(0.05)
    raise AssertionError(f"queue did not finish: {studio.download_manager.state['queue']}")


class HelperTests(unittest.TestCase):
    def test_paths_and_names(self):
        self.assertEqual(gx.build_path("D:/M", "AC/DC", "Back: in Black", 1, "Hells Bells?").as_posix(),
                         "D:/M/ACDC/Back in Black/01 - Hells Bells.mp3")
        self.assertEqual(gx.safe_name("CON"), "_CON")
        self.assertEqual(gx.normalize_track({"id": 1, "name": "A &amp; B", "artist": "X / Y、Z"}, "joox")["artists"],
                         ["X", "Y", "Z"])

    def test_format_detection(self):
        path = Path(tempfile.mkdtemp()) / "a.part"
        path.write_bytes(MP3)
        self.assertEqual(gx.detect_audio_format(path), "mp3")
        path.write_bytes(b"fLaC" + b"0" * 20)
        self.assertEqual(gx.detect_audio_format(path), "flac")
        path.write_bytes(b"<html>")
        with self.assertRaises(ValueError):
            gx.detect_audio_format(path)

    def test_quota_holds_the_46th_call(self):
        now = [0.0]
        limiter = QuotaLimiter(interval=0, max_calls=3, period=300, reserve=1, clock=lambda: now[0])
        slept = []

        def fake_sleep(secs):
            slept.append(secs)
            now[0] += secs
        gx._tls.sleep = fake_sleep
        try:
            for _ in range(3):
                limiter.wait()
            self.assertEqual(limiter.status()["used"], 3)
            limiter.wait()  # the 4th has to wait for the first slot to free up
            self.assertGreaterEqual(now[0], 300)
        finally:
            gx._tls.sleep = None

    def test_netease_cover_url_needs_no_api_call(self):
        self.assertTrue(gx.netease_pic_url("109951163", 500).startswith("https://p3.music.126.net/"))
        self.assertEqual(gx.netease_pic_url("abc", 500), "")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.gd = self.studio.gd

    def test_exact_album_only(self):
        self.gd.search_results[("netease_album", "Parachutes", 1)] = [
            song(1, "Don't Panic", "Coldplay", "Parachutes"), song(2, "Yellow", "Coldplay", "Parachutes")]
        data = self.studio.galaxy.album("Parachutes", "Coldplay")
        self.assertTrue(data["found"])
        self.assertEqual([(t["title"], t["track_no"], t["status"]) for t in data["tracks"]],
                         [("Don't Panic", 1, "new"), ("Yellow", 2, "have")])
        self.assertIn("Coldplay/Parachutes/02 - Yellow", data["tracks"][1]["path"].replace("\\", "/"))
        # Only another edition: skipped, never swapped in.
        self.gd.search_results[("netease_album", "Funcrusher", 1)] = [song(3, "Blind", "Company Flow", "Funcrusher Plus")]
        self.assertFalse(self.studio.galaxy.album("Funcrusher", "Company Flow")["found"])

    def test_artist_search_keeps_credited_songs(self):
        self.gd.search_results[("netease", "Coldplay", 1)] = [
            song(1, "Yellow", "Coldplay", "Parachutes"), song(2, "Clocks", "Coldplay", "A Rush of Blood"),
            song(3, "Yellow", "Coldplay", "Parachutes"), song(4, "Cover", "Someone", "Tribute")]
        data = self.studio.galaxy.search("artist", "Coldplay", "netease")
        self.assertEqual([t["title"] for t in data["tracks"]], ["Yellow", "Clocks"])
        self.assertEqual({a["name"] for a in data["albums"]}, {"Parachutes", "A Rush of Blood"})

    def test_bad_input(self):
        with self.assertRaises(GalaxyError):
            self.studio.galaxy.search("artist", "", "netease")
        with self.assertRaises(GalaxyError):
            self.studio.galaxy.search("artist", "x", "napster")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.studio = LocalStudio()
        self.gd = self.studio.gd
        self.manager = self.studio.download_manager

    def track(self, i, title, artist="Coldplay", album="Ghost Stories", n=None):
        return {"id": str(i), "title": title, "artists": [artist], "album": album, "pic_id": "109951",
                "source": "netease", "track_no": n}

    def test_album_download_queues_missing_and_holds_owned(self):
        self.gd.urls[("netease", "1", 320)] = "https://cdn.example/1.mp3"
        result = self.studio.galaxy.add([self.track(1, "Magic", n=1), self.track(2, "Yellow", album="Parachutes", n=2)],
                                        album_artist="Coldplay", total=9)
        self.assertEqual((result["queued"], result["held"]), (1, 1))
        wait_idle(self.studio)
        done = next(i for i in self.manager.state["queue"] if i["title"] == "Magic")
        self.assertEqual(done["state"], "done", done.get("note"))
        path = Path(done["path"])
        self.assertEqual(path.relative_to(self.studio.folder).as_posix(), "Coldplay/Ghost Stories/01 - Magic.mp3")
        from mutagen.id3 import ID3
        tags = ID3(str(path))
        self.assertEqual(str(tags["TIT2"]), "Magic")
        self.assertEqual(str(tags["TRCK"]), "1/9")
        self.assertEqual(str(tags["TPE2"]), "Coldplay")
        self.assertEqual(tags["APIC:Cover"].mime, "image/jpeg")
        self.assertFalse((self.studio.folder / "_incomplete").exists())
        # It is now in the history, so the same song is held next time, and so is a file in the folder.
        self.assertEqual(self.manager.check([{"kind": "song", "artist": "Coldplay", "title": "Magic"}])["results"][0]["status"],
                         "downloaded")
        self.assertEqual(self.studio.galaxy.add([self.track(9, "Magic", n=1)], "Coldplay")["held"], 1)

    def test_fallbacks_and_failures(self):
        # No 320 link: 128 is used.
        self.gd.urls[("netease", "1", 128)] = "https://cdn.example/1.mp3"
        # No link on netease at all: joox has the same song.
        self.gd.search_results[("joox", "Coldplay Midnight", 1)] = [song(77, "Midnight", "Coldplay", "Ghost Stories", "joox")]
        self.gd.urls[("joox", "77", 320)] = "https://cdn.example/77.mp3"
        # Nothing anywhere.
        self.studio.galaxy.add([self.track(1, "Magic"), self.track(2, "Midnight"), self.track(3, "Ink")])
        wait_idle(self.studio)
        states = {i["title"]: (i["state"], i.get("note", "")) for i in self.manager.state["queue"]}
        self.assertEqual(states["Magic"][0], "done")
        self.assertEqual(states["Midnight"][0], "done")
        self.assertEqual(states["Ink"][0], "failed")
        self.assertIn("No download link", states["Ink"][1])
        # Retry failed puts it back in the queue; a link now exists.
        self.gd.urls[("netease", "3", 320)] = "https://cdn.example/3.mp3"
        self.studio.galaxy.control("retry")
        wait_idle(self.studio)
        self.assertEqual(next(i for i in self.manager.state["queue"] if i["title"] == "Ink")["state"], "done")

    def test_previews_are_rejected(self):
        # The API announces a 5 MB song at 320 kbps (~130 s), but only 40 s arrives.
        self.gd.urls[("netease", "1", 320)] = "https://cdn.example/1.mp3"
        api = self.gd

        def big(params, timeout):
            r = api(params, timeout)
            if params["types"] == "url" and params["br"] == 320:
                r.text = json.dumps({"url": "https://cdn.example/1.mp3", "br": 320, "size": 5_000_000})
            return r
        self.studio.galaxy.api.get = big
        self.studio.galaxy.add([self.track(1, "Magic")])
        wait_idle(self.studio)
        item = self.manager.state["queue"][0]
        self.assertEqual(item["state"], "failed")
        self.assertIn("Preview only", item["note"])

    def test_pause_top_and_clear(self):
        self.studio.galaxy.control("pause")
        self.studio.galaxy.add([self.track(1, "A"), self.track(2, "B"), self.track(3, "C")])
        ids = [i["id"] for i in self.manager.state["queue"]]
        self.studio.galaxy.control("top", [ids[2]])
        self.assertEqual([i["title"] for i in self.manager.state["queue"]], ["C", "A", "B"])
        time.sleep(0.3)
        self.assertTrue(all(i["state"] == "queued" for i in self.manager.state["queue"]))  # paused
        self.studio.galaxy.control("clear_waiting")
        self.assertEqual(self.manager.state["queue"], [])
        self.assertTrue(self.studio.galaxy.status()["paused"])

    def test_download_anyway_starts_the_download(self):
        self.gd.urls[("netease", "2", 320)] = "https://cdn.example/2.mp3"
        self.studio.galaxy.add([self.track(2, "Yellow", album="Parachutes")])
        held = self.manager.queue_view()["held"]
        self.assertEqual(len(held), 1)
        self.manager.decide([held[0]["id"]], "download")
        wait_idle(self.studio)
        self.assertEqual(self.manager.state["queue"][0]["state"], "done")


class CoverTests(unittest.TestCase):
    def test_covers_are_cached_and_netease_needs_no_api_call(self):
        studio = LocalStudio()
        studio.home = tempfile.mkdtemp()
        g = studio.galaxy
        self.assertEqual(g.thumbnail("netease", "109951", 300), JPEG)
        self.assertEqual([c for c in studio.gd.calls if c["types"] == "pic"], [])
        g.fetch = lambda url, **kw: FakeResponse(500)  # cached on disk: no new download
        self.assertEqual(g.thumbnail("netease", "109951", 300), JPEG)
        # joox needs one low-priority API call; a missing cover is not asked for again.
        self.assertIsNone(g.thumbnail("joox", "abc", 300))
        calls = len(studio.gd.calls)
        self.assertIsNone(g.thumbnail("joox", "abc", 300))
        self.assertEqual(len(studio.gd.calls), calls)
        with self.assertRaises(GalaxyError):
            g.thumbnail("netease", "../etc", 300)


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def json(self, url, params=None, **kw):
        self.calls.append(url)
        for key, value in self.answers.items():
            if url.endswith(key):
                return value
        return None


class ArtistCardTests(unittest.TestCase):
    def answers(self):
        return {
            "/ws/2/artist": {"artists": [
                {"id": "mb-1", "name": "Lotus", "disambiguation": "US electronic jam band", "country": "US",
                 "life-span": {"begin": "1999"}},
                {"id": "mb-2", "name": "Lotus", "disambiguation": "Swedish rock band", "country": "SE"},
                {"id": "mb-3", "name": "Lotus Eaters"}]},
            "/ws/2/release-group": {"release-groups": []},
            "/search/artist": {"data": [{"id": 11, "name": "Lotus", "nb_fan": 900, "nb_album": 30, "picture_medium": "p"},
                                        {"id": 12, "name": "LOTUS", "nb_fan": 5, "nb_album": 2}]},
        }

    def test_cards_and_which_artist(self):
        studio = DownloadsStudio()
        http = FakeHttp(self.answers())
        albums = {"mb-1": ["Nomad", "Hammerstrike"], "mb-2": ["Lotus II"]}
        dz = {11: ["Nomad", "Hammerstrike", "Spiral"], 12: ["Something Else"]}
        orig = http.json

        def json_(url, params=None, **kw):
            if url.endswith("/release-group"):
                return {"release-groups": [{"title": t} for t in albums[params["artist"]]]}
            if "/artist/" in url and url.endswith("/albums"):
                return {"data": [{"title": t} for t in dz[int(url.split("/")[-2])]]}
            return orig(url, params)
        http.json = json_
        cards = ArtistCards(studio, http)
        data = cards.cards_for("Lotus", ["Nomad", "Lotus II", "Unknown Thing"], {})
        ids = [c["id"] for c in data["cards"]]
        self.assertEqual(ids, ["mb:mb-1", "mb:mb-2", "dz:12"])  # Deezer 11 merged into mb-1 (same albums)
        self.assertEqual(data["cards"][0]["fans"], 900)
        self.assertEqual(data["cards"][0]["description"], "US electronic jam band")
        self.assertEqual(data["assign"], {"Nomad": "mb:mb-1", "Lotus II": "mb:mb-2", "Unknown Thing": "unsure"})
        # A remembered pick decides "Which artist?".
        picks = {f"{'lotus'}|{'unknown thing'}": "mb:mb-2"}
        self.assertEqual(cards.cards_for("Lotus", ["Unknown Thing"], picks)["assign"]["Unknown Thing"], "mb:mb-2")
        # Cached: no new requests.
        before = len(http.calls)
        cards.cards_for("LOTUS", ["Nomad"], {})
        self.assertEqual(len(http.calls), before)


if __name__ == "__main__":
    unittest.main()
