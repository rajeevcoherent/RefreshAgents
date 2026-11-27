"""Market-sizing analyst for generic markets: 2025 anchor and CAGR of the root geography by triangulation.

Step 1 (analyst): for every top-level segment of the priced dimension, a demand-side volume build
  (a base count times multiplicative factors, each labelled and sourced) and a price per unit;
  a top-down route (a parent / adjacent market value x the share this market takes of it);
  a read of every cited figure (scope adjustment); the growth view.
Code: volume = base x factors; bottom-up = sum of volume x price; top-down = parent x share; cited
  figures moved to 2025 in-scope terms.
Step 2 (analyst): sees the three routes and sets triangulation weights. Code blends.
Step 3 (analyst, growth check): published FORECASTS of this market (a value in 2028-2035) are converted by
  code into the CAGR each implies from OUR 2025 base. Growth rates quoted by those sources are not reused:
  they start from their own (usually smaller) bases. The analyst sets the final CAGR against that implied
  range and must justify, with specific evidence, any CAGR outside it.
The result has the same shape as the gearbox sizing (bottom_up.by_application keyed by priced
segment, with spend_per_unit_usd = price per unit), so pricing, compute and the writer are shared.
"""
from __future__ import annotations

from .common import canon_keys, dump, fmt_facts, house, num, scope_block, select_facts
from .generic import INDUSTRY, PERSONA

STEP1 = PERSONA + """

TASK: You are the market-sizing analyst. Size the {root} 2025 market (US$ Mn) for the scope given.
You set parameters; the code does the arithmetic.

ROUTE A - bottom-up, for EVERY top-level segment of "{dim}" listed below:
  volume_build: a chain of numbers whose PRODUCT is the 2025 volume of that segment in {root}, in the
    volume unit you state. The first entry is a base count (e.g. treated patients, animals, sites, labs,
    installed units, packs); each next entry is a multiplicative factor (e.g. share treated, share on
    this product, adherence-adjusted months / 12, units per site). Label each entry and give its
    evidence id or "analyst".
  price_usd_per_unit: average 2025 price per volume unit at the value basis of the scope (e.g.
    manufacturer net price, not list price, when the scope counts net sales).
  The code computes value = volume x price.
ROUTE B - top-down: a parent or adjacent market whose 2025 value you can anchor (from evidence or
  your knowledge, say which) and the share of it that falls in THIS market's scope.
ROUTE C - cited figures: published sizes of this market or company product revenues; give the scope
  factor that converts each to THIS scope (state the derivation).

Return json:
{{
 "volume_unit": "what one unit of volume is",
 "segments": {{"<segment>": {{"volume_build": [{{"label": "...", "value": number, "evidence": "W# or analyst"}}, ...],
                           "price_usd_per_unit": number, "price_reasoning": "1-2 sentences",
                           "reasoning": "2-3 sentences on why this segment is this size"}} ...every segment...}},
 "top_down": {{"parent_market": "...", "parent_value_2025_usd_mn": number, "share_of_parent": number,
              "evidence": "W# or analyst", "reasoning": "..."}},
 "cited_figures": [{{"evidence": "W#", "figure_usd_mn": number, "year": int, "scope_factor": number, "reasoning": "..."}}],
 "cited_forecasts": [{{"evidence": "W#", "figure_usd_mn": number, "year": int (2028-2035), "scope_factor": number,
                      "reasoning": "what the forecast covers and why this factor converts it to our scope"}}],
 "global_cagr": number, "historical_cagr_2020_2025": number,
 "cagr_reasoning": "4-6 sentences",
 "drivers": [..], "restraints": [..], "geopolitics": [..]
}}
Use decimals for shares and factors. Only list cited figures that are actually informative; an empty list is acceptable.
cited_forecasts = every published FUTURE value of this market in the evidence (the value, not the CAGR the source
quotes). The code works out the growth each implies from our own 2025 base - do not copy a source's CAGR, because
it starts from that source's base year and base size."""
if INDUSTRY == "neutral":                       # non-healthcare markets (FLEET_INDUSTRY=neutral); healthcare unchanged
    _HC_BASE = "(e.g. treated patients, animals, sites, labs,\n    installed units, packs)"
    if _HC_BASE not in STEP1:
        raise RuntimeError("sizing_generic.py neutral wording: base-count example not found in STEP1")
    STEP1 = STEP1.replace(_HC_BASE, "(e.g. acreage treated, households, installed assets,\n"
                                    "    facilities, units shipped, production volume)")

STEP2 = PERSONA + """

TASK: You are the market-sizing analyst, triangulating. You see three independent routes to the
{root} 2025 market value. Decide how much weight each deserves given how reliable its inputs are,
and state your final CAGR 2025-2032. The code computes the final value as the weighted blend.
Return json:
{{"weights": {{"bottom_up": number, "top_down": number, "cited": number}},
 "reasoning": "4-6 sentences: which route you trust and why, and what the blended number means",
 "global_cagr": number, "confidence": "High"|"Medium"|"Low"}}
Weights must be >= 0 and sum to 1. Give "cited" zero weight if no cited figure survived."""

STEP3 = PERSONA + """

TASK: You are the market-sizing analyst, checking growth. The code has converted every published forecast of
this market into the CAGR it implies from OUR {root} 2025 base of US$ {base:,.1f} Mn (table below). A growth
rate quoted by a source is not comparable: it starts from that source's own base year and base size, so a CAGR
copied from a source applied to a different base gives a wrong 2032 value.
Set the final {root} CAGR 2025-2032. Inside the implied range is the default. Outside it (by more than 1 pp) is
allowed only with specific, evidence-based reasons why every published endpoint is wrong for this scope.
Return json:
{{"global_cagr": number, "implied_2032_usd_mn": number,
 "reasoning": "4-6 sentences: the growth view, how it relates to the published endpoints, the main drivers and restraints",
 "departure_reason": "required only if outside the implied range by more than 1 pp, else empty"}}"""

CAGR_MIN, CAGR_MAX = -0.15, 0.60


def endpoints(p1: dict, base: float, base_year: int) -> list[dict]:
    out = []
    for f in p1.get("cited_forecasts") or []:
        fig, yr, sf = num(f.get("figure_usd_mn")), num(f.get("year")), num(f.get("scope_factor"))
        if not fig or not yr or sf is None or sf <= 0 or not (base_year + 2 <= int(yr) <= base_year + 12) or base <= 0:
            continue
        v = fig * sf
        out.append({**f, "value_in_scope_usd_mn": v,
                    "implied_cagr": (v / base) ** (1 / (int(yr) - base_year)) - 1})
    return out


def _check3(lo: float, hi: float):
    def check(out: dict) -> str | None:
        c = num(out.get("global_cagr"))
        if c is None or not (CAGR_MIN <= c <= CAGR_MAX):
            return f"global_cagr must be a decimal between {CAGR_MIN} and {CAGR_MAX}"
        if len(str(out.get("reasoning", ""))) < 120:
            return "reasoning must be 4-6 concrete sentences"
        if not (lo - 0.01 <= c <= hi + 0.01) and len(str(out.get("departure_reason", ""))) < 120:
            return (f"global_cagr {c:.2%} is outside the implied range {lo:.2%}-{hi:.2%} by more than 1 pp: either move "
                    f"inside it or give a specific, evidence-based departure_reason (why every published endpoint is wrong)")
        return None
    return check


def _check1(segs: list[str]):
    def check(out: dict) -> str | None:
        sg = canon_keys(out.get("segments") or {}, segs)
        miss = [s for s in segs if s not in sg]
        if miss:
            return f"segments missing (exact names): {miss}"
        for s in segs:
            it = sg[s] or {}
            vb = it.get("volume_build")
            if not isinstance(vb, list) or not vb:
                return f"segments[{s}].volume_build must be a non-empty list of {{label, value, evidence}}"
            for e in vb:
                v = num((e or {}).get("value"))
                if v is None or v <= 0:
                    return f"segments[{s}].volume_build entry '{(e or {}).get('label')}' needs a positive number"
            p = num(it.get("price_usd_per_unit"))
            if p is None or p <= 0:
                return f"segments[{s}].price_usd_per_unit must be a positive number"
        out["segments"] = sg
        td = out.get("top_down") or {}
        pv, sh = num(td.get("parent_value_2025_usd_mn")), num(td.get("share_of_parent"))
        if pv is None or pv <= 0 or sh is None or not (0 < sh <= 1):
            return "top_down needs parent_value_2025_usd_mn > 0 and share_of_parent between 0 and 1"
        c = num(out.get("global_cagr"))
        if c is None or not (CAGR_MIN <= c <= CAGR_MAX):
            return f"global_cagr must be a decimal between {CAGR_MIN} and {CAGR_MAX}"
        return None
    return check


def _check2(out: dict) -> str | None:
    w = out.get("weights") or {}
    vals = [num(w.get(k)) for k in ("bottom_up", "top_down", "cited")]
    if any(v is None or v < 0 for v in vals):
        return "weights.bottom_up/top_down/cited must be non-negative numbers"
    if abs(sum(vals) - 1) > 0.02:
        return f"weights sum to {sum(vals):.3f}; they must sum to 1"
    c = num(out.get("global_cagr"))
    if c is None or not (CAGR_MIN <= c <= CAGR_MAX):
        return f"global_cagr must be a decimal between {CAGR_MIN} and {CAGR_MAX}"
    return None


def bottom_up(p1: dict, segs: list[str]) -> dict:
    rows, total = {}, 0.0
    for s in segs:
        it = p1["segments"][s]
        units = 1.0
        for e in it["volume_build"]:
            units *= num(e["value"])
        price = num(it["price_usd_per_unit"])
        val = units * price / 1e6
        rows[s] = {"installed_units": units, "oem_asp_usd": price, "spend_pct": 1.0, "spend_per_unit_usd": price,
                   "value_usd_mn": val, "volume_build": it["volume_build"]}
        total += val
    for s in rows:
        rows[s]["share_of_bottom_up"] = rows[s]["value_usd_mn"] / total if total else 0.0
    return {"by_application": rows, "total_usd_mn": total, "installed_units": sum(r["installed_units"] for r in rows.values())}


def sizing_node(state: dict) -> dict:
    llm, cfg, tax = state["llm"], state["cfg"], state["tax"]
    dim = tax.priced_dimension
    segs = list(dim.top)
    root = tax.root
    facts = select_facts(state.get("facts", []), themes=("global_size", "pricing", "installed_base", "players"), limit=60)
    critique = state.get("critique", {}).get("sizing", "")
    notes = state.get("segment_definitions") or {}
    seg_txt = "\n".join(f"- {s}" + (f" ({notes[s]})" if notes.get(s) else "")
                        + (f"; sub-segments: {', '.join(dim.children[s])}" if dim.children.get(s) else "") for s in segs)
    user = (f"MARKET: {cfg.market_name}\nGEOGRAPHY OF THIS SHEET: {root}\nSCOPE: {dump(state['scope'])}\n\n"
            f"PRICED DIMENSION: {dim.title}\nSEGMENTS (exact names):\n{seg_txt}\n\n"
            f"WEB EVIDENCE (verified quotes):\n{fmt_facts(facts, limit=60)}\n"
            + scope_block(state) + house(state, "sizing")
            + (f"\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}\n" if critique else ""))
    p1 = llm.json("sizing_step1", STEP1.format(root=root, dim=dim.title), user, check=_check1(segs),
                  temperature=0.3, retries=5)
    bu = bottom_up(p1, segs)
    td_raw = p1["top_down"]
    td = num(td_raw["parent_value_2025_usd_mn"]) * num(td_raw["share_of_parent"])
    cagr1 = num(p1["global_cagr"])
    cited = []
    for c in p1.get("cited_figures") or []:
        fig, yr, sf = num(c.get("figure_usd_mn")), num(c.get("year")), num(c.get("scope_factor"))
        if not fig or not yr or sf is None or sf <= 0:
            continue
        cited.append({**c, "value_2025_in_scope_usd_mn": fig * sf * (1 + cagr1) ** (cfg.base_year - int(yr))})
    cited_med = sorted(c["value_2025_in_scope_usd_mn"] for c in cited)[len(cited) // 2] if cited else None
    routes = (f"ROUTE A bottom-up (volume x price): US$ {bu['total_usd_mn']:,.1f} Mn; by segment "
              + ", ".join(f"{s}: {r['installed_units']:,.0f} units x US$ {r['spend_per_unit_usd']:,.2f} = {r['value_usd_mn']:,.1f}"
                          for s, r in bu["by_application"].items()) + "\n"
              f"ROUTE B top-down ({td_raw.get('parent_market')} US$ {num(td_raw['parent_value_2025_usd_mn']):,.1f} Mn x "
              f"{num(td_raw['share_of_parent']):.3f}): US$ {td:,.1f} Mn\n"
              f"ROUTE C cited figures moved to 2025 in-scope terms: "
              + ("; ".join(f"{c['evidence']}: US$ {c['value_2025_in_scope_usd_mn']:,.1f} Mn" for c in cited) or "none")
              + (f" (median US$ {cited_med:,.1f} Mn)" if cited_med else ""))
    p2 = llm.json("sizing_step2", STEP2.format(root=root), f"{user}\n\nYOUR STEP-1 PARAMETERS:\n{dump(p1)}\n\nCODE RESULTS:\n{routes}",
                  check=_check2, temperature=0.2)
    w = {k: num(p2["weights"].get(k)) or 0.0 for k in ("bottom_up", "top_down", "cited")}
    if cited_med is None:
        w["cited"] = 0.0
    s = sum(w.values()) or 1.0
    w = {k: v / s for k, v in w.items()}
    final = w["bottom_up"] * bu["total_usd_mn"] + w["top_down"] * td + w["cited"] * (cited_med or 0.0)
    cagr = num(p2["global_cagr"])
    ends = endpoints(p1, final, cfg.base_year)
    p3 = None
    if ends:
        imp = sorted(e["implied_cagr"] for e in ends)
        lo, hi, med = imp[0], imp[-1], imp[len(imp) // 2]
        tbl = "\n".join(f"- {e.get('evidence')}: US$ {num(e['figure_usd_mn']):,.0f} Mn in {int(num(e['year']))} x scope "
                        f"{num(e['scope_factor']):.3f} = US$ {e['value_in_scope_usd_mn']:,.0f} Mn -> implied CAGR from our base "
                        f"{e['implied_cagr']:.2%}" for e in ends)
        p3 = llm.json("sizing_step3_growth", STEP3.format(root=root, base=final),
                      f"{user}\n\nYOUR STEP-2 VIEW: CAGR {cagr:.2%} - {p2.get('reasoning', '')}\n\nPUBLISHED ENDPOINTS:\n{tbl}\n"
                      f"IMPLIED RANGE: {lo:.2%} to {hi:.2%} (median {med:.2%}); your step-2 CAGR gives US$ "
                      f"{final * (1 + cagr) ** 7:,.0f} Mn in 2032.", check=_check3(lo, hi), temperature=0.2)
        cagr = num(p3["global_cagr"])
        # downstream readers (regional analyst, sheet text, validators) read step1's growth fields
        p1["cagr_reasoning_initial"], p1["global_cagr_initial"] = p1.get("cagr_reasoning", ""), p1.get("global_cagr")
        p1["cagr_reasoning"] = (f"{p3.get('reasoning', '')} Published endpoints imply {lo:.2%}-{hi:.2%} a year from this "
                                f"base (median {med:.2%})." + (f" Departure: {p3['departure_reason']}"
                                                               if p3.get("departure_reason") else ""))
        p1["global_cagr"] = cagr
    sizing = {"mode": "generic", "volume_unit": p1.get("volume_unit") or state["scope"].get("volume_unit", "unit"),
              "step1": p1, "step2": p2, "bottom_up": bu, "top_down_usd_mn": td,
              "top_down_parent": td_raw, "oem_2025_usd_mn": num(td_raw["parent_value_2025_usd_mn"]),
              "cited": cited, "cited_median_usd_mn": cited_med, "weights": w,
              "global_value_2025": final, "global_cagr": cagr, "endpoints": ends, "step3": p3,
              "growth_reasoning": (p3 or {}).get("reasoning") or p1.get("cagr_reasoning", "")}
    return {"sizing": sizing,
            "log": state.get("log", []) + [f"sizing: {root} 2025 = US$ {final:,.1f} Mn, CAGR {sizing['global_cagr']:.2%}"]}
