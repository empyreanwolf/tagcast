"""One-off helper (fork v49): make every CSS font size follow the text-size setting.

Rewrites `font-size:Npx` as `font-size:calc(Mpx*var(--ts))`, where --ts is 0.92 / 1 / 1.15 / 1.3
(set from the data-text-size attribute on <html>) and M is N raised to a readable minimum:
12px for ordinary text, 11px for small uppercase or letter-spaced labels. Decorative text
(logo tagline, avatar initials, cover placeholder caption, glyph buttons) keeps its size.
Safe to run again: sizes already inside calc() are left alone.
"""
import re
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "tagcast" / "static"
KEEP = ("brand small", "brand-description", "cover-caption", "artist-avatar", "suggestion-art span",
        ".play", "cover-button>span", "col-bar b", "top-list li:before", ".score", "library-total small")


def minimum(selector, body):
    if any(k in selector for k in KEEP):
        return 0
    return 11 if re.search(r"text-transform:\s*uppercase|letter-spacing", body) else 12


def rewrite(css):
    out, pos, count = [], 0, 0
    # Walk rule by rule; @media wrappers are transparent because only the innermost {...} is matched.
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = m.group(1), m.group(2)
        if "font-size" not in body:
            continue
        floor = minimum(selector, body)

        def one(f):
            nonlocal count
            n = float(f.group(2))
            count += 1
            new = max(n, floor)
            text = f"{new:g}"
            return f"{f.group(1)}calc({text}px*var(--ts))"
        new_body = re.sub(r"(font-size:\s*)([0-9.]+)px(?!\*var)", one, body)
        new_body = re.sub(r"font-size:clamp\(\s*([0-9.]+)px\s*,([^,]+),\s*([0-9.]+)px\s*\)",
                          lambda c: f"font-size:clamp(calc({c.group(1)}px*var(--ts)),{c.group(2)},calc({c.group(3)}px*var(--ts)))",
                          new_body)
        out.append((m.start(2), m.end(2), new_body))
    for start, end, new_body in reversed(out):
        css = css[:start] + new_body + css[end:]
    return css, count


if __name__ == "__main__":
    for name in ("style.css", "gapless-controls.css"):
        path = STATIC / name
        css, count = rewrite(path.read_text(encoding="utf-8"))
        path.write_text(css, encoding="utf-8")
        print(f"{name}: {count} font sizes now follow --ts")
