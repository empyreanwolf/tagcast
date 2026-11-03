import unittest

from test_discography import LocalStudio
from tagcast.discography import discogs_kind, discogs_name, release_key


class FakeDiscogsHttp:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def json(self, url, params=None, headers=None, ttl=3600):
        if "discogs.com" in url:
            self.calls.append((url, headers))
            if url.endswith("/database/search"):
                return {"results": [{"id": 1, "title": "Neroche"}, {"id": 2, "title": "Neroche Fan"}]}
            if url.endswith("/artists/1/releases"):
                return {"pagination": {"pages": 1}, "releases": [
                    {"title": "Blue Tomorrows", "year": 2011, "role": "Main", "type": "master", "artist": "Neroche"},
                    {"title": "Instrumental Beats", "year": 2011, "role": "Main", "type": "release", "artist": "Brainchild*", "format": "File, Album"},
                    {"title": "The Stone Circle E.P", "year": 2015, "role": "Main", "type": "release", "artist": "Neroche", "format": "File, EP"},
                    {"title": "Some Comp", "year": 2016, "role": "Appearance", "artist": "Various"},
                    {"title": "Remix credit", "year": 2016, "role": "Remix", "artist": "Other"}]}
            return {}
        return self.inner.json(url, params, headers=headers, ttl=ttl) if hasattr(self.inner, "json") else None


class DiscogsTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(discogs_name("Brainchild* (2)"), "Brainchild")
        self.assertEqual(release_key("The Rise in Fall (feat. Neroche)"), release_key("The Rise in Fall"))
        self.assertEqual(discogs_kind({"role": "Main", "format": "File, EP"}), "ep")
        self.assertEqual(discogs_kind({"role": "Appearance"}), "compile")

    def test_discogs_rows_join_the_discography_when_a_token_is_saved(self):
        studio = LocalStudio()
        studio.source_keys = lambda: {"discogs_token": "tok"}
        d = studio.discography
        d.http = FakeDiscogsHttp(d.http)
        data = {"name": "Neroche", "releases": [{"title": "Blue Tomorrows", "year": "", "type": "album", "cover": "", "sources": ["Deezer"]}], "notes": []}
        d._add_discogs(data)
        rows = {r["title"]: r for r in data["releases"]}
        self.assertEqual(rows["Blue Tomorrows"]["sources"], ["Deezer", "Discogs"])
        self.assertEqual(rows["Blue Tomorrows"]["year"], "2011")
        self.assertEqual(rows["Instrumental Beats"]["sources"], ["Discogs (as Brainchild)"])
        self.assertEqual(rows["The Stone Circle E.P"]["type"], "ep")
        self.assertEqual(rows["Some Comp"]["type"], "compile")
        self.assertNotIn("Remix credit", rows)
        self.assertEqual(d.http.calls[0][1], {"Authorization": "Discogs token=tok"})

    def test_without_a_token_only_a_tip(self):
        studio = LocalStudio()
        studio.source_keys = lambda: {}
        data = {"name": "X", "releases": [], "notes": []}
        studio.discography._add_discogs(data)
        self.assertEqual(data["releases"], [])
        self.assertIn("Discogs token", data["notes"][0])
