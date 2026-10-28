"""Download item icons from Deadlock Wiki and embed them into Deadlock-Stacking.html.

Run from anywhere:  python tools/build_icons.py
Item names are read from CATS in the HTML; the result replaces the block
between /* ICONS:BEGIN */ and /* ICONS:END */ with `const ICONS = {...}`.
Downloads are cached in tools/.icon-cache/ so reruns are offline.
"""
import base64
import io
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "Deadlock-Stacking.html"
CACHE = ROOT / "tools" / ".icon-cache"
CDN = "https://cdn.deadlock.wiki/"
SIZE = 72      # shown at 36px, doubled for sharp screens
QUALITY = 80

# item name -> wiki file name (without .png) when they differ
RENAME = {}

HERO_SIZE = 48  # hero portraits next to abilities, shown at 20–24px

# non-item icons: key in ICONS -> height in px (keeps the aspect ratio)
EXTRA = {"Souls": 36,  # soul glyph next to prices, shown 18px tall
         # scaling badges in ability cards, shown 14px tall
         "Spirit_scaling": 28, "Weapon_scaling": 28, "Melee_scaling": 28, "Boon_scaling": 28}

# variants like "Vampiric Burst (active)" use the base item's icon; keep in sync with iconOf() in the HTML
SUFFIX = re.compile(r" \((active|on hit|max)\)$")


def item_names(html):
    cats = html.split("const CATS", 1)[1].split("\n];", 1)[0]
    found = re.findall(r"\[(?:'([^']+)'|\"([^\"]+)\"),\d+,\d+", cats)
    return sorted({SUFFIX.sub("", a or b) for a, b in found})


def ability_icons(html):
    """ICONS keys and wiki file names for heroes and abilities listed in the ABILITIES block."""
    m = re.search(r"const ABILITIES = \[(.*?)\];", html, re.S)
    rows = [json.loads(line.rstrip(",")) for line in (m.group(1).strip().splitlines() if m else []) if line.strip()]
    jobs = {}
    for r in rows:
        jobs["hero:" + r["hero"]] = (r["hero"], HERO_SIZE)
        jobs["ab:" + r["name"]] = (r["name"], None)
    return jobs


def fetch(name):
    stem = RENAME.get(name, name).replace(" ", "_")
    path = CACHE / (stem + ".png")
    if not path.exists():
        url = CDN + urllib.parse.quote(stem) + ".png"
        req = urllib.request.Request(url, headers={"User-Agent": "deadlock-stacking-icons/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            path.write_bytes(r.read())
    return path


def to_data_uri(path, height=None):
    img = Image.open(path).convert("RGBA")
    size = (round(img.width * height / img.height), height) if height else (SIZE, SIZE)
    img = img.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=QUALITY, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    html = HTML.read_text(encoding="utf-8")
    icons, missing = {}, []
    # (ICONS key, wiki file name, height or None for a SIZE square)
    jobs = [(name, name, None) for name in item_names(html)] + [(k, k, h) for k, h in EXTRA.items()]
    jobs += [(key, file, h) for key, (file, h) in ability_icons(html).items()]
    for key, file, height in jobs:
        try:
            icons[key] = to_data_uri(fetch(file), height)
        except urllib.error.HTTPError as e:
            missing.append(f"{key} (HTTP {e.code})")

    body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v)}" for k, v in icons.items())
    block = "/* ICONS:BEGIN */\nconst ICONS = {\n" + body + "\n};\n/* ICONS:END */"
    new, n = re.subn(r"/\* ICONS:BEGIN \*/.*?/\* ICONS:END \*/", lambda m: block, html, flags=re.S)
    if n != 1:
        sys.exit("ICONS:BEGIN/END markers not found in the HTML")
    HTML.write_text(new, encoding="utf-8", newline="\n")

    size = sum(len(v) for v in icons.values())
    print(f"embedded {len(icons)} icons, {size / 1024:.0f} KB of data URIs")
    if missing:
        print("no icon for:\n  " + "\n  ".join(missing))


if __name__ == "__main__":
    main()
