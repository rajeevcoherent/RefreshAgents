"""Plain-language explanation for every cell of the Input Sheet (Data + ASP).

The analysts' own notes are dense (evidence ids, clipped phrases). An editor (fast model) rewrites,
per geography, every cell's facts into 2-4 plain sentences a client can read: what the number is,
how it was built, the main reason, and the geopolitical / policy factor when it materially moves
that number. Guard rails (code):
  - the editor sees only that cell's facts and the geography's context, never other runs or sheets
  - every number in the text must appear in those facts (rounding allowed); otherwise the cell is
    sent back with the offending numbers named; after two rounds it falls back to the structured text
  - no evidence ids in the prose (they stay in the Evidence column)
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .llm import LLMFatal
from .syslog import event

_STOP = {"fatal": ""}

NUM = re.compile(r"(?<![A-Za-z])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)")
EVID = re.compile(r"\bW\d+\b|\bOEM\b(?= ?\))")

EDITOR_SYSTEM = """You are the editor who turns an analyst's working notes into the explanation a client
reads next to each number of a market-sizing input sheet. Market: {market}.
For EVERY cell key given, write 3-5 plain-English sentences (50-130 words) that say:
 1. what the number is, in words (e.g. "Compressors account for about half of Germany's gearbox
    aftermarket"), using the cell's own value;
 2. how it was calculated, walking the reader through the key steps of the CALCULATION with its
    numbers (e.g. "Germany's market is US$ 44 Mn; compressors take 50% of it, so about US$ 22 Mn"),
    and the main assumption each step rests on;
 3. the main real-world reason it sits at this level (installed fleet, industries, utilisation,
    ageing assets, capacity additions, labour cost, OEM vs independent service ...);
 4. ONLY if it materially affects this number: the geopolitical or policy factor (sanctions, war,
    energy-security policy, trade restrictions or tariffs, local-content rules, currency stress,
    decarbonisation rules) and in which direction it pushes the number. Do not add boilerplate.
Rules: use only facts given for that cell and its geography context; every number you write must
appear in those facts (round sensibly, e.g. 50.2% -> "about 50%"); do not cite evidence ids such as
W12; no jargon such as "re-centring" or "normalisation" - say "adjusted so the parts add up".
Return json: {{"texts": {{"<cell key>": "explanation", ...every key...}}}}"""


def _nums(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _ok_number(x: float, allowed: list[float]) -> bool:
    if 1900 <= x <= 2100 and float(x).is_integer():       # years
        return True
    if x <= 10 and float(x).is_integer():                  # counts and small ordinals
        return True
    for y in allowed:
        y = abs(y)
        if abs(x - y) <= 0.051:                                   # same figure, one-decimal rounding
            return True
        if y >= 10 and float(x).is_integer() and abs(x - y) < 1.0:  # rounded to a whole number
            return True
        if y >= 100 and abs(x - y) <= 0.025 * y:                  # money rounded ("about US$ 8,400")
            return True
    return False


_UNIT = {"label": None}


def _fmt_value(r: dict) -> str:
    v = r["value"]
    if v is None:
        return "n/a"
    if r["kind"] == "size_value":
        return f"US$ {v:,.2f} Mn"
    if r["kind"] == "asp":
        return f"US$ {v:,.2f} per {_UNIT['label']}" if _UNIT["label"] else f"US$ {v:,.0f} per installed gearbox per year"
    return f"{v:.2%}" if "cagr" in r["kind"] else f"{v:.1%}"


def _label(r: dict) -> str:
    k = r["kind"]
    if k == "seg_share":
        return f"share of {r['seg']} in {r['geo']} ({'share of ' + r['parent_seg'] if r['parent_seg'] else r['dim']})"
    if k == "seg_cagr":
        return f"growth rate 2025-2032 of {r['seg']} in {r['geo']} ({r['dim']})"
    if k in ("country_cagr", "size_cagr"):
        return f"growth rate 2025-2032 of {r['geo']}"
    if k == "size_share":
        return f"share of {r['geo']} in {r['parent_geo'] or 'the world'}"
    if k == "size_value":
        return f"2025 market value of {r['geo']}"
    if k == "asp":
        return (f"average price per {_UNIT['label']}, {r['seg']}, {r['geo']}" if _UNIT["label"]
                else f"average annual aftermarket price per installed gearbox, {r['seg']}, {r['geo']}")
    return k


def _geo_context(state: dict, geo: str) -> str:
    from .cell_reasoning import _geo_item
    tax = state["tax"]
    parts = []
    for node in [geo, tax.parent_of.get(geo)]:
        if not node:
            continue
        it = _geo_item(state, node)
        if it:
            parts.append(f"{node}: {str(it.get('reasoning', ''))[:700]} Drivers: {'; '.join(map(str, it.get('drivers') or []))}. "
                         f"Restraints: {'; '.join(map(str, it.get('restraints') or []))}. "
                         f"Geopolitics/policy: {'; '.join(map(str, it.get('geopolitics') or [])) or 'none recorded'}.")
    return "\n".join(parts)


def _seg_geopolitics(state: dict, r: dict) -> str:
    from .cell_reasoning import _group_of
    if not r.get("seg") or r["kind"] == "asp":
        return ""
    group = _group_of(state["tax"], r["geo"])
    si = ((state.get("segments_raw") or {}).get(f"{group}||{r['dim']}") or {}).get("segments", {}).get(r["seg"]) or {}
    g = "; ".join(map(str, si.get("geopolitics") or []))
    d = "; ".join(map(str, si.get("drivers") or []))
    return (f" Segment drivers: {d}." if d else "") + (f" Segment geopolitics/policy: {g}." if g else "")


def _facts(state: dict, r: dict) -> str:
    return (f"[{r['sheet']}!{r['cell']}] {_label(r)} = {_fmt_value(r)}. CALCULATION: {r.get('calc') or r['method']} "
            f"Analyst notes: {str(r['reasoning'])[:900]}{_seg_geopolitics(state, r)}"
            + (f" Adjustments: {r['adjustments'][:200]}" if r.get("adjustments") else "")
            + (f" Validator: {r['validator'][:300]}" if r.get("validator") else ""))


def plain_texts(state: dict, rows: list[dict]) -> dict[str, str]:
    """{'Data!E7': text, ...} for every row; structured fallback where the editor fails."""
    llm, cfg, tax = state["llm"], state["cfg"], state["tax"]
    gen = state["sizing"].get("mode") == "generic"
    _UNIT["label"] = state["sizing"].get("volume_unit") if gen else None
    system = EDITOR_SYSTEM.format(market=cfg.market_name)
    if gen:
        system = (system.replace("Compressors account for about half of Germany's gearbox\n    aftermarket",
                                 "Tablets account for about half of Germany's market")
                  .replace("(installed fleet, industries, utilisation,\n    ageing assets, capacity additions, labour cost, "
                           "OEM vs independent service ...)",
                           "(patients and demand base, access and reimbursement,\n    prices, competition and generics, "
                           "approvals and launches ...)")
                  .replace("compressors take 50%", "tablets take 50%"))
    by_geo: dict[str, list[dict]] = {}
    for r in rows:
        key = r["geo"] if r["geo"] in tax.leaves else "(regions and aggregates)"
        by_geo.setdefault(key, []).append(r)

    def run(geo: str, rs: list[dict]) -> dict[str, str]:
        ctx = _geo_context(state, geo) if geo in tax.leaves else ""
        facts = {f"{r['sheet']}!{r['cell']}": _facts(state, r) for r in rs}
        allowed = {k: _nums(f + " " + ctx) for k, f in facts.items()}
        todo, done, notes = list(facts), {}, {}
        for rnd in range(3):
            if not todo:
                break
            # keep calls a sensible size
            for i in range(0, len(todo), 24):
                chunk = todo[i:i + 24]
                user = (f"GEOGRAPHY CONTEXT:\n{ctx or '(see each cell)'}\n\nCELLS:\n" + "\n".join(facts[k] for k in chunk)
                        + ("\n\nFIX THESE FROM YOUR LAST ATTEMPT:\n" + "\n".join(f"{k}: {notes[k]}" for k in chunk if k in notes)
                           if any(k in notes for k in chunk) else ""))
                if _STOP["fatal"]:
                    continue
                try:
                    out = llm.json(f"plain:{geo}:{rnd}:{chunk[0]}", system, user,
                                   tier="fast", temperature=0.2, retries=2,
                                   check=lambda o: None if isinstance(o.get("texts"), dict) else "texts object required")
                except LLMFatal as e:                           # no balance / bad key: stop all editor calls
                    _STOP["fatal"] = str(e)
                    event("plain.stopped", reason=str(e)[:200])
                    continue
                except Exception as e:                          # network or model failure: fall back for this chunk
                    event("plain.failed", geo=geo, error=str(e)[:200])
                    continue
                for k in chunk:
                    t = str((out["texts"] or {}).get(k, "")).strip()
                    if not t:
                        notes[k] = "missing"
                        continue
                    bad = [x for x in _nums(t) if not _ok_number(x, allowed[k])]
                    words = len(t.split())
                    if bad:
                        notes[k] = f"these numbers are not in the cell's facts: {bad[:5]} - use only the given numbers"
                    elif EVID.search(t):
                        notes[k] = "remove evidence ids such as W12"
                    elif not (30 <= words <= 170):
                        notes[k] = f"{words} words; write 50-130"
                    else:
                        done[k] = t
                        notes.pop(k, None)
            todo = [k for k in todo if k not in done]
        if todo:
            event("restriction.plain_fallback", geo=geo, cells=todo[:20], n=len(todo), reasons=[notes.get(k) for k in todo[:5]])
        return done

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as ex:
        for res in ex.map(lambda kv: run(*kv), by_geo.items()):
            out.update(res)
    edited = len(out)
    for r in rows:                                          # structured fallback, so no cell is ever blank
        k = f"{r['sheet']}!{r['cell']}"
        if k not in out:
            out[k] = f"{_label(r)} = {_fmt_value(r)}. {r['method']} {str(r['reasoning'])[:300]}"
    event("plain.done", cells=len(rows), rewritten_by_editor=edited, structured_fallback=len(rows) - edited,
          stopped=_STOP["fatal"][:120])
    return out
