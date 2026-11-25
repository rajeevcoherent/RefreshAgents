"""Growth-only correction of a finished Input Sheet: every 2025 number stays exactly as it was; only growth changes.

    python growth_correction.py outputs/HC/global-tirzepatide-market  [--out outputs/HC_growthfix]

Why: 2032 value = 2025 value x (1 + CAGR)^7, so growth can be corrected without touching any base-year number.
Locked (verified by code before anything is written): every 2025 value and share (global, regions, countries),
every segment share, every price. Re-set:
  1. global CAGR - published forecast VALUES of this market (each distinct value counted once) are converted by
     code into the CAGR each implies from OUR 2025 base; the sizing analyst sets the CAGR against that range and
     must justify anything above the median endpoint or outside the range
  2. regional and country CAGRs - analysts see the locked shares and the house growth-gap bands (72 past MEs) and
     set growth only; code reconciles children to their parent exactly
  3. segment growth follows automatically (segment CAGR = country CAGR + the unchanged premium)
  4. house-prior validation on GROWTH items only (never shares)
Plain-language notes are reused for unchanged cells (read from the original workbook) and rewritten only for cells
whose value changed. Output: a new folder; the original sheet is not modified.
"""
from __future__ import annotations

import argparse
import re
import copy
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from analyst_fleet import compute, syslog
from analyst_fleet.agents import generic as G
from analyst_fleet.agents.common import canon_keys, dump, fmt_facts, num, select_facts
from analyst_fleet.config import Config
from analyst_fleet.corpus.priors import Library
from analyst_fleet.llm import LLM
from analyst_fleet.prior_validator import adjudicate, apply, find_items, target_record
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.validator import finalize_notes
from analyst_fleet.writer import dump_state, summary_md, write_workbook

ROOT = Path(__file__).resolve().parent
GROWTH_KINDS = {"geo_cagr", "country_cagr_spread", "seg_premium_scale"}      # never shares
LABEL = "Growth recalibrated"

ENDPOINTS = G.PERSONA + """

TASK: The code extracted every published forecast VALUE (a future-year market size) from the verified evidence.
For EACH candidate give the scope factor that converts it to THIS market's client scope:
  ~1.0 = the same products under another name. If the product is only sold in one form, "<product> market" and
         "<product> <form> market" are the SAME scope - different values are different publishers' estimates, not
         different markets.
  0.5-0.95 = clearly broader or narrower, by an amount you state
  0    = ONLY for a genuinely different product: an API / ingredient market, a drug-class or parent market, a single
         company's guidance
Judge every candidate on its own; do not merge or skip any (the code removes duplicates of the same number).
Return json: {"candidates": {"<id>": {"scope_factor": number, "source_name": "publisher if visible",
                                      "reasoning": "what it covers vs our scope"} ...every id...}}"""

_FC = re.compile(r"(?:US\$|USD|\$)\s?([\d][\d,]*(?:\.\d+)?)\s*(million|mn|billion|bn)\b[^.]{0,40}?\bby\s+(20\d\d)", re.I)


def forecast_candidates(facts: list[dict], keywords: list[str]) -> list[dict]:
    """Every 'reaches US$ X million/billion by YEAR' statement about this market (not 'grows BY US$ X')."""
    out = []
    for f in facts:
        text = f"{f.get('claim', '')} || {f.get('quote', '')}"
        if keywords and not any(k in text.lower() for k in keywords):
            continue
        for m in _FC.finditer(f.get("claim", "")):
            if re.search(r"grow(?:s|th)?\s+by\s*$", f["claim"][:m.start()].lower()[-20:]):
                continue                                   # an increment, not a market value
            v = float(m.group(1).replace(",", "")) * (1000 if m.group(2).lower() in ("billion", "bn") else 1)
            out.append({"id": f"C{len(out) + 1}", "evidence": f["id"], "figure_usd_mn": v, "year": int(m.group(3)),
                        "claim": f["claim"][:220]})
    return out

GROWTH = G.PERSONA + """

TASK: You are the market-sizing analyst, setting the {root} CAGR 2025-2032 for a FINISHED sheet whose 2025 value
(US$ {base:,.1f} Mn) is final. The code converted every distinct published forecast into the CAGR it implies from
OUR base (table below). A growth rate quoted by a source is not comparable: it starts from that source's own base
year and size. Outside the range needs
specific, evidence-based reasons in departure_reason.
Default: the MIDPOINT of the implied range (every published forecast weighs the same); anywhere inside the range
is acceptable with reasoning; outside the range (by more than 1 pp) needs a departure_reason.
Return json:
{{"global_cagr": number, "reasoning": "4-6 sentences: growth view, relation to the endpoints, main drivers and restraints",
 "departure_reason": "required only if outside the implied range by more than 1 pp, else empty"}}"""

LAUNCH_RULE = """
LAUNCH-PHASE PRODUCT: this product is in its first years on the market, so growth across geographies follows
LAUNCH TIMING, not the mature-market pattern: where it launched recently, is not yet reimbursed, or is still being
rolled out, growth is far above the parent rate; where it is established (early launch, broad coverage, high price
already reached) growth is closer to or below the parent rate. Say for each geography where it is on that curve
(approval date, reimbursement status, supply, price) and size the gap accordingly. Wide differences between
geographies are expected here."""

GEO_GROWTH = G.PERSONA + """

TASK: You are the {who} analyst for {parent}. The 2025 shares below are FINAL - do not change or restate them.
Set ONLY each geography's 2025-2032 value CAGR. The share-weighted average of your CAGRs must equal {parent}'s
CAGR of {cagr:.2%} (code reconciles small gaps). Children differ from the parent only as far as specific evidence
shows; follow the growth guidance given at the end (house bands, or the launch-phase rule) and explain any
large gap with a named, market-specific reason.
Return json: {{"items": {{"<geography>": {{"cagr": number,
   "growth_reasoning": "2-4 sentences on WHY this growth rate (drivers, restraints, geopolitics)",
   "drivers": [..], "restraints": [..], "geopolitics": [..]}} ...every geography...}}}}"""


def _check_endpoints(ids: list[str]):
    def check(o: dict) -> str | None:
        c = canon_keys(o.get("candidates") or {}, ids)
        miss = [i for i in ids if i not in c]
        if miss:
            return f"judge every candidate (missing ids: {miss})"
        bad = [i for i in ids if num((c[i] or {}).get("scope_factor")) is None]
        return f"scope_factor must be a number for {bad}" if bad else None
    return check


def distinct_endpoints(fs: list[dict], base: float, base_year: int = 2025) -> list[dict]:
    seen, out = set(), []
    for f in fs:
        fig, yr, sf = num(f.get("figure_usd_mn")), int(num(f.get("year")) or 0), num(f.get("scope_factor"))
        if not fig or sf is None or sf <= 0 or not (base_year + 2 <= yr <= base_year + 12):
            continue
        key = (round(fig, -1), yr)                     # republished copies of the same number count once
        if key in seen:
            continue
        seen.add(key)
        v = fig * sf
        out.append({**f, "value_in_scope_usd_mn": v, "implied_cagr": (v / base) ** (1 / (yr - base_year)) - 1})
    return out


def _check_growth(lo: float, med: float, hi: float):
    def check(o: dict) -> str | None:
        c = num(o.get("global_cagr"))
        if c is None or not (-0.15 <= c <= 0.6):
            return "global_cagr must be a decimal"
        if len(str(o.get("reasoning", ""))) < 120:
            return "reasoning must be 4-6 concrete sentences"
        if (c < lo - 0.01 or c > hi + 0.01) and len(str(o.get("departure_reason", ""))) < 120:
            return (f"global_cagr {c:.2%} is outside {lo:.2%}-{hi:.2%} by more than 1 pp: move it or give a specific, "
                    f"evidence-based departure_reason")
        return None
    return check


def _check_geo(kids: list[str]):
    def check(o: dict) -> str | None:
        items = canon_keys(o.get("items") or {}, kids)
        miss = [k for k in kids if k not in items]
        if miss:
            return f"missing geographies (exact names): {miss}"
        for k in kids:
            c = num((items[k] or {}).get("cagr"))
            if c is None or not (G.GEO_CAGR_RANGE[0] <= c <= G.GEO_CAGR_RANGE[1]):
                return f"{k}: cagr must be a decimal in {G.GEO_CAGR_RANGE}"
            if len(str((items[k] or {}).get("growth_reasoning", ""))) < 60:
                return f"{k}: growth_reasoning too thin"
        return None
    return check


def locked_view(state: dict) -> dict:
    n = state["numbers"]
    return {"value": n["geo"]["value"], "share": n["geo"]["share"], "seg": n["seg"]["shares"], "asp": n["price"]["asp"]}


def assert_locked(a: dict, b: dict) -> None:
    def walk(x, y, path):
        if isinstance(x, dict):
            assert set(x) == set(y), f"locked keys changed at {path}"
            for k in x:
                walk(x[k], y[k], f"{path}/{k}")
        else:
            assert abs(float(x) - float(y)) <= 1e-9 * max(1.0, abs(float(x))), f"LOCKED NUMBER CHANGED at {path}: {x} -> {y}"
    walk(a, b, "")


def plain_from_workbook(xlsx: Path, rows: list[dict]) -> dict:
    """Recover the plain-language note of every Data/ASP cell from the original workbook's cell comments."""
    wb = load_workbook(xlsx)
    out = {}
    for r in rows:
        ws = wb[r["sheet"]]
        c = ws[r["cell"]]
        if c.comment and c.comment.text:
            t = c.comment.text.split("\n\nCALCULATION:")[0].strip()
            if t and not t.startswith("CALCULATION:"):
                out[f"{r['sheet']}!{r['cell']}"] = {"text": t, "value": r["value"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "HC_growthfix")
    ap.add_argument("--launch-phase", action="store_true",
                    help="product in its first years on the market: growth follows launch timing; the mature-market "
                         "house growth bands and the growth prior-validation are not applied")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    src = args.run_dir
    spec = json.loads((src / "spec.json").read_text(encoding="utf-8"))
    out_dir = args.out / src.name
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in ("spec.json", "taxonomy.json"):
        (out_dir / f).write_text((src / f).read_text(encoding="utf-8"), encoding="utf-8")
    syslog.setup(ROOT / "system.log", "growth-fix")
    cfg = replace(Config(), market_name=spec["title"], taxonomy_path=out_dir / "taxonomy.json", generic=True)
    llm = LLM(cfg)
    llm.run_label = f"growth-fix: {spec['title']}"
    saved = json.loads((src / "state.json").read_text(encoding="utf-8"))
    tax = Taxonomy(out_dir / "taxonomy.json")
    state = {**saved, "cfg": cfg, "tax": tax, "llm": llm, "oem": {}, "segment_definitions": spec["segment_notes"],
             "scope_rules": spec["scope_rules"]}
    state["numbers"] = compute.compute_all(state)
    before = copy.deepcopy(locked_view(state))
    old_geo = copy.deepcopy(state["numbers"]["geo"])
    root, base = tax.root, state["sizing"]["global_value_2025"]
    from analyst_fleet.cell_reasoning import explain_all
    old_rows = explain_all(state, include_asp=True)          # ORIGINAL cell values, to reuse unchanged notes only

    # ---- 1. global CAGR from distinct published endpoints (code extracts, the analyst judges scope)
    kw = [w for w in tax.short_name.lower().split() if len(w) > 3]
    cands = forecast_candidates(state.get("facts", []), kw)
    fcs = []
    if cands:
        fc = llm.json("growthfix_endpoints", ENDPOINTS,
                      f"MARKET: {cfg.market_name}\nSCOPE: {dump(state['scope'])}\n\nCANDIDATES:\n"
                      + "\n".join(f"- {c['id']} ({c['evidence']}): US$ {c['figure_usd_mn']:,.0f} Mn by {c['year']} - {c['claim']}"
                                  for c in cands),
                      check=_check_endpoints([c["id"] for c in cands]), temperature=0.1)
        jc = canon_keys(fc["candidates"], [c["id"] for c in cands])
        fcs = [{**c, **{k: jc[c["id"]].get(k) for k in ("scope_factor", "source_name", "reasoning")}} for c in cands]
    ends = distinct_endpoints(fcs, base)
    sz = state["sizing"]
    old_cagr = sz["global_cagr"]
    if ends:
        imp = sorted(e["implied_cagr"] for e in ends)
        lo, hi, med = imp[0], imp[-1], (imp[(len(imp) - 1) // 2] + imp[len(imp) // 2]) / 2
        tbl = "\n".join(f"- {e.get('evidence')} ({e.get('source_name', '')}): US$ {num(e['figure_usd_mn']):,.0f} Mn in "
                        f"{int(num(e['year']))} x scope {num(e['scope_factor']):.3f} -> implied {e['implied_cagr']:.2%}" for e in ends)
        g = llm.json("growthfix_global", GROWTH.format(root=root, base=base),
                     f"MARKET: {cfg.market_name}\nCURRENT SHEET CAGR: {old_cagr:.2%} (being corrected)\nPREVIOUS GROWTH VIEW: "
                     f"{sz['step1'].get('cagr_reasoning', '')}\n\nDISTINCT PUBLISHED ENDPOINTS:\n{tbl}\nIMPLIED RANGE {lo:.2%}-{hi:.2%}, "
                     f"midpoint {(lo + hi) / 2:.2%}", check=_check_growth(lo, med, hi), temperature=0.2)
        new_cagr = num(g["global_cagr"])
        growth_text = (f"{g['reasoning']} Distinct published endpoints imply {lo:.2%}-{hi:.2%} a year from this base "
                       f"(midpoint {(lo + hi) / 2:.2%}).") + (f" Departure: {g['departure_reason']}" if g.get("departure_reason") else "")
    else:
        new_cagr, growth_text = old_cagr, sz["step1"].get("cagr_reasoning", "")
        print("no published forecast values found - global CAGR left unchanged")
    sz["step1"]["cagr_reasoning_initial"] = sz["step1"].get("cagr_reasoning", "")
    sz["step1"]["global_cagr_initial"] = old_cagr
    sz["step1"]["cagr_reasoning"] = f"[{LABEL}: {old_cagr:.2%} -> {new_cagr:.2%}] " + growth_text
    sz["step1"]["global_cagr"] = sz["global_cagr"] = sz["step2"]["global_cagr"] = new_cagr
    sz["endpoints"] = ends

    # ---- 2. regional and country CAGRs, shares locked, level by level
    def geo_call(parent: str, pcagr: float):
        kids = tax.children_of[parent]
        holder = state["regional_raw"] if parent == root else state["country_raw"][parent]
        g = compute.geo_numbers(state)
        rows = "\n".join(f"- {k}: 2025 share of {parent} {g['share'][k]:.1%} (FINAL), current CAGR {g['cagr'][k]:.2%}. "
                         f"Share reasoning: {str((holder['items'].get(k) or {}).get('reasoning', ''))[:400]}" for k in kids)
        fx = select_facts(state.get("facts", []), themes=("regional", "current_events"), geos=kids + [parent], limit=35)
        who = "regional" if parent == root else "country"
        user = (f"MARKET: {cfg.market_name}\n{parent} 2025 value US$ {g['value'][parent]:,.1f} Mn; {parent} CAGR {pcagr:.2%} "
                f"(corrected)\nGLOBAL GROWTH VIEW: {growth_text}\n\nGEOGRAPHIES (exact names):\n{rows}\n\n"
                f"WEB EVIDENCE (verified quotes):\n{fmt_facts(fx)}"
                + (LAUNCH_RULE if args.launch_phase else G.house_growth_bands("region" if parent == root else "country")))
        o = llm.json(f"growthfix_geo:{parent}", GEO_GROWTH.format(who=who, parent=parent, cagr=pcagr), user,
                     check=_check_geo(kids), temperature=0.2)
        for k in kids:
            it, new = holder["items"][k], o["items"][k]
            oc = num(it.get("cagr"))
            it["cagr_initial"], it["cagr"] = oc, num(new["cagr"])
            it["reasoning"] = (f"[{LABEL}: analyst CAGR {oc:.2%} -> {num(new['cagr']):.2%}] GROWTH: {new['growth_reasoning']} "
                               f"BASE-YEAR SHARE (unchanged): {it.get('reasoning', '')}")
            for f in ("drivers", "restraints", "geopolitics"):
                if new.get(f):
                    it[f] = new[f]
        return parent

    if tax.regions:
        geo_call(root, new_cagr)
    levels: dict[int, list[str]] = {}
    for p in tax.parent_nodes:
        if p != root:
            levels.setdefault(tax.depth(p), []).append(p)
    for d in sorted(levels):
        g = compute.geo_numbers(state)
        with ThreadPoolExecutor(max_workers=cfg.llm_workers) as ex:
            list(ex.map(lambda p: geo_call(p, g["cagr"][p]), levels[d]))
    state["numbers"] = compute.compute_all(state)

    # ---- 3. house-prior validation, growth items only
    lib = Library(json.loads((ROOT / "data/corpus/library.json").read_text(encoding="utf-8")))
    rules = json.loads((ROOT / "data/corpus/rules.json").read_text(encoding="utf-8"))
    target = target_record(state)
    items, chk = find_items(state, lib, rules, target)
    items = [i for i in items if i["kind"] in GROWTH_KINDS] if not args.launch_phase else []
    records = []
    if items:
        verdicts = adjudicate(state, items, target)
        upd, records = apply(state, items, verdicts)
        for r in records:
            r["prior"] = r["human"]
        state.update({k: v for k, v in upd.items() if v is not None})
        state["validation"], state["validation_mode"] = records, "priors (growth only)"
        state["numbers"] = compute.compute_all(state)
        finalize_notes(state, records)

    # ---- 4. lock check, then write
    assert_locked(before, locked_view(state))
    state["plain_texts"] = plain_from_workbook(src / f"Input Sheet - {spec['title']}.xlsx", old_rows)
    new_geo = state["numbers"]["geo"]
    state["growth_correction"] = {
        "source": str(src), "global_cagr": [old_cagr, new_geo["cagr"][root]], "endpoints": ends,
        "prior_items": [r["id"] for r in records],
        "cagr_changes": {k: [old_geo["cagr"][k], new_geo["cagr"][k]] for k in new_geo["cagr"]}}
    state["log"] = (state.get("log") or []) + [f"growth correction: CAGR {old_cagr:.2%} -> {new_geo['cagr'][root]:.2%}; "
                                               "all 2025 values, shares and prices locked"]
    xlsx = out_dir / f"Input Sheet - {spec['title']}.xlsx"
    dump_state(state, out_dir / "state.json")
    write_workbook(state, xlsx)
    assert_locked(before, locked_view(state))
    dump_state(state, out_dir / "state.json")
    (out_dir / "summary.md").write_text(summary_md(state), encoding="utf-8")
    u = llm.usage
    print(f"\n{spec['title']}: CAGR {old_cagr:.2%} -> {new_geo['cagr'][root]:.2%} (all 2025 numbers verified unchanged)")
    for e in ends:
        print(f"  endpoint {e.get('evidence')} {e.get('source_name', '')}: US$ {num(e['figure_usd_mn']):,.0f} Mn {int(num(e['year']))} "
              f"-> {e['implied_cagr']:.2%}")
    print(f"  prior items (growth only): {[r['id'] for r in records]}")
    print(f"  LLM live {u['calls']} cached {u['cached']}, cost US$ {u['cost_usd']:.3f}")
    print(f"  {xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
