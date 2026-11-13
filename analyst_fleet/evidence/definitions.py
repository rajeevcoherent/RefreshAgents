"""Market definition documents (docx) -> plain text + per-segment definitions."""
from __future__ import annotations

import re
from pathlib import Path

import docx


def _norm(s: str) -> str:
    s = s.lower().replace("–", "-").replace("—", "-").replace("↳", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def read_docx(path: Path) -> tuple[str, list[list[list[str]]]]:
    d = docx.Document(str(path))
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip() and p.text.strip() != "↓"]
    tables = [[[c.text.strip() for c in row.cells] for row in t.rows] for t in d.tables]
    return "\n".join(paras), tables


def definitions_text(path: Path) -> str:
    """Whole document as compact text (paragraphs, then tables as pipe rows)."""
    paras, tables = read_docx(path)
    parts = [paras]
    for t in tables:
        parts.append("\n".join(" | ".join(r) for r in t))
    return "\n\n".join(parts)


# segment label in the deliverable -> words that identify its row in the definitions tables
_KEYS = {
    "Steam Turbines": "steam turbines",
    "Gas Turbines": "gas turbines",
    "Compressors": "compressors overall",
    "Centrifugal Compressor": "centrifugal compressors",
    "IGC (Motor driven & Turbine driven)": "igc integrally geared compressors",
    "Pumps (High-speed centrifugal pumps (Motor driven & Turbine driven)": "pumps overall",
    "Turbo expanders": "turboexpanders",
    "Blowers (Turbine driven)": "blowers turbine driven",
    "Oil & Gas": "oil gas",
    "Petrochemical": "petrochemical",
    "Fertilizers": "fertilizers",
    "FPSO and Gas pipeline projects": "fpso and gas pipeline projects",
    "Oil pipeline projects": "oil pipeline projects",
    "Power Generation": "power generation",
    "Cement": "cement",
    "Steel": "steel",
    "Marine": "marine",
    "Others": "others",
}


def segment_definitions(path: Path) -> dict[str, str]:
    """Deliverable segment name -> 'definition | inclusions | exclusions' from the doc tables.
    Sub-rows (motor/turbine-driven variants) are appended to their parent row."""
    _, tables = read_docx(path)
    rows = [r for t in tables for r in t if len(r) >= 4]
    out: dict[str, str] = {}
    for seg, key in _KEYS.items():
        for i, r in enumerate(rows):
            if _norm(r[0]) == key:
                text = f"Definition: {r[1]} Includes: {r[2]} Excludes/classification: {r[3]}"
                # attach indented variants (rows starting with the arrow) that follow
                for r2 in rows[i + 1:]:
                    if r2[0].startswith("↳"):
                        text += f" || {r2[0].lstrip('↳ ').strip()}: {r2[1]} Includes: {r2[2]} Excludes: {r2[3]}"
                    else:
                        break
                out[seg] = text
                break
    return out


ALLOC_WORDS = re.compile(r"(?i)(classified under|allocated|counted|fall under|assigned to|not additional|double count|"
                         r"one primary|each gearbox is assigned|each aftermarket transaction)")


def scope_rules(aftermarket_docx: Path, oem_docx: Path | None = None) -> str:
    """Binding scope rulebook built verbatim from the client documents (no LLM): the market definition,
    the INCLUDED / EXCLUDED pairs, segment allocation rules and the OEM / aftermarket boundary."""
    paras, tables = read_docx(aftermarket_docx)
    lines = ["SCOPE RULEBOOK (client definition documents - binding for every number):"]
    lines += [f"  {p}" for p in paras.split("\n") if len(p) > 80][:5]
    inc_exc = next((t for t in tables if t and len(t[0]) == 2 and "INCLUDED" in t[0][0].upper()), None)
    if inc_exc:
        lines.append("COUNT (included):")
        lines += [f"  + {r[0]}" for r in inc_exc[1:] if r[0]]
        lines.append("DO NOT COUNT (excluded):")
        lines += [f"  - {r[1]}" for r in inc_exc[1:] if len(r) > 1 and r[1]]
    alloc = [p for p in paras.split("\n") if ALLOC_WORDS.search(p)]
    for t in tables:
        for r in t[1:]:
            if len(r) >= 4 and ALLOC_WORDS.search(r[3]):
                alloc.append(f"{r[0].lstrip('↳ ').strip()}: {r[3]}")
    if oem_docx and Path(oem_docx).exists():
        op, ot = read_docx(oem_docx)
        alloc += [p for p in op.split("\n") if ALLOC_WORDS.search(p)]
        oem_tab = next((t for t in ot if t and len(t[0]) == 2 and "INCLUDED" in t[0][0].upper()), None)
        if oem_tab:
            lines.append("OEM / AFTERMARKET BOUNDARY (from the OEM definition):")
            lines += [f"  OEM, not aftermarket: {r[0]}" for r in oem_tab[1:4]]
            lines += [f"  Excluded from OEM (aftermarket or out of scope): {r[1]}" for r in oem_tab[1:]
                      if re.search(r"(?i)replacement|retrofit|spare|maintenance|servic", r[1])]
        val = [p for p in op.split("\n") if re.search(r"(?i)market value represents|market volume represents", p)]
        lines += [f"  {v}" for v in val]
    if alloc:
        lines.append("ALLOCATION RULES:")
        seen = set()
        for a in alloc:
            if a not in seen:
                seen.add(a)
                lines.append(f"  * {a}")
    return "\n".join(lines)
