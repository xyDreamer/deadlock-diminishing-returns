"""Download item cards (wiki infobox) from Deadlock Wiki and embed them into Deadlock-Stacking.html.

Run from anywhere:  python tools/build_cards.py
Each item's {{Infobox item/auto}} is rendered through the wiki API; only the Default
tab is parsed. The result replaces the block between /* CARDS:BEGIN */ and
/* CARDS:END */ with `const CARDS = {...}`. Downloads are cached in
tools/.icon-cache/cards/ so reruns are offline.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from html import escape

from bs4 import BeautifulSoup, NavigableString

from build_icons import HTML, CACHE, item_names

CARD_CACHE = CACHE / "cards"
API = "https://deadlock.wiki/api.php"
SLOTS = {"weapon": "w", "spirit": "s", "vitality": "v"}


def fetch(name):
    path = CARD_CACHE / (name.replace(" ", "_") + ".html")
    if not path.exists():
        query = urllib.parse.urlencode({
            "action": "parse", "format": "json", "prop": "text", "contentmodel": "wikitext",
            "text": "{{Infobox item/auto|%s}}" % name,
        })
        req = urllib.request.Request(API + "?" + query, headers={"User-Agent": "deadlock-stacking-cards/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_text(json.load(r)["parse"]["text"]["*"], encoding="utf-8")
    return path.read_text(encoding="utf-8")


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def rich(el):
    """Description text with the wiki's bold keywords kept as <b>, italic notes as <i>, line breaks as <br>."""
    out = []
    for node in el.children:
        if isinstance(node, NavigableString):
            out.append(escape(str(node)))
            continue
        style = (node.get("style") or "").replace(" ", "")
        if node.name == "br":
            out.append("<br>")
        elif "font-weight:bold" in style or node.name == "b":
            out.append("<b>" + escape(node.get_text()) + "</b>")
        elif "font-style:italic" in style or node.name == "i":
            out.append("<i>" + rich(node) + "</i>")
        else:
            out.append(rich(node))
    return re.sub(r"\s+", " ", "".join(out))


def stats(container):
    """Big stats ([value, label]) and small extra lines from a passive/active stat block."""
    main, notes = [], []
    for el in container.find_all(class_=re.compile(r"^(passive|active)-stat$")):
        full = text(el)
        if any(c.endswith("-main-stat") for c in el["class"]):
            value = text(el.find(class_="stat-attr"))
            main.append([value, full[len(value):].strip() if full.startswith(value) else full])
        else:
            notes.append(full)
    return main, notes


def parse(html):
    table = BeautifulSoup(html, "html.parser").find(id="tabber-Default").find("table")
    slot = next(SLOTS[c[len("item-type-"):]] for c in table["class"] if c.startswith("item-type-"))
    card = {"slot": slot, "tier": None, "cost": None, "innate": [], "sections": [], "from": [], "to": []}
    section = None
    for cell in table.find_all(["td", "th"]):
        cls = cell.get("class") or []
        if "infobox-label" in cls:
            value = text(cell.find_next_sibling(class_="infobox-value")).replace(",", "")
            if text(cell) in ("Cost", "Tier") and value.isdigit():
                card[text(cell).lower()] = int(value)
        elif "infobox-stat" in cls:
            card["innate"].append(text(cell))
        elif "passive-header" in cls or "active-header" in cls:
            section = {"kind": "Active" if "active-header" in cls else "Passive", "cd": None, "desc": "", "stats": [], "notes": []}
            card["sections"].append(section)
        elif "cooldown-value" in cls or "chargeup-value" in cls:
            section["cd"] = text(cell)
        elif "passive-description" in cls or "active-description" in cls:
            section["desc"] = rich(cell).strip()
        elif any(c.endswith("-stat-container") for c in cls) and text(cell):
            if section is None:  # stats without a header still belong to a passive block
                section = {"kind": "Passive", "cd": None, "desc": "", "stats": [], "notes": []}
                card["sections"].append(section)
            main, notes = stats(cell)
            section["stats"] += main
            section["notes"] += notes
        elif "components-header" in cls:
            card["from"].append(text(cell).split(":", 1)[-1].strip())  # later rows carry just the name
        elif "is-component-of-header" in cls:
            card["to"].append(text(cell).split(":", 1)[-1].strip())
    return card


def main():
    CARD_CACHE.mkdir(parents=True, exist_ok=True)
    page = HTML.read_text(encoding="utf-8")
    cards, failed = {}, []
    for name in item_names(page):
        try:
            cards[name] = parse(fetch(name))
        except Exception as e:  # report and keep going: one odd page shouldn't block the rest
            failed.append(f"{name} ({type(e).__name__}: {e})")

    body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                      for k, v in cards.items())
    block = "/* CARDS:BEGIN */\nconst CARDS = {\n" + body + "\n};\n/* CARDS:END */"
    new, n = re.subn(r"/\* CARDS:BEGIN \*/.*?/\* CARDS:END \*/", lambda m: block, page, flags=re.S)
    if n != 1:
        sys.exit("CARDS:BEGIN/END markers not found in the HTML")
    HTML.write_text(new, encoding="utf-8", newline="\n")

    print(f"embedded {len(cards)} cards, {len(body) / 1024:.0f} KB")
    if failed:
        print("failed:\n  " + "\n  ".join(failed))


if __name__ == "__main__":
    main()
