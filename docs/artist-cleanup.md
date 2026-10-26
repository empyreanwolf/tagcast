# Artist cleanup

Added in this fork. Open **Album artists → Fix duplicate artists…** or
**Overview → Metadata health → Fix duplicate artists…**.

iBroadcast has no command to merge or rename an artist, so Tagcast re-points every
track and album that uses a duplicate entry to the entry you keep. Nothing is
written until you prepare a preview, review it, tick the approval box and start.
Local music files are never changed.

## Tabs

1. **Duplicates.** Entries that are the same artist:
   - **Case or spaces:** `ILL BILL` / `Ill Bill`, `Adventure Club ` with a trailing space.
   - **Simplified / traditional Chinese:** `陳奕迅` / `陈奕迅`.
   - **English + Chinese:** the same two parts in another order or spacing
     (`Edison Chen 陳冠希`, `陳冠希Edison Chen`), and one-language entries that
     equal a part (`王菲`, `Faye Wong` → `Faye Wong 王菲`).

   One-language matches in a different genre, or with very short names, start
   unticked (`Air` is not `艾热 AIR`). A name is rewritten as
   `English 中文` only when the entries show the parts are separable; otherwise the
   most-used spelling is kept (`AR刘夫阳`, `Gm仙`). Chinese script: the form used
   most. Pick another kept name or type one per card.
2. **Combined names.** `Coldplay/BTS` becomes **Coldplay** as the primary artist and
   **BTS** as an additional artist, so the album shows under both. Each part joins an
   existing entry where there is one. `/`, `;` and `、` are suggested; `&`, `,`, `x`,
   `vs` and `and` are listed unticked because many acts are one name
   (`Simon & Garfunkel`, `Tyler, The Creator` is never offered).
3. **Featured in titles.** Guests in titles — `(feat. X)`, `[ft. A & B]`,
   `feat.X`, `Song ft. X`, `(with X)` — become additional track artists. Titles stay
   unchanged. Known `&` acts (`Blu & Exile`) stay whole; names with brackets
   (`(G)I-DLE`, `派克特（PACT）`) are kept intact. Unreadable titles are listed.
   Run **Duplicates** first so guests join the merged entries.
4. **Search & merge.** Find any entries by name, add them, choose the name to keep.
5. **Review & run.** Every affected group with before/after examples. Untick groups
   to exclude them, approve, start. Stop, resume and the exported report work like
   **Clean up metadata**.

## Safety

Batches hold at most 200 tracks from 20 groups. Before each batch Tagcast checks
that every record still has the values from the preview; afterwards it downloads
the library again and confirms each change. A conflict, request error or
unconfirmed write stops processing. New artist names are created only for the
batch being sent and are never retried blindly. The draft and report are kept in
`artist_cleanup.json` in the settings folder; export the report to keep the
original values of every record.

Changing album artists requires **Combine Multi-Disc Album Sets** to be off in
iBroadcast. The old, now unused entries stay in iBroadcast's database because there
is no command to delete an artist.

Traditional → simplified matching uses OpenCC's character table
(`static/opencc-ts-characters.txt`, Apache License 2.0, see
`static/opencc-LICENSE.txt`).
