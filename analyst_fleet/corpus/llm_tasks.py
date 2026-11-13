"""LLM steps of the learning pipeline (all cached; code verifies whatever the model proposes).

  classify_market   category / offering / customer / lifecycle for analog retrieval
  extract_method    the analyst's base-number method, read from their Secondary sheets
  propose_rules     candidate rules over the scale-free features, each with a rationale and
                    a machine-checkable predicate; code then measures support on held-out markets
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..agents.common import canon_keys
from ..syslog import event

CATEGORIES = ["Industrial machinery & equipment", "Industrial automation, electronics & components",
              "Chemicals & materials", "Pharmaceuticals & APIs", "Medical devices & healthcare services",
              "Food & beverage", "Consumer goods & retail", "ICT, software & digital services",
              "Energy, water & utilities", "Construction, travel & other services"]
OFFERINGS = ["capital equipment", "components & consumables", "aftermarket & services", "software & platforms",
             "bulk chemicals & materials", "specialty chemicals & materials", "drugs & APIs", "branded consumer products",
             "food & agri products", "infrastructure & projects", "other services"]
CUSTOMERS = ["industrial B2B", "healthcare providers & patients", "consumers", "enterprises & government", "mixed"]

CLASSIFY_SYSTEM = f"""You classify market-research reports so similar markets can be compared.
Pick exactly one value per field from these lists:
category: {CATEGORIES}
offering: {OFFERINGS}
customer: {CUSTOMERS}
lifecycle: ["emerging", "growth", "mature"]
Also give "keywords": 5-8 lowercase words describing the product and its end-use, and
"demand_drivers": 3-5 short phrases (what drives demand).
Return json: {{"category", "offering", "customer", "lifecycle", "keywords": [], "demand_drivers": []}}"""


def _check_class(out: dict) -> str | None:
    for k, allowed in (("category", CATEGORIES), ("offering", OFFERINGS), ("customer", CUSTOMERS),
                       ("lifecycle", ["emerging", "growth", "mature"])):
        if out.get(k) not in allowed:
            return f"{k} must be one of {allowed} (exact text); got {out.get(k)!r}"
    if not isinstance(out.get("keywords"), list) or len(out["keywords"]) < 3:
        return "keywords must be a list of 5-8 words"
    return None


def classify_market(llm, name: str, segments: list[str]) -> dict:
    user = f"MARKET: {name}\nSEGMENTATION (dimensions and segments): {', '.join(segments[:60])}"
    return llm.json(f"corpus.classify:{name[:40]}", CLASSIFY_SYSTEM, user, tier="fast", check=_check_class, temperature=0.0)


METHOD_SYSTEM = """You read the secondary-research notes an analyst left in a market-sizing workbook
(URLs, numbers, short derivation notes) and reconstruct HOW they sized the base year.
Be concrete and only state what the notes support. Return json:
{"sizing_method": one of ["supply-side production/capacity", "trade data", "company revenue roll-up",
   "installed base x spend", "patients/epidemiology x treatment cost", "consumption per capita/user x price",
   "units x ASP", "cited third-party estimate", "mixed", "unclear"],
 "method_summary": "2-4 sentences: the chain of numbers from source to base-year value",
 "anchor_numbers": ["each key number with its unit and what it is"],
 "source_types": ["e.g. company annual report, trade association, government statistics, trade database, news, paid database"],
 "transferable_lessons": ["1-3 lessons an analyst sizing a DIFFERENT market could reuse"],
 "quality": "High" | "Medium" | "Low"}"""


def extract_method(llm, name: str, lines: list[str]) -> dict:
    text = "\n".join(lines)[:7000]
    return llm.json(f"corpus.method:{name[:40]}", METHOD_SYSTEM, f"MARKET: {name}\n\nNOTES:\n{text}", tier="fast",
                    check=lambda o: None if o.get("sizing_method") and o.get("method_summary") else "sizing_method and method_summary required",
                    temperature=0.0)


RULES_SYSTEM = """You are a research director distilling a house playbook from a library of past
market estimates. You get distributions of SCALE-FREE features (shares, growth spreads) across
markets, overall and by category. Propose rules that a future analyst (or a validator with no
reference sheet) can use to judge a new market's numbers.

Each rule must be checkable by code over the features of one market:
  {"id": "R1",
   "statement": "plain-English rule",
   "lhs": "<feature name>",
   "op": ">" | "<" | "between",
   "rhs": <number> | "<feature name>" | [lo, hi],
   "rhs_offset": <number, optional, added to an rhs feature>,
   "scope": null | {"category": [..]} | {"offering": [..]} | {"customer": [..]},
   "kind": "economics" | "house_convention",
   "rationale": "WHY this tends to hold - the market mechanism (economics) or the methodology habit (house_convention)",
   "use": "how a validator should use it (e.g. 'outside this band needs market-specific evidence')"}
Use only feature names from the list given. Prefer rules that are informative (narrow bands,
directional facts) over trivially true ones. Mark as house_convention anything that reflects how
the team builds sheets rather than how markets behave (e.g. near-identical segment mixes across
countries, forecast CAGR set just above history). 18-28 rules.
Return json: {"rules": [ ... ]}"""


def propose_rules(llm, feature_names: list[str], stats: dict[str, Any], scopes: dict[str, list[str]]) -> list[dict]:
    names = set(feature_names)

    def check(out: dict) -> str | None:
        rules = out.get("rules")
        if not isinstance(rules, list) or len(rules) < 10:
            return "need a list of 18-28 rules"
        for r in rules:
            if r.get("lhs") not in names:
                return f"rule {r.get('id')}: lhs {r.get('lhs')!r} is not a feature name"
            if r.get("op") not in (">", "<", "between"):
                return f"rule {r.get('id')}: op must be >, < or between"
            rhs = r.get("rhs")
            if r["op"] == "between" and not (isinstance(rhs, list) and len(rhs) == 2 and all(isinstance(x, (int, float)) for x in rhs)):
                return f"rule {r.get('id')}: between needs rhs [lo, hi] numbers"
            if r["op"] != "between" and not (isinstance(rhs, (int, float)) or rhs in names):
                return f"rule {r.get('id')}: rhs must be a number or a feature name"
            sc = r.get("scope")
            if sc:
                for k, vals in sc.items():
                    if k not in scopes or any(v not in scopes[k] for v in vals):
                        return f"rule {r.get('id')}: scope {k} values must come from {scopes.get(k)}"
            if r.get("kind") not in ("economics", "house_convention") or len(str(r.get("rationale", ""))) < 30:
                return f"rule {r.get('id')}: kind economics/house_convention and a real rationale are required"
        return None

    user = ("FEATURES (name -> meaning is in the doc below):\n" + "\n".join(feature_names) +
            "\n\nDISTRIBUTIONS (p10 / median / p90, n) overall and by category, plus directional frequencies:\n" +
            json.dumps(stats, indent=1)[:60000])
    return llm.json("corpus.rules", RULES_SYSTEM, user, check=check, temperature=0.2, retries=4)["rules"]


def run_parallel(fn, jobs: list[tuple], workers: int = 6) -> list[Any]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, *j) for j in jobs]
        out = []
        for j, f in zip(jobs, futs):
            try:
                out.append(f.result())
            except Exception as e:   # one failed market must not stop the corpus
                event("corpus.llm_failed", job=str(j[1])[:80], error=str(e)[:300])
                out.append(None)
        return out
