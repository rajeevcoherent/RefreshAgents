"""Pricing analyst: what one unit of aftermarket volume costs, by country.

Unit = one installed qualifying gearbox serviced for one year (US$ of aftermarket
revenue per installed gearbox per year). The global average per application comes from
the sizing analyst's spend-per-unit (installed-base build), so price x volume is
consistent with the anchor. This analyst sets each country's price level relative to
the global average and the price trend; code normalises the indices.
"""
from __future__ import annotations

from .common import PERSONA, canon_keys, dump, fmt_facts, house, scope_block, num, select_facts

PRICING_SYSTEM = PERSONA + """

TASK: You are the pricing analyst. One unit of aftermarket volume = one installed
qualifying gearbox serviced for one year; its price = average annual aftermarket revenue
per installed gearbox (parts + services), US$.
The global average price per application is fixed by the installed-base build (given).
Set, for EVERY country, a price index relative to the global average (1.00 = global
average): think labour and engineering rates, share of work done by OEM service centres
vs independents/local shops, import duties and logistics for spare gear sets, currency,
sanctions-driven substitution (e.g. local or Chinese parts replacing Western OEM parts),
fleet type (larger, faster machines cost more to service) and contract structures
(long-term service agreements).
Also give the expected annual change of aftermarket prices 2025-2032 (decimal).
Return json:
{"unit_definition": "...",
 "country_index": {"<country>": {"index": number, "reasoning": "<=35 words"} ...every country...},
 "price_trend": {"annual_change": number, "reasoning": "2-3 sentences"},
 "application_notes": {"<application>": "1-2 sentences on what drives this application's service price"},
 "overview": "3-4 sentences"}"""


def _check(countries):
    def check(out: dict) -> str | None:
        ci = canon_keys(out.get("country_index") or {}, countries)
        miss = [c for c in countries if c not in ci]
        if miss:
            return f"country_index missing (exact names): {miss}"
        for c in countries:
            v = num((ci[c] or {}).get("index"))
            if v is None or not (0.3 <= v <= 2.5):
                return f"country_index[{c}].index must be between 0.3 and 2.5"
        t = num((out.get("price_trend") or {}).get("annual_change"))
        if t is None or not (-0.05 <= t <= 0.1):
            return "price_trend.annual_change must be a decimal between -0.05 and 0.10"
        return None
    return check


def pricing_node(state: dict) -> dict:
    llm, tax, oem = state["llm"], state["tax"], state["oem"]
    bu = state["sizing"]["bottom_up"]["by_application"]
    base = "\n".join(f"- {a}: global average US$ {r['spend_per_unit_usd']:,.0f} per installed gearbox per year "
                     f"({r['spend_pct']:.1%} of a new unit's US$ {r['oem_asp_usd']:,.0f})" for a, r in bu.items())
    reg_asp = []
    for r in tax.regions:
        o = oem.get(r)
        if o and o.asp_usd():
            reg_asp.append(f"- {r}: " + ", ".join(f"{a}: US$ {v:,.0f}" for a, v in o.asp_usd().items() if a in bu))
    facts = select_facts(state.get("facts", []), themes=("pricing", "players"), limit=30)
    critique = state.get("critique", {}).get("pricing", "")
    user = (f"MARKET: {state['cfg'].market_name}\nSCOPE: {state['scope'].get('value_basis', '')}\n\n"
            f"GLOBAL AVERAGE PRICE PER APPLICATION:\n{base}\n\n"
            f"OEM NEW-UNIT PRICE BY REGION (value / units, evidence id 'OEM'):\n" + ("\n".join(reg_asp) or "n/a") + "\n\n"
            f"COUNTRIES (exact names): {tax.leaves}\n\nWEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}"
            + scope_block(state)
            + house(state, "pricing")
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}" if critique else ""))
    out = llm.json("pricing", PRICING_SYSTEM, user, check=_check(tax.leaves), temperature=0.3)
    return {"pricing_raw": out, "log": state.get("log", []) + ["pricing: done"]}
