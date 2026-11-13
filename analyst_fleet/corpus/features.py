"""Scale-free features of one market (human ME or the agent's sheet).

Absolute sizes do not transfer between markets (and some MEs are in thousands or units),
so every feature is a ratio, a share or a growth spread:

  growth.fcst_cagr          root CAGR 2025 -> 2032 (or the last year when 2032 is absent)
  growth.hist_cagr          root CAGR 2021 -> 2025
  growth.fcst_minus_hist    forecast minus history (pp)
  growth.price_cagr         implied price CAGR (value / volume) where volume exists
  region.share.<R>          2025 share of the root, R in NA / Europe / APAC / LATAM / MEA (global markets)
  region.cagr_gap.<R>       region CAGR minus root CAGR
  region.cagr_range         fastest minus slowest region CAGR
  region.top                largest region share
  country.share.<C>         country share of its region (U.S. of NA, China of APAC, ...)
  country.rest_share        mean share of the "Rest of ..." residual inside regions
  country.cagr_gap_sd       spread of country CAGRs around their region's CAGR
  seg.n / top_share / hhi   per-dimension concentration (mean over dimensions, root level)
  seg.cagr_spread           fastest minus slowest top-level segment CAGR (mean over dimensions)
  seg.cagr_dev              share-weighted mean |segment CAGR - root CAGR|
  seg.small_fast            mean rank correlation of share vs CAGR (negative: small segments grow faster)
  seg.country_mix_sd        how much segment mix varies across leaf countries (pp std)
"""
from __future__ import annotations

import math
import re
from statistics import mean, pstdev
from typing import Any

MACRO = {"NA": ("north america",),
         "LATAM": ("latin america", "south america", "central america", "latam", "central & south america"),
         "Europe": ("europe", "western europe", "eastern europe"),
         "APAC": ("asia pacific", "apac", "asia", "asia-pacific", "asia & pacific"),
         "MEA": ("middle east", "africa", "middle east and africa", "middle east & africa", "mea", "mena", "gcc")}
COUNTRY_ALIAS = {"us": "U.S.", "u.s": "U.S.", "u.s.": "U.S.", "usa": "U.S.", "united states": "U.S.",
                 "uk": "U.K.", "u.k": "U.K.", "u.k.": "U.K.", "united kingdom": "U.K.",
                 "saudi arabia (ksa)": "Saudi Arabia", "ksa": "Saudi Arabia", "united arab emirates (uae)": "UAE",
                 "united arab emirates": "UAE", "korea": "South Korea", "republic of korea": "South Korea"}
KEY_COUNTRIES = ("U.S.", "Canada", "Germany", "U.K.", "France", "Italy", "Spain", "Russia", "China", "Japan", "India",
                 "South Korea", "Australia", "Brazil", "Mexico", "Argentina", "Saudi Arabia", "UAE", "South Africa", "GCC",
                 "Turkey", "Israel", "Egypt")


def macro_of(name: str) -> str | None:
    t = re.sub(r"\s+", " ", name.lower()).strip()
    for k, alts in MACRO.items():
        if t in alts:
            return k
    return None


def canon_country(name: str) -> str:
    t = re.sub(r"\s+", " ", name).strip()
    return COUNTRY_ALIAS.get(t.lower().rstrip(), t)


def cagr(a: float | None, b: float | None, n: int) -> float | None:
    return (b / a) ** (1 / n) - 1 if a and b and a > 0 and b > 0 and n > 0 else None


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rk = lambda v: {i: r for r, i in enumerate(sorted(range(len(v)), key=lambda i: v[i]))}
    rx, ry = rk(x), rk(y)
    mx, my = mean(rx.values()), mean(ry.values())
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(x)))
    den = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(len(x))) * sum((ry[i] - my) ** 2 for i in range(len(x))))
    return num / den if den else None


# --------------------------------------------------------------------------- uniform view
def view_from_me(parsed: dict) -> dict[str, Any]:
    """-> {"root", "v": {geo: (v21, v25, vEnd)}, "n_fc", "kids": {geo: [geo]}, "seg": {geo: {dim: [(seg, parent, v25, vEnd)]}},
           "price_cagr"}"""
    root = parsed["root"]
    geos = parsed["geos"]
    ys = geos[root]["years"]
    i25 = ys.index(2025) if 2025 in ys else None
    if i25 is None:
        return {}
    iend = ys.index(2032) if 2032 in ys else len(ys) - 1
    i21 = ys.index(2021) if 2021 in ys else None
    key = {k.lower().rstrip("."): k for k in geos}

    def at(ser, i):
        return ser[i] if i is not None and i < len(ser) else None

    v, kids, seg = {}, {}, {}
    for nm, s in geos.items():
        if s["years"] != ys:
            continue
        v[nm] = (at(s["total"], i21), s["total"][i25], at(s["total"], iend))
        seg[nm] = {d["title"]: [(x["name"], x["parent"], x["series"][i25], at(x["series"], iend)) for x in d["segments"]]
                   for d in s["dims"]}
        kk = []
        for c, ser in s["children"]:
            ck = key.get(c.lower().rstrip("."))
            name = ck or c
            kk.append(name)
            if not ck or ck not in v:
                v.setdefault(name, (at(ser, i21), ser[i25], at(ser, iend)))
        if kk:
            kids[nm] = kk
    r = geos[root]
    pc = None
    if r.get("volume_total") and all(r["volume_total"][i] for i in (i25, iend)):
        pc = cagr(r["total"][i25] / r["volume_total"][i25], r["total"][iend] / r["volume_total"][iend], iend - i25)
    return {"root": root, "v": v, "n_fc": iend - i25, "kids": kids, "seg": seg, "price_cagr": pc}


# --------------------------------------------------------------------------- features
def features(view: dict[str, Any]) -> dict[str, float]:
    if not view:
        return {}
    f: dict[str, float] = {}
    root, v, kids, n = view["root"], view["v"], view["kids"], view["n_fc"]
    v21, v25, vend = v[root]
    rc = cagr(v25, vend, n)
    if rc is not None:
        f["growth.fcst_cagr"] = rc
    hc = cagr(v21, v25, view.get("n_hist", 4))
    if hc is not None:
        f["growth.hist_cagr"] = hc
        if rc is not None:
            f["growth.fcst_minus_hist"] = rc - hc
    if view.get("price_cagr") is not None:
        f["growth.price_cagr"] = view["price_cagr"]

    # ---- regions (only when the root is split into >= 4 macro regions)
    regions: dict[str, list[str]] = {}
    for k in kids.get(root, []):
        m = macro_of(k)
        if m:
            regions.setdefault(m, []).append(k)
    if len(regions) >= 4 and v25:
        rs, rg = {}, {}
        for m, nodes in regions.items():
            a25 = sum(v[x][1] for x in nodes if x in v)
            aend = sum((v[x][2] or 0) for x in nodes if x in v)
            rs[m] = a25 / v25
            c = cagr(a25, aend, n)
            if c is not None and rc is not None:
                rg[m] = c - rc
        for m, s in rs.items():
            f[f"region.share.{m}"] = s
        for m, g in rg.items():
            f[f"region.cagr_gap.{m}"] = g
        f["region.top"] = max(rs.values())
        if len(rg) >= 3:
            f["region.cagr_range"] = max(rg.values()) - min(rg.values())
            f["region.fastest_is_apac"] = 1.0 if max(rg, key=rg.get) == "APAC" else 0.0
            f["region.slowest_is_europe"] = 1.0 if min(rg, key=rg.get) == "Europe" else 0.0

    # ---- countries inside regions (any region node with country children)
    rest, gaps = [], []
    for p, ks in kids.items():
        if p == root and len(regions) >= 2:
            pass
        pv = v.get(p)
        if not pv or not pv[1] or macro_of(p) is None:
            continue
        pc = cagr(pv[1], pv[2], n)
        for k in ks:
            kv = v.get(k)
            if not kv or kv[1] is None:
                continue
            sh = kv[1] / pv[1]
            cn = canon_country(k)
            if cn in KEY_COUNTRIES:
                f[f"country.share.{cn}"] = sh
            if k.lower().startswith("rest of"):
                rest.append(sh)
            kc = cagr(kv[1], kv[2], n)
            if kc is not None and pc is not None:
                gaps.append(kc - pc)
    if rest:
        f["country.rest_share"] = mean(rest)
    if len(gaps) >= 3:
        f["country.cagr_gap_sd"] = pstdev(gaps)
        f["country.cagr_gap_absmax"] = max(abs(g) for g in gaps)

    # ---- segments at the root
    tops, hhis, spreads, devs, corrs, ns, sub_devs = [], [], [], [], [], [], []
    for dim, rows in (view["seg"].get(root) or {}).items():
        top = [(s, a, b) for s, p, a, b in rows if p is None and a is not None and a >= 0]
        tot = sum(a for _, a, _ in top)
        if len(top) < 2 or tot <= 0:
            continue
        sh = [a / tot for _, a, _ in top]
        cg = [cagr(a, b, n) for _, a, b in top]
        ns.append(len(top))
        tops.append(max(sh))
        hhis.append(sum(x * x for x in sh))
        ok = [(s_, c_) for s_, c_ in zip(sh, cg) if c_ is not None]
        if len(ok) >= 2 and rc is not None:
            cs = [c_ for _, c_ in ok]
            spreads.append(max(cs) - min(cs))
            devs.append(sum(s_ * abs(c_ - rc) for s_, c_ in ok) / sum(s_ for s_, _ in ok))
            r = _spearman([s_ for s_, _ in ok], cs)
            if r is not None:
                corrs.append(r)
        par = {s: (a, b) for s, p, a, b in rows if p is None}
        for s, p, a, b in rows:
            if p and p in par:
                pc_, kc_ = cagr(*par[p], n), cagr(a, b, n)
                if pc_ is not None and kc_ is not None:
                    sub_devs.append(abs(kc_ - pc_))
    if ns:
        f["seg.n"] = mean(ns)
        f["seg.top_share"] = mean(tops)
        f["seg.hhi"] = mean(hhis)
    if spreads:
        f["seg.cagr_spread"] = mean(spreads)
        f["seg.cagr_dev"] = mean(devs)
    if corrs:
        f["seg.small_fast"] = mean(corrs)
    if sub_devs:
        f["seg.sub_cagr_dev"] = mean(sub_devs)

    # ---- how much the segment mix varies across leaf countries
    leaves = [g for g in view["seg"] if g not in kids and view["seg"][g]]
    sds = []
    if len(leaves) >= 3:
        dims = (view["seg"].get(root) or {}).keys()
        for dim in dims:
            per_seg: dict[str, list[float]] = {}
            for g in leaves:
                rows = view["seg"][g].get(dim) or []
                top = [(s, a) for s, p, a, _ in rows if p is None and a is not None]
                tot = sum(a for _, a in top)
                if tot > 0:
                    for s, a in top:
                        per_seg.setdefault(s, []).append(a / tot)
            sds += [pstdev(x) for x in per_seg.values() if len(x) >= 3]
    if sds:
        f["seg.country_mix_sd"] = mean(sds)
    return f


def segment_names(parsed: dict) -> list[str]:
    r = parsed["geos"][parsed["root"]]
    return [d["title"] for d in r["dims"]] + [x["name"] for d in r["dims"] for x in d["segments"]]
