"""Reviewed artist merges, combined-name splits and featured-artist links.

Modelled on ``cleanup.Cleanup``: a read-only preview, explicit approval,
bounded batches (200 tracks / 20 groups), a fresh check before every write,
read-back after it, and explicit stop/resume. The draft and report are kept in
``artist_cleanup.json`` in the settings folder.

iBroadcast has no merge or rename command for artists, so a merge re-points
every track and album that uses a duplicate entry to the entry that is kept.
Three modes:

- ``merge``: chosen groups of entries become one (track artist, album artist,
  and every additional credit, whatever its role).
- ``split``: a combined entry ("Coldplay/BTS") becomes its first part as the
  primary artist and the other parts as additional artists of the same role.
- ``feat``: guests named in a track title ("(feat. X)") become additional track
  artists. Titles stay unchanged.

Names are resolved to existing entries in use before anything new is created.
"""

import copy
import logging
import secrets
import threading
from collections import Counter

from . import artist_match as match
from .cleanup import album_context, credit_state, same_artists
from .library import LibraryError, fold, number, stored_genres, text

log = logging.getLogger("tagcast")
MODES = {"merge", "split", "feat"}
BATCH_TRACKS = 200
BATCH_GROUPS = 20
PERFORMER = (None, "", "artist", "band")
EXAMPLES = 3


def snapshot(library, record, kind):
    return {"artist_id": number(record.get("artist_id")),
            "artists_additional": credit_state(library, record, "album-artists" if kind == "album" else None)}


class Index:
    """Which artist entries are in use, how much, and in which genres."""

    def __init__(self, library):
        self.library = library
        self.usage, self.genres = Counter(), {}
        self.track_refs, self.album_refs = Counter(), Counter()
        for album_id in library.album_ids():
            album = library.albums[album_id]
            tracks = library.active_track_ids(album_id)
            ids = {number(album.get("artist_id")), *(e["artist_id"] for e in snapshot(library, album, "album")["artists_additional"])}
            for track_id in tracks:
                track = library.tracks[track_id]
                own = {number(track.get("artist_id")), *(e["artist_id"] for e in library.extra_artists(track))}
                genres = stored_genres(track)
                for artist_id in (own | ids) - {0}:
                    self.usage[artist_id] += 1
                    counter = self.genres.setdefault(artist_id, Counter())
                    counter.update(g for g in genres if g)
                for artist_id in own - {0}:
                    self.track_refs[artist_id] += 1
            for artist_id in ids - {0}:
                self.album_refs[artist_id] += 1
        self.names = {i: text(library.artists.get(i, {}).get("name")) for i in self.usage}
        self.names = {i: n for i, n in self.names.items() if n}
        self.by_clean, self.by_key, self.parts, self.pairs = {}, {}, {}, {}
        self.new_names = {}  # match key → the spelling first used for a name to create
        for artist_id, name in self.names.items():
            self.by_clean.setdefault(match.clean(name), []).append(artist_id)
            self.by_key.setdefault(match.match_key(name), []).append(artist_id)
            pair = match.bilingual(name)
            if pair:
                self.pairs.setdefault(frozenset(match.match_key(p) for p in pair), []).append(artist_id)
                for part in pair:
                    self.parts.setdefault(match.match_key(part), []).append(artist_id)

    def best(self, ids):
        return max(ids, key=lambda i: (self.usage[i], -i))

    def resolve(self, name, genres=None):
        """{"id": n} for an entry in use (or an existing name), else {"name": new name}."""
        cleaned = match.clean(name)
        if not cleaned:
            raise LibraryError("An artist name cannot be empty.")
        if cleaned in self.by_clean:
            return {"id": self.best(self.by_clean[cleaned])}
        key = match.match_key(cleaned)
        if key in self.by_key:
            return {"id": self.best(self.by_key[key])}
        pair = match.bilingual(cleaned)
        if pair:  # "派克特(PACT)" → the entry "派克特Pact"
            same = self.pairs.get(frozenset(match.match_key(p) for p in pair))
            if same:
                return {"id": self.best(same)}
        owners = set(self.parts.get(key, ()))
        if len(owners) == 1 and len(key) >= (2 if match.HAS_CJK.search(key) else 3):
            owner = owners.pop()
            if genres is None or match.top_genres(genres) & match.top_genres(self.genres.get(owner, Counter())):
                return {"id": owner}
        existing = self.library.find_artist(cleaned)
        if existing:
            return {"id": existing}
        # Two spellings of one new name ("269B", "269b") create one entry.
        return {"name": self.new_names.setdefault(key, cleaned)}


def ref_key(ref):
    return ("id", ref["id"]) if "id" in ref else ("name", ref["name"].casefold())


def transform(before, mapping, extras=()):
    """Apply ``mapping`` (artist ID → [refs]) and add ``extras`` (refs) as performers.

    Returns {"primary": ref, "credits": [ref + type + phrase]}; the first ref of a
    split primary stays primary and the rest become credits with the artist role.
    Credits keep their role and phrase; duplicates and performer credits equal to
    the primary artist are dropped.
    """
    refs = mapping.get(before["artist_id"]) or [{"id": before["artist_id"]}]
    primary = dict(refs[0])
    credits = [{**r, "type": "artist", "phrase": None} for r in refs[1:]]
    for entry in before["artists_additional"]:
        mapped = mapping.get(entry["artist_id"]) or [{"id": entry["artist_id"]}]
        credits.append({**mapped[0], "type": entry.get("type"), "phrase": entry.get("phrase")})
        credits += [{**r, "type": entry.get("type") or "artist", "phrase": None} for r in mapped[1:]]
    credits += [{**r, "type": "artist", "phrase": None} for r in extras]
    out, seen = [], set()
    for credit in credits:
        key = ref_key(credit)
        role = credit.get("type") if credit.get("type") not in PERFORMER else "artist"
        if role == "artist" and key == ref_key(primary):
            continue
        if (key, role) in seen:
            continue
        seen.add((key, role))
        out.append(credit)
    return {"primary": primary, "credits": out}


def unchanged(before, after):
    if "id" not in after["primary"] or any("id" not in c for c in after["credits"]):
        return False
    wire = {"artist_id": after["primary"]["id"],
            "artists_additional": [{"artist_id": c["id"], "phrase": c.get("phrase"), "type": c.get("type")}
                                   for c in after["credits"]]}
    return same_artists(before, wire)


class ArtistCleanup:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.task = studio._read("artist_cleanup.json") or None
        if self.task and self.task.get("state") == "running":
            self.task["state"] = "stopped"
            self.task["error"] = "Tagcast stopped. Resume will first check any outstanding writes."
        self.running = False
        self._index = (None, None)

    # -- helpers ---------------------------------------------------------------

    def _persist(self):
        self.studio._write("artist_cleanup.json", self.task)

    def _owner(self, client):
        if client is not self.studio.client:
            raise LibraryError("The account connection changed. Stop and open artist cleanup again.")
        if self.task and self.task["owner"] != self.studio.library_user:
            raise LibraryError("This artist cleanup belongs to another iBroadcast account.")

    def index(self, library):
        if self._index[0] is not library:
            self._index = (library, Index(library))
        return self._index[1]

    def name(self, library, ref):
        return library.artist_name(ref["id"]) if "id" in ref else ref["name"]

    def label(self, library, ref):
        return self.name(library, ref) if "id" in ref else f"{ref['name']} (new)"

    # -- reading ---------------------------------------------------------------

    def suggestions(self, kind):
        self.studio._require_client()
        library, _ = self.studio._library()
        index = self.index(library)
        if kind == "merge":
            groups = match.merge_groups(index.names, index.usage, index.genres)
            return {"kind": kind, "groups": groups, "total": len(groups)}
        if kind == "split":
            known = set(index.by_key)
            items = match.split_suggestions(index.names, index.usage, known)
            for item in items:
                genres = index.genres.get(item["id"], Counter())
                resolved = []
                for part in item["parts"]:
                    try:
                        ref = index.resolve(part, genres)
                    except LibraryError:
                        ref = None
                    resolved.append({"name": part, "existing": bool(ref and "id" in ref),
                                     "as": self.name(library, ref) if ref else part})
                item["resolved"] = resolved
                item["album_artist"] = index.album_refs[item["id"]] > 0
            return {"kind": kind, "items": items, "total": len(items)}
        if kind == "feat":
            plan = self._feat_plan(library, index)
            return {"kind": kind, "tracks": len(plan["tracks"]), "guests": len(plan["guests"]),
                    "new_guests": sorted(n for n, ref in plan["guests"].items() if "name" in ref),
                    "problems": plan["problems"][:200], "problem_count": len(plan["problems"])}
        raise LibraryError("Choose merge, split or feat suggestions.")

    def search(self, query="", limit=50):
        self.studio._require_client()
        library, _ = self.studio._library()
        index = self.index(library)
        query = fold(query).strip()
        if not query:
            return {"artists": []}
        hits = [i for i, n in index.names.items() if query in fold(n) or query in match.match_key(n)]
        hits.sort(key=lambda i: (fold(index.names[i]) != query, -index.usage[i], fold(index.names[i])))
        return {"artists": [{"id": i, "name": index.names[i], "tracks": index.usage[i],
                             "album_artist": index.album_refs[i] > 0} for i in hits[:int(limit)]]}

    # -- planning --------------------------------------------------------------

    def _merge_plan(self, library, index, groups):
        if not isinstance(groups, list) or not groups or len(groups) > 2000:
            raise LibraryError("Choose at least one group of artists to merge.")
        mapping, labels, seen = {}, {}, set()
        for position, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("members"), list):
                raise LibraryError("Invalid merge group.")
            members = [number(i) for i in group["members"]]
            if not members or any(i <= 0 or i not in library.artists for i in members) or len(set(members)) != len(members):
                raise LibraryError("A merge group contains an unknown artist.")
            if seen & set(members):
                raise LibraryError("An artist appears in more than one merge group.")
            seen |= set(members)
            target = group.get("target")
            if isinstance(target, dict) and "id" in target:
                target = {"id": number(target["id"])}
                if target["id"] not in members:
                    raise LibraryError("The kept artist must be one of the merged artists.")
            elif isinstance(target, dict) and isinstance(target.get("name"), str):
                name = match.clean(target["name"])
                if not name or len(name) > 1000:
                    raise LibraryError("A new artist name must contain 1–1,000 characters.")
                exact = [i for i in members if text(library.artists[i].get("name")) == name]
                existing = exact[0] if exact else library.find_artist(name)
                target = {"id": existing} if existing and existing not in seen - set(members) else {"name": name}
                if target.get("id") == 0:
                    raise LibraryError("Choose a named artist, not Various Artists.")
            else:
                raise LibraryError("Choose which artist to keep for every merge group.")
            moved = [i for i in members if target.get("id") != i]
            if not moved:
                raise LibraryError("A merge group needs another artist, or a new name.")
            group_id = f"m{position}"
            for i in moved:
                mapping[i] = [target]
            names = [library.artist_name(i) for i in members]
            labels[group_id] = {"title": f"{' + '.join(names)} → {self.label(library, target)}",
                                "members": set(members)}
        return mapping, labels

    def _split_plan(self, library, index, ids):
        if not isinstance(ids, list) or not ids or len(ids) > 5000:
            raise LibraryError("Choose at least one combined name to split.")
        mapping, labels = {}, {}
        for artist_id in dict.fromkeys(number(i) for i in ids):
            if artist_id not in index.names:
                raise LibraryError("A combined name is no longer in use. Refresh the suggestions.")
            _, parts = match.split_parts(index.names[artist_id])
            if len(parts) < 2:
                raise LibraryError(f"“{index.names[artist_id]}” has no separator to split on.")
            genres = index.genres.get(artist_id, Counter())
            refs, keys = [], set()
            for part in parts:
                ref = index.resolve(part, genres)
                if ref.get("id") == artist_id:
                    ref = {"name": part}  # never map a name back onto the combined entry
                if ref_key(ref) not in keys:
                    keys.add(ref_key(ref))
                    refs.append(ref)
            mapping[artist_id] = refs
            labels[f"s{artist_id}"] = {"title": f"{index.names[artist_id]} → "
                                                f"{' + '.join(self.label(library, r) for r in refs)}",
                                       "members": {artist_id}}
        return mapping, labels

    def _feat_plan(self, library, index):
        duos = [n for n in index.names.values() if "&" in n or " and " in n.casefold()]
        plan = {"tracks": {}, "guests": {}, "problems": []}
        for album_id in library.album_ids():
            for track_id in library.active_track_ids(album_id):
                track = library.tracks[track_id]
                title = text(track.get("title"))
                names, problem = match.featured(title, duos)
                if problem:
                    plan["problems"].append({"track_id": track_id, "title": title, "problem": problem,
                                             "artist": library.artist_name(track.get("artist_id"))})
                if not names:
                    continue
                genres = Counter(g for g in stored_genres(track) if g)
                refs = []
                for name in names:
                    try:
                        ref = index.resolve(name, genres)
                    except LibraryError:
                        continue
                    plan["guests"].setdefault(ref["name"] if "name" in ref else f"#{ref['id']}", ref)
                    refs.append(ref)
                if refs:
                    plan["tracks"][track_id] = refs
        return plan

    def prepare(self, mode, groups=None, ids=None):
        if mode not in MODES:
            raise LibraryError("Choose merge, split or feat.")
        client = self.studio._require_client()
        if not self.studio.save_lock.acquire(blocking=False):
            raise LibraryError("Another save is still running.")
        try:
            library, _ = self.studio._current(client)
            library._browse_index("genres")  # displayed, merged album membership
            index = self.index(library)
            with self.lock:
                if self.running:
                    raise LibraryError("Stop the running artist cleanup first.")
            extras, labels = {}, {}
            if mode == "merge":
                mapping, labels = self._merge_plan(library, index, groups)
            elif mode == "split":
                mapping, labels = self._split_plan(library, index, ids)
            else:
                mapping, plan = {}, self._feat_plan(library, index)
                extras = plan["tracks"]
            items = self._items(library, mode, mapping, extras, labels)
            if any(i["kind"] == "album" for i in items) and library.combine_sets:
                raise LibraryError("Disable Combine Multi-Disc Album Sets in iBroadcast before changing album artists.")
            with self.lock:
                if self.running:
                    raise LibraryError("Stop the running artist cleanup first.")
                self.task = {"id": secrets.token_urlsafe(16), "owner": self.studio.library_user,
                             "mode": mode, "state": "preview", "items": items, "artists": {},
                             "error": "", "excluded": [], "combine_sets": bool(library.combine_sets),
                             "groups": {k: v["title"] for k, v in labels.items()}}
                self._persist()
            return self.view()
        finally:
            self.studio.save_lock.release()

    def _items(self, library, mode, mapping, extras, labels):
        items = []
        group_of = {}
        for group_id, label in labels.items():
            for member in label["members"]:
                group_of[member] = group_id

        def group_for(before, record_kind, album_id):
            if mode == "feat":
                return str(library._browse_albums[album_id])
            ids = [before["artist_id"], *(e["artist_id"] for e in before["artists_additional"])]
            return next(group_of[i] for i in ids if i in group_of)

        for album_id in library.album_ids():
            album = library.albums[album_id]
            tracks = library.active_track_ids(album_id)
            records = [("album", album_id, album)] if mode != "feat" else []
            records += [("track", t, library.tracks[t]) for t in tracks]
            for record_kind, record_id, record in records:
                before = snapshot(library, record, record_kind)
                involved = [before["artist_id"], *(e["artist_id"] for e in before["artists_additional"])]
                if mode != "feat" and not any(i in mapping for i in involved):
                    continue  # leave records that only look untidy alone
                after = transform(before, mapping, extras.get(record_id, ()) if record_kind == "track" else ())
                if unchanged(before, after):
                    continue
                item = {"id": record_id, "kind": record_kind, "album_id": album_id,
                        "original_album_id": number(record.get("album_id")) if record_kind == "track" else album_id,
                        "group": group_for(before, record_kind, album_id),
                        "album": text(album.get("name")),
                        "title": (f"Album · disc {number(album.get('disc')) or 1}" if record_kind == "album"
                                  else text(record.get("title"))),
                        "before": before, "after": after,
                        "display_before": [library.artist_name(before["artist_id"]),
                                           *(library.artist_name(e["artist_id"]) for e in before["artists_additional"])],
                        "display_after": [self.label(library, after["primary"]),
                                          *(self.label(library, c) for c in after["credits"])],
                        "status": "pending"}
                if record_kind == "album":
                    item["tracks"] = len(tracks)
                    item["context"] = album_context(library, album_id)
                    if len(tracks) > BATCH_TRACKS:
                        item["status"] = "blocked"
                        item["blocked"] = f"This disc has more than {BATCH_TRACKS} tracks. Edit its album artists individually."
                if len(after["credits"]) > 100:
                    item["status"] = "blocked"
                    item["blocked"] = "More than 100 additional artists."
                items.append(item)
        return items

    # -- view ------------------------------------------------------------------

    def view(self, offset=0, limit=25, report=False):
        self.studio._require_client()
        try:
            offset, limit = int(offset), int(limit)
        except (ValueError, TypeError):
            raise LibraryError("Invalid artist cleanup page.") from None
        if offset < 0 or not 1 <= limit <= 100:
            raise LibraryError("Invalid artist cleanup page.")
        self.studio._library()
        with self.lock:
            self._owner(self.studio.client)
            if not self.task:
                return {"state": "empty"}
            task = self.task
            groups = {}
            for item in task["items"]:
                g = groups.setdefault(item["group"], {
                    "id": item["group"], "tracks": 0, "records": 0, "examples": [],
                    "title": task["groups"].get(item["group"]) or item["album"],
                    "statuses": Counter()})
                g["tracks"] += item.get("tracks", 1) if item["kind"] == "album" else 1
                g["records"] += 1
                g["statuses"][item["status"]] += 1
                if len(g["examples"]) < EXAMPLES or item.get("blocked"):
                    g["examples"].append({"title": item["title"], "album": item["album"], "kind": item["kind"],
                                          "before": item["display_before"], "after": item["display_after"],
                                          **({"blocked": item["blocked"]} if item.get("blocked") else {})})
            ordered = sorted(groups.values(), key=lambda g: -g["tracks"]) if task["mode"] != "feat" else list(groups.values())
            for g in ordered:
                g["statuses"] = dict(g["statuses"])
            result = {k: copy.deepcopy(task[k]) for k in ("id", "mode", "state", "error", "excluded")}
            created = {name: i for name, i in task["artists"].items()}
            result.update(records=len(task["items"]), groups_total=len(groups), offset=offset,
                          tracks=sum(g["tracks"] for g in groups.values()),
                          counts=dict(Counter(i["status"] for i in task["items"])),
                          new_artists=sorted({c["name"] for i in task["items"] if i["after"]
                                              for c in [i["after"]["primary"], *i["after"]["credits"]] if "name" in c}),
                          created=len(created), batch_tracks=BATCH_TRACKS, batch_groups=BATCH_GROUPS,
                          groups=ordered[offset:offset + limit],
                          selection=[{"id": g["id"], "tracks": g["tracks"]} for g in ordered])
            if report:
                result["items"] = copy.deepcopy(task["items"])
                result["artists"] = copy.deepcopy(task["artists"])
            return result

    # -- running ---------------------------------------------------------------

    def start(self, task_id, excluded=None):
        client = self.studio._require_client()
        if not self.studio.save_lock.acquire(blocking=False):
            raise LibraryError("Another save is still running.")
        try:
            library, _ = self.studio._current(client)
            with self.lock:
                self._owner(client)
                if not self.task or self.task["id"] != task_id or self.running:
                    raise LibraryError("This preview is no longer available.")
                if self.task["state"] == "done":
                    raise LibraryError("This artist cleanup has already finished.")
                self._settings(library)
                if self.task["state"] == "preview":
                    if not isinstance(excluded, list) or any(not isinstance(i, str) for i in excluded):
                        raise LibraryError("Choose which groups to exclude.")
                    groups = {i["group"] for i in self.task["items"]}
                    if not set(excluded) <= groups:
                        raise LibraryError("An excluded group is not in this preview.")
                    self.task["excluded"] = excluded
                    for item in self.task["items"]:
                        if item["group"] in excluded:
                            item["status"] = "excluded"
                if any(i["status"] == "blocked" for i in self.task["items"]):
                    raise LibraryError("Some changes are blocked. Exclude their groups first.")
                self.stop.clear()
                self.task["state"], self.task["error"] = "running", ""
                self._persist()
                self.running = True
            threading.Thread(target=self._run, args=(client,), daemon=True).start()
        except Exception:
            self.studio.save_lock.release()
            raise
        return self.view()

    def cancel(self, task_id):
        with self.lock:
            self._owner(self.studio._require_client())
            if not self.task or self.task["id"] != task_id:
                raise LibraryError("This artist cleanup is no longer available.")
            self.stop.set()
        return self.view()

    def _settings(self, library):
        albums = any(i["kind"] == "album" and i["status"] not in ("confirmed", "excluded") for i in self.task["items"])
        if albums and library.combine_sets:
            raise LibraryError("Combine Multi-Disc Album Sets is enabled. Disable it in iBroadcast and resume.")

    def _resolve(self, library, client, ref):
        if "id" in ref:
            if ref["id"] not in library.artists:
                raise LibraryError("An artist chosen in the preview no longer exists. Prepare a new preview.")
            return ref["id"]
        name = ref["name"]
        found = self.task["artists"].get(name) or library.find_artist(name)
        if found is None:
            self._owner(client)
            if self.stop.is_set():
                raise LibraryError("Stopped before creating another artist.")
            # Never retry create_artist after an ambiguous reply; resume looks the
            # name up in a fresh library first.
            found = number(client._jsonrequest("create_artist", name=name).get("artist_id"))
            if not found:
                raise LibraryError(f"No ID returned for new artist “{name}”.")
        with self.lock:
            self.task["artists"][name] = found
            self._persist()
        return found

    def _wire(self, library, client, item):
        after = item["after"]
        return {"artist_id": self._resolve(library, client, after["primary"]),
                "artists_additional": [{"artist_id": self._resolve(library, client, c),
                                        "phrase": c.get("phrase"), "type": c.get("type")}
                                       for c in after["credits"]]}

    def _check(self, library, item):
        if item["kind"] == "album":
            if album_context(library, item["id"]) != item["context"]:
                return "conflict"
            record = library.albums[item["id"]]
        else:
            try:
                album_id = library.active_track_album(item["id"])
            except LibraryError:
                return "conflict"
            if album_id != item["album_id"] or number(library.tracks[item["id"]].get("album_id")) != item["original_album_id"]:
                return "conflict"
            record = library.tracks[item["id"]]
        now = snapshot(library, record, item["kind"])
        if item.get("wire") and same_artists(item["wire"], now):
            return "confirmed"
        return "pending" if same_artists(item["before"], now) else "conflict"

    def _run(self, client):
        try:
            first = True
            while not self.stop.is_set():
                self._owner(client)
                fresh, _ = self.studio._current(client, refresh=first)
                first = False
                self._owner(client)
                self._settings(fresh)
                batch, groups, weight = [], set(), 0
                with self.lock:
                    for item in self.task["items"]:
                        if item["status"] in ("confirmed", "excluded"):
                            continue
                        checked = self._check(fresh, item)
                        if checked == "confirmed":
                            item["status"] = "confirmed"
                            continue
                        if checked == "conflict":
                            item["status"] = "conflict"
                            raise LibraryError(f"“{item['title']}” ({item['album']}) changed since the preview. Prepare a new preview.")
                        cost = item.get("tracks", 1) if item["kind"] == "album" else 1
                        if weight + cost > BATCH_TRACKS or (item["group"] not in groups and len(groups) >= BATCH_GROUPS):
                            break
                        item["status"] = "pending"
                        groups.add(item["group"])
                        batch.append(item)
                        weight += cost
                    self._persist()
                if not batch:
                    with self.lock:
                        self.task["state"] = "done"
                    break
                rows = {"track": [], "album": []}
                for item in batch:
                    if self.stop.is_set():
                        break
                    wire = self._wire(fresh, client, item)
                    with self.lock:
                        item["wire"] = wire
                    key = "album_id" if item["kind"] == "album" else "file_id"
                    rows[item["kind"]].append({key: item["id"], **wire})
                if self.stop.is_set():
                    break
                current, _ = self.studio._current(client)
                self._owner(client)
                self._settings(current)
                if any(self._check(current, item) not in ("pending", "confirmed") for item in batch):
                    raise LibraryError("Metadata changed while preparing the batch. Prepare a new preview.")
                if self.stop.is_set():
                    break
                with self.lock:
                    for item in batch:
                        item["status"] = "sent"
                    self._persist()
                error = None
                try:
                    if rows["track"]:
                        client._jsonrequest("update_track", tracks=rows["track"])
                    if rows["album"]:
                        client._jsonrequest("update_album", albums=rows["album"])
                except Exception as failure:
                    error = failure
                try:
                    after, _ = self.studio._current(client, refresh=True)
                    self._owner(client)
                    with self.lock:
                        for item in batch:
                            item["status"] = "confirmed" if self._check(after, item) == "confirmed" else "unconfirmed"
                        self._persist()
                except Exception:
                    with self.lock:
                        for item in batch:
                            item["status"] = "unconfirmed"
                    raise
                if error:
                    raise LibraryError(f"iBroadcast did not confirm the request: {error}")
                if any(i["status"] != "confirmed" for i in batch):
                    raise LibraryError("Some writes could not be confirmed. Processing stopped; resume checks them first.")
        except Exception as failure:
            log.warning("Artist cleanup stopped: %s", failure)
            with self.lock:
                self.task["state"], self.task["error"] = "error", str(failure)
        finally:
            with self.lock:
                if self.task["state"] == "running":
                    self.task["state"] = "stopped"
                try:
                    self._persist()
                finally:
                    self.running = False
                    self.studio.save_lock.release()
