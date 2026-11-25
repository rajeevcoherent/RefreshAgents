"""Reviewer: a sceptical research director who reads the whole draft and sends specific
pieces back. It never edits numbers; it names the stage and the instruction."""
from __future__ import annotations

from . import generic as G
from .common import PERSONA, num, scope_block

REVIEW_SYSTEM = PERSONA + """

TASK: You are the research director reviewing a junior team's draft Input Sheet before it
goes to the client. Be sceptical and specific. Score each criterion 1-10:
 1 scope      - do sizes reflect gear-specific aftermarket only (not whole turbine/compressor service)? Check
                every number against the client SCOPE RULEBOOK given (included / excluded lists, allocation
                rules, OEM boundary) and name the rule an out-of-scope number breaks.
 2 regional   - are regional/country shares plausible for where the installed base actually is?
 3 growth     - are CAGRs coherent across levels and defensible (no copy-paste rates)?
 4 segments   - do application and vertical mixes fit each country's industry (and each other)?
 5 pricing    - are price levels across countries plausible?
 6 reasoning  - is every number explained with concrete drivers and geopolitics, not generic text?
Do NOT score on closeness to any published market size.
List concrete issues. Each issue names the target to redo, using EXACTLY one of:
 "sizing" | "regional" | "country:<parent node>" | "segments:<group>||<dimension>" | "pricing"
and an instruction precise enough for the analyst to act on. Valid targets:
 VALID_TARGETS
Raise at most 8 issues, most
important first; only raise issues that would change numbers materially.
Return json:
{"scores": {"scope": n, "regional": n, "growth": n, "segments": n, "pricing": n, "reasoning": n},
 "overall": number, "issues": [{"target": "...", "problem": "...", "instruction": "..."}],
 "executive_summary": "5-7 sentences a client could read: size, growth, where and why"}"""


def digest(state: dict, numbers: dict) -> str:
    tax, geo, seg, price = state["tax"], numbers["geo"], numbers["seg"], numbers["price"]
    sz = state["sizing"]
    if sz.get("mode") == "generic":
        td = sz.get("top_down_parent") or {}
        lines = [f"{tax.root.upper()} 2025: US$ {sz['global_value_2025']:,.1f} Mn; CAGR 2025-32 {sz['global_cagr']:.2%}; "
                 f"routes: bottom-up (volume x price) {sz['bottom_up']['total_usd_mn']:,.1f}, top-down "
                 f"({td.get('parent_market')} x {td.get('share_of_parent')}) {sz['top_down_usd_mn']:,.1f}, cited median "
                 f"{sz['cited_median_usd_mn'] or 'none'}; weights {sz['weights']}; volume unit: {sz.get('volume_unit')}; "
                 + "; ".join(f"{a}: {r['installed_units']:,.0f} units x US$ {r['spend_per_unit_usd']:,.2f}"
                             for a, r in sz["bottom_up"]["by_application"].items())]
        if sz.get("endpoints"):
            imp = sorted(e["implied_cagr"] for e in sz["endpoints"])
            lines.append(f"PUBLISHED FORECAST ENDPOINTS ({len(imp)}) imply a CAGR of {imp[0]:.2%}-{imp[-1]:.2%} from this base; "
                         f"sheet CAGR {sz['global_cagr']:.2%} -> 2032 US$ {sz['global_value_2025'] * (1 + sz['global_cagr']) ** 7:,.0f} Mn")
    else:
        lines = [f"GLOBAL 2025: US$ {sz['global_value_2025']:.1f} Mn; CAGR 2025-32 {sz['global_cagr']:.2%}; "
             f"routes: bottom-up {sz['bottom_up']['total_usd_mn']:.1f}, top-down {sz['top_down_usd_mn']:.1f}, "
             f"cited median {sz['cited_median_usd_mn'] or 'none'}; weights {sz['weights']}; "
             f"OEM 2025 {sz['oem_2025_usd_mn']:.1f}; installed base {sz['bottom_up']['installed_units']:,.0f} units"]
    lines = list(lines)
    for p in tax.parent_nodes:
        kids = tax.children_of[p]
        lines.append(f"\n{p} (US$ {geo['value'].get(p, 0):.2f} Mn, CAGR {geo['cagr'].get(p, 0):.2%}) -> " + "; ".join(
            f"{k} {geo['share'].get(k, 0):.1%} / {geo['cagr'].get(k, 0):.1%}" for k in kids))
    for dim in tax.dimensions:
        def mix(sh, dim=dim):
            parts = []
            for s in dim.top:
                kids = dim.children.get(s, ())
                sub = (" [of which: " + ", ".join(f"{k[:22]} {sh.get(k, 0):.1%}" for k in kids) + "]") if kids else ""
                parts.append(f"{s[:24]} {sh.get(s, 0):.1%}{sub}")
            return "; ".join(parts)
        lines.append(f"\nMIX {dim.title}. Top-level segments sum to 100%. Bracketed sub-segments are shares OF their "
                     f"parent (they sum to 100% of the parent) - never add them to the top level.")
        for node in [tax.root] + tax.regions + [n for n in tax.parent_nodes if tax.depth(n) == 2]:
            r = seg["rollup"][dim.title].get(node)
            if r:
                lines.append(f"  {node}: {mix(r)}")
        for c in sorted(tax.leaves, key=lambda c: -geo["value"][c])[:8]:
            lines.append(f"  [{c}]: {mix(seg['shares'][dim.title].get(c, {}))}")
    # reasoning samples, so the 'reasoning' criterion is judged on actual text
    lines.append("\nREASONING SAMPLES (verbatim, truncated):")
    lines.append(f"  [Global sizing] {sz['step2'].get('reasoning', '')[:500]}")
    reg = (state.get("regional_raw") or {}).get("items", {})
    for r in tax.regions:
        it = reg.get(r) or {}
        lines.append(f"  [{r}] {it.get('reasoning', '')[:400]} | geopolitics: {it.get('geopolitics')}")
    for c in sorted(tax.leaves, key=lambda c: -geo["value"][c])[:4]:
        it = ((state.get("country_raw") or {}).get(tax.parent_of[c], {}).get("items", {}) or {}).get(c, {})
        lines.append(f"  [{c}] {it.get('reasoning', '')[:400]}")
    lines.append("\nPRICE INDEX (normalised, global avg = 1): " + ", ".join(f"{c} {i:.2f}" for c, i in price["index"].items()))
    lines.append("\nCODE FLAGS: " + ("; ".join(f"[{f['severity']}] {f['check']} {f['scope']}: {f['message']}"
                                              for f in numbers["flags"][:40]) or "none"))
    lines.append("\nADJUSTMENTS MADE BY CODE: " + ("; ".join(f"{a['scope']}: {a['detail']}" for a in numbers["adjustments"][:30]) or "none"))
    return "\n".join(lines)


def review(state: dict, numbers: dict) -> dict:
    tax = state["tax"]
    valid = (["sizing", "regional", "pricing"] + [f"country:{p}" for p in tax.parent_nodes[1:]]
             + [f"segments:{g}||{d.title}" for g in tax.leaf_groups for d in tax.dimensions])
    tpl = G.REVIEW if getattr(state["cfg"], "generic", False) else REVIEW_SYSTEM
    system = tpl.replace("VALID_TARGETS", ", ".join(f'"{v}"' for v in valid))

    def check(out: dict) -> str | None:
        if num(out.get("overall")) is None:
            return "overall score missing"
        for i in out.get("issues") or []:
            if str(i.get("target", "")).strip() not in valid:
                return (f"issue target '{i.get('target')}' is not valid; use exactly one of the listed targets "
                        f"(split a global issue into the specific groups it concerns)")
        return None
    return state["llm"].json("review", system,
                             f"MARKET: {state['cfg'].market_name}\nSCOPE: {state['scope'].get('scope_statement', '')}\n\n"
                             f"DRAFT:\n{digest(state, numbers)}" + scope_block(state), check=check, temperature=0.2)
