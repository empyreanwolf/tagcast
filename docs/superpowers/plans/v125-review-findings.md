# v125 static review findings (bandcamp.py, spotify.py, anylinks.py)

| File | Bug | Fixed |
|---|---|---|
| bandcamp.py `cover()` | A failed cover download was cached as `None`, so every later song of the album had no cover | yes (test_bandcamp V125CoverRetryTests) |
| spotify.py `_save()` | `re.search("rate\|429\|too many")` matched words like "generate", reporting a wrong "Spotify is limiting" error | yes (test_v66 V125RateErrorTests) |
| anylinks.py `_bandcamp()` | Logged "queued" (ok) even when every release failed | yes (test_anylinks V125BandcampOutcomeTests) |
| spotify.py `song_row()` | `int()` on a non-numeric track/disc number would drop the whole list | not fixed: spotDL always sends integers (unproven) |
| bandcamp.py `add()` | `int(n)` on a non-numeric track number gives a 500 | not fixed: UI only sends integers |
| spotify.py settings | `client_secret` is inside spotify.json, which the settings backup zip includes | not fixed: say so to the user; backups stay on their PC |
| anylinks.py `split_links` | URLs with a second `http://` in the query are split; a trailing `)` is stripped | not fixed: rare |
| v117–v124 | Read only through the files above; no real download was run from here | n/a |
