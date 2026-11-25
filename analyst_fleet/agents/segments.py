"""Segment analysts, per (country group, dimension), in two steps:

1. Group view (one call): reasoning, drivers, geopolitics and a growth premium for
   every segment across the group.
2. Country splits (chunks of <= CHUNK countries, in parallel): base-year shares for each
   country with a short reason per cell, given the group view.

Splitting keeps each DeepSeek call small enough to finish quickly (one call per whole
group with ~100 reasons was being dropped by the network after ~12 minutes). The merged
result has one shape - {"segments", "countries", "overview"} - used everywhere else.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..evidence.oem import oem_table
from .. import compute
from ..syslog import event
from . import generic as G
from .common import PERSONA, canon_keys, dim_stage, dump, fmt_facts, house, scope_block, num, select_facts

CHUNK = 4

_CONVENTION = """Share convention (strict):
 - top-level segments: share of the country's total aftermarket; they sum to 1.0
 - indented sub-segments: share of THEIR PARENT; each parent's sub-segments sum to 1.0
Segments (sub-segments indented):
{tree}"""

_THINK = """Think: which machines make up the INSTALLED fleet (not just recent orders), how
much gear-specific service each machine type needs (speed, power, duty, overhaul cycles),
and which end-use industries dominate (LNG, refining, petrochemicals, fertilizers, gas
transmission, power, steel, cement, marine). The OEM mix is a signal of recent
installations - move away from it where the installed base differs and say why."""

GROUP_SYSTEM = PERSONA + """

TASK: You are the {dim} analyst for {group}. Give the GROUP-LEVEL view of every segment:
its role and size in {group}'s aftermarket and its growth premium.
""" + _CONVENTION + "\n\n" + _THINK + """

Growth premium = segment's 2025-2032 CAGR minus the country CAGR (decimal, e.g. 0.012 =
+1.2 pp). Faster-growing segments positive, slower negative; code re-centres them so the
mix adds back to the country rate.

Return json:
{{
 "segments": {{"<segment>": {{"group_share_view": number (your view of the group-wide share, same convention),
                             "reasoning": "3-4 sentences: why this segment's size and role in {group}'s aftermarket",
                             "drivers": [..], "restraints": [..], "geopolitics": [..],
                             "growth_premium": number, "growth_reasoning": "1-2 sentences",
                             "evidence": ["W#" or "OEM"], "confidence": "High"|"Medium"|"Low"}} ...every segment...}},
 "overview": "3-4 sentences"
}}"""

COUNTRY_SYSTEM = PERSONA + """

TASK: You are the {dim} analyst for {group}. For EVERY country listed, set the 2025
aftermarket value split across the segments. Your group-level view is given; make each
country genuinely differ from it where its industrial base differs.
""" + _CONVENTION + "\n\n" + _THINK + """

Return json:
{{
 "countries": {{"<country>": {{"shares": {{"<segment>": number ...every segment...}},
                             "why": {{"<segment>": "<=30 words: why this share in this country" ...every segment...}},
                             "growth_premium_overrides": {{"<segment>": number, only where this country clearly deviates}} }}
               ...every country...}}
}}"""


def _generic_prompt(tpl: str) -> str:
    out = tpl.replace(PERSONA, G.PERSONA, 1).replace(_THINK, G.SEG_THINK)
    out = out.replace("'s aftermarket", "'s market").replace("total aftermarket", "total market")
    return out.replace("aftermarket value split", "market value split").replace('"OEM"', '"analyst"')


GROUP_SYSTEM_G = _generic_prompt(GROUP_SYSTEM)
COUNTRY_SYSTEM_G = _generic_prompt(COUNTRY_SYSTEM)


def _gen(state: dict) -> bool:
    return bool(getattr(state["cfg"], "generic", False))


def _premium_max(state: dict) -> float:
    return G.PREMIUM_MAX if _gen(state) else 0.08


def _tree(dim) -> str:
    return "\n".join(("    - " if parent else "  - ") + s + (f"   (share of {parent})" if parent else "")
                     for s, parent in dim.rows)


def _check_group(dim, pmax: float = 0.08):
    def check(out: dict) -> str | None:
        segs = canon_keys(out.get("segments") or {}, dim.all_segments)
        miss = [s for s in dim.all_segments if s not in segs]
        if miss:
            return f"'segments' is missing (exact names): {miss}"
        for s in dim.all_segments:
            p = num((segs[s] or {}).get("growth_premium"))
            if p is None or abs(p) > pmax:
                return f"segments[{s}].growth_premium must be a decimal between -{pmax} and {pmax}"
            if len(str((segs[s] or {}).get("reasoning", ""))) < 60:
                return f"segments[{s}].reasoning is too thin - give 3-4 concrete sentences"
        return None
    return check


def _check_countries(dim, countries, pmax: float = 0.08):
    def check(out: dict) -> str | None:
        cs = canon_keys(out.get("countries") or {}, countries)
        for c in cs.values():
            if isinstance(c, dict):
                canon_keys(c.get("shares"), dim.all_segments)
                canon_keys(c.get("why"), dim.all_segments)
                canon_keys(c.get("growth_premium_overrides"), dim.all_segments)
        missc = [c for c in countries if c not in cs]
        if missc:
            return f"'countries' is missing (exact names): {missc}"
        for c in countries:
            sh = (cs[c] or {}).get("shares") or {}
            # one missing top-level segment = residual of the others (logged, not silent)
            miss_top = [s for s in dim.top if num(sh.get(s)) is None]
            if len(miss_top) == 1:
                rest = sum(num(sh[s]) for s in dim.top if s != miss_top[0])
                if 0 <= rest < 1:
                    sh[miss_top[0]] = round(1 - rest, 6)
                    cs[c].setdefault("autofilled", []).append(miss_top[0])
                    event("restriction.autofill", country=c, dimension=dim.title, segment=miss_top[0],
                          value=round(1 - rest, 6), reason="analyst omitted one top-level share; set to 1 - others")
            m = [s for s in dim.all_segments if num(sh.get(s)) is None]
            if m:
                return f"countries[{c}].shares missing numbers for: {m}"
            t = sum(num(sh[s]) for s in dim.top)
            if abs(t - 1) > 0.03:
                return f"countries[{c}]: top-level shares sum to {t:.3f}, must be 1.0"
            for parent, kids in dim.children.items():
                kt = sum(num(sh[k]) for k in kids)
                if abs(kt - 1) > 0.03:
                    return f"countries[{c}]: sub-shares of {parent} sum to {kt:.3f}, must be 1.0 (they are shares OF {parent})"
            if not isinstance((cs[c] or {}).get("why"), dict):
                return f"countries[{c}].why must be an object keyed by segment"
            for s, v in ((cs[c] or {}).get("growth_premium_overrides") or {}).items():
                if num(v) is None or abs(num(v)) > pmax:
                    return f"countries[{c}].growth_premium_overrides[{s}] must be a decimal between -{pmax} and {pmax}"
        return None
    return check


def _context(state: dict, group: str, dim, geo: dict, countries: list[str]) -> str:
    tax, oem = state["tax"], state["oem"]
    defs = state["segment_definitions"]
    def_txt = "\n".join(f"- {s}: {defs.get(s, '(no separate definition; see the client scope)')}" for s in dim.all_segments)
    craw = (state.get("country_raw") or {}).get(group, {}).get("items", {})
    ctx = "\n".join(f"- {c}: 2025 value US$ {geo['value'][c]:.2f} Mn, CAGR {geo['cagr'][c]:.2%}. Country analyst: "
                    f"{(craw.get(c) or {}).get('reasoning', '')[:500]}" for c in countries)
    extra = ""
    if dim.priced and _gen(state):
        bu = state["sizing"]["bottom_up"]["by_application"]
        extra = (f"{tax.root.upper()} BOTTOM-UP VIEW (sizing analyst, volume x price - adjust per country): value by "
                 "segment = " + ", ".join(f"{a}: {r['share_of_bottom_up']:.1%}" for a, r in bu.items()) + "\n")
    elif dim.priced:
        bu = state["sizing"]["bottom_up"]["by_application"]
        extra = ("GLOBAL INSTALLED-BASE VIEW (sizing analyst, global average - adjust per country): aftermarket value by "
                 "application = " + ", ".join(f"{a}: {r['share_of_bottom_up']:.1%}" for a, r in bu.items())
                 + " (shares of total across leaf applications; Compressors = Centrifugal + IGC)\n")
    facts = select_facts(state.get("facts", []), themes=("segment", "installed_base"), geos=countries + [group], limit=35)
    critique = state.get("critique", {}).get(f"segments:{group}||{dim.title}", "")
    return (f"MARKET: {state['cfg'].market_name}\nGROUP: {group}\nCOUNTRIES (exact names): {countries}\n{ctx}\n\n"
            f"SEGMENT DEFINITIONS (client document):\n{def_txt}\n\n{extra}"
            + ("" if _gen(state) else f"OEM (new-unit) 2025 MIX, same share convention (evidence id 'OEM'):\n"
               f"{oem_table(oem, countries + [group], tax, dim.title)}\n\n")
            + f"WEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}"
            + scope_block(state, "allocation")
            + house(state, dim_stage(state, dim.title))
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}" if critique else ""))


def _context_cached(state: dict, group: str, dim, geo: dict, countries: list[str], gview_txt: str = "") -> str:
    """Generic-market context ordered for DeepSeek prefix caching: what every call of this dimension shares
    comes first (market, definitions, bottom-up view, rules, house judgement), then what every call of this
    group shares (group facts, group view), and only the chunk's countries and critique last."""
    tax = state["tax"]
    defs = state["segment_definitions"]
    def_txt = "\n".join(f"- {s}: {defs.get(s, '(no separate definition; see the client scope)')}" for s in dim.all_segments)
    extra = ""
    if dim.priced:
        bu = state["sizing"]["bottom_up"]["by_application"]
        extra = (f"{tax.root.upper()} BOTTOM-UP VIEW (sizing analyst, volume x price - adjust per country): value by "
                 "segment = " + ", ".join(f"{a}: {r['share_of_bottom_up']:.1%}" for a, r in bu.items()) + "\n\n")
    members = tax.leaf_groups[group]
    facts = select_facts(state.get("facts", []), themes=("segment", "installed_base"), geos=members + [group], limit=35)
    craw = (state.get("country_raw") or {}).get(group, {}).get("items", {})
    ctx = "\n".join(f"- {c}: 2025 value US$ {geo['value'][c]:.2f} Mn, CAGR {geo['cagr'][c]:.2%}. Country analyst: "
                    f"{(craw.get(c) or {}).get('reasoning', '')[:500]}" for c in countries)
    critique = state.get("critique", {}).get(f"segments:{group}||{dim.title}", "")
    return (f"MARKET: {state['cfg'].market_name}\nDIMENSION: {dim.title}\n\n"
            f"SEGMENT DEFINITIONS (client document):\n{def_txt}\n\n{extra}"
            + scope_block(state, "allocation").lstrip("\n")
            + house(state, dim_stage(state, dim.title))
            + f"\n\nGROUP: {group}\nWEB EVIDENCE FOR THIS GROUP (verified quotes):\n{fmt_facts(facts)}"
            + (f"\n\nYOUR GROUP-LEVEL VIEW:\n{gview_txt}" if gview_txt else "")
            + f"\n\nCOUNTRIES (exact names): {countries}\n{ctx}"
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}" if critique else ""))


def _seg_job(state: dict, group: str, dim, geo: dict, pool: ThreadPoolExecutor) -> dict:
    llm, tax = state["llm"], state["tax"]
    members = tax.leaf_groups[group]
    role = f"segments:{group}||{dim.title}"
    gen, pmax = _gen(state), _premium_max(state)
    gsys, csys = (GROUP_SYSTEM_G, COUNTRY_SYSTEM_G) if gen else (GROUP_SYSTEM, COUNTRY_SYSTEM)
    ctx_fn = _context_cached if gen else _context
    gview = llm.json(role, gsys.format(dim=dim.title, group=group, tree=_tree(dim)),
                     ctx_fn(state, group, dim, geo, members), check=_check_group(dim, pmax), temperature=0.3)
    gtxt = dump({s: {k: v for k, v in (gview["segments"].get(s) or {}).items()
                     if k in ("group_share_view", "reasoning", "growth_premium")} for s in dim.all_segments})
    size = max(1, min(CHUNK, 120 // max(1, len(dim.all_segments))))   # keep each reply to ~120 cells
    chunks = [members[i:i + size] for i in range(0, len(members), size)]
    event("batch.segment_chunks", job=role, chunks=chunks)
    futs = [pool.submit(llm.json, f"{role}:{ch[0]}..",
                        csys.format(dim=dim.title, group=group, tree=_tree(dim)),
                        (_context_cached(state, group, dim, geo, ch, gtxt) if gen else
                         _context(state, group, dim, geo, ch) + f"\n\nYOUR GROUP-LEVEL VIEW:\n{gtxt}"),
                        check=_check_countries(dim, ch, pmax), temperature=0.3, retries=5) for ch in chunks]
    countries: dict = {}
    for f in futs:
        countries.update(f.result()["countries"])
    return {"segments": gview["segments"], "overview": gview.get("overview", ""), "countries": countries}


def segments_node(state: dict, only: list[str] | None = None) -> dict:
    tax = state["tax"]
    geo = compute.geo_numbers(state)
    raw = dict(state.get("segments_raw") or {})
    jobs = [(g, d) for g in tax.leaf_groups for d in tax.dimensions
            if only is None or f"{g}||{d.title}" in only]
    event("batch.segments", jobs=[f"{g}||{d.title}" for g, d in jobs])
    workers = state["cfg"].llm_workers
    if _gen(state) and getattr(state["cfg"], "batch_segments", False):
        from .segments_batch import segments_batched       # several dimensions per call, same validators
        raw.update(segments_batched(state, geo, jobs, workers))
        return {"segments_raw": raw, "log": state.get("log", []) + [f"segments: {len(jobs)} group x dimension jobs, batched"]}
    # two pools: group views on one, country chunks on the other (no nested-pool deadlock)
    with ThreadPoolExecutor(max_workers=workers) as jobs_pool, ThreadPoolExecutor(max_workers=workers) as chunk_pool:
        futs = {f"{g}||{d.title}": jobs_pool.submit(_seg_job, state, g, d, geo, chunk_pool) for g, d in jobs}
        for k, f in futs.items():
            raw[k] = f.result()
    return {"segments_raw": raw, "log": state.get("log", []) + [f"segments: {len(jobs)} group views + country chunks"]}
