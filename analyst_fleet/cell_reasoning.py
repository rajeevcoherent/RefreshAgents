"""Reasoning and method for EVERY value cell of the Input Sheet's Data sheet, plus the run's research
methodology. Used by the writer (cell comments, 'Cell Reasoning' and 'Research Methodology' sheets)
and by cell_audit.py (cell-by-cell comparison with the human sheet).

data_layout(tax) is the single source of truth for where each number sits:
  seg_share     band 1: segment share (top level: of the dimension; sub-segment: of its parent)
  country_cagr  band 2, row 2: country CAGR 2025-32
  seg_cagr      band 2: segment CAGR 2025-32
  size_share / size_cagr / size_value   sizing block: share of parent, CAGR, value 2025 (US$ Mn)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HEUR = re.compile(r"\b2[ABC]-\d+\b")


@dataclass
class Cell:
    row: int
    col: int
    kind: str
    geo: str
    dim: str = ""
    seg: str = ""
    parent_seg: str | None = None
    parent_geo: str | None = None
    sheet: str = "Data"


def data_layout(tax) -> tuple[list[Cell], dict[str, Any]]:
    leaves = tax.leaves
    b2 = 3 + len(leaves) + 3
    cells: list[Cell] = [Cell(2, b2 + 1 + ci, "country_cagr", g) for ci, g in enumerate(leaves)]
    labels: dict[str, Any] = {"b2": b2, "dim_rows": [], "seg_rows": []}
    r = 4
    for dim in tax.dimensions:
        labels["dim_rows"].append((r, dim.title))
        r += 1
        for s, parent in dim.rows:
            labels["seg_rows"].append((r, s, parent))
            for ci, g in enumerate(leaves):
                cells.append(Cell(r, 3 + ci, "seg_share", g, dim.title, s, parent))
                cells.append(Cell(r, b2 + 1 + ci, "seg_cagr", g, dim.title, s, parent))
            r += 1
        r += 1
    r = max(r + 2, 36)
    labels["size_header"] = r
    r += 1
    size_rows = []

    def put(row, name, parent, bold):
        size_rows.append((row, name, parent, bold))
        for col, k in ((3, "size_share"), (4, "size_cagr"), (5, "size_value")):
            cells.append(Cell(row, col, k, name, parent_geo=parent))

    for reg in tax.regions:
        put(r, reg, tax.root, True)
        r += 1
    put(r, tax.root, None, True)
    r += 2
    for p in tax.parent_nodes[1:]:
        put(r, p, tax.parent_of[p], True)
        r += 1
        for k in tax.children_of[p]:
            put(r, k, p, False)
            r += 1
        r += 1
    labels["size_rows"] = size_rows
    return cells, labels


def asp_layout(tax) -> list[Cell]:
    """ASP sheet: rows from 5 in the priced dimension's order, one column per leaf country (mirrors the writer)."""
    dim = tax.priced_dimension
    cells = []
    for i, (s, parent) in enumerate(dim.rows):
        for ci, g in enumerate(tax.leaves):
            cells.append(Cell(5 + i, 3 + ci, "asp", g, dim.title, s, parent, sheet="ASP"))
    return cells


def value_of(numbers: dict, c: Cell) -> float | None:
    geo, seg = numbers["geo"], numbers["seg"]
    try:
        if c.kind == "asp":
            return numbers["price"]["asp"][c.geo][c.seg]
        if c.kind == "seg_share":
            return seg["shares"][c.dim][c.geo][c.seg]
        if c.kind == "seg_cagr":
            return seg["seg_cagr"][c.dim][c.geo][c.seg]
        if c.kind in ("country_cagr", "size_cagr"):
            return geo["cagr"][c.geo]
        if c.kind == "size_share":
            return geo["share"][c.geo]
        if c.kind == "size_value":
            return geo["value"][c.geo]
    except (KeyError, TypeError):
        return None
    return None


def address(c: Cell) -> str:
    from openpyxl.utils import get_column_letter
    return f"{get_column_letter(c.col)}{c.row}"


# --------------------------------------------------------------------------------- explanations
def _group_of(tax, leaf: str) -> str | None:
    return next((g for g, ls in tax.leaf_groups.items() if leaf in ls), None)


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _stage_of_dim(state, dim_title: str) -> str:
    tag = state["tax"].strategy_of(dim_title) if hasattr(state["tax"], "strategy_of") else None
    if tag:
        return {"2B": "2B use case (industry vertical) split", "2C": "2C product type x application split"}.get(tag, tag)
    try:
        from .agents.common import dim_stage
        return "2B use-case split" if dim_stage(state, dim_title) == "use_case" else "2C type split"
    except Exception:
        return "segment split"


def _geo_item(state, node: str) -> dict:
    tax = state["tax"]
    p = tax.parent_of.get(node)
    if p is None:
        return {}
    holder = state.get("regional_raw") if p == tax.root else (state.get("country_raw") or {}).get(p)
    return ((holder or {}).get("items") or {}).get(node) or {}


def _validator_notes(obj: dict) -> str:
    vs = obj.get("validation") or []
    return " | ".join(f"{v.get('id')}: {v.get('verdict')} (w {v.get('weight_on_human', 0):.2f}) - "
                      f"{str(v.get('validated_reasoning', ''))[:400]}" for v in vs)


def scope_basis(state: dict, c: Cell) -> str:
    """Which client definition the number rests on (segment row for segment cells, market boundary otherwise)."""
    defs = state.get("segment_definitions") or {}
    if c.seg:
        d = defs.get(c.seg, "")
        par = f" Parent {c.parent_seg}: {defs.get(c.parent_seg, '')[:300]}" if c.parent_seg else ""
        return (d[:700] + par) if d else "(segment not found in the client definition document)"
    if c.kind == "size_value" and c.geo == state["tax"].root:
        sp = state["sizing"]["step1"].get("spend_pct_of_new_unit_price") or {}
        ex = "; ".join(f"{a}: {str(v.get('excluded', ''))[:160]}" for a, v in sp.items() if isinstance(v, dict) and v.get("excluded"))
        return ("Counts the client's included categories only (gearbox parts, maintenance/overhaul/repair, refurbishment & "
                "upgrades, like-for-like replacement gearboxes, dedicated gearbox lube & condition monitoring, gear-only IGC). "
                + (f"Excluded by the sizing analyst: {ex}" if ex else ""))
    return ("In-scope aftermarket per the client definition for this geography: gearbox-specific parts and services on "
            "installed qualifying high-speed helical gearboxes; turbine/compressor/pump-only work, low-speed gearboxes and new "
            "greenfield gearboxes excluded.")


def explain(state: dict, c: Cell, stage_cache: dict[str, str]) -> dict[str, str]:
    tax, numbers = state["tax"], state["numbers"]
    geo, seg = numbers["geo"], numbers["seg"]
    adjs = numbers.get("adjustments") or []
    val = value_of(numbers, c)
    out = {"scope": "", "approach": "", "method": "", "reasoning": "", "adjustments": "", "validator": "", "evidence": "", "confidence": "",
           "heuristics": ""}

    if c.kind == "asp":
        pr = numbers["price"]
        praw = state.get("pricing_raw") or {}
        ci = (praw.get("country_index") or {}).get(c.geo) or {}
        bu = (state["sizing"].get("bottom_up") or {}).get("by_application", {})
        idx, idx_raw = pr["index"].get(c.geo), _f(ci.get("index"))
        trend = praw.get("price_trend") or {}
        out["approach"] = "Price (ASP) - global average annual spend per installed gearbox x country price index"
        if c.seg in bu:
            b = bu[c.seg]
            out["method"] = (f"Global average for {c.seg}: US$ {b['spend_per_unit_usd']:,.0f} per installed gearbox per year "
                             f"(= {b['spend_pct']:.1%} of the new gearbox price US$ {b['oem_asp_usd']:,.0f}, from the sizing build). "
                             f"{c.geo} price index {idx_raw:.2f} set by the pricing analyst"
                             + (f", scaled to {idx:.3f} so implied serviced units reconcile with value" if idx and idx_raw
                                and abs(idx - idx_raw) > 5e-4 else "") + f" -> US$ {val:,.0f} per gearbox per year.")
        else:
            out["method"] = (f"Parent row: value of {c.seg} in {c.geo} divided by the serviced units of its sub-segments "
                             f"= US$ {val:,.0f} per gearbox per year.")
        out["reasoning"] = (f"COUNTRY PRICE LEVEL: {ci.get('reasoning', '')}  APPLICATION: "
                            f"{(praw.get('application_notes') or {}).get(c.seg, '')}  TREND: "
                            f"{(trend.get('annual_change') or 0):.1%} a year - {trend.get('reasoning', '')}")
        out["adjustments"] = "; ".join(f"{a['kind']}: {a['detail']}" for a in adjs if a["stage"] == "pricing")
        out["evidence"] = "OEM (new-unit prices)"
        gi = _geo_item(state, c.geo)
        out["confidence"] = str(gi.get("confidence") or "")
    elif c.kind in ("seg_share", "seg_cagr"):
        group = _group_of(tax, c.geo)
        raw = (state.get("segments_raw") or {}).get(f"{group}||{c.dim}") or {}
        si = (raw.get("segments") or {}).get(c.seg) or {}
        cr = (raw.get("countries") or {}).get(c.geo) or {}
        stage = stage_cache.setdefault(c.dim, _stage_of_dim(state, c.dim))
        cv = geo["value"].get(c.geo, 0.0)
        sh = seg["shares"][c.dim].get(c.geo, {})
        if c.kind == "seg_share":
            raw_sh = _f((cr.get("shares") or {}).get(c.seg))
            absval = cv * (val or 0) * (sh.get(c.parent_seg, 1.0) if c.parent_seg else 1.0)
            base = f"share of {c.parent_seg}" if c.parent_seg else f"share of {c.geo}'s {c.dim} total"
            out["approach"] = (f"{stage} - top-down: {c.geo} total (2A) x segment share; "
                               + ("sub-segment split of its parent (2C nesting)" if c.parent_seg else "top-level split"))
            gv = _f(si.get("group_share_view"))
            out["method"] = (f"{c.geo} 2025 value US$ {cv:,.2f} Mn. Group analyst ({group}) view for {c.seg}: "
                             f"{gv:.1%} ({base}). " if gv is not None else f"{c.geo} 2025 value US$ {cv:,.2f} Mn. ") + \
                (f"Country analyst set {raw_sh:.1%}" if raw_sh is not None else "Country analyst share") + \
                (f", normalised to {val:.1%} so the split adds to 100%" if raw_sh is not None and val is not None
                 and abs(raw_sh - val) > 5e-4 else "") + f". Implied 2025 value of this cell: US$ {absval:,.2f} Mn."
            if state.get("oem") and c.geo in state["oem"] and not state["oem"][c.geo].scale_uncertain:
                try:
                    om = state["oem"][c.geo].mix(tax, c.dim).get(c.seg)
                    if om is not None:
                        out["method"] += f" OEM new-unit mix for comparison: {om:.1%} (installed-flow signal)."
                except Exception:
                    pass
            dim = tax.dimension(c.dim)
            if dim.priced and not c.parent_seg:
                bu = (state["sizing"].get("bottom_up") or {}).get("by_application", {}).get(c.seg)
                if bu:
                    out["method"] += (f" Bottom-up installed-base view (global): {bu['share_of_bottom_up']:.1%} of "
                                      f"aftermarket value.")
            out["reasoning"] = (f"COUNTRY: {(cr.get('why') or {}).get(c.seg, '')}  GROUP: {si.get('reasoning', '')}")
            scopes = {f"{c.geo} / {c.dim}"} | ({f"{c.geo} / {c.parent_seg}"} if c.parent_seg else set())
            out["adjustments"] = "; ".join(f"{a['kind']}: {a['detail']}" for a in adjs
                                           if a["stage"] == "segments" and a["scope"] in scopes and "normalisation" in a["kind"])
        else:
            lc = geo["cagr"].get(c.geo)
            ov = _f((cr.get("growth_premium_overrides") or {}).get(c.seg))
            gp = _f(si.get("growth_premium"))
            raw_p = ov if ov is not None else gp
            fin_p = (val - lc) if val is not None and lc is not None else None
            out["approach"] = f"{stage} - growth: country CAGR + segment growth premium (bottom-up mix consistency)"
            out["method"] = (f"{c.geo} CAGR {lc:.2%} (sizing block) + premium for {c.seg}: analyst "
                             + (f"{raw_p * 100:+.2f} pp" if raw_p is not None else "n/a")
                             + (" (country override)" if ov is not None else " (group view)")
                             + (f" -> {fin_p * 100:+.2f} pp after re-centring so the share-weighted segment growth equals the "
                                f"country rate" if fin_p is not None and raw_p is not None and abs(fin_p - raw_p) > 5e-5 else "")
                             + f" = {val:.2%}." if val is not None and lc is not None else "")
            out["reasoning"] = str(si.get("growth_reasoning") or si.get("reasoning") or "")
            out["adjustments"] = "; ".join(f"{a['kind']}: {a['detail']}" for a in adjs
                                           if a["stage"] == "segments" and a["scope"] == f"{c.geo} / {c.dim}"
                                           and "premium" in a["kind"])
        out["evidence"] = "; ".join(map(str, si.get("evidence") or []))
        out["confidence"] = str(si.get("confidence") or "")
        out["validator"] = _validator_notes(si)

    else:
        node = c.geo
        it = _geo_item(state, node)
        p = tax.parent_of.get(node)
        sz = state["sizing"]
        if node == tax.root:
            if c.kind == "size_value":
                bu, td, cm = sz["bottom_up"]["total_usd_mn"], sz["top_down_usd_mn"], sz.get("cited_median_usd_mn")
                w = sz["weights"]
                out["approach"] = "2A base-year anchor - triangulation of bottom-up, top-down and cited figures"
                out["method"] = (f"Bottom-up (installed base {sz['bottom_up']['installed_units']:,.0f} gearboxes x annual service "
                                 f"spend) US$ {bu:,.1f} Mn x {w.get('bottom_up', 0):.2f} + top-down (OEM 2025 US$ "
                                 f"{sz['oem_2025_usd_mn']:,.1f} Mn x aftermarket/OEM ratio) US$ {td:,.1f} Mn x {w.get('top_down', 0):.2f}"
                                 + (f" + cited median US$ {cm:,.1f} Mn x {w.get('cited', 0):.2f}" if cm else "")
                                 + f" = US$ {val:,.1f} Mn.")
                spx = sz["step1"].get("spend_pct_of_new_unit_price") or {}
                bld = "; ".join(f"{a}: " + " + ".join(f"{k.split('_')[0]} {v:.2%}" for k, v in (it.get("components") or {}).items())
                                + f" = {it.get('value', 0):.2%}" for a, it in spx.items() if isinstance(it, dict) and it.get("components"))
                if bld:
                    out["method"] += f" Spend build-up per application (fraction of new gearbox price per year): {bld}."
                out["reasoning"] = str(sz["step2"].get("reasoning", ""))
                out["confidence"] = str(sz["step2"].get("confidence", ""))
            elif c.kind == "size_cagr":
                out["approach"] = "2A growth - forecast CAGR set by the sizing analyst from drivers and history"
                out["method"] = f"Global CAGR 2025-32 = {val:.2%} (analyst {(_f(sz['step1'].get('global_cagr')) or 0):.2%})."
                out["reasoning"] = str(sz["step1"].get("cagr_reasoning", ""))
            else:
                out["approach"], out["method"] = "root", "Global = 100% by definition."
                out["reasoning"] = "The global market is the reference total; every regional share in this block is a share of it."
            out["evidence"] = "OEM; " + "; ".join(str(x.get("evidence")) for x in sz.get("cited") or [])
        else:
            pv = geo["value"].get(p, 0.0)
            lvl = "regional" if p == tax.root else "country"
            if c.kind == "size_share":
                rs = _f(geo.get("raw_share", {}).get(node)) if isinstance(geo.get("raw_share"), dict) else _f(it.get("share"))
                out["approach"] = f"2A {lvl} split - share of {p}, set by the {lvl} analyst, normalised across siblings"
                out["method"] = (f"Analyst share {rs:.1%}" if rs is not None else "Analyst share") + \
                    (f" -> {val:.2%} after normalising {p}'s children to 100%" if rs is not None and val is not None
                     and abs(rs - val) > 5e-4 else "") + "."
            elif c.kind == "size_cagr":
                ac = _f(geo.get("analyst_cagr", {}).get(node))
                out["approach"] = f"2A {lvl} growth - CAGR set by the {lvl} analyst, reconciled with {p}"
                out["method"] = (f"Analyst CAGR {ac:.2%}" if ac is not None else "Analyst CAGR") + \
                    (f" -> {val:.2%} after the parent/child CAGR reconciliation" if ac is not None and val is not None
                     and abs(ac - val) > 5e-5 else "") + "."
                out["adjustments"] = "; ".join(f"{a['kind']}: {a['detail']}" for a in adjs
                                               if a["stage"] == "geo" and a["scope"] == p)
            else:
                out["approach"] = f"2A top-down value - share of {p} x {p} value"
                out["method"] = f"{geo['share'].get(node, 0):.2%} x US$ {pv:,.2f} Mn ({p}) = US$ {val:,.2f} Mn."
            out["reasoning"] = str(it.get("reasoning", ""))
            out["evidence"] = "; ".join(map(str, it.get("evidence") or []))
            out["confidence"] = str(it.get("confidence") or "")
            out["validator"] = _validator_notes(it)
            if c.kind == "size_cagr" and it.get("drivers"):
                out["reasoning"] += "  DRIVERS: " + "; ".join(map(str, it.get("drivers") or [])) + \
                    "  RESTRAINTS: " + "; ".join(map(str, it.get("restraints") or [])) + \
                    ("  GEOPOLITICS: " + "; ".join(map(str, it.get("geopolitics") or [])) if it.get("geopolitics") else "")
    out["scope"] = scope_basis(state, c)
    out["heuristics"] = ", ".join(sorted(set(HEUR.findall(out["reasoning"] + " " + out["validator"]))))
    if not out["reasoning"].strip():
        out["reasoning"] = "(no analyst text recorded - flagged in Checks)"
    return out


def explain_all(state: dict, include_asp: bool = False) -> list[dict]:
    cells, _ = data_layout(state["tax"])
    if include_asp:
        cells = cells + asp_layout(state["tax"])
    cache: dict[str, str] = {}
    from .calc_chain import chain
    rows = []
    for c in cells:
        e = explain(state, c, cache)
        e.update(chain(state, c))
        rows.append({"sheet": c.sheet, "cell": address(c), "row": c.row, "col": c.col, "kind": c.kind, "geo": c.geo,
                     "parent_geo": c.parent_geo or state["tax"].parent_of.get(c.geo) or "", "dim": c.dim, "seg": c.seg,
                     "parent_seg": c.parent_seg or "", "value": value_of(state["numbers"], c), **e})
    return rows


# --------------------------------------------------------------------------------- methodology
def methodology(state: dict) -> list[tuple[str, str]]:
    """(heading, paragraph) pairs describing how THIS run produced its numbers."""
    tax, cfg, n = state["tax"], state["cfg"], state["numbers"]
    sz, geo = state["sizing"], n["geo"]
    sc = state.get("scope") or {}
    bu = sz["bottom_up"]
    ib = sz["step1"].get("installed_base", {})
    adj_kinds: dict[str, int] = {}
    for a in n.get("adjustments") or []:
        adj_kinds[a["kind"]] = adj_kinds.get(a["kind"], 0) + 1
    rev = state.get("reviews") or []
    val = state.get("validation") or []
    roles = {}
    for d in tax.dimensions:
        roles[d.title] = _stage_of_dim(state, d.title)
    S = []
    S.append(("1. Scope and definitions",
              f"{tax.market_name}. Base year {cfg.base_year}, forecast CAGR window {cfg.base_year}-{cfg.forecast_end_year}, "
              f"value in US$ Mn. Scope statement: {sc.get('scope_statement', '')} Value basis: {sc.get('value_basis', '')}. "
              f"Segment definitions are taken from the client definitions document and applied verbatim. Geography: "
              f"{len(tax.leaves)} leaf countries in a {max(tax.depth(l) for l in tax.leaves)}-level tree."))
    for k in ("inclusions", "exclusions"):
        items = sc.get(k) or []
        if items:
            S.append((f"1{'a' if k == 'inclusions' else 'b'}. Scope {k} (source: client document or analyst)",
                      " | ".join(f"[{i.get('source', '?')}] {i.get('item', i)}" + (f" (why: {i.get('why')})" if isinstance(i, dict)
                                 and i.get("source") == "analyst" else "") for i in items if isinstance(i, (dict, str)))))
    if sc.get("allocation_rules"):
        S.append(("1c. Allocation rules", " | ".join(map(str, sc["allocation_rules"]))))
    S.append(("2. Evidence base",
              f"Desk research with {len(state.get('evidence') or [])} web sources read and {len(state.get('facts') or [])} facts "
              f"kept under the verbatim-quote rule (a number is used only if the quoted sentence is on the page). "
              f"{'Blind mode: the publisher of the comparison sheet was blocked from search and reading. ' if cfg.blind else ''}"
              f"Primary quantitative input: the client's OEM (new-unit) workbooks - units shipped 2020-25 and values by "
              f"application, vertical and geography."))
    S.append(("3. Base-year size (2A anchor): three independent routes",
              f"Bottom-up: installed base = OEM unit shipments 2020-25 plus a back-extension of older vintages "
              f"(life {ib.get('service_life_years', ib.get('life', 'n/a'))} years) x active share, giving "
              f"{bu['installed_units']:,.0f} serviced gearboxes; x annual aftermarket spend per gearbox set as a % of new-unit "
              f"price per application ({', '.join(f'{a}: {r['spend_pct']:.1%}' for a, r in bu['by_application'].items())}) "
              f"= US$ {bu['total_usd_mn']:,.1f} Mn. Top-down: OEM 2025 US$ {sz['oem_2025_usd_mn']:,.1f} Mn x aftermarket/OEM "
              f"ratio {(_f(sz['step1']['aftermarket_to_oem_ratio'].get('value')) or 0):.2f} = US$ {sz['top_down_usd_mn']:,.1f} Mn. "
              f"Cited: {len(sz.get('cited') or [])} published figures moved to 2025 in-scope terms"
              + (f" (median US$ {sz['cited_median_usd_mn']:,.1f} Mn)" if sz.get("cited_median_usd_mn") else "")
              + f". The sizing analyst weighted the routes {sz['weights']} -> US$ {geo['value'][tax.root]:,.1f} Mn. "
              f"Reason: {sz['step2'].get('reasoning', '')}"))
    S.append(("4. Growth",
              f"Global CAGR {geo['cagr'][tax.root]:.2%}: {sz['step1'].get('cagr_reasoning', '')} Each region and country "
              f"analyst then set its own CAGR against the parent; code shifts children equally so share-weighted child growth "
              f"equals the parent ({adj_kinds.get('CAGR reconciliation', 0)} reconciliations)."))
    S.append(("5. Geography (2A, top-down)",
              f"A regional analyst split the global value across {len(tax.regions)} regions, then one analyst per parent node "
              f"split it across its children, level by level - each with a share, a CAGR and written reasoning (installed "
              f"base, capacity additions, utilisation, geopolitics), compared with the OEM installed-flow signal. Values are "
              f"share x parent value; shares are normalised to 100% within each parent."))
    S.append(("6. Segments (2B use cases / 2C types)",
              "Dimension roles: " + "; ".join(f"{d} = {r}" for d, r in roles.items()) + ". For every country group a segment "
              "analyst forms a group view (share and growth premium per segment, with reasoning), then country analysts set "
              "each country's shares cell by cell with a written 'why'. Sub-segments are shares of their parent. Segment "
              "CAGR = country CAGR + growth premium; premiums are re-centred so the mix-weighted segment growth equals the "
              f"country CAGR ({adj_kinds.get('growth-premium re-centring', 0)} re-centrings, "
              f"{adj_kinds.get('share normalisation', 0)} share normalisations)."))
    S.append(("7. Pricing",
              f"Unit: {(state.get('pricing_raw') or {}).get('unit_definition', 'aftermarket revenue per installed gearbox per year')}. "
              "Global average spend per unit from the bottom-up route, a country price index set by the pricing analyst and "
              "normalised so implied serviced units reconcile with value."))
    hj = "on" if getattr(cfg, "use_opinions", False) else "off"
    S.append(("8. House judgement",
              f"House judgement {hj}: analysts received stage heuristics distilled from past human estimates (2A geography and "
              "growth, 2B use-case splits, 2C type splits and price) as reasoning habits, not targets; heuristic ids they "
              "used are cited in their reasoning and listed per cell."))
    S.append(("9. Review and validation",
              f"{len(rev)} review pass(es); best pass kept (score "
              f"{next((r.get('overall') for r in rev if r.get('pass') == state.get('best_pass')), 'n/a')}). "
              + (f"Validation ({state.get('validation_mode', 'human reference')}): {len(val)} disputed items adjudicated; each "
                 "moved number carries the validator's reasoning next to it." if val else "No validation step applied.")))
    S.append(("10. Arithmetic rules",
              "LLM decides, code computes: every product, sum, normalisation and reconciliation is done in code and logged "
              f"({', '.join(f'{k}: {v}' for k, v in adj_kinds.items())}). Every value cell of the Data sheet has a row in "
              "'Cell Reasoning' with its approach, derivation, reasoning, adjustments and evidence."))
    S.append(("11. Limitations",
              "Absolute size depends on the service-spend assumption per installed gearbox, which has thin public evidence; "
              "country-level segment mixes for small countries rest on analyst judgement; band 3 trend tags are not produced."))
    return S
