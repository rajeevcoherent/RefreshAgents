"""Step-by-step calculation behind every Input Sheet number, from the first assumption to the cell.

Each step is one line "[stage] what = arithmetic = result", tagged with the strategy that produced it
(2A totals and growth, 2B use case = industry vertical, 2C product type x application), and the
analyst-set inputs it rests on are listed separately as assumptions. Everything is recomputed from
the saved state, so the arithmetic shown is exactly the arithmetic that produced the sheet.
"""
from __future__ import annotations

from typing import Any


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _path(tax, node: str) -> list[str]:
    out = [node]
    while tax.parent_of.get(out[-1]):
        out.append(tax.parent_of[out[-1]])
    return out[::-1]


def _raw_items(state, parent: str) -> dict:
    tax = state["tax"]
    holder = state.get("regional_raw") if parent == tax.root else (state.get("country_raw") or {}).get(parent)
    return (holder or {}).get("items") or {}


# ----------------------------------------------------------------------------------- global anchor
def global_steps(state: dict, full: bool) -> tuple[list[str], list[str]]:
    sz, tax = state["sizing"], state["tax"]
    geo = state["numbers"]["geo"]
    G = geo["value"][tax.root]
    bu, td, cm = sz["bottom_up"], sz["top_down_usd_mn"], sz.get("cited_median_usd_mn")
    w = {k: _f(v, 0.0) for k, v in (sz.get("weights") or {}).items()}
    ib = sz["step1"].get("installed_base") or {}
    life, g, af = int(_f(ib.get("service_life_years"), 0)), _f(ib.get("pre2020_unit_growth"), 0.0), _f(ib.get("active_fraction"), 1.0)
    ratio = _f((sz["step1"].get("aftermarket_to_oem_ratio") or {}).get("value"), 0.0)
    steps, assum = [], []
    blend = (f"[2A] Global 2025 = {w.get('bottom_up', 0):.2f} x bottom-up US$ {bu['total_usd_mn']:,.1f} Mn + "
             f"{w.get('top_down', 0):.2f} x top-down US$ {td:,.1f} Mn"
             + (f" + {w.get('cited', 0):.2f} x published US$ {cm:,.1f} Mn" if cm else "") + f" = US$ {G:,.1f} Mn")
    if not full:
        return [blend + " (full build-up: Global value cell / Sizing sheet)"], [
            f"route weights bottom-up {w.get('bottom_up', 0):.2f} / top-down {w.get('top_down', 0):.2f} / published {w.get('cited', 0):.2f}"]
    oem = (state.get("oem") or {}).get("Global")
    sp = sz["step1"].get("spend_pct_of_new_unit_price") or {}
    for a, r in bu["by_application"].items():
        recent = sum((oem.seg_units_y.get(a, {}) if oem else {}).get(y, 0.0) for y in range(2020, 2026)) if oem else 0.0
        older = r["installed_units"] / af - recent if af else 0.0
        comp = (sp.get(a) or {}).get("components") or {}
        cstr = (" (" + " + ".join(f"{k.replace('_', ' ')} {v:.2%}" for k, v in comp.items()) + ")") if comp else ""
        steps.append(f"[2C] {a}: {r['installed_units']:,.0f} gearboxes in service (OEM shipments 2020-25 {recent:,.0f} + "
                     f"earlier units {older:,.0f}, x {af:.0%} still operating) x US$ {r['oem_asp_usd']:,.0f} new gearbox price "
                     f"x {r['spend_pct']:.2%} annual in-scope spend{cstr} = US$ {r['value_usd_mn']:,.1f} Mn")
        if comp:
            assum.append(f"{a} annual spend as % of new gearbox price: {r['spend_pct']:.2%}{cstr}")
    steps.append(f"[2C] Bottom-up total = sum of applications = US$ {bu['total_usd_mn']:,.1f} Mn")
    steps.append(f"[2A] Top-down = OEM new-gearbox market 2025 US$ {sz['oem_2025_usd_mn']:,.1f} Mn x aftermarket/OEM ratio "
                 f"{ratio:.2f} = US$ {td:,.1f} Mn")
    if cm:
        cites = "; ".join(f"US$ {_f(c.get('figure_usd_mn'), 0):,.0f} Mn ({c.get('year')}) x scope factor {_f(c.get('scope_factor'), 0):.3f}"
                          f" moved to 2025 = US$ {c['value_2025_in_scope_usd_mn']:,.1f} Mn" for c in sz.get("cited") or [])
        steps.append(f"[2A] Published figures converted to the client scope: {cites}; median = US$ {cm:,.1f} Mn")
    steps.append(blend)
    assum = [f"installed base: service life {life} years, earlier fleet {g:.1%} smaller each year going back, "
             f"{af:.0%} still operating and serviced in the market", f"aftermarket/OEM ratio {ratio:.2f}",
             f"route weights bottom-up {w.get('bottom_up', 0):.2f} / top-down {w.get('top_down', 0):.2f} / "
             f"published {w.get('cited', 0):.2f}"] + assum
    return steps, assum


# ----------------------------------------------------------------------------------- geography
def geo_steps(state: dict, node: str, full_global: bool = False) -> tuple[list[str], list[str]]:
    tax = state["tax"]
    geo = state["numbers"]["geo"]
    steps, assum = global_steps(state, full_global)
    for k in _path(tax, node)[1:]:
        p = tax.parent_of[k]
        items = _raw_items(state, p)
        raw = {c: max(0.0, _f((items.get(c) or {}).get("share"), 0.0)) for c in tax.children_of[p]}
        tot = sum(raw.values()) or 1.0
        line = (f"[2A] {k} = {geo['share'][k]:.2%} of {p} (US$ {geo['value'][p]:,.2f} Mn) = US$ {geo['value'][k]:,.2f} Mn")
        if abs(tot - 1) > 1e-4:
            line += f" (analyst set {raw[k]:.2%}; {p}'s shares added to {tot:.2%}, so each was divided by {tot:.4f})"
        steps.append(line)
        assum.append(f"{k} share of {p}: {raw[k]:.2%} (analyst judgement)")
    return steps, assum


def cagr_steps(state: dict, node: str) -> tuple[list[str], list[str]]:
    tax, sz = state["tax"], state["sizing"]
    geo = state["numbers"]["geo"]
    gc = geo["cagr"][tax.root]
    hist = _f(sz["step1"].get("historical_cagr_2020_2025"))
    steps = [f"[2A] Global grows {gc:.2%} a year 2025-2032"
             + (f" (history 2020-25 {hist:.2%} + {(gc - hist) * 100:+.2f} pp for the forecast drivers)" if hist is not None else "")]
    assum = [f"global growth {gc:.2%}" + (f" vs history {hist:.2%}" if hist is not None else "")]
    for k in _path(tax, node)[1:]:
        p = tax.parent_of[k]
        ac = _f(geo["analyst_cagr"].get(k))
        fin = geo["cagr"][k]
        line = f"[2A] {k} grows {fin:.2%}"
        if ac is not None and abs(fin - ac) > 5e-5:
            line += (f" (analyst {ac:.2%}, shifted {(fin - ac) * 100:+.2f} pp like all of {p}'s parts so they average to "
                     f"{p}'s {geo['cagr'][p]:.2%})")
        elif ac is not None:
            line += f" (analyst {ac:.2%})"
        steps.append(line)
        if ac is not None:
            assum.append(f"{k} growth {ac:.2%} (analyst judgement)")
    return steps, assum


# ----------------------------------------------------------------------------------- per cell
def chain(state: dict, c) -> dict[str, str]:
    from .cell_reasoning import _group_of, value_of
    tax, numbers = state["tax"], state["numbers"]
    geo, seg = numbers["geo"], numbers["seg"]
    val = value_of(numbers, c)
    steps: list[str] = []
    assum: list[str] = []
    if c.kind in ("size_value", "size_share"):
        steps, assum = geo_steps(state, c.geo, full_global=(c.geo == tax.root and c.kind == "size_value"))
        if c.geo == tax.root and c.kind == "size_share":
            steps = ["[2A] Global is the reference total = 100%"]
    elif c.kind in ("size_cagr", "country_cagr"):
        steps, assum = cagr_steps(state, c.geo)
        v25 = geo["value"][c.geo]
        steps.append(f"=> {c.geo} 2032 = US$ {v25:,.2f} Mn x (1 + {val:.2%})^7 = US$ {v25 * (1 + val) ** 7:,.2f} Mn")
    elif c.kind in ("seg_share", "seg_cagr"):
        dim = tax.dimension(c.dim)
        tag = tax.strategy_of(c.dim) or "2B"
        group = _group_of(tax, c.geo)
        raw = (state.get("segments_raw") or {}).get(f"{group}||{c.dim}") or {}
        cr = (raw.get("countries") or {}).get(c.geo) or {}
        rs = {s: max(0.0, _f((cr.get("shares") or {}).get(s), 0.0)) for s in dim.all_segments}
        sh = seg["shares"][c.dim][c.geo]
        cv = geo["value"][c.geo]
        top_seg = c.parent_seg or c.seg
        abs_top = cv * sh[top_seg]
        if c.kind == "seg_share":
            steps, assum = geo_steps(state, c.geo)
            ttot = sum(rs[s] for s in dim.top) or 1.0
            gv = _f(((raw.get("segments") or {}).get(top_seg) or {}).get("group_share_view"))
            line = (f"[{tag}] {top_seg} = {sh[top_seg]:.2%} of {c.geo}'s US$ {cv:,.2f} Mn = US$ {abs_top:,.2f} Mn"
                    + (f" ({group} view {gv:.1%}; {c.geo} analyst {rs[top_seg]:.1%}" if gv is not None else
                       f" ({c.geo} analyst {rs[top_seg]:.1%}")
                    + (f"; the {c.dim} shares added to {ttot:.2%}, so each was divided by {ttot:.4f})" if abs(ttot - 1) > 1e-4 else ")"))
            steps.append(line)
            assum.append(f"{top_seg} share of {c.geo}: {rs[top_seg]:.1%} (analyst judgement)")
            if c.parent_seg:
                kids = dim.children.get(c.parent_seg, ())
                kt = sum(rs[k] for k in kids) or 1.0
                steps.append(f"[{tag}] {c.seg} = {val:.2%} of {c.parent_seg} (US$ {abs_top:,.2f} Mn) = US$ {abs_top * val:,.2f} Mn"
                             + (f" (analyst {rs[c.seg]:.1%}; sub-shares added to {kt:.2%}, divided by {kt:.4f})"
                                if abs(kt - 1) > 1e-4 else f" (analyst {rs[c.seg]:.1%})"))
                assum.append(f"{c.seg} share of {c.parent_seg}: {rs[c.seg]:.1%} (analyst judgement)")
        else:
            steps, assum = cagr_steps(state, c.geo)
            lc = geo["cagr"][c.geo]
            si = (raw.get("segments") or {}).get(c.seg) or {}
            ov = _f((cr.get("growth_premium_overrides") or {}).get(c.seg))
            rp = ov if ov is not None else _f(si.get("growth_premium"), 0.0)
            fp = val - lc
            steps.append(f"[{tag}] {c.seg} growth premium: analyst {rp * 100:+.2f} pp ({'country-specific' if ov is not None else 'group view'})"
                         + (f", adjusted {(fp - rp) * 100:+.2f} pp so the {c.dim} segments average back to {c.geo}'s rate"
                            if abs(fp - rp) > 5e-5 else ""))
            steps.append(f"[{tag}] {c.seg} grows {lc:.2%} {fp * 100:+.2f} pp = {val:.2%}")
            assum.append(f"{c.seg} growth premium {rp * 100:+.2f} pp vs {c.geo} (analyst judgement)")
            a25 = abs_top * (sh[c.seg] if c.parent_seg else 1.0)
            steps.append(f"=> {c.seg} in {c.geo}: US$ {a25:,.2f} Mn (2025) x (1 + {val:.2%})^7 = US$ {a25 * (1 + val) ** 7:,.2f} Mn (2032)")
    elif c.kind == "asp":
        pr = numbers["price"]
        bu = state["sizing"]["bottom_up"]["by_application"]
        sp = state["sizing"]["step1"].get("spend_pct_of_new_unit_price") or {}
        raw_i = _f(pr.get("index_raw", {}).get(c.geo))
        k = _f(pr.get("index_scale"), 1.0)
        idx = pr["index"][c.geo]
        units = pr.get("units", {}).get(c.geo, {}).get(c.seg)
        dim = tax.priced_dimension
        sh = seg["shares"][dim.title][c.geo]
        cv = geo["value"][c.geo]
        if c.seg in bu:
            r = bu[c.seg]
            comp = (sp.get(c.seg) or {}).get("components") or {}
            cstr = (" (" + " + ".join(f"{kk.replace('_', ' ')} {v:.2%}" for kk, v in comp.items()) + ")") if comp else ""
            steps.append(f"[2C] {c.seg} global base price = new gearbox US$ {r['oem_asp_usd']:,.0f} x {r['spend_pct']:.2%} annual "
                         f"in-scope spend{cstr} = US$ {r['spend_per_unit_usd']:,.0f} per gearbox per year")
            steps.append(f"[2A] {c.geo} price index = analyst {raw_i:.2f} x {k:.4f} (all countries scaled so serviced gearboxes x "
                         f"price add up to the market value) = {idx:.3f}")
            steps.append(f"=> US$ {r['spend_per_unit_usd']:,.0f} x {idx:.3f} = US$ {val:,.0f} per installed gearbox per year")
            assum += [f"{c.seg} annual spend {r['spend_pct']:.2%} of new gearbox price{cstr}",
                      f"{c.geo} price level {raw_i:.2f} x global average (analyst judgement)"]
        else:
            kids = dim.children.get(c.seg, ())
            steps.append(f"[2C] {c.seg} price = value of {c.seg} / serviced gearboxes of its types "
                         f"({', '.join(kids)}) = US$ {val:,.0f} per gearbox per year")
        if units:
            a_val = cv * (sh.get(c.seg, 0) * (sh.get(c.parent_seg, 1) if c.parent_seg else 1))
            steps.append(f"=> serviced gearboxes implied: {c.seg} value US$ {a_val:,.2f} Mn / US$ {val:,.0f} = {units:,.0f} gearboxes")
    contrib = {s: " | ".join(x for x in steps if x.startswith(f"[{s}]")) or "not used for this number" for s in ("2A", "2B", "2C")}
    return {"calc": "\n".join(steps), "assumptions": "; ".join(dict.fromkeys(assum)),
            "c2A": contrib["2A"], "c2B": contrib["2B"], "c2C": contrib["2C"]}
