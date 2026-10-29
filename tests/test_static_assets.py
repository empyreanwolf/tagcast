"""Fork v48: content-hashed, cacheable scripts and styles, and gzip for text responses."""

import gzip
import os
import tempfile
import threading
import unittest
from functools import partial
from http.client import HTTPConnection
from pathlib import Path

from tagcast import app, static_assets

INDEX = ('<html><link rel="stylesheet" href="style.css"><link rel="icon" href="favicon.svg">'
         '<script src="app.js" defer></script><script src="https://cdn.example/x.js"></script>'
         '<script src="missing.js" defer></script>' + ("<!-- padding -->" * 100) + '</html>')


class StaticAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "index.html").write_text(INDEX, encoding="utf-8")
        (self.root / "app.js").write_text("console.log('one');" * 200, encoding="utf-8")
        (self.root / "style.css").write_text("body{color:red}", encoding="utf-8")
        handler = type("StaticHandler", (app.Handler,), {"port": 0})
        self.server = app.Server(("127.0.0.1", 0), partial(handler, directory=str(self.root)))
        handler.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.directory.cleanup()

    def get(self, path, gzip_ok=False, method="GET"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            headers = {"Accept-Encoding": "gzip, deflate, br"} if gzip_ok else {}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_the_page_links_local_scripts_and_styles_by_content_hash_and_is_never_cached(self):
        status, headers, body = self.get("/")
        page = body.decode()
        js, css = static_assets.version(self.root / "app.js"), static_assets.version(self.root / "style.css")
        self.assertEqual(status, 200)
        self.assertIn(f'src="app.js?v={js}"', page)
        self.assertIn(f'href="style.css?v={css}"', page)
        self.assertIn('src="https://cdn.example/x.js"', page)  # remote links untouched
        self.assertIn('href="favicon.svg"', page)               # only .js/.css are tagged
        self.assertIn('src="missing.js"', page)                 # unreadable files keep their plain link
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_a_current_hash_is_cached_long_and_an_old_one_never_is(self):
        js = static_assets.version(self.root / "app.js")
        status, headers, body = self.get(f"/app.js?v={js}")
        self.assertEqual((status, headers["Cache-Control"]), (200, static_assets.LONG_CACHE))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Type"], "text/javascript")
        # Same size, same timestamp, different contents (a zip extracted over the folder).
        stat = (self.root / "app.js").stat()
        (self.root / "app.js").write_text("console.log('two');" * 200, encoding="utf-8")
        os.utime(self.root / "app.js", ns=(stat.st_atime_ns, stat.st_mtime_ns))
        status, headers, body = self.get(f"/app.js?v={js}")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn(b"two", body)
        self.assertNotEqual(static_assets.version(self.root / "app.js"), js)
        self.assertIn(f"app.js?v={static_assets.version(self.root / 'app.js')}", self.get("/")[2].decode())

    def test_text_is_gzipped_only_when_the_browser_accepts_it(self):
        plain = self.get("/app.js")
        packed = self.get("/app.js", gzip_ok=True)
        self.assertNotIn("Content-Encoding", plain[1])
        self.assertEqual(packed[1]["Content-Encoding"], "gzip")
        self.assertEqual(packed[1]["Vary"], "Accept-Encoding")
        self.assertEqual(int(packed[1]["Content-Length"]), len(packed[2]))
        self.assertEqual(gzip.decompress(packed[2]), plain[2])
        self.assertLess(len(packed[2]), len(plain[2]) / 5)
        page = self.get("/", gzip_ok=True)
        self.assertEqual(page[1]["Content-Encoding"], "gzip")
        self.assertIn(b"app.js?v=", gzip.decompress(page[2]))
        # Tiny files are sent as they are.
        self.assertNotIn("Content-Encoding", self.get("/style.css", gzip_ok=True)[1])

    def test_head_and_missing_files(self):
        status, headers, body = self.get("/app.js", method="HEAD")
        self.assertEqual((status, body, headers["Cache-Control"]), (200, b"", "no-store"))
        self.assertEqual(int(headers["Content-Length"]), len((self.root / "app.js").read_bytes()))
        self.assertEqual(self.get("/nothing.js")[0], 404)

    def test_accept_encoding_parsing(self):
        self.assertTrue(static_assets.accepts_gzip("gzip, deflate, br, zstd"))
        self.assertTrue(static_assets.accepts_gzip("*"))
        self.assertFalse(static_assets.accepts_gzip("identity"))
        self.assertFalse(static_assets.accepts_gzip("gzip;q=0"))
        self.assertFalse(static_assets.accepts_gzip(""))

    def test_the_real_page_tags_every_local_script_and_stylesheet(self):
        page = static_assets.page(app.STATIC / "index.html").decode()
        import re
        local = re.findall(r'\s(?:src|href)="([A-Za-z0-9_.-]+\.(?:js|css)[^"]*)"', page)
        self.assertGreater(len(local), 40)
        self.assertTrue(all("?v=" in ref for ref in local), [r for r in local if "?v=" not in r])


class JsonGzipTests(unittest.TestCase):
    def answer(self, payload, accept):
        import io, json
        sent = []

        class Fake:
            headers = {"Accept-Encoding": accept} if accept else {}
            wfile = io.BytesIO()
            send_response = lambda self, status: sent.append(("status", status))
            send_header = lambda self, k, v: sent.append((k, v))
            end_headers = lambda self: None
        fake = Fake()
        app.Handler._json(fake, 200, payload)
        return dict(h for h in sent if h[0] != "status"), fake.wfile.getvalue()

    def test_big_json_is_gzipped_for_browsers_and_small_or_plain_requests_are_not(self):
        import json
        big = {"albums": [{"id": str(i), "name": f"Album {i}"} for i in range(500)]}
        headers, body = self.answer(big, "gzip, deflate")
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(body)), big)
        self.assertEqual(int(headers["Content-Length"]), len(body))
        headers, body = self.answer(big, "")
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(json.loads(body), big)
        headers, body = self.answer({"ok": True}, "gzip")
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
