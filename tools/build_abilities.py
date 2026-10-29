"""Collect hero abilities that change the app's stats and embed them into Deadlock-Stacking.html.

Run from anywhere:  python tools/build_abilities.py   (then python tools/build_icons.py for their icons)
Source: Data:AbilityData.json and Data:HeroData.json on Deadlock Wiki (cached in tools/.icon-cache/).
Each ability property that maps to a stat becomes one row per distinct value across upgrade
levels; the row is labelled with the upgrade that first gives that value (0 / 1 / 2 / 5 points).
The result replaces the block between /* ABILITIES:BEGIN */ and /* ABILITIES:END */.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from build_cards import API, rich, text
from build_icons import HTML, CACHE

CARD_CACHE = CACHE / "abcards"

DATA = "https://deadlock.wiki/index.php?title=Data:%s.json&action=raw"
LEVELS = [0, 1, 2, 5]  # ability point cost of each upgrade: base, 1st, 2nd, 3rd

# ability property -> [(stat tab id, column, applies only to the ability itself)]
SLOW = [("slow", "a", False)]
PROPS = {
    "HealAmpReceivePenaltyPercent": [("antiheal", "a", False)],
    "BulletLifestealPercent": [("bls", "a", False)],
    "BulletLifesteal": [("bls", "a", False)],
    "LifestealPercentHero": [("bls", "a", False), ("sls", "a", False)],  # Napalm: "+15% Lifesteal", both kinds
    "TechLifestealPercent": [("sls", "a", False)],
    "AbilityLifestealPercentHero": [("sls", "a", True)],
    "LifeStealPercentOnHit": [("sls", "a", True)],
    "BeamLifesteal": [("sls", "a", True)],
    "BulletResist": [("bullet", "a", False)],
    "BulletResistOnActive": [("bullet", "a", False)],
    "BulletResistReduction": [("bullet", "b", False)],
    "BulletArmorReduction": [("bullet", "b", False)],
    "DiamondResistShred": [("bullet", "b", False), ("spirit", "b", False)],
    "TechResist": [("spirit", "a", False)],
    "SpiritResist": [("spirit", "a", False)],
    "TechArmorGain": [("spirit", "a", False)],
    "TechArmorDamageReduction": [("spirit", "b", False)],
    "TechResistDebuff": [("spirit", "b", False)],
    "MaxStolenSpiritResist": [("spirit", "b", False)],
    "SlowPercent": SLOW, "EnemySlowPct": SLOW, "MoveSpeedSlowPct": SLOW, "MoveSlowPercent": SLOW,
    "MovementSlowPct": SLOW, "MovementSlow": SLOW, "MovementSpeedSlow": SLOW, "ClubSlowPercent": SLOW,
    "CoatedSlowPercent": SLOW, "FadingSlowPercent": SLOW, "MaxSlowPercent": SLOW, "BigExplosionSlowPercent": SLOW,
    "DamageSlowPercent": SLOW, "SpinSlowPercent": SLOW, "MoveSpeedSlowMaxPct": SLOW,
    "SlowResistance": [("slow", "b", False)],
    "SlowResistancePercent": [("slow", "b", False)],
    "BonusFireRate": [("firerate", "a", False)],
    "FireRateSlow": [("firerate", "b", False)],
    "MaxFireRateSlowPercent": [("firerate", "b", False)],
    "CooldownRefundPercent": [("cdr", "a", True)],
    "CurrentCooldownRefundPercent": [("cdr", "a", True)],
    "CancelCooldownRefundPercentage": [("cdr", "a", True)],
}
# short hint shown next to the ability name when one ability has several properties for a stat
NOTES = {
    "BigExplosionSlowPercent": "большой взрыв", "DamageSlowPercent": "при уроне", "SpinSlowPercent": "вращение",
    "ClubSlowPercent": "трефы", "DiamondResistShred": "бубны", "BulletResistOnActive": "при активации",
    "MaxSlowPercent": "максимум", "MaxFireRateSlowPercent": "максимум", "FadingSlowPercent": "затухает",
    "MoveSpeedSlowMaxPct": "максимум", "BeamLifesteal": "луч", "LifeStealPercentOnHit": "от урона способности",
}
# columns whose abilities debuff enemies: several heroes can stack there
ENEMY = {("antiheal", "a"), ("bullet", "b"), ("spirit", "b"), ("slow", "a"), ("firerate", "b")}
# abilities that buff allies (in every column, or only in the given one): heroes on one team can stack them
ALLY = {("Kudzu Connection", None), ("Plot Armor", None),
        ("Medicinal Specter", "spirit"), ("Lil Helpers", "spirit")}
# (hero, ability, property) whose percent only works for the ability's own shots
SELF = {("Silver", "Slam Fire", "BonusFireRate")}


def load(name):
    path = CACHE / f"{name}.json"
    if not path.exists():
        req = urllib.request.Request(DATA % name, headers={"User-Agent": "deadlock-stacking-abilities/1.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            path.write_bytes(r.read())
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_card(hero, slot):
    """HTML of the wiki's ability card ({{AbilitySection}}) for a hero's ability slot."""
    path = CARD_CACHE / f"{hero.replace(' ', '_')}_{slot}.html"
    if not path.exists():
        query = urllib.parse.urlencode({
            "action": "parse", "format": "json", "prop": "text", "contentmodel": "wikitext",
            "text": "{{AbilitySection|hero_name=%s|ability_num=%s}}" % (hero, slot),
        })
        req = urllib.request.Request(API + "?" + query, headers={"User-Agent": "deadlock-stacking-abilities/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_text(json.load(r)["parse"]["text"]["*"], encoding="utf-8")
    return path.read_text(encoding="utf-8")


def tight(el):
    return re.sub(r"\s+", "", el.get_text()) if el else ""


def scale_kind(box):
    """What the value scales with: spirit / weapon / melee / boon (wiki class ac-scaling--<kind>)."""
    el = box.select_one("[class*='ac-scaling--']")
    if not el:
        return ""
    return next(c[len("ac-scaling--"):] for c in el["class"] if c.startswith("ac-scaling--"))


def parse_card(html):
    """Base (no upgrades) view of the ability card: header stats, description, stat boxes, upgrades."""
    card = BeautifulSoup(html, "html.parser").select_one(".ability-card")
    head = []
    for h in card.select(".ability-headerattr"):
        label = h.find(title=True)
        if "hide" not in h["class"] and label:
            head.append([label["title"], tight(h)])
    sections = []
    for info in card.select(".ability-info"):  # separate stat groups (e.g. on collide / on wall hit); hidden boxes appear only with upgrades
        boxes = [{"t": text(m.select_one(".ac-mainbox-title")).rstrip(":"),
                  "e": text(m.select_one(".ac-statusbox-effect")),
                  "v": tight(m.select_one(".ac-mainbox-value, .ac-statusbox-value")),
                  "l": text(m.select_one(".ac-mainbox-label, .ac-statusbox-label")),
                  "s": tight(m.select_one(".ac-mainbox-scale")),
                  "sk": scale_kind(m)}
                 for m in info.select(".ability-mainbox") if "hide" not in m["class"]]
        alt = [text(x) for x in info.select(".ac-info-alt > *") if text(x)]
        desc = info.select_one(".ac-info-desc")
        desc = rich(desc).strip() if desc else ""
        if desc or boxes or alt:
            sections.append({"desc": desc, "boxes": boxes, "alt": alt})
    ups = [[tight(u.select_one(".ability-upgrade-header")), rich(u.select_one(".ac-upgrade-desc")).strip()]
           for u in card.select(".ability-upgrade")]
    return {"head": head, "sections": sections, "ups": ups}


def who(tab, side, name):
    """enemy: debuff on enemies; ally: buff others get too; self: buffs only the caster, so one hero per column."""
    if (tab, side) in ENEMY:
        return "enemy"
    return "ally" if (name, None) in ALLY or (name, tab) in ALLY else "self"


def value(x):
    return x.get("Value") if isinstance(x, dict) else x


def levels(ability, prop):
    """Value of a property at each upgrade level (upgrades add on top of the base)."""
    cur = value(ability.get(prop)) or 0
    out = [cur]
    for up in [u for u in ability.get("Upgrades") or [] if isinstance(u, dict)]:
        if prop in up:
            cur = (cur or 0) + (value(up[prop]) or 0)
        out.append(cur)
    return (out + [cur] * 4)[:4]


def main():
    A, H = load("AbilityData"), load("HeroData")
    heroes = sorted((h for h in H.values() if isinstance(h, dict) and h.get("IsSelectable")
                     and not h.get("IsDisabled") and not h.get("InDevelopment")), key=lambda h: h["Name"])
    rows, seen, sources = [], set(), {}  # sources: ability name -> (wiki hero name, slot) for its card
    for hero in heroes:
        name = hero["Name"].replace(" (Transformed)", "")
        for slot in sorted(hero.get("BoundAbilities") or {}, key=int):
            ab = A.get(hero["BoundAbilities"][slot]["Key"])
            if not isinstance(ab, dict):
                continue
            for prop, targets in PROPS.items():
                vals = [round(abs(v), 2) for v in levels(ab, prop)]
                if prop == "BonusFireRate" and min(levels(ab, prop)) < 0:
                    continue  # a fire rate penalty on yourself, not a bonus
                last = 0
                for lvl, v in enumerate(vals):
                    if not v or v == last or v > 100 and prop != "BonusFireRate":
                        last = v or last
                        continue
                    last = v
                    for tab, side, own in targets:
                        key = (name, ab["Name"], prop, tab, side, v)
                        if key in seen:  # Silver's wolf form repeats Lycan Curse
                            continue
                        seen.add(key)
                        sources.setdefault(ab["Name"], (hero["Name"], slot))
                        rows.append({"tab": tab, "side": side, "hero": name, "slot": int(slot), "name": ab["Name"],
                                     "lvl": LEVELS[lvl], "v": v, "self": own or (name, ab["Name"], prop) in SELF,
                                     "note": NOTES.get(prop, ""), "who": who(tab, side, ab["Name"])})

    rows.sort(key=lambda r: (r["tab"], r["side"], r["hero"], r["slot"], r["note"], r["lvl"]))
    body = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)

    CARD_CACHE.mkdir(parents=True, exist_ok=True)
    cards, failed = {}, []
    for name, (hero, slot) in sorted(sources.items()):
        try:
            cards[name] = parse_card(fetch_card(hero, slot))
        except Exception as e:  # report and keep going: one odd card shouldn't block the rest
            failed.append(f"{hero} {slot} {name} ({type(e).__name__}: {e})")
    cards_body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                            for k, v in cards.items())

    block = ("/* ABILITIES:BEGIN */\nconst ABILITIES = [\n" + body + "\n];\n"
             "// ability cards: base view of the wiki's {{AbilitySection}}, keyed by ability name\n"
             "const ABCARDS = {\n" + cards_body + "\n};\n/* ABILITIES:END */")
    page = HTML.read_text(encoding="utf-8")
    new, n = re.subn(r"/\* ABILITIES:BEGIN \*/.*?/\* ABILITIES:END \*/", lambda m: block, page, flags=re.S)
    if n != 1:
        sys.exit("ABILITIES:BEGIN/END markers not found in the HTML")
    HTML.write_text(new, encoding="utf-8", newline="\n")
    print(f"embedded {len(rows)} ability rows for {len({r['hero'] for r in rows})} heroes, {len(cards)} cards")
    if failed:
        print("cards failed:\n  " + "\n  ".join(failed))


if __name__ == "__main__":
    main()
