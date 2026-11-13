"""Parse a human ME workbook (one sheet per geography) into a uniform record.

Layout handled (all the CMI-style MEs in April/May/July and the gearbox ME):
  - a header row holding >= 5 consecutive years (2015-2040)
  - labels in the column(s) left of the first year column
  - a value block first ("Market Value (US$ Mn)"); later ASP / Volume blocks are cut off,
    except the volume total, which is kept for an implied price trend
  - inside the value block, groups separated by blank rows: the first row of a group is the
    dimension header carrying the total, the rest are segments ("By Region"/"By Country"
    groups are the geography children)
  - nested sub-segments are detected arithmetically (a row equal to the sum of the rows
    right after it), because read-only openpyxl does not give indents reliably
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

YEAR_MIN, YEAR_MAX = 2015, 2040
GEO_DIM = re.compile(r"(?i)\b(region|country|countries|geograph|state|states|city|cities|zone|province|sub-?region)\b|^region_")
STOP_BLOCK = re.compile(r"(?i)(\basp\b|volume|price|\bunits?\b|y-?o-?y|market share|growth)")


GEO_WORDS = {w.lower() for w in (
    "Global", "World", "North America", "Latin America", "South America", "Central America", "Europe", "Western Europe",
    "Eastern Europe", "Asia Pacific", "APAC", "Asia", "Middle East", "Africa", "Middle East and Africa", "Middle East & Africa",
    "MEA", "MENA", "GCC", "LATAM", "South East Asia", "Southeast Asia", "ASEAN", "Oceania", "U.S.", "US", "USA",
    "United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru", "Germany", "U.K.", "UK",
    "United Kingdom", "France", "Italy", "Spain", "Russia", "Netherlands", "Poland", "China", "India", "Japan",
    "South Korea", "Australia", "Indonesia", "Thailand", "Vietnam", "Malaysia", "Philippines", "Singapore",
    "Saudi Arabia", "UAE", "Israel", "Turkey", "South Africa", "Egypt", "Nigeria", "Kenya", "Qatar", "Kuwait", "Oman",
    "Bahrain", "Northeast", "Southeast", "Midwest", "Southwest", "West")}


def is_geo_name(s: str) -> bool:
    t = norm(s).lower()
    return t in GEO_WORDS or t.startswith("rest of") or t.split(" (")[0] in GEO_WORDS


def norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()


def _as_year(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer() and YEAR_MIN <= v <= YEAR_MAX:
        return int(v)
    if isinstance(v, str):
        m = re.match(r"^\s*((?:19|20)\d\d)\s*[A-Za-z]?\s*$", v)
        if m and YEAR_MIN <= int(m.group(1)) <= YEAR_MAX:
            return int(m.group(1))
    return None


def _year_row(rows: list[tuple]) -> tuple[int, int, list[int]] | None:
    for i, r in enumerate(rows[:12]):
        for j in range(len(r)):
            ys = []
            k = j
            while k < len(r) and (y := _as_year(r[k])) is not None and (not ys or y == ys[-1] + 1):
                ys.append(y)
                k += 1
            if len(ys) >= 5:
                return i, j, ys
    return None


def _is_year_seq(vals: list[float]) -> bool:
    return all(float(v).is_integer() and YEAR_MIN <= v <= YEAR_MAX for v in vals) and         all(b - a == 1 for a, b in zip(vals, vals[1:]))


def _label(r: tuple, c0: int) -> str:
    for j in range(c0 - 1, -1, -1):
        if isinstance(r[j], str) and norm(r[j]) and not norm(r[j]).startswith("Region_"):
            return norm(r[j])
    return ""


def _nums(r: tuple, c0: int, n: int) -> list[float] | None:
    vals = r[c0:c0 + n]
    if not vals or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return None
    return [float(v) for v in vals]


def _sums_to(parent: list[float], kids: list[list[float]]) -> bool:
    ok = False
    for t, v in enumerate(parent):
        tot = sum(k[t] for k in kids)
        if abs(tot - v) > 0.002 * max(abs(v), 1e-9) + 1e-9:
            return False
        ok = ok or v > 0
    return ok


def _nest(segs: list[tuple[str, list[float]]], b: int, header: list[float] | None = None) -> list[dict]:
    """Mark sub-segments: a row equal (in every year) to the sum of the k rows right after it.
    Only used when the flat rows do not add up to the dimension header."""
    flat = [{"name": n, "series": s, "parent": None} for n, s in segs]
    if header is not None and _sums_to(header, [s for _, s in segs]):
        return flat
    out, i = [], 0
    while i < len(segs):
        name, ser = segs[i]
        kids = None
        if ser[b] > 0:
            for k in range(2, len(segs) - i):
                if _sums_to(ser, [s for _, s in segs[i + 1:i + 1 + k]]):
                    kids = segs[i + 1:i + 1 + k]
                    break
                if sum(s[1][b] for s in segs[i + 1:i + 1 + k]) > ser[b] * 1.002:
                    break
        out.append({"name": name, "series": ser, "parent": None})
        if kids:
            out += [{"name": n, "series": s, "parent": name} for n, s in kids]
            i += 1 + len(kids)
        else:
            i += 1
    if header is not None and not _sums_to(header, [o["series"] for o in out if o["parent"] is None]):
        return flat
    return out


def parse_sheet(ws) -> dict | None:
    rows = list(ws.iter_rows(max_row=500, max_col=60, values_only=True))
    if not rows:
        return None
    yr = _year_row(rows)
    if not yr:
        return None
    hi, c0, years = yr
    n = len(years)
    b = years.index(2025) if 2025 in years else min(len(years) - 1, 4)
    groups: list[list[tuple[str, list[float]]]] = []
    cur: list[tuple[str, list[float]]] = []
    total = None
    volume_total = None
    in_value = True
    for r in rows[hi + 1:]:
        r = tuple(r) + (None,) * max(0, c0 + n - len(r))
        lab = _label(r, c0)
        vals = _nums(r, c0, n)
        if in_value and vals is None and lab and STOP_BLOCK.search(lab) and not GEO_DIM.search(lab):
            in_value = False
            if cur:
                groups.append(cur)
                cur = []
            if re.search(r"(?i)volume", lab):
                volume_total = "next"
            continue
        if not in_value:
            if volume_total is None and vals is None and any(isinstance(v, str) and re.search(r"(?i)volume", v) for v in r[:c0 + 2]):
                volume_total = "next"
            elif volume_total == "next" and vals is not None and lab:
                volume_total = vals
            continue
        # header-row text in the year columns (e.g. "Market Value (US$ Mn)") inside the value block
        if vals is None:
            if any(isinstance(v, str) and STOP_BLOCK.search(v) for v in r[c0:c0 + n]) and (cur or groups):
                in_value = False
                if cur:
                    groups.append(cur)
                    cur = []
                if any(isinstance(v, str) and re.search(r"(?i)volume", v) for v in r[c0:c0 + n]):
                    volume_total = "next"
                continue
            if cur:
                groups.append(cur)
                cur = []
            continue
        if lab and _is_year_seq(vals):
            if cur:
                groups.append(cur)
            cur = [(lab, None)]
            continue
        if lab and re.match(r"(?i)^(grand )?total$", lab):
            if cur:
                groups.append(cur)
                cur = []
            continue
        if not lab:
            if total is None and not cur:
                total = vals
            continue
        if re.search(r"(?i)\b(value|volume) in\b", lab) or (re.match(r"(?i)^(value|market value|total)\b", lab) and not cur):
            if re.search(r"(?i)volume", lab):
                volume_total = vals
            elif total is None or not cur:
                total = vals
            continue
        cur.append((lab, vals))
    if cur:
        groups.append(cur)

    dims: list[dict] = []
    children: list[tuple[str, list[float]]] = []
    for g in groups:
        if len(g) == 1:
            total = total or g[0][1]
            continue
        head, segs = g[0], g[1:]
        if head[1] is None:                       # header row that only repeated the years
            if not total:
                continue
            head = (head[0], total)
        if total is None:
            total = head[1]
        is_geo = bool(GEO_DIM.search(head[0]))
        # a header whose value does not equal the segments' sum is not a header: then all rows are segments
        if abs(sum(s[1][b] for s in segs) - head[1][b]) > 0.01 * max(head[1][b], 1e-9) and \
                abs(sum(s[1][b] for s in _top_only(segs, b, head[1])) - head[1][b]) > 0.01 * max(head[1][b], 1e-9):
            if total and abs(sum(s[1][b] for s in g) - total[b]) <= 0.01 * max(total[b], 1e-9):
                head, segs = ("(unnamed)", total), g
            else:
                continue
        if is_geo:
            children = [(x["name"], x["series"]) for x in _nest(segs, b, head[1]) if x["parent"] is None]
        else:
            dims.append({"title": head[0], "segments": _nest(segs, b, head[1])})
    if total is None:
        return None
    vt = volume_total if isinstance(volume_total, list) else None
    unit = next((norm(v) for row in rows[:hi + 1] for v in row
                 if isinstance(v, str) and re.search(r"(?i)(us\$|usd|mn|million|bn|billion|thousand)", v)), "")
    return {"name": norm(ws.title), "years": years, "total": total, "dims": dims, "unit": unit,
            "children": [(nme, ser) for nme, ser in children], "volume_total": vt}


def _top_only(segs, b, header=None):
    return [(s["name"], s["series"]) for s in _nest(segs, b, header) if s["parent"] is None]


def parse_me(path: Path) -> dict | None:
    """-> {"file", "root", "years", "geos": {geo: sheet}, "tree": {geo: [children]}} or None."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return None
    sheets: dict[str, dict] = {}
    try:
        for ws in wb.worksheets:
            try:
                s = parse_sheet(ws)
            except Exception:
                s = None
            if s and s["name"] not in sheets and (s["dims"] or s["children"]):
                sheets[s["name"]] = s
    finally:
        wb.close()
    if not sheets:
        return None
    key = {k.lower().rstrip("."): k for k in sheets}
    for nm, s in sheets.items():
        if s["children"]:
            continue
        for d in list(s["dims"]):
            names = [x["name"] for x in d["segments"] if x["parent"] is None]
            hits = sum(1 for x in names if x.lower().rstrip(".") in key or is_geo_name(x))
            if names and hits / len(names) >= 0.6 and (d["title"] == nm or hits / len(names) >= 0.8 or d["title"].lower().rstrip(".") in key):
                s["children"] = [(x["name"], x["series"]) for x in d["segments"] if x["parent"] is None]
                s["dims"].remove(d)
                break

    def resolve(nm: str) -> str | None:
        return key.get(norm(nm).lower().rstrip("."))

    tree: dict[str, list[str]] = {}
    child_set: set[str] = set()
    for nm, s in sheets.items():
        kids = [c for c, _ in s["children"]]
        if kids:
            tree[nm] = kids
            child_set.update(filter(None, (resolve(c) for c in kids)))
    roots = [nm for nm in sheets if nm not in child_set]
    top = [nm for nm in roots if nm in tree and sheets[nm]["years"] == sheets[roots[0]]["years"]]
    if len(top) >= 2 and not any(re.match(r"(?i)^(global|world)", nm) for nm in top):
        # e.g. "North America & Europe": no global sheet -> a combined root summing the top regions
        ys = sheets[top[0]]["years"]
        tot = [sum(sheets[nm]["total"][t] for nm in top) for t in range(len(ys))]
        dims = []
        for d in sheets[top[0]]["dims"]:
            segs = []
            for sg in d["segments"]:
                parts = [next((x["series"] for dd in sheets[nm]["dims"] if dd["title"] == d["title"]
                               for x in dd["segments"] if x["name"] == sg["name"]), None) for nm in top]
                if all(parts):
                    segs.append({**sg, "series": [sum(p[t] for p in parts) for t in range(len(ys))]})
            if len(segs) == len(d["segments"]):
                dims.append({"title": d["title"], "segments": segs})
        sheets["Combined"] = {"name": "Combined", "years": ys, "total": tot, "dims": dims, "unit": sheets[top[0]]["unit"],
                              "children": [(nm, sheets[nm]["total"]) for nm in top], "volume_total": None, "synthetic": True}
        tree["Combined"] = top
        roots = ["Combined"]
    # root = the parentless sheet with the largest value (avoids stray scratch sheets)
    b = lambda s: s["total"][s["years"].index(2025)] if 2025 in s["years"] else s["total"][min(4, len(s["total"]) - 1)]
    root = max(roots or list(sheets), key=lambda nm: (nm in tree, b(sheets[nm])))
    return {"file": str(path), "root": root, "years": sheets[root]["years"], "geos": sheets, "tree": tree}
