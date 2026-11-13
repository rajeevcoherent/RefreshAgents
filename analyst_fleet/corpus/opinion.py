"""Opinion learning: how past analysts FORM their numbers, not what the numbers were.

1. dossier(view)          code: the analyst's quantitative decisions in one market - region and
                          country splits, growth gaps, how each segment's share shifts by region,
                          which segments got growth premiums, sub-segment splits, price by region.
2. reverse_engineer()     LLM: "you built this sheet - what opinion and which qualitative factor
                          produced each decision?" -> structured opinions per decision type.
3. distil()               LLM over many markets: heuristics "when <qualitative condition>, the
                          analyst <moves number X> by <typical magnitude>, because <why>",
                          grouped by the 2A / 2B / 2C stage they belong to (system.md section 7).
4. guidance()             the text block an analyst agent receives at each stage.

The heuristics are reasoning habits with typical magnitudes, never target numbers.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from ..agents.common import canon_keys
from ..syslog import event
from .features import cagr, macro_of

# decision types, by the strategy stage of system.md section 7 they belong to
STAGES = {
    "2A": ["base_year_anchor", "forecast_growth_vs_history", "region_split", "region_growth",
           "country_split", "country_growth"],
    "2B": ["use_case_split", "use_case_growth", "use_case_mix_by_region"],
    "2C": ["type_split", "type_growth", "type_mix_by_region", "sub_segment_split", "price_by_region"],
}
DECISIONS = [d for ds in STAGES.values() for d in ds]
FACTORS = ["installed base / asset age", "income & healthcare spend", "manufacturing & industrial base",
           "population & consumption", "regulation & policy", "technology adoption / product lifecycle",
           "price & premiumisation", "trade & supply chain", "geopolitics & sanctions", "infrastructure & capex cycle",
           "channel shift", "market maturity / saturation", "residual / long-tail convention", "analyst convention"]
DIM_ROLES = ["use_case", "type", "other"]   # use_case -> 2B (application / end-use / end-user), type -> 2C


# ------------------------------------------------------------------------------------- dossier
def _pp(x: float) -> str:
    return f"{x * 100:+.1f}pp"


def dossier(view: dict, price: dict[str, float] | None = None, max_children: int = 12) -> str:
    """Compact, code-computed description of the decisions a sheet embodies."""
    if not view:
        return ""
    root, v, kids, n = view["root"], view["v"], view["kids"], view["n_fc"]
    v21, v25, vend = v[root]
    rc = cagr(v25, vend, n)
    hc = cagr(v21, v25, view.get("n_hist", 4))
    L = [f"ROOT {root}: forecast CAGR {rc:.2%}" + (f", history CAGR {hc:.2%} (forecast {_pp(rc - hc)} vs history)" if hc else "")]
    ch = [k for k in kids.get(root, []) if k in v and v[k][1]]
    if ch:
        L.append("SPLIT OF ROOT (share 2025 | CAGR gap vs root):")
        for k in sorted(ch, key=lambda k: -v[k][1])[:max_children]:
            kc = cagr(v[k][1], v[k][2], n)
            L.append(f"  {k}: {v[k][1] / v25:.1%} | {_pp(kc - rc) if kc is not None else 'n/a'}"
                     + (f" | price index {price[k]:.2f}" if price and k in price else ""))
    # countries inside each region
    for p in ch:
        cc = [k for k in kids.get(p, []) if k in v and v[k][1]]
        if len(cc) >= 2:
            pc = cagr(v[p][1], v[p][2], n)
            parts = []
            for k in sorted(cc, key=lambda k: -v[k][1])[:8]:
                kc = cagr(v[k][1], v[k][2], n)
                parts.append(f"{k} {v[k][1] / v[p][1]:.0%} ({_pp(kc - pc) if kc is not None and pc is not None else 'n/a'})")
            L.append(f"  inside {p}: " + ", ".join(parts))
    # segments at root, and how each region's mix differs
    for dim, rows in (view["seg"].get(root) or {}).items():
        top = [(s, a, b) for s, p, a, b in rows if p is None and a]
        tot = sum(a for _, a, _ in top)
        if len(top) < 2 or not tot:
            continue
        L.append(f"DIMENSION '{dim}' (global share | growth premium vs root | share shift by region, pp):")
        for s, a, b in top:
            sc = cagr(a, b, n)
            shifts = []
            for r in ch:
                rrows = (view["seg"].get(r) or {}).get(dim) or []
                rt = sum(x for nm, pp_, x, _ in rrows if pp_ is None and x)
                ra = next((x for nm, pp_, x, _ in rrows if nm == s), None)
                if rt and ra is not None:
                    shifts.append(f"{r} {(ra / rt - a / tot) * 100:+.0f}")
            L.append(f"  {s}: {a / tot:.1%} | {_pp(sc - rc) if sc is not None else 'n/a'}" + (f" | {', '.join(shifts)}" if shifts else ""))
        subs = [(s, p, a, b) for s, p, a, b in rows if p]
        if subs:
            par = {s: (a, b) for s, p, a, b in rows if p is None}
            for s, p, a, b in subs:
                if p in par and par[p][0]:
                    pc_, kc_ = cagr(*par[p], n), cagr(a, b, n)
                    L.append(f"    sub-segment {s} of {p}: {a / par[p][0]:.0%} of parent | "
                             f"{_pp(kc_ - pc_) if kc_ is not None and pc_ is not None else 'n/a'} vs parent")
    return "\n".join(L)


def region_price_index(parsed: dict) -> dict[str, float]:
    geos, root = parsed["geos"], parsed["root"]
    r = geos[root]
    ys = r["years"]
    if 2025 not in ys or not r.get("volume_total"):
        return {}
    i = ys.index(2025)
    p0 = r["total"][i] / r["volume_total"][i] if r["volume_total"][i] else None
    out = {}
    for c, _ in r["children"]:
        g = geos.get(c)
        if p0 and g and g.get("volume_total") and g["years"] == ys and g["volume_total"][i]:
            out[c] = (g["total"][i] / g["volume_total"][i]) / p0
    return out


# ------------------------------------------------------------------------------------- reverse-engineer
RE_SYSTEM = f"""You are the senior analyst who BUILT the market estimate described below. The sheet
holds numbers only; reconstruct the opinions behind them. For each notable decision say what you
decided (quantitatively, from the dossier), and the qualitative reason an experienced analyst would
have had - the market mechanism, not "the data said so". If a decision looks like a mechanical
convention (e.g. countries copied from the region mix, a residual "Rest of" share, forecast set just
above history), say so honestly with factor "analyst convention".

decision must be one of: {DECISIONS}
  (use_case_* = application / end-use / end-user dimensions; type_* = product type / technology /
   form / grade dimensions; sub_segment_split = nested segments; price_by_region only if a price
   index is given)
factor must be one of: {FACTORS}

Return json:
{{"dimension_roles": {{"<dimension title>": "use_case" | "type" | "other"}},
 "opinions": [{{"decision": "...", "what": "the quantitative decision, citing the dossier numbers",
               "why": "the opinion / mechanism behind it (1-2 sentences)", "factor": "...",
               "generalisable": true | false}}  ... 10-18 items covering geography, growth and segments],
 "style": ["2-4 notes on how this analyst forms numbers in general (e.g. how bold the growth gaps are)"]}}"""


def reverse_engineer(llm, m: dict) -> dict:
    dims = list((m.get("dims") or []))

    def check(o: dict) -> str | None:
        ops = o.get("opinions")
        if not isinstance(ops, list) or len(ops) < 6:
            return "need 10-18 opinions"
        for x in ops:
            if x.get("decision") not in DECISIONS:
                return f"decision {x.get('decision')!r} not in the allowed list"
            if x.get("factor") not in FACTORS:
                return f"factor {x.get('factor')!r} not in the allowed list"
        roles = canon_keys(o.get("dimension_roles") or {}, dims)
        if dims and any(roles.get(d) not in DIM_ROLES for d in dims):
            return f"dimension_roles must give use_case/type/other for every dimension: {dims}"
        o["dimension_roles"] = roles
        return None

    c = m.get("class") or {}
    user = (f"MARKET: {m['name']}\nCLASSIFICATION: {json.dumps(c, ensure_ascii=False)}\n"
            + (f"HOW THE BASE YEAR WAS SIZED (from the analyst's notes): {json.dumps(m['method'], ensure_ascii=False)[:1500]}\n"
               if m.get("method") else "") + f"\nDOSSIER (code-computed from the sheet):\n{m['dossier']}")
    return llm.json(f"opinion.reverse:{m['name'][:40]}", RE_SYSTEM, user, check=check, temperature=0.2, retries=4)


# ------------------------------------------------------------------------------------- distil
DISTIL_SYSTEM = """You are the research director writing the HOUSE OPINION PLAYBOOK for stage {stage}
of the market-sizing method. Below are opinions reconstructed from many past market estimates
(each tagged with market, category, decision, factor), plus code-computed statistics.

Write heuristics an independent analyst can apply to a NEW market of any kind. Each heuristic says
how a qualitative situation translates into a direction and a typical magnitude for one decision:
  "when <qualitative condition>, set/shift <number> by <typical magnitude>, because <mechanism>".
Ground every magnitude in the statistics or the opinions given (cite typical ranges, not single
points). Separate genuine market mechanisms from house conventions. Note where the past analysts
were probably mechanical or wrong, so the new analyst does better than copying them.
Also write a short 'method' paragraph: the order in which an analyst should form opinions at this
stage and what evidence to look for first.

Decisions in this stage: {decisions}

Return json:
{{"method": "...",
 "heuristics": [{{"id": "{stage}-1", "decision": "<one of the decisions>",
                 "condition": "qualitative situation", "action": "direction + typical magnitude",
                 "why": "mechanism", "kind": "mechanism" | "house_convention",
                 "markets": ["names of supporting markets from the input"], "caveat": "when NOT to apply"}} ... 8-16],
 "pitfalls": ["mistakes to avoid at this stage"]}}"""


def distil(llm, stage: str, records: list[dict], stats: dict, tag: str) -> dict:
    decisions = STAGES[stage]
    names = {r["market"] for r in records}
    rows = [r for r in records if r["decision"] in decisions]
    text = "\n".join(f"- [{r['market']} | {r['category']}] {r['decision']} ({r['factor']}): {r['what']} -> {r['why']}"
                     for r in rows)[:70000]

    def check(o: dict) -> str | None:
        hs = o.get("heuristics")
        if not isinstance(hs, list) or len(hs) < 5 or not o.get("method"):
            return "need method and 8-16 heuristics"
        for h in hs:
            if h.get("decision") not in decisions:
                return f"heuristic {h.get('id')}: decision must be one of {decisions}"
            if len(str(h.get("action", ""))) < 15 or len(str(h.get("why", ""))) < 20:
                return f"heuristic {h.get('id')}: action and why must be specific"
        return None

    user = f"STATISTICS:\n{json.dumps(stats, indent=0)[:20000]}\n\nOPINIONS ({len(rows)}):\n{text}"
    out = llm.json(f"opinion.distil:{stage}:{tag}", DISTIL_SYSTEM.format(stage=stage, decisions=decisions), user,
                   check=check, temperature=0.2, retries=4)
    for h in out["heuristics"]:                       # keep only real supporting markets
        h["markets"] = [x for x in h.get("markets") or [] if x in names]
        h["n_markets"] = len(h["markets"])
    event("opinion.distil", stage=stage, tag=tag, heuristics=len(out["heuristics"]), opinions_used=len(rows))
    return out


def opinion_stats(markets: list[dict]) -> dict:
    """Numbers the distiller may cite: medians by category for the decisions it writes about."""
    keys = ["growth.fcst_minus_hist", "region.share.NA", "region.share.Europe", "region.share.APAC", "region.share.LATAM",
            "region.share.MEA", "region.cagr_gap.NA", "region.cagr_gap.Europe", "region.cagr_gap.APAC",
            "region.cagr_gap.LATAM", "region.cagr_gap.MEA", "country.share.U.S.", "country.share.China",
            "country.share.Germany", "country.share.Japan", "country.share.India", "country.cagr_gap_sd",
            "country.rest_share", "seg.top_share", "seg.cagr_spread", "seg.cagr_dev", "seg.sub_cagr_dev",
            "seg.small_fast", "seg.country_mix_sd"]
    by: dict[str, list[dict]] = {}
    for m in markets:
        by.setdefault((m.get("class") or {}).get("category", "?"), []).append(m)
    rnd = lambda x: round(x, 4)
    out = {"all": {k: [rnd(median(v)), len(v)] for k in keys if (v := [m["features"][k] for m in markets if k in m["features"]])}}
    for c, ms in by.items():
        out[c] = {k: [rnd(median(v)), len(v)] for k in keys if len(v := [m["features"][k] for m in ms if k in m["features"]]) >= 2}
    return out


# ------------------------------------------------------------------------------------- guidance for the fleet
_DECISION_BY_STAGE = {
    "sizing": ["base_year_anchor", "forecast_growth_vs_history"],
    "regional": ["region_split", "region_growth"],
    "country": ["country_split", "country_growth"],
    "use_case": ["use_case_split", "use_case_growth", "use_case_mix_by_region"],
    "type": ["type_split", "type_growth", "type_mix_by_region", "sub_segment_split"],
    "pricing": ["price_by_region"],
}


def load_playbook(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None


def guidance(pb: dict | None, stage: str, extra_decisions: tuple[str, ...] = ()) -> str:
    if not pb:
        return ""
    want = set(_DECISION_BY_STAGE.get(stage, [])) | set(extra_decisions)
    want -= set(pb.get("disabled_decisions") or {})       # measured to hurt in the mimic test
    lines, methods = [], []
    for st, blk in pb["stages"].items():
        hs = [h for h in blk["heuristics"] if h["decision"] in want]
        if hs:
            methods.append(f"[{st}] {blk['method']}")
        for h in hs:
            tag = "house habit" if h.get("kind") == "house_convention" else "mechanism"
            lines.append(f"- ({h['id']}, {tag}, seen in {h.get('n_markets', 0)} past markets) WHEN {h['condition']} -> "
                         f"{h['action']}. Why: {h['why']}" + (f" Not when: {h['caveat']}" if h.get("caveat") else ""))
        lines += [f"- PITFALL: {p}" for p in blk.get("pitfalls") or [] if any(w in p.lower() for w in _keywords(stage))]
    if not lines:
        return ""
    cal = [c for c in pb.get("calibration") or [] if c["applies_to"] == "all" or c["applies_to"] in want]
    if cal:
        lines = ["PRECEDENCE (read first): " + " ".join(f"({c['id']}) {c['note']}" for c in cal)] + lines
    return ("\n\nHOUSE JUDGEMENT - how experienced analysts in this team form these opinions, distilled from "
            f"{pb['n_markets']} past estimates across many industries. These are reasoning habits with typical "
            "magnitudes, NOT targets: apply the mechanism to THIS market's own facts, and when you deviate, say "
            "which heuristic you are departing from and why. Cite heuristic ids you used in your reasoning.\n"
            + "\n".join(methods) + "\n" + "\n".join(lines))


def _keywords(stage: str) -> tuple[str, ...]:
    return {"sizing": ("history", "forecast", "base", "anchor", "cagr"), "regional": ("region", "apac", "europe", "america"),
            "country": ("country", "countr", "rest of"), "use_case": ("application", "use", "segment", "end"),
            "type": ("type", "sub", "segment"), "pricing": ("price", "asp")}.get(stage, ())


DIMS_SYSTEM = """Label each segmentation dimension of a market report:
 "use_case" = application / end-use / end-user / customer industry (who uses it, what for) -> 2B stage
 "type"     = product type / technology / form / grade / the equipment the product is built into -> 2C stage
 "other"    = channel, brand, geography or anything else.
Return json: {"roles": {"<dimension title>": "use_case" | "type" | "other"}}"""


def classify_dims(llm, market: str, dims: dict[str, list[str]]) -> dict[str, str]:
    titles = list(dims)

    def check(o):
        r = canon_keys(o.get("roles") or {}, titles)
        bad = [t for t in titles if r.get(t) not in DIM_ROLES]
        return f"give use_case/type/other for {bad}" if bad else None
    out = llm.json("opinion.dim_roles", DIMS_SYSTEM, f"MARKET: {market}\nDIMENSIONS: {json.dumps(dims, ensure_ascii=False)}",
                   tier="fast", check=check, temperature=0.0)
    return canon_keys(out["roles"], titles)
