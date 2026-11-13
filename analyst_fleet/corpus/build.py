"""Scan the month folders (April/, May/, July/, ...) and build data/corpus/markets.json.

A *market* is a folder holding an ME workbook. When a folder has several versions
(Updated/, Revised_, July Update/ ...) the newest ME per market name wins. Two different
markets in one folder (e.g. "API" and "API + FDF") are both kept; they share a *family*
(the top-level market folder) so leave-one-out evaluation never lets one predict the other.

Also collected per market, from the Data File / ME / Input Sheet workbooks:
  - the analyst's secondary-research text (sheets named Secondary / Base Number / Sources /
    Rationale / RM ...): URLs and derivation notes. Text blocks that appear verbatim in
    two or more different families are template leftovers and are dropped.
"""
from __future__ import annotations

import hashlib
import re
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..syslog import event
from .parse_me import norm, parse_me


def _rel(p: Path) -> str:
    """ME path relative to the project root (portable; no local user folders in library.json)."""
    try:
        return p.resolve().relative_to(Path(__file__).resolve().parents[2]).as_posix()
    except ValueError:
        return str(p)

warnings.filterwarnings("ignore", module="openpyxl")

ME_NAME = re.compile(r"(?i)^(updated |revised_?|value_)?me[\s\-_]")
SKIP_NAME = re.compile(r"(?i)(input|template|pivot|dataset|data ?file|formula|volume|asp|book1|consumption|calculation)")
SECONDARY_SHEET = re.compile(r"(?i)(secondary|base number|source|rational|assum|\brm\b|snapshot|calculation)")


def market_key(fname: str) -> str:
    s = Path(fname).stem
    s = re.sub(r"(?i)^(updated |revised_?|value_)?me\s*[-_ ]\s*", "", s)
    s = re.sub(r"(?i)(_?coherent market insights|_cmi)$", "", s)
    s = re.sub(r"(?i)\b(market|global)\b", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _secondary_text(paths: list[Path], limit_chars: int = 6000) -> list[str]:
    out: list[str] = []
    for p in paths:
        try:
            wb = load_workbook(p, read_only=True, data_only=True)
        except Exception:
            continue
        try:
            for ws in wb.worksheets:
                if not SECONDARY_SHEET.search(ws.title):
                    continue
                for r in ws.iter_rows(max_row=200, max_col=25, values_only=True):
                    cells = [norm(v) for v in r if isinstance(v, str) and len(norm(v)) >= 12]
                    nums = [v for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
                    if cells:
                        line = " | ".join(cells)
                        if nums and len(nums) <= 6:
                            line += " | " + ", ".join(f"{v:,.4g}" for v in nums)
                        out.append(line)
        finally:
            wb.close()
    seen, uniq = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return uniq


def scan(roots: list[Path]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for month in roots:
        for fam in sorted(p for p in month.iterdir() if p.is_dir()):
            files = [f for f in fam.rglob("*.xlsx") if not f.name.startswith("~$")]
            mes = [f for f in files if ME_NAME.match(f.name) and not SKIP_NAME.search(f.name.split("-", 1)[-1][:12])]
            by_key: dict[str, list[Path]] = defaultdict(list)
            for f in mes:
                by_key[market_key(f.name)].append(f)
            # a sub-market whose key is contained in another's (e.g. "x api" vs "x api fdf") stays separate
            for key, fs in by_key.items():
                fs.sort(key=lambda f: -f.stat().st_mtime)
                parsed, used = None, None
                for f in fs:                      # newest parseable version
                    parsed = parse_me(f)
                    if parsed and (parsed["geos"][parsed["root"]]["dims"] or parsed["tree"]):
                        used = f
                        break
                if not parsed:
                    event("corpus.skip", family=fam.name, key=key, reason="no parseable ME", files=[f.name for f in fs])
                    continue
                near = [f for f in files if f.parent == used.parent or f.parent == fam]
                sec_src = [f for f in near if re.match(r"(?i)^(data ?file|input|dataset)", f.name) or f == used]
                markets.append({"id": f"{month.name}/{fam.name}/{key}", "month": month.name, "family": f"{month.name}/{fam.name}",
                                "name": re.sub(r"\s+", " ", re.sub(r"(?i)^(updated |revised_?|value_)?me\s*[-_ ]\s*", "", used.stem)).strip(),
                                "me_file": _rel(used), "versions": len(fs), "parsed": parsed,
                                "secondary": _secondary_text(sec_src)})
                r = parsed["geos"][parsed["root"]]
                event("corpus.market", id=markets[-1]["id"], file=used.name, versions=len(fs), root=parsed["root"],
                      geos=len(parsed["geos"]), dims=[d["title"] for d in r["dims"]], years=f"{parsed['years'][0]}-{parsed['years'][-1]}",
                      secondary_lines=len(markets[-1]["secondary"]))
    # drop secondary text that is shared verbatim across families (template residue)
    owners: dict[str, set[str]] = defaultdict(set)
    for m in markets:
        for line in m["secondary"]:
            owners[hashlib.md5(line.encode()).hexdigest()].add(m["family"])
    for m in markets:
        before = len(m["secondary"])
        m["secondary"] = [l for l in m["secondary"] if len(owners[hashlib.md5(l.encode()).hexdigest()]) == 1]
        if before - len(m["secondary"]):
            event("restriction.template_text", id=m["id"], dropped=before - len(m["secondary"]), kept=len(m["secondary"]))
    return markets
