"""Combine finished component markets (e.g. Hardware / Software / Services of one market) into ONE market sheet.

    python combine_family.py SCOPE_SHEET --out outputs/STM_combined \
        --member "Hardware=outputs/STM_rescaled/<hardware-dir>=By Hardware Type" \
        --member "Software=...=By Software Type" --member "Services=...=By Service Type" \
        --component-dim "By Component" --fill "By Primary Analytics Hosting=0.62,0.38" ... [--dry]

Pure arithmetic on the members' finished numbers (no new research):
  value of every geography = sum of the members' values; 2032 value = sum of the members' 2032 values;
  CAGR = (2032 / 2025)^(1/7) - 1 at every level
  "By Component" = one row per member (its country value), sub-rows = the member's own type dimension
  shared dimensions: segment value = sum over members of (member country value x member segment share), growth from the
  summed 2032 segment values. A member that has no split for a dimension uses --fill (whole-market split from the
  client's source document) and the growth premium of the first member that has the dimension.
The members' sheets are not modified. Every member analyst text is carried over, labelled with its member, with
evidence ids prefixed (H-W12 = Hardware sheet's W12). Plain-language notes: written by the editor (--dry: none).
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import sys
from dataclasses import replace
from pathlib import Path

from analyst_fleet import compute, syslog
from analyst_fleet.corpus.opinion import classify_dims
from analyst_fleet.config import Config
from analyst_fleet.llm import LLM
from analyst_fleet.market_spec import load_sheet, strategies
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.writer import dump_state, summary_md, write_workbook

ROOT = Path(__file__).resolve().parent
YRS = 7
CONF = {"low": 0, "medium": 1, "high": 2}


def cagr_of(v25: float, v32: float) -> float:
    return (v32 / v25) ** (1 / YRS) - 1 if v25 > 0 and v32 > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scope_sheet", type=Path)
    ap.add_argument("--member", action="append", required=True, help="LABEL=RUN_DIR=TYPE_DIMENSION")
    ap.add_argument("--component-dim", default="By Component")
    ap.add_argument("--fill", action="append", default=[], help="DIMENSION=s1,s2,... whole-market split for members without it")
    ap.add_argument("--fill-source", default="client source document")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reuse-notes", action="store_true", help="keep the notes of the existing sheet in --out (same values)")
    ap.add_argument("--note-fixes", type=Path, help='JSON {"Data!M51": [["old text", "new text"], ...]} applied to reused notes')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    syslog.setup(ROOT / "system.log", "combine")

    spec = load_sheet(args.scope_sheet)[0]
    out_dir = args.out / spec["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    tax_raw = spec["taxonomy"]
    tax_path = out_dir / "taxonomy.json"
    tax_path.write_text(json.dumps(tax_raw, indent=1, ensure_ascii=False), encoding="utf-8")
    cfg = replace(Config(), market_name=spec["title"], taxonomy_path=tax_path, generic=True, plain_language=not args.dry)
    llm = LLM(cfg)
    llm.run_label = f"combine: {spec['title']}"
    roles = classify_dims(llm, cfg.market_name, {d["title"]: [s["name"] for s in d["segments"]] for d in tax_raw["dimensions"]})
    tax_raw["strategies"] = strategies(tax_raw, roles)
    tax_path.write_text(json.dumps(tax_raw, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "spec.json").write_text(json.dumps({k: v for k, v in spec.items() if k != "taxonomy"}, indent=1,
                                                  ensure_ascii=False), encoding="utf-8")
    tax = Taxonomy(tax_path)
    root, leaves = tax.root, tax.leaves
    cdim = next(d for d in tax.dimensions if d.title == args.component_dim)
    fills = {}
    for f in args.fill:
        dname, vals = f.split("=", 1)
        d = next(x for x in tax.dimensions if x.title == dname)
        fills[dname] = dict(zip(d.top, map(float, vals.split(","))))
        assert len(fills[dname]) == len(d.top) and abs(sum(fills[dname].values()) - 1) < 1e-6, f"bad --fill for {dname}"

    # ---------------------------------------------------------------- members
    M = []
    for m in args.member:
        label, d, tdim = m.split("=")
        d = Path(d)
        st = json.loads((d / "state.json").read_text(encoding="utf-8"))
        mt = json.loads((d / "taxonomy.json").read_text(encoding="utf-8"))
        assert mt["geography"] == tax_raw["geography"], f"{label}: geography differs"
        assert label in cdim.top, f"{label} is not a {cdim.title} segment"
        types = [s["name"] for s in next(x for x in mt["dimensions"] if x["title"] == tdim)["segments"]]
        assert types == list(cdim.children[label]), f"{label}: {tdim} rows differ from the {cdim.title} sub-rows"
        M.append({"label": label, "dir": d, "tdim": tdim, "st": st, "n": st["numbers"], "p": label[0].upper(),
                  "dims": {x["title"] for x in mt["dimensions"]}})
    labels = [m["label"] for m in M]

    def pre(m, text):                     # evidence ids of a member -> member-prefixed ids
        return re.sub(r"\b([WF])(\d+)\b", lambda mt_: f"{m['p']}-{mt_.group(1)}{mt_.group(2)}", text) if isinstance(text, str) else text

    def pre_all(m, x):
        if isinstance(x, str):
            return pre(m, x)
        if isinstance(x, list):
            return [pre_all(m, v) for v in x]
        if isinstance(x, dict):
            return {k: pre_all(m, v) for k, v in x.items()}
        return x

    # ---------------------------------------------------------------- geography: sums of 2025 and 2032 values
    V, W = {}, {}
    nodes = [root] + tax.regions + leaves
    for g in leaves:                                # 2032 built from the countries up, so every level adds up in 2032 too
        V[g] = sum(m["n"]["geo"]["value"][g] for m in M)
        W[g] = sum(m["n"]["geo"]["value"][g] * (1 + m["n"]["geo"]["cagr"][g]) ** YRS for m in M)
    for g in tax.regions + [root]:
        kids = tax.leaves_under(g) if hasattr(tax, "leaves_under") else [c for c in leaves if g in tax.path(c)]
        V[g] = sum(V[c] for c in kids)
        W[g] = sum(W[c] for c in kids)
        assert abs(V[g] - sum(m["n"]["geo"]["value"][g] for m in M)) < 1e-6 * V[g], f"2025 value of {g}"
    C = {g: cagr_of(V[g], W[g]) for g in nodes}
    for p in [root] + tax.regions:                 # members already add up; so do the sums
        assert abs(sum(V[k] for k in tax.children_of[p]) - V[p]) < 1e-6 * V[p], f"2025 children of {p} do not add up"

    def comp_line(g):
        return "; ".join(f"{m['label']} US$ {m['n']['geo']['value'][g]:,.2f} Mn ({m['n']['geo']['value'][g] / V[g]:.1%}, "
                         f"CAGR {m['n']['geo']['cagr'][g]:.2%})" for m in M)

    def lowest_conf(items):
        cs = [str(i.get("confidence", "")).lower() for i in items if i and i.get("confidence")]
        cs = [c for c in cs if c in CONF]
        return min(cs, key=CONF.get).capitalize() if cs else ""

    def merged_list(items, key, cap=6):
        out = []
        for m, it in items:
            for x in (it or {}).get(key) or []:
                x = f"[{m['label']}] {pre(m, x)}"
                if x not in out:
                    out.append(x)
        return out[: cap * len(M)]

    def geo_item(parent, k, raw_of):
        its = [(m, raw_of(m).get("items", {}).get(k)) for m in M]
        text = (f"{k} = {comp_line(k)} -> combined US$ {V[k]:,.2f} Mn in 2025 = {V[k] / V[parent]:.2%} of {parent}; "
                f"2032 value US$ {W[k]:,.2f} Mn (sum of the components' 2032 values, built up from the countries) -> CAGR {C[k]:.2%}. ")
        text += " ".join(f"[{m['label']} analyst] {pre(m, (it or {}).get('reasoning', ''))}" for m, it in its)
        ev = []
        for m, it in its:
            ev += [pre(m, e) for e in (it or {}).get("evidence") or []]
        return {"share": V[k] / V[parent], "cagr": C[k], "reasoning": text, "drivers": merged_list(its, "drivers"),
                "restraints": merged_list(its, "restraints"), "geopolitics": merged_list(its, "geopolitics"),
                "evidence": ev, "confidence": lowest_conf([it for _, it in its])}

    regional_raw = {"items": {r: geo_item(root, r, lambda m: m["st"]["regional_raw"]) for r in tax.regions},
                    "overview": " ".join(f"[{m['label']}] {pre(m, m['st']['regional_raw'].get('overview', ''))}" for m in M)}
    country_raw = {r: {"items": {c: geo_item(r, c, lambda m, r=r: m["st"]["country_raw"][r]) for c in tax.children_of[r]},
                       "overview": " ".join(f"[{m['label']}] {pre(m, m['st']['country_raw'][r].get('overview', ''))}" for m in M)}
                   for r in tax.regions}

    # ---------------------------------------------------------------- segments
    def member_abs(m, dtitle, leaf):
        """member's absolute share (of its country value) and CAGR for every row of a combined dimension."""
        d = next(x for x in tax.dimensions if x.title == dtitle)
        cc = m["n"]["geo"]["cagr"][leaf]
        if dtitle == cdim.title:
            ab = {s: 0.0 for s in d.all_segments}
            g = {s: cc for s in d.all_segments}
            ab[m["label"]] = 1.0
            sh = m["n"]["seg"]["shares"][m["tdim"]][leaf]
            for t in d.children[m["label"]]:
                ab[t] = sh[t]
                g[t] = m["n"]["seg"]["seg_cagr"][m["tdim"]][leaf][t]
            return ab, g, "own"
        if dtitle in m["dims"]:
            sh = m["n"]["seg"]["shares"][dtitle][leaf]
            ab = compute._absolute(d, sh)
            return ab, dict(m["n"]["seg"]["seg_cagr"][dtitle][leaf]), "own"
        ref = next(x for x in M if dtitle in x["dims"])            # growth premium of the first member that has it
        rc = ref["n"]["geo"]["cagr"][leaf]
        ab = {s: fills[dtitle][s] for s in d.top}
        g = {s: cc + (ref["n"]["seg"]["seg_cagr"][dtitle][leaf][s] - rc) for s in d.top}
        return ab, g, f"filled ({args.fill_source} split; growth premium from the {ref['label']} sheet)"

    segments_raw, seg_values = {}, {}
    for d in tax.dimensions:
        for group, members_ in tax.leaf_groups.items():
            countries, grp_a, grp_b = {}, {}, {}
            notes_by_seg = {s: [] for s in d.all_segments}
            for c in members_:
                A = {s: 0.0 for s in d.all_segments}
                B = {s: 0.0 for s in d.all_segments}
                how = {}
                for m in M:
                    ab, g, kind = member_abs(m, d.title, c)
                    v = m["n"]["geo"]["value"][c]
                    how[m["label"]] = kind
                    for s in d.all_segments:
                        A[s] += v * ab.get(s, 0.0)
                        B[s] += v * ab.get(s, 0.0) * (1 + g.get(s, 0.0)) ** YRS
                seg_values[(d.title, c)] = (A, B)
                sh, prem, why = {}, {}, {}
                ttot = sum(A[s] for s in d.top)
                for s in d.top:
                    sh[s] = A[s] / ttot
                    kids = d.children.get(s, ())
                    kt = sum(A[k] for k in kids)
                    for k in kids:
                        sh[k] = A[k] / kt if kt else 0.0
                for s in d.all_segments:
                    prem[s] = (cagr_of(A[s], B[s]) - C[c]) if A[s] > 0 else 0.0
                    parts = "; ".join(f"{m['label']} US$ {m['n']['geo']['value'][c] * member_abs(m, d.title, c)[0].get(s, 0.0):,.2f} Mn"
                                      + ("" if how[m["label"]] == "own" else " [filled]") for m in M)
                    why[s] = (f"{s} in {c} = {parts} -> US$ {A[s]:,.2f} Mn in 2025, US$ {B[s]:,.2f} Mn in 2032 "
                              f"(CAGR {cagr_of(A[s], B[s]):.2%}).")
                    grp_a[s] = grp_a.get(s, 0.0) + A[s]
                    grp_b[s] = grp_b.get(s, 0.0) + B[s]
                filled = [f"{lb}: {k}" for lb, k in how.items() if k != "own"]
                if filled:
                    for s in d.all_segments:
                        why[s] += " Filled: " + "; ".join(filled) + "."
                countries[c] = {"shares": sh, "growth_premium_overrides": prem, "why": why}
            gv = sum(V[c] for c in members_)
            gc = cagr_of(gv, sum(W[c] for c in members_))
            segs = {}
            for s in d.all_segments:
                its = []
                for m in M:
                    if d.title == cdim.title:
                        if s in d.children.get(m["label"], ()):
                            its.append((m, m["st"]["segments_raw"].get(f"{group}||{m['tdim']}", {}).get("segments", {}).get(s)))
                        elif s == m["label"]:
                            its.append((m, {"reasoning": m["st"]["segments_raw"].get(f"{group}||{m['tdim']}", {}).get("overview", "")}))
                    elif d.title in m["dims"]:
                        its.append((m, m["st"]["segments_raw"].get(f"{group}||{d.title}", {}).get("segments", {}).get(s)))
                its = [(m, it) for m, it in its if it]
                segs[s] = {"group_share_view": None,
                           "reasoning": (f"{group}: {s} = US$ {grp_a[s]:,.2f} Mn in 2025 summed over the component markets. "
                                         + " ".join(f"[{m['label']} analyst] {pre(m, it.get('reasoning', ''))}" for m, it in its)),
                           "growth_premium": (cagr_of(grp_a[s], grp_b[s]) - gc) if grp_a[s] > 0 else 0.0,
                           "growth_reasoning": " ".join(f"[{m['label']}] {pre(m, it.get('growth_reasoning', ''))}" for m, it in its
                                                        if it.get("growth_reasoning")),
                           "drivers": merged_list(its, "drivers", 3), "restraints": merged_list(its, "restraints", 3),
                           "geopolitics": merged_list(its, "geopolitics", 3),
                           "evidence": [pre(m, e) for m, it in its for e in it.get("evidence") or []],
                           "confidence": lowest_conf([it for _, it in its])}
            ov = [f"[{m['label']}] {pre(m, m['st']['segments_raw'].get(f'{group}||{d.title}', {}).get('overview', ''))}"
                  for m in M if d.title in m["dims"] and m["st"]["segments_raw"].get(f"{group}||{d.title}", {}).get("overview")]
            if d.title in fills:
                ov.append(f"Members without their own {d.title} split use the {args.fill_source} split "
                          + ", ".join(f"{s} {v:.0%}" for s, v in fills[d.title].items()) + ".")
            segments_raw[f"{group}||{d.title}"] = {"segments": segs, "overview": " ".join(ov), "countries": countries}

    # ---------------------------------------------------------------- pricing (sub-type base prices, value-weighted index)
    base, appn, by_app = {}, {}, {}
    for m in M:
        for t in cdim.children[m["label"]]:
            base[t] = m["n"]["price"]["base_usd"][t]
            appn[t] = f"[{m['label']}] " + pre(m, (m["st"]["pricing_raw"].get("application_notes") or {}).get(t, ""))
    def raw_index(m, c):                   # analyst's raw country index (what compute normalises)
        try:
            v = float((m["st"]["pricing_raw"]["country_index"].get(c) or {}).get("index"))
            return v if v > 0 else 1.0
        except (TypeError, ValueError):
            return 1.0
    idx = {}
    for c in leaves:
        idx[c] = {"index": sum(m["n"]["geo"]["value"][c] * raw_index(m, c) for m in M) / V[c],
                  "reasoning": "Value-weighted average of the component analysts' indices: "
                               + "; ".join(f"{m['label']} {raw_index(m, c):.2f}" for m in M) + ". "
                               + " ".join(f"[{m['label']}] {pre(m, (m['st']['pricing_raw']['country_index'].get(c) or {}).get('reasoning', ''))}"
                                          for m in M)}
    units_def = "Mixed by component - " + "; ".join(f"{m['label']}: {pre(m, m['st']['pricing_raw'].get('unit_definition', ''))}" for m in M)
    pricing_raw = {"unit_definition": units_def, "country_index": idx,
                   "price_trend": {"annual_change": None, "reasoning": " ".join(
                       f"[{m['label']}] {pre(m, (m['st']['pricing_raw'].get('price_trend') or {}).get('reasoning', ''))}" for m in M)},
                   "application_notes": appn,
                   "overview": "Prices are kept per sub-type (each component sheet's own base price); a single average price "
                               "for the whole market would mix units, licences and service contracts. "
                               + " ".join(f"[{m['label']}] {pre(m, m['st']['pricing_raw'].get('overview', ''))}" for m in M)}
    gA = {}
    for t in base:
        gA[t] = sum(seg_values[(cdim.title, c)][0][t] for c in leaves)
        by_app[t] = {"installed_units": gA[t] * 1e6 / base[t], "spend_per_unit_usd": base[t], "value_usd_mn": gA[t],
                     "oem_asp_usd": base[t], "spend_pct": 1.0, "share_of_bottom_up": gA[t] / V[root],
                     "volume_build": [{"label": f"{t} 2025 value (sum of the 26 countries, US$ Mn) x 1e6 / global price",
                                       "value": round(gA[t] * 1e6 / base[t]), "evidence": "component sheet"}]}
    bu_total = sum(gA.values())
    assert abs(bu_total - V[root]) < 1e-6 * V[root], "sub-type values do not add to the total"

    # ---------------------------------------------------------------- sizing
    s1m = [m["st"]["sizing"]["step1"] for m in M]
    hist = None
    if all(s.get("historical_cagr_2020_2025") is not None for s in s1m):
        v20 = sum(m["n"]["geo"]["value"][root] / (1 + float(s["historical_cagr_2020_2025"])) ** 5 for m, s in zip(M, s1m))
        hist = (V[root] / v20) ** (1 / 5) - 1
    combo = (f"Combined market = sum of the {len(M)} component markets: {comp_line(root)} = US$ {V[root]:,.1f} Mn in 2025; "
             f"2032 = US$ {W[root]:,.1f} Mn -> CAGR {C[root]:.2%}.")
    step1 = {"volume_unit": "mixed by component (see ASP sheet)",
             "segments": {t: {k: pre(m, v) for k, v in (m["st"]["sizing"]["step1"].get("segments") or {}).get(t, {}).items()
                              if isinstance(v, str)} for m in M for t in cdim.children[m["label"]]},
             "global_cagr": C[root], "historical_cagr_2020_2025": hist,
             "cagr_reasoning": combo + " " + " ".join(f"[{m['label']} sizing analyst] {pre(m, s.get('cagr_reasoning', ''))}"
                                                      for m, s in zip(M, s1m)),
             "drivers": merged_list([(m, s) for m, s in zip(M, s1m)], "drivers", 4),
             "restraints": merged_list([(m, s) for m, s in zip(M, s1m)], "restraints", 4),
             "geopolitics": merged_list([(m, s) for m, s in zip(M, s1m)], "geopolitics", 4),
             "cited_figures": [], "cited_forecasts": [],
             "top_down": {"parent_market": " + ".join(labels) + " component markets (sum)",
                          "parent_value_2025_usd_mn": round(V[root], 1), "share_of_parent": 1.0}}
    basis = " ".join(f"[{m['label']}] {pre(m, (m['st']['sizing'].get('rescale') or {}).get('basis', ''))}" for m in M[:1])
    step2 = {"weights": {"bottom_up": 0.5, "top_down": 0.5, "cited": 0.0}, "global_cagr": C[root],
             "reasoning": combo + " Both routes are the same sum: A = the 11 component sub-types, B = the 3 component totals. "
                          "Component totals come from the component sheets, which were tied to one whole-market total: "
                          + basis + " " + " ".join(f"[{m['label']} sizing analyst] {pre(m, (m['st']['sizing'].get('step2') or {}).get('reasoning', ''))}"
                                                   for m in M),
             "confidence": lowest_conf([m["st"]["sizing"].get("step2") for m in M])}
    sizing = {"mode": "generic", "volume_unit": step1["volume_unit"], "step1": step1, "step2": step2,
              "step3": {"global_cagr": C[root], "implied_2032_usd_mn": W[root], "reasoning": combo, "departure_reason": ""},
              "bottom_up": {"by_application": by_app, "total_usd_mn": bu_total,
                            "installed_units": sum(r["installed_units"] for r in by_app.values())},
              "top_down_usd_mn": V[root],
              "top_down_parent": {"parent_market": " + ".join(labels) + " component markets (sum)",
                                  "parent_value_2025_usd_mn": round(V[root], 1), "share_of_parent": 1.0,
                                  "reasoning": comp_line(root)},
              "oem_2025_usd_mn": 0.0, "cited": [], "cited_median_usd_mn": None,
              "weights": step2["weights"], "global_value_2025": V[root], "global_cagr": C[root], "endpoints": [],
              "growth_reasoning": combo, "combined_from": {m["label"]: str(m["dir"]) for m in M}}

    # ---------------------------------------------------------------- evidence, reviews, scope
    facts, evidence = [], []
    for m in M:
        facts += [pre_all(m, f) for f in m["st"].get("facts") or []]
        evidence += [pre_all(m, e) for e in m["st"].get("evidence") or []]
    reviews = []
    for m in M:
        for rv in m["st"].get("reviews") or []:
            r2 = copy.deepcopy(pre_all(m, rv))
            r2["pass"] = f"{m['label']} sheet"
            for i in r2.get("issues") or []:
                i["problem"] = f"[{m['label']} sheet] {i.get('problem', '')}"
            reviews.append(r2)
    incl = []
    for m in M:
        for i in (m["st"].get("scope") or {}).get("inclusions") or []:
            it = dict(i) if isinstance(i, dict) else {"item": str(i), "source": "client"}
            it["item"] = f"[{m['label']}] {it.get('item', '')}"
            incl.append(it)
    excl = [{"item": x.strip("•◦▪ -").strip(), "source": "client"} for x in spec["exclusions"].splitlines() if x.strip()]
    scope = {"scope_statement": spec["definition"],
             "value_basis": "vendor revenue for hardware and software, service-provider revenue for services (US$ Mn); "
                            + " | ".join(f"[{m['label']}] {(m['st'].get('scope') or {}).get('value_basis', '')}" for m in M),
             "volume_unit": "mixed by component (see ASP sheet)", "inclusions": incl, "exclusions": excl}

    state = {"cfg": cfg, "tax": tax, "llm": llm, "oem": {}, "segment_definitions": spec["segment_notes"],
             "scope_rules": spec["scope_rules"], "scope": scope, "sizing": sizing, "regional_raw": regional_raw,
             "country_raw": country_raw, "segments_raw": segments_raw, "pricing_raw": pricing_raw, "facts": facts,
             "evidence": evidence, "reviews": reviews, "best_pass": "n/a (combined from component sheets)",
             "validation": None, "validation_mode": None, "plain_texts": {},
             "log": ["combined from " + ", ".join(f"{m['label']} ({m['dir']})" for m in M)]
                    + [f"{d} for members without it: {args.fill_source} split {fills[d]}" for d in fills]}
    state["numbers"] = n = compute.compute_all(state)

    # prices: every sub-type keeps its component sheet's own price in every country (one shared country index would move
    # them by up to ~20%); component rows = value / units of their sub-types, as in the standard arithmetic
    pz = n["price"]
    seg_idx = {}
    for m in M:
        for t in cdim.children[m["label"]]:
            seg_idx[t] = {c: m["n"]["price"]["asp"][c][t] / base[t] for c in leaves}
    for c in leaves:
        sh = n["seg"]["shares"][cdim.title][c]
        ab = compute._absolute(cdim, sh)
        for m in M:
            for t in cdim.children[m["label"]]:
                pz["asp"][c][t] = m["n"]["price"]["asp"][c][t]
            kids = cdim.children[m["label"]]
            denom = sum(sh[k] / pz["asp"][c][k] for k in kids if pz["asp"][c].get(k))
            pz["asp"][c][m["label"]] = 1.0 / denom if denom else 0.0
        for s in cdim.all_segments:
            pz["units"][c][s] = V[c] * ab.get(s, 0.0) * 1e6 / pz["asp"][c][s] if pz["asp"][c].get(s) else 0.0
    pz["total_units"] = sum(pz["units"][c][s] for c in leaves for s in cdim.top)
    pz["segment_index"] = seg_idx
    n["adjustments"] = [a for a in n["adjustments"] if a["stage"] != "pricing"]
    pz["adjustments"] = []

    # ---------------------------------------------------------------- checks before writing
    geo = n["geo"]
    for g in nodes:
        assert abs(geo["value"][g] - V[g]) < 1e-6 * max(1, V[g]), f"value of {g}"
    hard = [f for f in n["flags"] if f["severity"] == "hard"]
    assert not hard, f"hard flags: {hard[:3]}"
    for m in M:                                             # component rows reproduce each member exactly (2025)
        for c in leaves:
            ab = compute._absolute(cdim, n["seg"]["shares"][cdim.title][c])
            assert abs(ab[m["label"]] * V[c] - m["n"]["geo"]["value"][c]) < 1e-6 * max(1, V[c]), f"{m['label']} in {c}"
    xlsx = out_dir / f"Input Sheet - {spec['title']}.xlsx"
    if args.reuse_notes and xlsx.exists():
        from analyst_fleet.cell_reasoning import explain_all
        from growth_correction import plain_from_workbook
        state["plain_texts"] = plain_from_workbook(xlsx, explain_all(state, include_asp=True))
        for k_, reps in (json.loads(args.note_fixes.read_text(encoding="utf-8")) if args.note_fixes else {}).items():
            txt = state["plain_texts"][k_]["text"]
            for a, b in reps:
                assert a in txt, f"{k_}: '{a}' not in the note"
                txt = txt.replace(a, b)
            state["plain_texts"][k_]["text"] = txt
        print(f"  notes reused: {len(state['plain_texts'])}")
    write_workbook(state, xlsx)
    dump_state(state, out_dir / "state.json")
    (out_dir / "summary.md").write_text(summary_md(state), encoding="utf-8")
    print(f"{spec['title']}: US$ {V[root]:,.1f} Mn 2025, CAGR {geo['cagr'][root]:.2%}; "
          + "; ".join(f"{r} {geo['share'][r]:.1%}/{geo['cagr'][r]:.2%}" for r in tax.regions)
          + f"\n  adjustments by the standard arithmetic: {len(n['adjustments'])}; flags: {len(n['flags'])}"
          + f"\n  cost US$ {llm.usage['cost_usd']:.3f}\n  {xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
