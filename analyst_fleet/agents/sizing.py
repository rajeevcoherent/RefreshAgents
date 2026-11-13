"""Market-sizing analyst: global 2025 anchor and global CAGR by triangulation.

Step 1 (analyst): installed-base parameters (service life, pre-2020 shipment growth,
  active share), annual aftermarket spend per installed gearbox as % of new-unit price
  by application, an aftermarket/OEM ratio view, a read of every cited figure (scope
  adjustment), and the global growth view.
Code: builds the installed base from OEM unit shipments, computes
  bottom-up = installed base x spend per unit, top-down = OEM value x ratio, and moves
  every cited figure to 2025 in-scope terms.
Step 2 (analyst): sees the three routes side by side and sets triangulation weights.
Code: final = weighted blend. Every route and weight is written to the Sizing sheet.
"""
from __future__ import annotations

from ..evidence.oem import OEMGeo
from .common import PERSONA, canon_keys, dump, fmt_facts, house, num, scope_block, select_facts

APPS_LEAF = ["Steam Turbines", "Gas Turbines", "Centrifugal Compressor", "IGC (Motor driven & Turbine driven)",
             "Pumps (High-speed centrifugal pumps (Motor driven & Turbine driven)", "Turbo expanders",
             "Blowers (Turbine driven)"]

STEP1_SYSTEM = PERSONA + """

TASK: You are the market-sizing analyst. Size the GLOBAL 2025 aftermarket (US$ Mn) for
the scope given, by building it bottom-up from the installed base, and by reading the
cited evidence. You set parameters; the code does the arithmetic.

Installed base: code sums OEM unit shipments 2020-2025 (given) and extends them back
(service_life_years - 6) more years, shrinking by pre2020_unit_growth per year going
back, then multiplies by active_fraction (share of that fleet still operating, in scope
and generating aftermarket revenue: excludes retired/mothballed units, units in warranty
years, units serviced in-house with no market spend).

Spend per unit: for each application, annual IN-SCOPE aftermarket spend per installed gearbox
as a fraction of its new-unit price. The new-unit price is the GEARBOX ASSEMBLY ONLY (OEM
definition: excludes the turbine, compressor, pump, motor and plant package). Build it from
the client's included categories, each amortised to a yearly fraction of that price - an event
costing C (fraction of new gearbox price) every N years contributes C / N per year:
  parts_and_internals             gear wheels, pinions, shafts, bearings, seals, gaskets (incl. spares stock)
  overhaul_and_repair_services    preventive / corrective maintenance, overhauls, emergency repairs (shop and field labour)
  inspection_monitoring_lubrication  dedicated gearbox inspection, condition monitoring, vibration, diagnostics, gearbox lube service
  replacement_gearboxes           like-for-like replacement gearboxes / gear internals in existing trains (amortised)
  upgrades_modernisation          refurbishment, reconditioning, rotor replacement, engineering upgrades, life extension
State what you EXCLUDED for each application (turbine/compressor/pump/motor work, IGC impellers,
seals and intercoolers, plant-wide lubrication, general maintenance contracts, ...). The code
adds the five components; their sum is the spend fraction.

Return json:
{
 "installed_base": {"service_life_years": int, "pre2020_unit_growth": number, "active_fraction": number, "reasoning": "..."},
 "spend_pct_of_new_unit_price": {"<application>": {"components": {"parts_and_internals": number,
        "overhaul_and_repair_services": number, "inspection_monitoring_lubrication": number,
        "replacement_gearboxes": number, "upgrades_modernisation": number},
        "excluded": "what out-of-scope spend you removed for this application",
        "reasoning": "intervals and costs behind each component"}, ...all 7 applications...},
 "aftermarket_to_oem_ratio": {"value": number, "reasoning": "global aftermarket revenue / global OEM revenue, from experience of similar capital equipment"},
 "cited_figures": [{"evidence": "W#", "figure_usd_mn": number, "year": int,
                    "scope_factor": number, "reasoning": "what the figure covers and why this factor converts it to our scope"}],
 "global_cagr": number, "historical_cagr_2020_2025": number,
 "cagr_reasoning": "4-6 sentences",
 "drivers": [..], "restraints": [..], "geopolitics": [..]
}
scope_factor converts a cited figure to THIS market's scope; derive it from what the cited figure
covers versus the client's included / excluded lists (state the derivation), not from a rule of
thumb. Only list figures that are actually informative; an empty list is acceptable."""

STEP2_SYSTEM = PERSONA + """

TASK: You are the market-sizing analyst, triangulating. You see three independent routes
to the global 2025 aftermarket value. Decide how much weight each deserves given how
reliable its inputs are, and state your final global CAGR. The code computes the final
value as the weighted blend of the routes.
Return json:
{"weights": {"bottom_up": number, "top_down": number, "cited": number},
 "reasoning": "4-6 sentences: which route you trust and why, and what the blended number means",
 "global_cagr": number, "confidence": "High"|"Medium"|"Low"}
Weights must be >= 0 and sum to 1. Give "cited" zero weight if no cited figure survived."""


SPEND_COMPONENTS = ("parts_and_internals", "overhaul_and_repair_services", "inspection_monitoring_lubrication",
                    "replacement_gearboxes", "upgrades_modernisation")


def _check_step1(out: dict) -> str | None:
    ib = out.get("installed_base") or {}
    life, g, af = num(ib.get("service_life_years")), num(ib.get("pre2020_unit_growth")), num(ib.get("active_fraction"))
    if not life or not (10 <= life <= 50):
        return "installed_base.service_life_years must be between 10 and 50"
    if g is None or not (-0.05 <= g <= 0.12):
        return "installed_base.pre2020_unit_growth must be a decimal between -0.05 and 0.12"
    if af is None or not (0.2 <= af <= 1.0):
        return "installed_base.active_fraction must be between 0.2 and 1.0"
    sp = canon_keys(out.get("spend_pct_of_new_unit_price") or {}, APPS_LEAF)
    miss = [a for a in APPS_LEAF if a not in sp]
    if miss:
        return f"spend_pct_of_new_unit_price missing applications (exact names): {miss}"
    for a in APPS_LEAF:
        it = sp[a] or {}
        comp = canon_keys(it.get("components") or {}, list(SPEND_COMPONENTS))
        bad = [k for k in SPEND_COMPONENTS if num(comp.get(k)) is None or num(comp.get(k)) < 0]
        if bad:
            return f"spend_pct_of_new_unit_price[{a}].components needs non-negative decimals for {bad}"
        v = sum(num(comp[k]) for k in SPEND_COMPONENTS)       # code adds the in-scope components
        if not (0.003 <= v <= 0.25):
            return (f"spend_pct_of_new_unit_price[{a}]: components add to {v:.4f}; the annual in-scope spend must be "
                    f"between 0.003 and 0.25 of the new gearbox assembly price")
        if len(str(it.get("excluded", ""))) < 20:
            return f"spend_pct_of_new_unit_price[{a}].excluded must state the out-of-scope spend you removed"
        it["components"], it["value"] = {k: num(comp[k]) for k in SPEND_COMPONENTS}, v
        sp[a] = it
    out["spend_pct_of_new_unit_price"] = sp
    r = num((out.get("aftermarket_to_oem_ratio") or {}).get("value"))
    if r is None or not (0.05 <= r <= 3):
        return "aftermarket_to_oem_ratio.value must be between 0.05 and 3"
    c = num(out.get("global_cagr"))
    if c is None or not (-0.02 <= c <= 0.2):
        return "global_cagr must be a decimal between -0.02 and 0.2"
    return None


def _check_step2(out: dict) -> str | None:
    w = out.get("weights") or {}
    vals = [num(w.get(k)) for k in ("bottom_up", "top_down", "cited")]
    if any(v is None or v < 0 for v in vals):
        return "weights.bottom_up/top_down/cited must be non-negative numbers"
    if abs(sum(vals) - 1) > 0.02:
        return f"weights sum to {sum(vals):.3f}; they must sum to 1"
    c = num(out.get("global_cagr"))
    if c is None or not (-0.02 <= c <= 0.2):
        return "global_cagr must be a decimal between -0.02 and 0.2"
    return None


def installed_base(oem_global: OEMGeo, app: str, life: int, g: float, active: float) -> float:
    units = oem_global.seg_units_y.get(app, {})
    recent = sum(units.get(y, 0.0) for y in range(2020, 2026))
    u2020 = units.get(2020, 0.0)
    older = sum(u2020 * (1 + g) ** (-t) for t in range(1, max(0, int(life) - 6) + 1))
    return (recent + older) * active


def bottom_up(oem_global: OEMGeo, p1: dict) -> dict:
    ib = p1["installed_base"]
    life, g, af = int(num(ib["service_life_years"])), num(ib["pre2020_unit_growth"]), num(ib["active_fraction"])
    asp = oem_global.asp_usd()
    rows, total = {}, 0.0
    for a in APPS_LEAF:
        units = installed_base(oem_global, a, life, g, af)
        pct = num(p1["spend_pct_of_new_unit_price"][a]["value"])
        spend = pct * asp.get(a, 0.0)
        val = units * spend / 1e6
        rows[a] = {"installed_units": units, "oem_asp_usd": asp.get(a, 0.0), "spend_pct": pct,
                   "spend_per_unit_usd": spend, "value_usd_mn": val}
        total += val
    for a in rows:
        rows[a]["share_of_bottom_up"] = rows[a]["value_usd_mn"] / total if total else 0.0
    return {"by_application": rows, "total_usd_mn": total,
            "installed_units": sum(r["installed_units"] for r in rows.values())}


def sizing_node(state: dict) -> dict:
    llm, cfg, oem = state["llm"], state["cfg"], state["oem"]
    g = oem["Global"]
    units_tbl = "\n".join(
        f"{a}: units 2020-2025 = {[round(g.seg_units_y.get(a, {}).get(y, 0)) for y in range(2020, 2026)]}; "
        f"OEM value 2025 = US$ {g.seg_value_k.get(a, 0) / 1000:.1f} Mn; implied new-unit price = US$ {g.asp_usd().get(a, 0):,.0f}"
        for a in APPS_LEAF)
    facts = select_facts(state.get("facts", []), themes=("global_size", "pricing", "installed_base", "players"), limit=50)
    critique = state.get("critique", {}).get("sizing", "")
    user = (f"MARKET: {cfg.market_name}\nSCOPE: {dump(state['scope'])}\n\n"
            f"GLOBAL OEM (new-unit) MARKET, from the client's OEM workbook (evidence id 'OEM'):\n"
            f"OEM value 2020 = US$ {g.v(2020):.1f} Mn, 2025 = US$ {g.v(2025):.1f} Mn, 2032 = US$ {g.v(2032):.1f} Mn "
            f"(CAGR 2020-25 {g.cagr(2020, 2025):.2%}, 2025-32 {g.cagr(2025, 2032):.2%}).\n{units_tbl}\n\n"
            f"WEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}\n"
            + scope_block(state)
            + house(state, "sizing")
            + (f"\nREVIEWER CRITIQUE OF YOUR LAST ATTEMPT - address it:\n{critique}\n" if critique else ""))
    p1 = llm.json("sizing_step1", STEP1_SYSTEM, user, check=_check_step1, temperature=0.3, retries=5)

    bu = bottom_up(g, p1)
    ratio = num(p1["aftermarket_to_oem_ratio"]["value"])
    td = g.v(2025) * ratio
    cagr1 = num(p1["global_cagr"])
    cited = []
    for c in p1.get("cited_figures") or []:
        fig, yr, sf = num(c.get("figure_usd_mn")), num(c.get("year")), num(c.get("scope_factor"))
        if not fig or not yr or sf is None or sf <= 0:
            continue
        v25 = fig * sf * (1 + cagr1) ** (cfg.base_year - int(yr))
        cited.append({**c, "value_2025_in_scope_usd_mn": v25})
    cited_med = sorted(c["value_2025_in_scope_usd_mn"] for c in cited)[len(cited) // 2] if cited else None

    routes = (f"ROUTE A bottom-up (installed base x spend per unit): US$ {bu['total_usd_mn']:.1f} Mn "
              f"(installed base {bu['installed_units']:,.0f} gearboxes; by application "
              + ", ".join(f"{a}: {r['value_usd_mn']:.1f}" for a, r in bu["by_application"].items()) + ")\n"
              f"ROUTE B top-down (OEM 2025 US$ {g.v(2025):.1f} Mn x ratio {ratio:.2f}): US$ {td:.1f} Mn\n"
              f"ROUTE C cited figures moved to 2025 in-scope terms: "
              + ("; ".join(f"{c['evidence']}: US$ {c['value_2025_in_scope_usd_mn']:.1f} Mn" for c in cited) or "none")
              + (f" (median US$ {cited_med:.1f} Mn)" if cited_med else ""))
    p2 = llm.json("sizing_step2", STEP2_SYSTEM,
                  f"{user}\n\nYOUR STEP-1 PARAMETERS:\n{dump(p1)}\n\nCODE RESULTS:\n{routes}",
                  check=_check_step2, temperature=0.2)
    w = {k: num(p2["weights"].get(k)) or 0.0 for k in ("bottom_up", "top_down", "cited")}
    if cited_med is None:
        w["cited"] = 0.0
    s = sum(w.values()) or 1.0
    w = {k: v / s for k, v in w.items()}
    final = w["bottom_up"] * bu["total_usd_mn"] + w["top_down"] * td + w["cited"] * (cited_med or 0.0)
    sizing = {"step1": p1, "step2": p2, "bottom_up": bu, "top_down_usd_mn": td, "oem_2025_usd_mn": g.v(2025),
              "cited": cited, "cited_median_usd_mn": cited_med, "weights": w,
              "global_value_2025": final, "global_cagr": num(p2["global_cagr"])}
    return {"sizing": sizing, "log": state.get("log", []) + [f"sizing: global 2025 = US$ {final:.1f} Mn, CAGR {sizing['global_cagr']:.2%}"]}
