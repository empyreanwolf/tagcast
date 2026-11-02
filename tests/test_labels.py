import unittest

from test_discography import LocalStudio
from tagcast.charts import bandcamp_rows
from tagcast.labels import LabelError, Labels, band_name, bandcamp_items

PAGE = '''<meta property="og:site_name" content="Rhymesayers Entertainment">
<ol id="music-grid"><li data-item-id="album-1" class="music-grid-item square">
<a href="/album/the-mouse-and-the-mask"><div class="art"></div><p class="title">
  The Mouse &amp; The Mask <br><span class="artist-override">Danger Doom</span></p></a></li>
<li class="music-grid-item"><a href="https://rhymesayers.bandcamp.com/album/own-label"><p class="title">Own Release</p></a></li>
</ol><ol data-client-items="[{&quot;title&quot;:&quot;Late One&quot;,&quot;artist&quot;:&quot;Atmosphere&quot;,&quot;page_url&quot;:&quot;/album/late-one&quot;}]"></ol>'''


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def json(self, url, params=None, **kw):
        self.calls.append((url, params))
        for key, value in self.answers.items():
            if url.endswith(key) and (key != "/release" or True):
                return value(params) if callable(value) else value
        return None


class LabelTests(unittest.TestCase):
    def test_bandcamp_page_rows_with_artist_override_and_json(self):
        self.assertEqual(bandcamp_items(PAGE), [("The Mouse & The Mask", "Danger Doom", "/album/the-mouse-and-the-mask"),
                                                ("Own Release", "", "/album/own-label"), ("Late One", "Atmosphere", "/album/late-one")])
        self.assertEqual(band_name(PAGE), "Rhymesayers Entertainment")

    def test_musicbrainz_label_groups_releases_and_skips_singles_and_compilations(self):
        def releases(params):
            group = lambda gid, title, kind, sec=(): {"id": gid, "title": title, "primary-type": kind, "secondary-types": list(sec),
                                                      "first-release-date": "2005-10-11", "artist-credit": [{"name": "Danger Doom", "joinphrase": ""}]}
            return {"release-count": 4, "releases": [
                {"id": "r1", "release-group": group("g1", "The Mouse and the Mask", "Album"), "label-info": [{"label": {"id": "L1", "name": "Epitaph"}}]},
                {"id": "r2", "release-group": group("g1", "The Mouse and the Mask", "Album")},
                {"id": "r3", "release-group": group("g2", "Sofa King", "Single")},
                {"id": "r4", "release-group": group("g3", "Best Of", "Album", ["Compilation"])}]}
        studio = LocalStudio()
        labels = Labels(studio, http=FakeHttp({"/label": {"labels": [{"id": "L0", "name": "Epitaph Europe"}, {"id": "L1", "name": "Epitaph"}]},
                                              "/release": releases}))
        out = labels.view("epitaph")
        self.assertEqual((out["label"], out["label_id"]), ("Epitaph", "L1"))
        self.assertEqual([o["id"] for o in out["others"]], ["L0"])
        self.assertEqual([(r["title"], r["artist"], r["year"], r["status"]) for r in out["rows"]],
                         [("The Mouse and the Mask", "Danger Doom", "2005", "new")])
        self.assertEqual(len(labels.view("epitaph", singles=True)["rows"]), 2)
        with self.assertRaises(LabelError):
            Labels(studio, http=FakeHttp({"/label": {"labels": []}})).view("nobody")

    def test_get_sends_bandcamp_rows_to_bandcamp_and_others_per_artist(self):
        studio = LocalStudio()
        sent, jobs = [], []
        studio.bandcamp = type("B", (), {"add": lambda self, url: sent.append(url) or {"queued": 3, "held": 0}})()
        studio.discography.get = lambda name, albums, songs: jobs.append((name, [a["title"] for a in albums])) or {}
        out = Labels(studio).get([{"title": "A", "artist": "X", "url": "https://x.bandcamp.com/album/a"},
                                  {"title": "B", "artist": "Y"}, {"title": "C", "artist": "Y"}])
        self.assertEqual(sent, ["https://x.bandcamp.com/album/a"])
        self.assertEqual(jobs, [("Y", ["B", "C"])])
        self.assertEqual((out["queued"], out["jobs"]), (3, 2))


class BandcampChartTests(unittest.TestCase):
    def test_both_answer_shapes(self):
        new = bandcamp_rows([{"title": "LP", "album_artist": "MF DOOM", "band_name": "Label", "item_url": "https://l.bandcamp.com/album/lp"}])
        self.assertEqual((new[0]["artist"], new[0]["source"], new[0]["kind"]), ("MF DOOM", "bandcamp", "album"))
        old = bandcamp_rows([{"primary_text": "Tape", "secondary_text": "Someone", "url_hints": {"subdomain": "s", "slug": "tape", "item_type": "a"}}])
        self.assertEqual(old[0]["source_id"], "https://s.bandcamp.com/album/tape")
        self.assertEqual(bandcamp_rows([{"title": "no link"}]), [])


class GetAllTests(unittest.TestCase):
    def test_get_all_takes_every_album_you_do_not_have(self):
        studio = LocalStudio()
        d = studio.discography
        d.view = lambda name: {"name": "Jay", "releases": [{"title": "A", "status": "missing", "type": "album"},
                                                           {"title": "B", "status": "have", "type": "album"},
                                                           {"title": "C", "status": "partly", "type": "ep"},
                                                           {"title": "D", "status": "missing", "type": "single"}]}
        got = []
        d.get = lambda name, albums, songs: got.append((name, [a["title"] for a in albums])) or {"total": len(albums)}
        d.get_all("jay")
        self.assertEqual(got, [("Jay", ["A", "C"])])
