"""All arithmetic. Analysts give opinions; this module turns them into the numbers on
the sheet and logs every adjustment it makes (normalisation, CAGR reconciliation,
growth-premium re-centring, price-index normalisation). Pure functions of the state.
"""
from __future__ import annotations

import math
from typing import Any

from .taxonomy import Taxonomy


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ geography
def geo_numbers(state: dict) -> dict[str, Any]:
    tax: Taxonomy = state["tax"]
    sizing = state["sizing"]
    tol = state["cfg"].cagr_reconcile_tolerance
    root = tax.root
    value = {root: sizing["global_value_2025"]}
    cagr = {root: sizing["global_cagr"]}
    share = {root: 1.0}
    analyst_cagr: dict[str, float] = {}
    raw_share: dict[str, float] = {}
    adjustments: list[dict] = []
    raw_by_parent = {root: state.get("regional_raw")} | dict(state.get("country_raw") or {})
    for p in tax.parent_nodes:
        out = raw_by_parent.get(p)
        if not out or p not in value:
            continue
        kids = tax.children_of[p]
        items = out.get("items", {})
        s = {k: max(0.0, _f(items.get(k, {}).get("share"))) for k in kids}
        tot = sum(s.values()) or 1.0
        if abs(tot - 1) > 1e-6:
            adjustments.append({"stage": "geo", "scope": p, "kind": "share normalisation",
                                "detail": f"analyst shares for {p}'s children summed to {tot:.4f}; divided by that sum"})
        c = {k: _f(items.get(k, {}).get("cagr")) for k in kids}
        implied = sum(s[k] / tot * c[k] for k in kids)
        gap = cagr[p] - implied
        if abs(gap) > tol:
            adjustments.append({"stage": "geo", "scope": p, "kind": "CAGR reconciliation",
                                "detail": f"share-weighted child CAGR {implied:.2%} vs {p} CAGR {cagr[p]:.2%}; "
                                          f"every child shifted by {gap * 100:+.2f} pp so children add up to the parent"})
        else:
            gap = 0.0
        for k in kids:
            raw_share[k] = s[k]
            share[k] = s[k] / tot
            value[k] = value[p] * share[k]
            analyst_cagr[k] = c[k]
            cagr[k] = c[k] + gap
    return {"value": value, "cagr": cagr, "share": share, "analyst_cagr": analyst_cagr,
            "raw_share": raw_share, "adjustments": adjustments}


# ------------------------------------------------------------------ segments
def _absolute(dim, sh: dict[str, float]) -> dict[str, float]:
    """share-of-parent convention -> share of dimension total, for every row."""
    out = {}
    for s in dim.top:
        out[s] = sh.get(s, 0.0)
        for c in dim.children.get(s, ()):
            out[c] = sh.get(s, 0.0) * sh.get(c, 0.0)
    return out


def _relative(dim, ab: dict[str, float]) -> dict[str, float]:
    out = {}
    tot = sum(ab.get(s, 0.0) for s in dim.top) or 1.0
    for s in dim.top:
        out[s] = ab.get(s, 0.0) / tot
        kids = dim.children.get(s, ())
        kt = sum(ab.get(c, 0.0) for c in kids) or 1.0
        for c in kids:
            out[c] = ab.get(c, 0.0) / kt
    return out


def segment_numbers(state: dict, geo: dict) -> dict[str, Any]:
    tax: Taxonomy = state["tax"]
    raw_all = state.get("segments_raw") or {}
    shares: dict[str, dict[str, dict[str, float]]] = {}     # dim -> leaf -> seg -> share-of-parent
    seg_cagr: dict[str, dict[str, dict[str, float]]] = {}   # dim -> leaf -> seg -> CAGR
    adjustments: list[dict] = []
    for dim in tax.dimensions:
        shares[dim.title], seg_cagr[dim.title] = {}, {}
        for group, leaves in tax.leaf_groups.items():
            raw = raw_all.get(f"{group}||{dim.title}")
            if not raw:
                continue
            seginfo = raw.get("segments", {})
            for leaf in leaves:
                c_raw = (raw.get("countries") or {}).get(leaf, {})
                sh_raw = {s: max(0.0, _f((c_raw.get("shares") or {}).get(s))) for s in dim.all_segments}
                sh = {}
                ttot = sum(sh_raw[s] for s in dim.top) or 1.0
                if abs(ttot - 1) > 0.005:
                    adjustments.append({"stage": "segments", "scope": f"{leaf} / {dim.title}", "kind": "share normalisation",
                                        "detail": f"top-level shares summed to {ttot:.4f}; divided by that sum"})
                for s in dim.top:
                    sh[s] = sh_raw[s] / ttot
                    kids = dim.children.get(s, ())
                    if kids:
                        kt = sum(sh_raw[k] for k in kids) or 1.0
                        if abs(kt - 1) > 0.005:
                            adjustments.append({"stage": "segments", "scope": f"{leaf} / {s}", "kind": "share normalisation",
                                                "detail": f"sub-shares of {s} summed to {kt:.4f}; divided by that sum"})
                        for k in kids:
                            sh[k] = sh_raw[k] / kt
                shares[dim.title][leaf] = sh
                # growth premiums: group view, optional country override; re-centred so the mix adds back
                overrides = c_raw.get("growth_premium_overrides") or {}
                prem = {s: _f(overrides.get(s, (seginfo.get(s) or {}).get("growth_premium"))) for s in dim.all_segments}
                wavg = sum(sh[s] * prem[s] for s in dim.top)
                for s in dim.top:
                    prem[s] -= wavg
                    kids = dim.children.get(s, ())
                    if kids:
                        wk = sum(sh[k] * prem[k] for k in kids)
                        for k in kids:
                            prem[k] += prem[s] - wk
                if abs(wavg) > 0.003:
                    adjustments.append({"stage": "segments", "scope": f"{leaf} / {dim.title}", "kind": "growth-premium re-centring",
                                        "detail": f"share-weighted segment premium was {wavg * 100:+.2f} pp; removed so segments grow at the country rate on average"})
                lc = geo["cagr"][leaf]
                seg_cagr[dim.title][leaf] = {s: lc + prem[s] for s in dim.all_segments}
    # value-weighted roll-ups to every parent node (reporting + review)
    rollup: dict[str, dict[str, dict[str, float]]] = {}
    for dim in tax.dimensions:
        rollup[dim.title] = {}
        for node in tax.parent_nodes:
            ab: dict[str, float] = {}
            for leaf in tax.leaves:
                if node in tax.path(leaf) and leaf in shares[dim.title]:
                    for s, v in _absolute(dim, shares[dim.title][leaf]).items():
                        ab[s] = ab.get(s, 0.0) + v * geo["value"][leaf]
            if ab:
                rollup[dim.title][node] = _relative(dim, ab)
    return {"shares": shares, "seg_cagr": seg_cagr, "rollup": rollup, "adjustments": adjustments}


# ------------------------------------------------------------------ pricing
def price_numbers(state: dict, geo: dict, seg: dict) -> dict[str, Any]:
    tax: Taxonomy = state["tax"]
    pr = state.get("pricing_raw") or {}
    dim = tax.priced_dimension
    base = {a: r["spend_per_unit_usd"] for a, r in state["sizing"]["bottom_up"]["by_application"].items()}
    idx_raw = {c: _f((pr.get("country_index") or {}).get(c, {}).get("index"), 1.0) or 1.0 for c in tax.leaves}
    vals = {c: geo["value"][c] for c in tax.leaves}
    k = sum(vals[c] / idx_raw[c] for c in tax.leaves) / (sum(vals.values()) or 1.0)
    idx = {c: idx_raw[c] * k for c in tax.leaves}
    asp: dict[str, dict[str, float]] = {}
    units: dict[str, dict[str, float]] = {}
    for c in tax.leaves:
        sh = seg["shares"][dim.title].get(c, {})
        ab = _absolute(dim, sh)
        asp[c], units[c] = {}, {}
        for s in dim.all_segments:
            if s in base:
                asp[c][s] = base[s] * idx[c]
        for s, kids in dim.children.items():  # parent price = value / units of its children
            denom = sum(sh.get(kk, 0.0) / asp[c][kk] for kk in kids if asp[c].get(kk))
            asp[c][s] = 1.0 / denom if denom else 0.0
        for s in dim.all_segments:
            v = geo["value"][c] * ab.get(s, 0.0)
            units[c][s] = v * 1e6 / asp[c][s] if asp[c].get(s) else 0.0
    total_units = sum(units[c][s] for c in tax.leaves for s in dim.top)
    return {"base_usd": base, "index_raw": idx_raw, "index": idx, "index_scale": k, "asp": asp, "units": units,
            "total_units": total_units,
            "adjustments": [{"stage": "pricing", "scope": "all countries", "kind": "price-index normalisation",
                             "detail": f"country indices multiplied by {k:.4f} so implied serviced units add up to "
                                       f"global value / global average spend per unit"}] if abs(k - 1) > 0.01 else []}


# ------------------------------------------------------------------ checks
def run_checks(state: dict, geo: dict, seg: dict, price: dict) -> list[dict]:
    tax: Taxonomy = state["tax"]
    flags: list[dict] = []

    def flag(sev, check, scope, msg):
        flags.append({"severity": sev, "check": check, "scope": scope, "message": msg})

    for n, v in geo["value"].items():
        if not math.isfinite(v) or v < 0:
            flag("hard", "value", n, f"value is {v}")
    for p in tax.parent_nodes:
        kids = tax.children_of[p]
        if p in geo["value"] and all(k in geo["value"] for k in kids):
            s = sum(geo["value"][k] for k in kids)
            if abs(s - geo["value"][p]) > 1e-6 * max(1, geo["value"][p]):
                flag("hard", "sum conservation", p, f"children {s:.4f} vs parent {geo['value'][p]:.4f}")
        else:
            flag("hard", "coverage", p, "no analyst split for this node")
    for dim in tax.dimensions:
        for leaf in tax.leaves:
            sh = seg["shares"][dim.title].get(leaf)
            if not sh:
                flag("hard", "segment coverage", f"{leaf} / {dim.title}", "no segment split")
                continue
            if abs(sum(sh[s] for s in dim.top) - 1) > 1e-3:
                flag("hard", "mix sums", f"{leaf} / {dim.title}", "top-level shares do not sum to 1")
            top = max(dim.top, key=lambda s: sh[s])
            if sh[top] > 0.6:
                flag("soft", "segment dominance", f"{leaf} / {dim.title}", f"{top} is {sh[top]:.0%} of the dimension")
    for leaf in tax.leaves:
        c = geo["cagr"].get(leaf)
        if c is not None and not (0.0 <= c <= 0.15):
            flag("soft", "CAGR range", leaf, f"CAGR {c:.2%} outside 0-15%")
    for a in geo["adjustments"]:
        if a["kind"] == "CAGR reconciliation":
            try:
                pp = float(a["detail"].split("shifted by ")[1].split(" pp")[0])
            except Exception:
                pp = 0
            if abs(pp) > 1.0:
                flag("soft", "CAGR coherence", a["scope"], f"analyst child CAGRs were {pp:+.2f} pp off the parent - large reconciliation")
    oem = state["oem"]
    for r in tax.regions:
        o = oem.get(r)
        if o and oem.get("Global"):
            os_ = o.v(2025) / oem["Global"].v(2025)
            if geo["share"].get(r, 0) > 0 and os_ > 0 and not (1 / 3 <= geo["share"][r] / os_ <= 3):
                flag("info", "vs OEM regional share", r,
                     f"aftermarket share {geo['share'][r]:.1%} vs OEM new-unit share {os_:.1%} (ratio {geo['share'][r] / os_:.1f}x)")
    for c, i in price["index"].items():
        if not (0.4 <= i <= 2.0):
            flag("soft", "price index", c, f"normalised price index {i:.2f} is extreme")
    return flags


def compute_all(state: dict) -> dict[str, Any]:
    geo = geo_numbers(state)
    seg = segment_numbers(state, geo)
    price = price_numbers(state, geo, seg)
    flags = run_checks(state, geo, seg, price)
    return {"geo": geo, "seg": seg, "price": price, "flags": flags,
            "adjustments": geo["adjustments"] + seg["adjustments"] + price["adjustments"]}
