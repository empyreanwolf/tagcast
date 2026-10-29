"""Fast, never-stale delivery of the page, its scripts and its styles (fork v48).

The page links every script and stylesheet as ``name.js?v=<hash>``, where the hash comes
from the file's contents. A request whose ``v`` matches the file on disk can be cached by
the browser for a year: extracting a newer zip changes the contents, so the page asks for a
new ``v`` and the old copy is never used again, even when timestamps stay the same. The page
itself, and any request without a current ``v``, stays ``no-store`` exactly as before.

Text responses are gzip-compressed when the browser accepts it. Files are read and hashed on every
request (cheap, and safe when an update keeps size and mtime); compressed copies are kept per hash.
"""

import gzip
import hashlib
import re
import threading
from pathlib import Path

LONG_CACHE = "public, max-age=31536000, immutable"
COMPRESSIBLE = {".html", ".js", ".css", ".svg", ".json", ".txt", ".md"}
MIN_GZIP = 1024          # smaller bodies are not worth the header
GZIP_LEVEL = 6
# src="x.js" / href="x.css" for local files only (no scheme, no leading slash, no query yet).
ASSET_REF = re.compile(r'(\s(?:src|href)=")([A-Za-z0-9_.-]+\.(?:js|css))(")')

_lock = threading.Lock()
_packed = {}  # content hash -> gzipped bytes (a zip extracted over the folder can keep size and mtime)
_PACKED_LIMIT = 400


def accepts_gzip(header):
    """True when an Accept-Encoding header allows gzip (and does not set q=0)."""
    for part in (header or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() in ("gzip", "*"):
            q = params.strip()
            return not (q.startswith("q=") and q[2:].strip() in ("0", "0.0", "0.00", "0.000"))
    return False


def _entry(path):
    """(bytes, content hash). Always read: timestamps and sizes cannot prove a file unchanged."""
    raw = Path(path).read_bytes()
    return raw, hashlib.sha1(raw).hexdigest()[:10]


def version(path):
    """Short content hash of a file, or "" when it cannot be read."""
    try:
        return _entry(path)[1]
    except OSError:
        return ""


def _gzipped(raw, digest):
    with _lock:
        packed = _packed.get(digest)
    if packed is None:
        packed = gzip.compress(raw, GZIP_LEVEL, mtime=0)
        with _lock:
            if len(_packed) >= _PACKED_LIMIT:
                _packed.clear()
            _packed[digest] = packed
    return packed


def page(index_path):
    """index.html with every local script/stylesheet link carrying its content hash."""
    index_path = Path(index_path)
    raw = _entry(index_path)[0]
    folder = index_path.parent

    def tag(match):
        name = match.group(2)
        v = version(folder / name)
        return f"{match.group(1)}{name}?v={v}{match.group(3)}" if v else match.group(0)

    return ASSET_REF.sub(tag, raw.decode("utf-8")).encode("utf-8")


def compress(body):
    """gzip for a dynamic body (JSON); callers check accepts_gzip and size first."""
    return gzip.compress(body, 5, mtime=0)


def respond(path, wanted_version, gzip_ok, is_index=False):
    """(body, headers) for one static file; raises OSError when it is missing."""
    path = Path(path)
    suffix = path.suffix.lower()
    if is_index:
        body, current = page(path), False
        encoded = gzip.compress(body, GZIP_LEVEL, mtime=0) if gzip_ok and len(body) >= MIN_GZIP else None
    else:
        body, digest = _entry(path)
        current = bool(wanted_version) and wanted_version == digest
        encoded = _gzipped(body, digest) if gzip_ok and suffix in COMPRESSIBLE and len(body) >= MIN_GZIP else None
    headers = {"Cache-Control": LONG_CACHE if current else "no-store"}
    if suffix in COMPRESSIBLE:
        headers["Vary"] = "Accept-Encoding"
    if encoded is not None and len(encoded) < len(body):
        headers["Content-Encoding"] = "gzip"
        body = encoded
    return body, headers
