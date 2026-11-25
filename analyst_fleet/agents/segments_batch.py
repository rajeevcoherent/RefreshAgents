"""Batched segment analysts for generic markets: one call covers SEVERAL dimensions of a country group.

Per country group, dimensions are packed into bins of <= BIN_ROWS segment rows. Per bin:
  1. one group-view call for every dimension in the bin (instead of one call per dimension)
  2. country-chunk calls covering every dimension in the bin (chunk size keeps each reply <= CELLS cells)
For Tirzepatide (5 dimensions, 17 rows, 7 groups) that is 7 + 15 = 22 calls per pass instead of 35 + 75 = 110.

Output quality guard: every batched reply is checked with the SAME validators as the one-dimension path
(segments._check_group / _check_countries, applied dimension by dimension) and gets the same corrective
retries. If a batched reply still fails, that bin falls back to the original one-dimension calls
(segments._seg_job), so the result is never produced by a weaker path than before. The merged result has
exactly the shape of the one-dimension path ({"segments", "overview", "countries"} per group||dimension),
so compute, review, cell reasoning and the writer are unchanged.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..llm import LLMError
from ..syslog import event
from . import generic as G
from .common import canon_keys, dim_stage, dump, fmt_facts, house, scope_block, select_facts
from .segments import _check_countries, _check_group, _premium_max, _seg_job, _tree

BIN_ROWS = 60      # max segment rows per batched group-view call
CELLS = 120        # max (country x segment) cells per batched country call
CHUNK_MAX = 4      # never more countries per call than the one-dimension path used

_CONV = """Share convention (strict), separately for EACH dimension:
 - top-level segments: share of the country's total market; they sum to 1.0 within the dimension
 - indented sub-segments: share of THEIR PARENT; each parent's sub-segments sum to 1.0
Every dimension is an independent split of the SAME total - never add shares across dimensions."""

GROUP_BATCH = G.PERSONA + """

TASK: You are the segment analyst for {group}. For EVERY dimension below give the GROUP-LEVEL view of
every segment: its role and size in {group}'s market and its growth premium. Give each dimension the
same care as if it were your only task.
""" + _CONV + """

DIMENSIONS AND SEGMENTS (sub-segments indented):
{trees}

""" + G.SEG_THINK + """

Growth premium = segment's 2025-2032 CAGR minus the country CAGR (decimal, e.g. 0.012 = +1.2 pp).
Faster-growing segments positive, slower negative; code re-centres them so each dimension's mix adds
back to the country rate.

Return json:
{{"dimensions": {{"<dimension title>": {{
    "segments": {{"<segment>": {{"group_share_view": number, "reasoning": "3-4 sentences: why this segment's size and role",
                               "drivers": [..], "restraints": [..], "geopolitics": [..],
                               "growth_premium": number, "growth_reasoning": "1-2 sentences",
                               "evidence": ["W#" or "analyst"], "confidence": "High"|"Medium"|"Low"}} ...every segment...}},
    "overview": "3-4 sentences"}} ...every dimension...}}}}"""

COUNTRY_BATCH = G.PERSONA + """

TASK: You are the segment analyst for {group}. For EVERY country listed and EVERY dimension below, set
the 2025 market value split across the segments. Your group-level views are given; make each country
genuinely differ from them where its market differs.
""" + _CONV + """

DIMENSIONS AND SEGMENTS (sub-segments indented):
{trees}

""" + G.SEG_THINK + """

Return json:
{{"countries": {{"<country>": {{"<dimension title>": {{
      "shares": {{"<segment>": number ...every segment of the dimension...}},
      "why": {{"<segment>": "<=30 words: why this share in this country" ...every segment...}},
      "growth_premium_overrides": {{"<segment>": number, only where this country clearly deviates}} }}
    ...every dimension...}} ...every country...}}}}"""


def _trees(dims) -> str:
    return "\n".join(f"[{d.title}]\n{_tree(d)}" for d in dims)


def bins(dims) -> list[list]:
    out, cur, n = [], [], 0
    for d in dims:
        r = len(d.all_segments)
        if cur and n + r > BIN_ROWS:
            out.append(cur)
            cur, n = [], 0
        cur.append(d)
        n += r
    if cur:
        out.append(cur)
    return out


def _context(state: dict, group: str, dims, geo: dict, countries: list[str], gviews: str = "") -> str:
    """Shared-first ordering (DeepSeek prefix cache): dimension material, rules, house judgement, then group, then chunk."""
    tax = state["tax"]
    defs = state["segment_definitions"]
    parts = [f"MARKET: {state['cfg'].market_name}"]
    for d in dims:
        dl = "\n".join(f"- {s}: {defs[s]}" for s in d.all_segments if defs.get(s))
        parts.append(f"DIMENSION {d.title}" + (f" - segment notes:\n{dl}" if dl else ""))
        if d.priced:
            bu = state["sizing"]["bottom_up"]["by_application"]
            parts.append(f"{tax.root.upper()} BOTTOM-UP VIEW for {d.title} (sizing analyst, volume x price - adjust per "
                         "country): value by segment = " + ", ".join(f"{a}: {r['share_of_bottom_up']:.1%}" for a, r in bu.items()))
    txt = "\n\n".join(parts) + scope_block(state, "allocation")
    for stage in dict.fromkeys(dim_stage(state, d.title) for d in dims):
        txt += house(state, stage)
    members = tax.leaf_groups[group]
    facts = select_facts(state.get("facts", []), themes=("segment", "installed_base"), geos=members + [group], limit=35)
    craw = (state.get("country_raw") or {}).get(group, {}).get("items", {})
    ctx = "\n".join(f"- {c}: 2025 value US$ {geo['value'][c]:.2f} Mn, CAGR {geo['cagr'][c]:.2%}. Country analyst: "
                    f"{(craw.get(c) or {}).get('reasoning', '')[:500]}" for c in countries)
    crit = "\n".join(f"[{d.title}] {state.get('critique', {}).get(f'segments:{group}||{d.title}')}" for d in dims
                     if state.get("critique", {}).get(f"segments:{group}||{d.title}"))
    return (txt + f"\n\nGROUP: {group}\nWEB EVIDENCE FOR THIS GROUP (verified quotes):\n{fmt_facts(facts)}"
            + (f"\n\nYOUR GROUP-LEVEL VIEWS:\n{gviews}" if gviews else "")
            + f"\n\nCOUNTRIES (exact names): {countries}\n{ctx}"
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{crit}" if crit else ""))


def _check_group_batch(dims, pmax):
    def check(out: dict) -> str | None:
        ds = canon_keys(out.get("dimensions") or {}, [d.title for d in dims])
        miss = [d.title for d in dims if not isinstance(ds.get(d.title), dict)]
        if miss:
            return f"'dimensions' is missing (exact titles): {miss}"
        for d in dims:
            err = _check_group(d, pmax)(ds[d.title])
            if err:
                return f"[{d.title}] {err}"
        return None
    return check


def _check_country_batch(dims, countries, pmax):
    def check(out: dict) -> str | None:
        cs = canon_keys(out.get("countries") or {}, countries)
        miss = [c for c in countries if not isinstance(cs.get(c), dict)]
        if miss:
            return f"'countries' is missing (exact names): {miss}"
        for c in countries:
            canon_keys(cs[c], [d.title for d in dims])
        for d in dims:
            md = [c for c in countries if not isinstance(cs[c].get(d.title), dict)]
            if md:
                return f"countries {md} are missing dimension '{d.title}'"
            err = _check_countries(d, countries, pmax)({"countries": {c: cs[c][d.title] for c in countries}})
            if err:
                return f"[{d.title}] {err}"
        return None
    return check


def _bin_job(state: dict, group: str, dims, geo: dict, pool: ThreadPoolExecutor) -> dict[str, dict]:
    llm, tax = state["llm"], state["tax"]
    pmax = _premium_max(state)
    members = tax.leaf_groups[group]
    role = f"segments:{group}||" + "+".join(d.title for d in dims)
    trees = _trees(dims)
    try:
        gv = llm.json(role, GROUP_BATCH.format(group=group, trees=trees), _context(state, group, dims, geo, members),
                      check=_check_group_batch(dims, pmax), temperature=0.3, retries=3)
    except LLMError as e:                                  # quality guard: original path for this bin
        event("segments.batch_fallback", group=group, dims=[d.title for d in dims], stage="group view", error=str(e)[:300])
        return {f"{group}||{d.title}": _seg_job(state, group, d, geo, pool) for d in dims}
    gdims = gv["dimensions"]
    gtxt = dump({d.title: {s: {k: v for k, v in (gdims[d.title]["segments"].get(s) or {}).items()
                                if k in ("group_share_view", "reasoning", "growth_premium")} for s in d.all_segments}
                 for d in dims})
    rows = sum(len(d.all_segments) for d in dims)
    size = max(1, min(CHUNK_MAX, CELLS // max(1, rows)))
    chunks = [members[i:i + size] for i in range(0, len(members), size)]
    event("batch.segment_chunks", job=role, chunks=chunks, batched_dims=len(dims))

    def chunk(ch):
        try:
            out = llm.json(f"{role}:{ch[0]}..", COUNTRY_BATCH.format(group=group, trees=trees),
                           _context(state, group, dims, geo, ch, gtxt), check=_check_country_batch(dims, ch, pmax),
                           temperature=0.3, retries=4)
            return {d.title: {c: out["countries"][c][d.title] for c in ch} for d in dims}
        except LLMError as e:                              # quality guard: one-dimension calls for this chunk
            event("segments.batch_fallback", group=group, dims=[d.title for d in dims], stage="country chunk",
                  countries=ch, error=str(e)[:300])
            from .segments import COUNTRY_SYSTEM_G, _context_cached
            res = {}
            for d in dims:
                g1 = dump({s: {k: v for k, v in (gdims[d.title]["segments"].get(s) or {}).items()
                               if k in ("group_share_view", "reasoning", "growth_premium")} for s in d.all_segments})
                o = llm.json(f"segments:{group}||{d.title}:{ch[0]}..", COUNTRY_SYSTEM_G.format(dim=d.title, group=group, tree=_tree(d)),
                             _context_cached(state, group, d, geo, ch, g1), check=_check_countries(d, ch, pmax),
                             temperature=0.3, retries=5)
                res[d.title] = {c: o["countries"][c] for c in ch}
            return res

    futs = [pool.submit(chunk, ch) for ch in chunks]
    countries: dict[str, dict] = {d.title: {} for d in dims}
    for f in futs:
        for dt, cs in f.result().items():
            countries[dt].update(cs)
    return {f"{group}||{d.title}": {"segments": gdims[d.title]["segments"], "overview": gdims[d.title].get("overview", ""),
                                    "countries": countries[d.title]} for d in dims}


def segments_batched(state: dict, geo: dict, jobs: list[tuple], workers: int) -> dict[str, dict]:
    """jobs = [(group, dim)] -> {group||dim: result}; dimensions of the same group are batched together."""
    by_group: dict[str, list] = {}
    for g, d in jobs:
        by_group.setdefault(g, []).append(d)
    work = [(g, b) for g, ds in by_group.items() for b in bins(ds)]
    event("batch.segments_batched", bins=[f"{g}: {[d.title for d in b]}" for g, b in work])
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as jp, ThreadPoolExecutor(max_workers=workers) as cp:
        futs = [jp.submit(_bin_job, state, g, b, geo, cp) for g, b in work]
        for f in futs:
            out.update(f.result())
    return out
