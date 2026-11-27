"""Market specs from a client scope sheet (one row per market: title, definition, segmentation, exclusions).

Turns each row into what the fleet needs for a market that is not the gearbox market:
  taxonomy   geography tree + segmentation dimensions (taxonomy.json shape)
  spec       definition text, exclusions, scope rulebook (verbatim, no LLM), per-segment notes

Segmentation text conventions handled (as written by the research team):
  "By X" / "1. By X"        a dimension header
  "- item" / "  - item"     items; two spaces of indent per level
  bare lines                top-level items in dimensions written as "Group" + "- member" lists
  "• item" / "  ◦ item"     bulleted sheets: "•" is a segment, "◦" or a deeper-indented "•" its sub-segment;
                            "Total" rows and "X Vs Y" cross-tables are dropped (text without bullets is
                            parsed exactly as before)
Levels below the second are not separate rows in the Input Sheet; they are kept as the
"covers" note of their level-2 parent so analysts still see them. Duplicate names within one
dimension get their parent in brackets so every row is unique.
"By Region" / "By Country" dimensions describe geography, not segments: a country list becomes
the geography tree for regional markets.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from openpyxl import load_workbook

PKG = Path(__file__).resolve().parent
HEADER = re.compile(r"^\s*(\d+\.\s*)?By\s+(.+?)\s*$", re.I)
GEO_DIM = re.compile(r"(?i)^by\s+(region|country|countries|geography)\b")

# Research team's healthcare geography: identical in every global healthcare ME of the corpus (HIFU, Next
# Generation Probiotics, Water for Injection Container, Protein Biomarkers, Codeine, Laser Photo-Rejuvenation).
APAC_DEFAULT = ["China", "India", "Japan", "South Korea", "ASEAN", "Australia", "Rest of Asia Pacific"]
HC_GLOBAL = {"Global": {
    "North America": ["U.S.", "Canada"],
    "Europe": ["U.K.", "Germany", "France", "Italy", "Spain", "Russia", "Rest of Europe"],
    "Asia Pacific": APAC_DEFAULT,
    "Latin America": ["Brazil", "Mexico", "Argentina", "Rest of Latin America"],
    "Middle East": ["GCC", "Israel", "Rest of Middle East"],
    "Africa": ["South Africa", "North Africa", "Central Africa"],
}}


def _standard_global() -> dict:
    return copy.deepcopy(HC_GLOBAL)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


BULLETS = "•◦▪"
XTAB = re.compile(r"(?i)\bvs\.?\s")


def _bullet_blocks(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Bulleted segmentation text ("•" items, "◦" or deeper-indented "•" sub-items) -> [(title, [(level, name)])].
    A non-indented line without a bullet is a dimension header; "X Vs Y" cross-tables and "Total" rows are dropped."""
    raw: list[tuple[str, list[str]]] = []
    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if not s:
            continue
        is_item = s[0] in BULLETS or s.startswith("-")
        m = HEADER.match(line)
        if not is_item and (m or not line[0].isspace()):
            raw.append(("By " + _clean(m.group(2)) if m else _clean(s), []))
        elif raw:
            raw[-1][1].append(line.rstrip())
    blocks = []
    for title, lines in raw:
        if XTAB.search(title):
            continue                                # cross-table of two dimensions, not a dimension
        dot_indents = sorted({len(l) - len(l.lstrip()) for l in lines if l.strip()[0] in "•▪-"})
        items, last = [], 0
        for l in lines:
            s = l.strip()
            name = _clean(s.lstrip(BULLETS + "-"))
            if not name or name.lower() == "total":
                continue
            if s[0] == "◦":
                lv = last + 1                       # hollow bullet: one level below the "•" above it
            else:
                ind = len(l) - len(l.lstrip())
                lv = dot_indents.index(ind) if ind in dot_indents else 0
                last = lv
            items.append((lv, name))
        blocks.append((title, items))
    return blocks


def parse_segmentation(text: str) -> tuple[list[dict], dict[str, list[str]]]:
    """-> (dimensions in taxonomy shape, geography dims {title: [names]})."""
    if any(c in (text or "") for c in BULLETS):
        # bulleted sheets: rewrite each dimension in the dashed form below and parse it the same way
        dims, geo_dims = [], {}
        for title, items in _bullet_blocks(text):
            if GEO_DIM.match(title):
                geo_dims[title] = [n for lv, n in items if lv == 0]
                continue
            d, _ = parse_segmentation("\n".join(["By DIMENSION"] + [("  " * (lv - 1) + "- " + n) if lv else n
                                                                     for lv, n in items]))
            for x in d:
                x["title"] = title
            dims += d
        return dims, geo_dims
    blocks: list[tuple[str, list[str]]] = []
    for line in (text or "").replace("\r", "").split("\n"):
        if not line.strip():
            continue
        m = HEADER.match(line)
        if m and not line.lstrip().startswith("-"):
            blocks.append(("By " + _clean(m.group(2)), []))
        elif blocks:
            blocks[-1][1].append(line.rstrip())
    dims, geo_dims = [], {}
    for title, lines in blocks:
        bare = any(not l.lstrip().startswith("-") for l in lines)
        items: list[tuple[int, str]] = []
        for l in lines:
            indent = len(l) - len(l.lstrip(" "))
            dashed = l.lstrip().startswith("-")
            name = _clean(l.lstrip().lstrip("-"))
            if not name:
                continue
            level = (indent // 2 + (1 if bare else 0)) if dashed else 0
            items.append((level, name))
        if GEO_DIM.match(title):
            geo_dims[title] = [n for lv, n in items if lv == 0]
            continue
        segs: list[dict] = []
        covers: dict[str, list[str]] = {}
        cur_top, cur_mid = None, None
        for lv, name in items:
            if lv == 0:
                cur_top = {"name": name, "children": []}
                segs.append(cur_top)
                cur_mid = None
            elif lv == 1 and cur_top is not None:
                cur_top["children"].append(name)
                cur_mid = (cur_top["name"], name)
            elif cur_mid is not None:
                covers.setdefault(f"{cur_mid[0]}||{cur_mid[1]}", []).append(name)
        # unique names within the dimension (children may repeat across parents, e.g. "CRISPR")
        seen: dict[str, int] = {}
        for s in segs:
            seen[s["name"]] = seen.get(s["name"], 0) + 1
            for c in s["children"]:
                seen[c] = seen.get(c, 0) + 1
        out_segs, notes = [], {}
        for s in segs:
            kids = []
            for c in s["children"]:
                nm = f"{c} ({s['name']})" if seen[c] > 1 else c
                kids.append(nm)
                if covers.get(f"{s['name']}||{c}"):
                    notes[nm] = "covers: " + ", ".join(covers[f"{s['name']}||{c}"])
            seg = {"name": s["name"]}
            if len(kids) >= 2:                      # a single child is not a split
                seg["children"] = kids
            elif kids:
                notes[s["name"]] = (notes.get(s["name"], "") + f" covers: {kids[0]}").strip()
            out_segs.append(seg)
        if len(out_segs) >= 2:
            dims.append({"title": title, "segments": out_segs, "notes": notes})
    return dims, geo_dims


def geography_for(title: str, geo_dims: dict[str, list[str]]) -> dict:
    t = title.lower()
    countries = next((v for k, v in geo_dims.items() if re.search(r"(?i)countr", k)), None)
    if t.startswith("japan"):
        return {"Japan": None}
    if t.startswith(("asia pacific", "apac", "asia-pacific")):
        return {"Asia Pacific": countries or APAC_DEFAULT}
    return _standard_global()


def _rulebook(title: str, definition: str, exclusions: str) -> str:
    lines = ["SCOPE RULEBOOK (client scope sheet - binding for every number):", f"  MARKET: {title}"]
    lines += [f"  {p.strip()}" for p in re.split(r"\n\s*\n", definition or "") if p.strip()]
    ex = [_clean(x.lstrip("•·-*• ")) for x in re.split(r"\n+", exclusions or "") if _clean(x)]
    if ex:
        lines.append("DO NOT COUNT (excluded):")
        lines += [f"  - {x}" for x in ex]
    return "\n".join(lines)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def load_sheet(path: Path) -> list[dict]:
    """Every market row of the scope sheet -> spec dict (taxonomy included)."""
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h or "").strip().lower() for h in rows[0]]

    def col(*keys):
        return next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)
    ci, cd, cs, ce = col("title"), col("definition"), col("segment", "taxonomy"), col("exclusion")
    specs = []
    for r in rows[1:]:
        title = _clean(str(r[ci] or ""))
        if not title:
            continue
        title = title.replace("CRISPER", "CRISPR")
        definition, seg_text, excl = (str(r[c] or "") if c is not None else "" for c in (cd, cs, ce))
        dims, geo_dims = parse_segmentation(seg_text)
        notes = {s: n for d in dims for s, n in d.pop("notes").items()}
        for i, d in enumerate(dims):
            d["priced"] = i == 0                   # first dimension is the product view in these sheets
        short = re.sub(r"(?i)^(global\s*(&|and)\s*(u\.?s\.?|russia)|global|asia pacific|japan)\s+", "", title)
        short = re.sub(r"(?i)\s+market$", "", short).strip().lower()
        tax = {"_note": f"Generated from the client scope sheet ({path.name}); labels only.",
               "market_name": title, "short_name": short, "base_year": 2025, "forecast_end_year": 2032,
               "currency": "US$ Mn", "geography": geography_for(title, geo_dims), "dimensions": dims,
               "strategies": {}}
        specs.append({"title": title, "slug": slug(title), "short_name": short, "definition": definition.strip(),
                      "exclusions": excl.strip(), "segmentation_text": seg_text.strip(), "taxonomy": tax,
                      "segment_notes": notes, "geo_dims": geo_dims,
                      "scope_rules": _rulebook(title, definition, excl),
                      "definitions_text": f"MARKET: {title}\n\nDEFINITION:\n{definition.strip()}\n\nSEGMENTATION:\n"
                                          f"{seg_text.strip()}\n\nEXCLUSIONS (out of scope):\n{excl.strip()}"})
    return specs


def strategies(tax: dict, roles: dict[str, str]) -> dict:
    """2A / 2B / 2C sheet text for a generic market, from the dimension roles (use_case / type / other)."""
    uc = [d for d, r in roles.items() if r == "use_case"]
    ty = [d for d, r in roles.items() if r != "use_case"]
    name = tax["short_name"]
    return {
        "2A": {"name": "Country totals, then top-down", "dimension": None,
               "stands_for": f"How big the {name} market is in total, in each region and country, and how fast each grows.",
               "in_this_market": "The global (or regional) 2025 value is triangulated from a demand-side bottom-up build "
                                 "(volume x price per product segment), a top-down share of a parent market and published "
                                 "figures converted to the client scope. It is then split top-down across regions and "
                                 "countries, each with a share of its parent and a 2025-2032 growth rate; growth rates are "
                                 "reconciled so children add up to their parent.",
               "produces": "Sizing block (share, CAGR, value 2025 per geography); country CAGR row; country price index on the ASP sheet."},
        "2B": {"name": "Use case x country", "dimension": None,
               "stands_for": "WHO uses the product and FOR WHAT - applications, indications, end users, channels.",
               "in_this_market": "Dimensions treated as use cases: " + (", ".join(uc) or "none") + ". Each country's 2A "
                                 "total is split by each of them; segment growth = country growth + a premium, re-centred "
                                 "so segments average back to the country rate.",
               "produces": "Shares and growth rates of the use-case dimensions for every country."},
        "2C": {"name": "Product type x country", "dimension": None,
               "stands_for": "WHAT is sold - product types, formulations, brands, offerings, formats.",
               "in_this_market": "Dimensions treated as product types: " + (", ".join(ty) or "none") + ". The first "
                                 "(priced) dimension also carries the bottom-up build: volume x price per segment, which "
                                 "feeds the 2A anchor and the ASP base prices.",
               "produces": "Shares and growth rates of the product dimensions, the bottom-up sizing route and the ASP sheet."},
        "how_they_combine": "2A sets every total. Every segmentation dimension is an independent split of the SAME country "
                            "total; dimensions are not multiplied together.",
    }
