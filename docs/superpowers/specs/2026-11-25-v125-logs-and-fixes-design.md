# v125 design: Claude-readable logs + fixes (approved 2026-11-25)

## Intent
User cannot carry log files to Claude every time. Claude must diagnose software-wide issues from a small, sanitized digest.

## Approach (user picked A)
- Digest builder (new `claude_digest.py`): version, last errors with tracebacks, yt-dlp last output + URL on FetchTimeout, search-line state. Reuses `debug_report.scrub`; no secrets.
- Delivery: (1) "Copy for Claude" button on Activity page; (2) auto-push `latest.md` to a separate private repo `tagcast-logs` via GitHub Contents API, fine-grained token (that repo only), at most once per 10 min after an error, plus "Send now". Off until token set in Settings.
- Rejected: logs branch in the fork (token could change code); debug zip as-is (too many clicks).

## Also in v125
- "Clear saved search" (search_line.json) and "Remove album" (1 click each).
- yt-dlp 90 s timeouts: record last output/URL/version on FetchTimeout (diagnosis comes from the new digest).
- Static review of bandcamp.py, spotify.py, v117-v124; fix real bugs found.
- Deferred ideas last; skip any too big and say so.

## Non-goals
No auto-upload of secrets; no full-library stress runs; no real-download testing from here.

## Left to builder
UI placement, digest size cap (~8 KB), token storage location (non-synced settings file, excluded from debug zip).
