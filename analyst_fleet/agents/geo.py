"""Regional analyst and country analysts (one per parent node of the geography tree)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ..evidence.oem import oem_table
from .. import compute
from ..syslog import event
from .common import GEO_ITEM_FIELDS, PERSONA, check_geo_items, dump, fmt_facts, house, scope_block, select_facts

log = logging.getLogger("fleet.geo")

REGIONAL_SYSTEM = PERSONA + """

TASK: You are the regional analyst. Split the GLOBAL 2025 aftermarket value across the
regions given and set each region's 2025-2032 value CAGR.
Think installed base first: where were high-speed gearboxes installed over the last
25-30 years (U.S. Gulf Coast refining/petrochemicals and gas pipelines; European
process industry, power and North Sea; Japan/Korea/China heavy industry; Middle East
oil, gas, LNG and petrochemicals; Russia's gas transmission; Latin America refining and
pipelines; African oil, gas and fertilizers)? Then utilisation, service culture and
pricing, then growth: new capacity entering service, ageing fleets needing overhauls,
energy transition, sanctions and energy-security policy.
The OEM new-unit regional split is a FLOW signal for recent installations; it is not the
installed base. You may move well away from it, but explain why.
The value-weighted average of your regional CAGRs should be close to the global CAGR given.
Return json: {"items": {"<region>": {...}}, "overview": "4-6 sentences on the regional picture"}
""" + GEO_ITEM_FIELDS

COUNTRY_SYSTEM = PERSONA + """

TASK: You are the country analyst for {parent}. Split {parent}'s 2025 aftermarket value
across the geographies listed and set each one's 2025-2032 value CAGR.
For each geography reason about: its installed base of qualifying high-speed gearboxes
(refineries, LNG/gas processing, petrochemical and fertilizer complexes, gas pipeline
compressor stations, steam/gas turbine power plants, steel blast-furnace blowers, cement
WHR, FPSOs), fleet age and utilisation, who services the fleet, new capacity coming
on line, and the geopolitical, regulatory and macro factors that raise or cut spend
(sanctions, trade/tariffs, energy-security programmes, local-content rules, currency
and financing stress, conflict risk). "Rest of ..." items cover all remaining countries
of that area - size them for what they contain.
The share-weighted average of the CAGRs should be close to {parent}'s CAGR of {cagr:.2%}.
Return json: {{"items": {{"<geography>": {{...}}}}, "overview": "3-5 sentences on {parent}"}}
""" + GEO_ITEM_FIELDS


def regional_node(state: dict) -> dict:
    llm, tax, oem, sizing = state["llm"], state["tax"], state["oem"], state["sizing"]
    regions = tax.regions
    facts = select_facts(state.get("facts", []), themes=("regional", "current_events", "installed_base"),
                         geos=regions + tax.leaves, limit=60)
    critique = state.get("critique", {}).get("regional", "")
    user = (f"MARKET: {state['cfg'].market_name}\nSCOPE: {state['scope'].get('scope_statement', '')}\n"
            f"GLOBAL 2025 AFTERMARKET VALUE: US$ {sizing['global_value_2025']:.1f} Mn; GLOBAL CAGR 2025-32: {sizing['global_cagr']:.2%}\n"
            f"Sizing analyst's growth view: {sizing['step1'].get('cagr_reasoning', '')}\n\n"
            f"OEM (new-unit) MARKET BY REGION (evidence id 'OEM'):\n{oem_table(oem, regions, tax, 'By Application')}\n\n"
            f"WEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}\n\nREGIONS (use exact names): {regions}"
            + scope_block(state)
            + house(state, "regional")
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}" if critique else ""))
    out = llm.json("regional", REGIONAL_SYSTEM, user, check=check_geo_items(regions), temperature=0.3)
    return {"regional_raw": out, "log": state.get("log", []) + ["regional: done"]}


def _country_call(state: dict, parent: str, pval: float, pcagr: float, critique: str = "") -> dict:
    llm, tax, oem = state["llm"], state["tax"], state["oem"]
    kids = tax.children_of[parent]
    facts = select_facts(state.get("facts", []), themes=("regional", "current_events"), geos=kids + [parent], limit=45)
    reg_note = ""
    rr = (state.get("regional_raw") or {}).get("items", {}).get(parent)
    if rr:
        reg_note = f"Regional analyst's view of {parent}: {rr.get('reasoning', '')}\n"
    elif parent in (state.get("country_raw") or {}).get(tax.parent_of.get(parent) or "", {}).get("items", {}):
        reg_note = f"Parent analyst's view of {parent}: {state['country_raw'][tax.parent_of[parent]]['items'][parent].get('reasoning', '')}\n"
    user = (f"MARKET: {state['cfg'].market_name}\n"
            f"{parent} 2025 AFTERMARKET VALUE: US$ {pval:.2f} Mn; {parent} CAGR 2025-32: {pcagr:.2%}\n{reg_note}\n"
            f"OEM (new-unit) MARKET FOR THESE GEOGRAPHIES (evidence id 'OEM'; grouped/'Rest of' OEM geographies shown where a country has no sheet):\n"
            f"{oem_table(oem, kids + [parent], tax, 'By Industry Vertical')}\n"
            f"Other OEM geographies that may contain these countries: "
            f"{', '.join(k for k in oem if k.startswith(('GCC', 'Rest of')))}\n\n"
            f"WEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}\n\nGEOGRAPHIES (use exact names): {kids}"
            + scope_block(state)
            + house(state, "country")
            + (f"\n\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}" if critique else ""))
    system = COUNTRY_SYSTEM.format(parent=parent, cagr=pcagr)
    return llm.json(f"country:{parent}", system, user, check=check_geo_items(kids), temperature=0.3)


def countries_node(state: dict, only: list[str] | None = None) -> dict:
    """Run country analysts level by level (a sub-region needs its parent's value first).
    `only` restricts the run to the listed parents (refine passes)."""
    tax = state["tax"]
    country_raw = dict(state.get("country_raw") or {})
    critique = state.get("critique", {})
    parents = [p for p in tax.parent_nodes if p != tax.root]
    levels: dict[int, list[str]] = {}
    for p in parents:
        levels.setdefault(tax.depth(p), []).append(p)
    for depth in sorted(levels):
        todo = [p for p in levels[depth] if (only is None or p in only)]
        if not todo:
            continue
        geo = compute.geo_numbers({**state, "country_raw": country_raw})
        event("batch.countries", level=depth, parents=todo)
        with ThreadPoolExecutor(max_workers=state["cfg"].llm_workers) as ex:
            futs = {p: ex.submit(_country_call, state, p, geo["value"][p], geo["cagr"][p],
                                 critique.get(f"country:{p}", "")) for p in todo}
            for p, f in futs.items():
                country_raw[p] = f.result()
    return {"country_raw": country_raw, "log": state.get("log", []) + [f"countries: {len(country_raw)} parent splits"]}
