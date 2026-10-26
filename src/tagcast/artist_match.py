"""Find artist entries that are the same person, and parse combined or featured names.

Pure functions over plain data (no iBroadcast requests), so suggestions can be
checked against any library snapshot:

- ``clean``/``match_key``: names that differ only in case, spacing, invisible
  characters, full-width forms or traditional/simplified characters share a key.
- ``chunks``/``bilingual``: "Edison Chen 陳冠希", "陳冠希Edison Chen" and
  "Edison Chen陳冠希" are the same Latin and CJK parts in another order.
- ``merge_groups``: union of the above, plus single-language entries ("王菲")
  linked to a bilingual entry ("Faye Wong 王菲"); those links start unticked
  unless the two entries share a genre.
- ``split_parts``: "Coldplay/BTS" → ["Coldplay", "BTS"]; other separators are
  suggestions only, because "Simon & Garfunkel" is one act.
- ``featured``: guest names from "(feat. X & Y)" style track titles.
"""

import functools
import re
import unicodedata
from collections import Counter
from pathlib import Path

INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
QUOTES = str.maketrans({"’": "'", "‘": "'", "`": "'", "＇": "'"})
CJK = "㐀-䶿一-鿿豈-﫿぀-ヿ가-힯"
CJK_RUN = re.compile(f"[{CJK}][{CJK}·・]*")
HAS_CJK = re.compile(f"[{CJK}]")
HAS_LATIN = re.compile(r"[A-Za-z]")
# What separates the parts of a bilingual name, besides the switch of script.
BILINGUAL_GAPS = re.compile(r"[\s()（）\[\]【】「」<>《》|]+")
# An underscore separates the two scripts ("翁杰_Winjay"), but belongs to a Latin name ("Wiz_H").
SCRIPT_UNDERSCORE = re.compile(f"(?<=[{CJK}])_+|_+(?=[{CJK}])")
# Names with these never count as one bilingual person; they are combined names.
COMBINED_HINT = re.compile(r"/|;|、|,|，|&|\+|\b(?:feat|ft|featuring|vs|with|and)\b\.?", re.I)

_TS = None


def _ts_table():
    """Traditional → simplified characters (OpenCC, see static/opencc-LICENSE.txt)."""
    global _TS
    if _TS is None:
        path = Path(__file__).with_name("static") / "opencc-ts-characters.txt"
        pairs = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if len(line) == 2 and not line.startswith("#"):
                pairs[ord(line[0])] = line[1]
        _TS = pairs
    return _TS


def simplified(value):
    return value.translate(_ts_table())


def clean(name):
    """The name as it should be stored: no invisible characters, single spaces, trimmed."""
    value = unicodedata.normalize("NFKC", str(name or "")).translate(INVISIBLE).translate(QUOTES)
    return " ".join(value.split())


def match_key(name):
    """Equal for names that only differ in case, spacing, width, quotes or Chinese script."""
    return simplified(clean(name).casefold())


def differences(names):
    """Human-readable reasons why these spellings are not identical."""
    reasons = []
    names = [str(n) for n in names]
    if any(n != " ".join(n.split()) for n in names):
        reasons.append("extra spaces")
    if any(any(ord(c) in INVISIBLE for c in n) for n in names):
        reasons.append("invisible characters")
    if any(unicodedata.normalize("NFKC", n) != n for n in names):
        reasons.append("full-width characters")
    if len({clean(n).translate(_ts_table()) for n in names}) < len({clean(n) for n in names}) \
            and len({clean(n).casefold() for n in names}) > 1:
        reasons.append("traditional/simplified Chinese")
    if len({clean(n) for n in names}) > 1 and len({simplified(clean(n)).casefold() for n in names}) < \
            len({simplified(clean(n)) for n in names}):
        reasons.append("upper/lower case")
    return reasons or ["different spelling"]


def chunks(name):
    """Latin and CJK parts in reading order: "陳冠希Edison Chen" → ["陳冠希", "Edison Chen"]."""
    value = SCRIPT_UNDERSCORE.sub(" ", clean(name))
    out = []
    for piece in BILINGUAL_GAPS.split(value):
        position = 0
        for match in CJK_RUN.finditer(piece):
            before = piece[position:match.start()].strip(" -.·")
            if before:
                out.append(before)
            out.append(match.group())
            position = match.end()
        rest = piece[position:].strip(" -·")
        if rest:
            out.append(rest)
    # Re-join Latin words that the gap pattern separated ("Edison", "Chen").
    joined = []
    for part in out:
        if joined and not HAS_CJK.search(part) and not HAS_CJK.search(joined[-1]):
            joined[-1] = f"{joined[-1]} {part}"
        else:
            joined.append(part)
    return joined


def bilingual(name):
    """(latin, cjk) for a name made of exactly one Latin part and one CJK part, else None."""
    if COMBINED_HINT.search(clean(name)):
        return None
    parts = chunks(name)
    latin = [p for p in parts if not HAS_CJK.search(p)]
    cjk = [p for p in parts if HAS_CJK.search(p)]
    # One Chinese character next to a Latin word ("Gm仙", "Lambert凌") is usually one name.
    if len(latin) == 1 and len(cjk) == 1 and HAS_LATIN.search(latin[0]) and len(cjk[0]) >= 2:
        return latin[0], cjk[0]
    return None


def latin_first(name):
    parts = chunks(name)
    return bool(parts) and not HAS_CJK.search(parts[0])


def bilingual_name(latin, cjk):
    """The agreed format: English name, a space, Chinese name."""
    return f"{clean(latin)} {clean(cjk)}"


def top_genres(counter, limit=3):
    return {g.casefold() for g, _ in counter.most_common(limit)}


class Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b, key=str)] = min(a, b, key=str)


def merge_groups(artists, usage, genres=None):
    """Suggested merges.

    artists: {id: name} of entries in use; usage: {id: tracks}; genres: {id: Counter}.
    Returns [{"id", "kind", "members": [{"id", "name", "tracks", "ticked", "reason"}],
              "target": {"id"} or {"name"}, "target_name"}], largest first.
    """
    genres = genres or {}
    union, reasons = Union(), {}
    by_key = {}
    for artist_id, name in artists.items():
        if not clean(name):
            continue
        by_key.setdefault(match_key(name), []).append(artist_id)
    for ids in by_key.values():
        for other in ids[1:]:
            union.join(ids[0], other)
    # Bilingual entries with the same two parts, in any order or spacing.
    by_pair = {}
    pairs = {}
    for artist_id, name in artists.items():
        pair = bilingual(name)
        if pair:
            pairs[artist_id] = pair
            key = frozenset(match_key(p) for p in pair)
            by_pair.setdefault(key, []).append(artist_id)
    for ids in by_pair.values():
        for other in ids[1:]:
            union.join(ids[0], other)
    # Single-language entries that equal one part of a bilingual entry.
    part_owner = {}
    for artist_id, pair in pairs.items():
        for part in pair:
            part_owner.setdefault(match_key(part), set()).add(union.find(artist_id))
    links = []
    for artist_id, name in artists.items():
        if artist_id in pairs or COMBINED_HINT.search(clean(name)):
            continue
        key = match_key(name)
        owners = {union.find(o) for o in part_owner.get(key, ())}
        if len(owners) != 1 or union.find(artist_id) in owners:
            continue
        owner = owners.pop()
        long_enough = len(key) >= (2 if HAS_CJK.search(key) else 3)
        owner_ids = [i for i in pairs if union.find(i) == owner]
        shared = any(top_genres(genres.get(artist_id, Counter())) & top_genres(genres.get(i, Counter()))
                     for i in owner_ids)
        reason = ("very short name, check this is the same artist" if not long_enough
                  else "same genre as the bilingual name" if shared
                  else "different genre, check this is the same artist")
        links.append((artist_id, owner, reason, long_enough and shared))
    for artist_id, owner, reason, ticked in links:
        for same in by_key[match_key(artists[artist_id])]:  # its own case/space variants too
            reasons[same] = (reason, ticked)
        union.join(owner, artist_id)
    groups = {}
    for artist_id in artists:
        groups.setdefault(union.find(artist_id), []).append(artist_id)
    out = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        names = [artists[i] for i in ids]
        is_bilingual = any(i in pairs for i in ids)
        members = []
        for i in sorted(ids, key=lambda i: (-usage.get(i, 0), str(i))):
            reason, ticked = reasons.get(i, (None, True))
            if reason is None:
                variants = [artists[j] for j in by_key[match_key(artists[i])]]
                reason = ("English + Chinese name" if i in pairs and len(variants) < 2
                          else ", ".join(differences(variants)) if len(variants) > 1
                          else "same name, other order or spacing")
            members.append({"id": i, "name": artists[i], "tracks": usage.get(i, 0),
                            "ticked": ticked, "reason": reason})
        target = choose_target(artists, usage, [m["id"] for m in members if m["ticked"]] or ids, pairs)
        kind = "bilingual" if is_bilingual else ("script" if "traditional/simplified Chinese" in differences(names) else "same")
        out.append({"id": "-".join(str(i) for i in sorted(ids, key=str)), "kind": kind,
                    "members": members, **target})
    out.sort(key=lambda g: -sum(m["tracks"] for m in g["members"]))
    return out


def choose_target(artists, usage, ids, pairs=None):
    """The entry to keep, or a new name to create, for these merged entries."""
    pairs = pairs if pairs is not None else {i: bilingual(artists[i]) for i in ids if bilingual(artists[i])}
    bilingual_ids = [i for i in ids if pairs.get(i)]
    # Only rewrite as "English 中文" when the entries show the parts are separable:
    # another order ("陳冠希Edison Chen"), or one part on its own ("王菲"). Case,
    # spacing or script variants alone keep the most used spelling ("AR刘夫阳").
    separable = bilingual_ids and (len(bilingual_ids) < len(ids)
                                   or len({latin_first(artists[i]) for i in bilingual_ids}) > 1)
    if separable:
        latin = Counter()
        cjk = Counter()
        for i in ids:
            name = artists[i]
            pair = pairs.get(i)
            weight = usage.get(i, 0) + 1
            if pair:
                latin[pair[0]] += weight
                cjk[pair[1]] += weight
            elif HAS_CJK.search(name):
                cjk[clean(name)] += weight
            else:
                latin[clean(name)] += weight
        # Prefer the Latin spelling used most; Chinese: the script used most.
        wanted = bilingual_name(latin.most_common(1)[0][0], cjk.most_common(1)[0][0])
    else:
        best = max(ids, key=lambda i: (clean(artists[i]) == artists[i], usage.get(i, 0), -len(str(i))))
        wanted = clean(artists[best])
    exact = [i for i in ids if artists[i] == wanted]
    if exact:
        keep = max(exact, key=lambda i: usage.get(i, 0))
        return {"target": {"id": keep}, "target_name": wanted}
    return {"target": {"name": wanted}, "target_name": wanted}


# Combined names --------------------------------------------------------------

SEPARATORS = [
    ("slash", re.compile(r"\s*/\s*")),
    ("semicolon", re.compile(r"\s*;\s*")),
    ("chinese comma", re.compile(r"\s*、\s*")),
    ("feat", re.compile(r"\s+(?:featuring\b|feat\.|feat\b|ft\.|ft\b)\s*", re.I)),
    ("ampersand", re.compile(r"\s*&\s*")),
    ("comma", re.compile(r"\s*,\s*")),
    ("x", re.compile(r"\s+[xX×]\s+")),
    ("vs", re.compile(r"\s+vs\.?\s+", re.I)),
    ("and", re.compile(r"\s+and\s+", re.I)),
]
SAFE_SEPARATORS = {"slash", "semicolon", "chinese comma"}
# Words after a comma that belong to the name: "Tyler, The Creator", "Oscar Brown, Jr".
NAME_TAILS = re.compile(r"^(?:the\b|jr\.?$|sr\.?$|ii+$|inc\.?$)", re.I)


def split_parts(name):
    """(kinds, parts) for a combined name, splitting only on the separators it contains."""
    value = clean(name)
    kinds = [kind for kind, pattern in SEPARATORS if pattern.search(value)]
    if not kinds:
        return [], [value]
    parts = [value]
    for kind, pattern in SEPARATORS:
        if kind in kinds:
            parts = [p for part in parts for p in pattern.split(part)]
    parts = [p.strip(" ()[]（）") for p in parts]
    out = []
    for part in parts:
        if part and part.casefold() not in {p.casefold() for p in out}:
            out.append(part)
    return kinds, out


def split_suggestions(artists, usage, known_keys):
    """Combined names worth splitting. ``known_keys``: match keys of entries in use.

    Slash, semicolon and 、 splits are ticked; &, comma, x, vs and "and" are
    offered unticked, flagged when no part exists on its own (likely one act).
    """
    out = []
    for artist_id, name in artists.items():
        kinds, parts = split_parts(name)
        if len(parts) < 2 or any(len(p) > 200 for p in parts):
            continue
        if "comma" in kinds and len(parts) == 2 and NAME_TAILS.match(parts[1]):
            continue
        known = [match_key(p) in known_keys for p in parts]
        safe = set(kinds) <= SAFE_SEPARATORS
        note = ("" if any(known) else "no part is an artist on its own, this may be one act's name")
        out.append({"id": artist_id, "name": name, "parts": parts, "known": known,
                    "kinds": kinds, "tracks": usage.get(artist_id, 0),
                    "ticked": safe, "note": note})
    out.sort(key=lambda s: (not s["ticked"], -s["tracks"], match_key(s["name"])))
    return out


# Featured artists in titles ---------------------------------------------------

FEAT_TOKEN = re.compile(r"(?<![A-Za-z0-9])(featuring\b|feat\.|feat\b|ft\.|ft\b|with\b)", re.I)
OPEN, CLOSE = "([（【", ")]）】"
# Guests are separated by commas, 、, slashes and semicolons, and by "&", "and" and
# "x" only with spaces around them ("G&B" and "X-Raided" are names).
GUEST_SPLIT = re.compile(r"\s*(?:,|，|、|/|;)\s*|\s+(?:&|and|x|×)\s+|\s*&\s+|\s+&\s*", re.I)
FEAT_WORD = re.compile(r"\b(?:feat|ft|featuring)\b", re.I)


def _guest_spans(value):
    """Text after each feat/ft/featuring (and "with" inside brackets), bracket-aware.

    Inside brackets the guests run to the bracket that closes the one the token is in;
    elsewhere they run to the end, a " - " or a new bracket. Brackets inside a name
    ("派克特（PACT）", "(G)I-DLE") stay part of it.
    """
    depth_at, depth = [], 0
    for char in value:
        depth_at.append(depth)
        if char in OPEN:
            depth += 1
        elif char in CLOSE and depth:
            depth -= 1
    spans = []
    for token in FEAT_TOKEN.finditer(value):
        inside = depth_at[token.start()]
        if token.group(1).lower() == "with" and not inside:
            continue  # "Lunch With Blockhead" is a title, not a guest
        if token.group(1).lower() == "with":
            before = value[:token.start()].rstrip()
            if not before or before[-1] not in OPEN:
                continue  # only "(with X)", never "(Remix With Strings)"
        depth, end = inside, len(value)
        for position in range(token.end(), len(value)):
            char = value[position]
            if char in OPEN:
                depth += 1
            elif char in CLOSE:
                if depth == inside:
                    end = position if inside else position
                    break
                depth -= 1
            elif not inside and depth == inside and (value.startswith(" - ", position) or
                                                     (char in OPEN and position > token.end())):
                end = position
                break
        if not inside:
            # A bare guest list stops at a new bracket: "Song ft. A (Remix)".
            cut = re.search(r"\s+[-–]\s+|\s*[(\[（【]", value[token.end():end])
            if cut and cut.start() > 0:
                end = token.end() + cut.start()
        spans.append(value[token.end():end])
    return spans


# Words that end up after a guest name in video and upload titles.
TITLE_NOISE = re.compile(r"\s*(?:official\s+(?:music\s+)?video|music\s+video|lyrics?\s+video|official\s+audio|"
                         r"\bmv\b|\bm/v\b|\blyrics?\b|\bhd\b|\b4k\b)\s*$", re.I)


def _strip_wrapping(name):
    previous = None
    while previous != name:
        previous, name = name, TITLE_NOISE.sub("", name)
    name = name.strip(" .-–")
    while len(name) > 1 and name[0] in OPEN and name[-1] in CLOSE and \
            OPEN.index(name[0]) == CLOSE.index(name[-1]):
        name = name[1:-1].strip(" .-–")
    return name


def _top_level_split(text):
    """Split on guest separators that are not inside brackets."""
    parts, depth, last = [], 0, 0
    masked = []
    for char in text:
        if char in OPEN:
            depth += 1
        elif char in CLOSE and depth:
            depth -= 1
        masked.append(char if depth == 0 or char in OPEN + CLOSE else "\0")
    masked = "".join(masked)
    for match in GUEST_SPLIT.finditer(masked):
        parts.append(text[last:match.start()])
        last = match.end()
    parts.append(text[last:])
    return parts


@functools.lru_cache(maxsize=4)
def _duo_patterns(duos):
    keep = sorted({clean(d) for d in duos if GUEST_SPLIT.search(clean(d))}, key=len, reverse=True)
    return [re.compile(rf"(?<!\w){re.escape(d)}(?!\w)", re.I) for d in keep]


def featured(title, duos=()):
    """Guest names in a title: (names, problem). ``duos``: names of acts kept whole ("Blu & Exile").

    "Pumpkin Seeds (feat. Lupe Fiasco)" → (["Lupe Fiasco"], None).
    "Friendship ft" → ([], "no name after feat/ft").
    """
    value = clean(title)
    spans = [s for s in _guest_spans(value)]
    if not spans:
        return [], ("no name after feat/ft" if FEAT_WORD.search(value) else None)
    names = []
    for span in spans:
        rest = span
        for duo in _duo_patterns(tuple(duos)):
            found = duo.search(rest)
            if found:
                names.append(found.group())
                rest = f"{rest[:found.start()]} , {rest[found.end():]}"
        names += _top_level_split(rest)
    out = []
    for name in names:
        name = _strip_wrapping(name)
        if not name or (len(name) == 1 and not HAS_CJK.search(name)):
            continue  # a stray letter ("ft i/ Daddy Yankee") is not an artist
        if len(name) <= 200 and match_key(name) not in {match_key(n) for n in out}:
            out.append(name)
    if not out:
        return [], "no name after feat/ft"
    return out, None
