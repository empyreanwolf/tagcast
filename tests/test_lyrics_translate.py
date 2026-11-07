import unittest
from unittest import mock

from tagcast import lyrics_translate
from tagcast.library import LibraryError
from tagcast.lyrics_translate import LyricsTranslator, assign_segments, has_cjk


class FakeStudio:
    def __init__(self):
        self.files = {}

    def _read(self, name):
        return self.files.get(name)

    def _write(self, name, data):
        self.files[name] = data


class Reply:
    def __init__(self, segments=None, status=200, content_type="application/json"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._segments = segments

    def json(self):
        return [self._segments, None, "zh-CN"]


def translator():
    t = LyricsTranslator(FakeStudio())
    return t


LINES = ["国产说唱", "Hook", "", "我们一起走"]
SEGMENTS = [["Domestic rap\n", "国产说唱\n"], ["Hook\n", "Hook\n"], ["\n", "\n"], ["Let us walk together", "我们一起走"]]


class TranslateTests(unittest.TestCase):
    def test_segments_are_mapped_back_to_their_lines(self):
        self.assertEqual(assign_segments(SEGMENTS, LINES), ["Domestic rap", "Hook", "", "Let us walk together"])
        merged = [["Domestic rap\nHook\n", "国产说唱\nHook\n"], ["\n", "\n"], ["Let us walk together", "我们一起走"]]
        self.assertEqual(assign_segments(merged, LINES), ["Domestic rap", "Hook", "", "Let us walk together"])
        self.assertIsNone(assign_segments([["x", "something else"]], LINES))

    def test_only_cjk_songs_are_translated(self):
        self.assertTrue(has_cjk(["a", "b", "你好"]))
        self.assertFalse(has_cjk(["a", "b", "c", "你好"]))
        self.assertFalse(has_cjk(["", " "]))
        t = translator()
        with mock.patch.object(lyrics_translate.requests, "get") as get:
            self.assertEqual(t.view({"lines": ["Hello", "World"]}), {"status": "skipped", "tr": []})
            get.assert_not_called()

    def test_translation_is_asked_once_and_remembered(self):
        t = translator()
        with mock.patch.object(lyrics_translate.requests, "get", return_value=Reply(SEGMENTS)) as get, \
                mock.patch.object(lyrics_translate, "MIN_GAP", 0):
            first = t.view({"lines": LINES})
            second = t.view({"lines": LINES})
        self.assertEqual(first["tr"], ["Domestic rap", "", "", "Let us walk together"])  # "Hook" needs none
        self.assertEqual(second, {"status": "ready", "tr": first["tr"]})
        self.assertEqual(get.call_count, 1)
        self.assertEqual(list(t.studio.files["lyrics_translations.json"].values()), [first["tr"]])
        # a new Tagcast run reads the saved translations
        again = LyricsTranslator(t.studio)
        with mock.patch.object(lyrics_translate.requests, "get") as get:
            self.assertEqual(again.view({"lines": LINES})["tr"], first["tr"])
            get.assert_not_called()

    def test_mismatching_answer_falls_back_to_line_by_line(self):
        t = translator()
        replies = [Reply([["???", "different"]]), Reply([["Domestic rap\n", "国产说唱\n"]]), Reply([["Hook", "Hook"]]),
                   Reply([["Let us walk together", "我们一起走"]])]
        with mock.patch.object(lyrics_translate.requests, "get", side_effect=replies) as get, \
                mock.patch.object(lyrics_translate, "MIN_GAP", 0):
            answer = t.view({"lines": LINES})
        self.assertEqual(answer["tr"], ["Domestic rap", "", "", "Let us walk together"])
        self.assertEqual(get.call_count, 4)

    def test_too_many_requests_pauses_the_translator(self):
        t = translator()
        with mock.patch.object(lyrics_translate.requests, "get", return_value=Reply(status=429, content_type="text/html")) as get, \
                mock.patch.object(lyrics_translate, "MIN_GAP", 0):
            with self.assertRaises(LibraryError):
                t.view({"lines": LINES})
            with self.assertRaises(LibraryError):
                t.view({"lines": LINES})
        self.assertEqual(get.call_count, 1)  # the second call did not even try
        self.assertNotIn("lyrics_translations.json", t.studio.files)

    def test_bad_input_is_refused(self):
        t = translator()
        for body in ({}, {"lines": []}, {"lines": "你好"}, {"lines": [1]}, {"lines": ["你" * 301]},
                     {"lines": ["你好"] * 301}, {"lines": ["你\x00好"]}):
            with self.assertRaises(LibraryError):
                t.view(body)

    def test_cache_keeps_the_newest_songs(self):
        t = translator()
        with mock.patch.object(lyrics_translate, "CACHE_MAX", 2), mock.patch.object(lyrics_translate, "MIN_GAP", 0):
            for number in range(3):
                line = f"歌{number}"
                with mock.patch.object(lyrics_translate.requests, "get", return_value=Reply([[f"Song {number}", line]])):
                    t.view({"lines": [line]})
        self.assertEqual(len(t.studio.files["lyrics_translations.json"]), 2)
        self.assertNotIn(["Song 0"], t.studio.files["lyrics_translations.json"].values())


if __name__ == "__main__":
    unittest.main()
