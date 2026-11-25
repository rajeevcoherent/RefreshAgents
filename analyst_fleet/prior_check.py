"""Prior validation inside a run (generic markets): judge the finished numbers against the bands learned from
past human MEs (data/corpus) before the workbook is written, the same engine as validate_with_priors.py.

Features outside the dispute band (analog p10-p90 widened by 1/4; 81% coverage on held-out markets) go to the
validation lead, who keeps the agent's number when it has market-specific evidence or moves it towards the
house's typical range. Moved numbers carry the validator's reasoning; compute re-runs all arithmetic.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .compute import compute_all
from .corpus.priors import Library
from .prior_validator import adjudicate, apply, find_items, target_record
from .syslog import event
from .validator import finalize_notes

CORPUS = Path(__file__).resolve().parent.parent / "data" / "corpus"


def run_priors(state: dict) -> dict:
    lib = Library(json.loads((CORPUS / "library.json").read_text(encoding="utf-8")))
    rules = json.loads((CORPUS / "rules.json").read_text(encoding="utf-8"))
    target = target_record(state)
    items, chk = find_items(state, lib, rules, target)
    event("priors.items", market=state["cfg"].market_name, n=len(items), by_kind=dict(Counter(i["kind"] for i in items)),
          outside=[f for f, c in chk["checks"].items() if c["outside"]])
    summary = {"checked": len(chk["checks"]), "outside": [i["id"] for i in items],
               "analogs": [m["name"] for _, m in lib.similar(target)[:8]], "class": target["class"]}
    if not items:
        return {**state, "prior_check": summary}
    verdicts = adjudicate(state, items, target)
    upd, records = apply(state, items, verdicts)
    for r in records:
        r["prior"] = r["human"]
    v = {**state, **upd, "validation": records, "validation_mode": "priors", "prior_check": summary,
         "log": (state.get("log") or []) + [f"prior validator: {len(records)} items adjudicated against "
                                            f"{len(lib.markets)} past markets"]}
    v["numbers"] = compute_all(v)
    finalize_notes(v, records)
    return v
