import unittest

from tagcast.genius_lyrics import Genius, _balanced_divs, _html_to_text, primary_artist, query_title


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def search_json(hits):
    return {"response": {"sections": [{"type": "song", "hits": hits}]}}


def hit(title, artist, url="https://genius.com/Some-song-lyrics"):
    return {"result": {"title": title, "url": url, "primary_artist": {"name": artist}}}


def page_with(inner):
    return f'<html><body><div data-lyrics-container="true" data-x="1">{inner}</div></body></html>'


class GeniusHelperTests(unittest.TestCase):
    def test_query_title_and_primary_artist_strip_features(self):
        track = {"title": "Poly Cotton Blend", "artist": "Aesop Rock"}
        self.assertEqual(query_title(track), "Poly Cotton Blend")
        self.assertEqual(primary_artist(track), "Aesop Rock")
        self.assertEqual(primary_artist({"title": "x", "artist": "Aesop Rock feat. Kimya Dawson"}), "Aesop Rock")

    def test_html_to_text_turns_br_and_tags_into_lines(self):
        html = "Line one<br>Line two<br/>  <a href=\"/x\">tagged word</a> end"
        self.assertEqual(_html_to_text(html), "Line one\nLine two\ntagged word end")

    def test_html_to_text_collapses_runs_of_blank_lines(self):
        html = "a<br><br><br><br>b"
        self.assertEqual(_html_to_text(html), "a\n\nb")

    def test_balanced_divs_keeps_nested_divs_whole(self):
        html = page_with('Verse one<div data-exclude-from-selection="true">annotation</div> tail<br>line two')
        blocks = _balanced_divs(html, 'data-lyrics-container="true"')
        self.assertEqual(len(blocks), 1)
        self.assertIn("annotation", blocks[0])
        self.assertIn("tail", blocks[0])
        self.assertIn("line two", blocks[0])

    def test_balanced_divs_ignores_unmarked_divs(self):
        html = '<div class="other">nope</div><div data-lyrics-container="true">yes</div>'
        self.assertEqual(_balanced_divs(html, 'data-lyrics-container="true"'), ["yes"])


class GeniusFindTests(unittest.TestCase):
    def test_finds_and_extracts_a_matching_song(self):
        track = {"title": "Poly Cotton Blend", "artist": "Aesop Rock"}
        calls = []

        def get(url, **kw):
            calls.append((url, kw))
            if "api/search" in url:
                return FakeResponse(json_data=search_json([hit("Poly Cotton Blend", "Aesop Rock")]))
            return FakeResponse(text=page_with("First line<br>Second line"))

        genius = Genius(get=get)
        self.assertEqual(genius.find(track), "First line\nSecond line")
        self.assertEqual(calls[0][1]["params"]["q"], "Aesop Rock Poly Cotton Blend")

    def test_skips_a_mismatched_hit_without_fetching_its_page(self):
        track = {"title": "Poly Cotton Blend", "artist": "Aesop Rock"}
        page_fetched = []

        def get(url, **kw):
            if "api/search" in url:
                return FakeResponse(json_data=search_json([hit("A Totally Different Song", "Someone Else")]))
            page_fetched.append(url)
            return FakeResponse(text=page_with("text"))

        genius = Genius(get=get)
        self.assertIsNone(genius.find(track))
        self.assertEqual(page_fetched, [])

    def test_search_http_error_returns_none(self):
        genius = Genius(get=lambda url, **kw: FakeResponse(status_code=500))
        self.assertIsNone(genius.find({"title": "x", "artist": "y"}))

    def test_search_bad_json_returns_none(self):
        genius = Genius(get=lambda url, **kw: FakeResponse(text="not json"))
        self.assertIsNone(genius.find({"title": "x", "artist": "y"}))

    def test_search_network_error_returns_none(self):
        import requests

        def get(url, **kw):
            raise requests.ConnectionError("down")

        genius = Genius(get=get)
        self.assertIsNone(genius.find({"title": "x", "artist": "y"}))

    def test_page_with_no_lyrics_container_returns_none(self):
        track = {"title": "Poly Cotton Blend", "artist": "Aesop Rock"}

        def get(url, **kw):
            if "api/search" in url:
                return FakeResponse(json_data=search_json([hit("Poly Cotton Blend", "Aesop Rock")]))
            return FakeResponse(text="<html><body>no lyrics here</body></html>")

        genius = Genius(get=get)
        self.assertIsNone(genius.find(track))

    def test_tries_the_next_hit_if_the_first_matching_page_has_nothing(self):
        track = {"title": "Poly Cotton Blend", "artist": "Aesop Rock"}
        pages = {"https://genius.com/first": FakeResponse(text="<html>empty</html>"),
                 "https://genius.com/second": FakeResponse(text=page_with("Real lyrics line"))}

        def get(url, **kw):
            if "api/search" in url:
                return FakeResponse(json_data=search_json([
                    hit("Poly Cotton Blend", "Aesop Rock", url="https://genius.com/first"),
                    hit("Poly Cotton Blend", "Aesop Rock", url="https://genius.com/second")]))
            return pages[url]

        genius = Genius(get=get)
        self.assertEqual(genius.find(track), "Real lyrics line")


if __name__ == "__main__":
    unittest.main()
