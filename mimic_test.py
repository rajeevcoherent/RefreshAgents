"""Cross-check: does the learned house judgement make an independent analyst form opinions closer to
the human team's, on markets it has never seen?

    python mimic_test.py

For every held-out (test-split) corpus market plus the gearbox market, an independent analyst sees
only the market name, its geography split and its segmentation, and commits to: global CAGR, each
top-level geography's share and CAGR, each top-level segment's share and CAGR (with a reason for
every number). Arm A: no playbook. Arm B: the playbook distilled from TRAIN markets only.
Scored against the human sheets on scale-free quantities:
  geo share MAE, geo growth-gap MAE (geo CAGR - global CAGR), global CAGR error,
  segment share MAE, segment growth-premium MAE (segment CAGR - global CAGR), segment growth-rank correlation.
Writes data/corpus/mimic_test.json and mimic_test.md.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean

from analyst_fleet import syslog
from analyst_fleet.agents.common import canon_keys, num
from analyst_fleet.config import Config
from analyst_fleet.corpus.features import _spearman, cagr, view_from_me
from analyst_fleet.corpus.llm_tasks import run_parallel
from analyst_fleet.corpus.opinion import DECISIONS, guidance, load_playbook
from analyst_fleet.corpus.parse_me import parse_me
from analyst_fleet.llm import LLM
from analyst_fleet.prior_validator import view_from_human
from analyst_fleet.taxonomy import Taxonomy
import compare_with_human as cmp

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "corpus"

ANALYST = """You are a senior, independent and opinionated market-research analyst. You are given a
market, its geographic split and its segmentation, and you must form your OWN opinion of the market's
structure for base year 2025 and growth to 2032, from what you know about the product, its users,
its economics and the world.
- Commit to a number for every item. No ranges, no refusals.
- Shares are decimals of the parent (geography shares sum to 1; each dimension's segments sum to 1).
- CAGR is the 2025-2032 value CAGR as a decimal.
- Every number gets a one-sentence reason.
Return json:
{"global_cagr": number, "global_cagr_why": "...",
 "geographies": {"<name>": {"share": number, "cagr": number, "why": "..."}},
 "segments": {"<dimension>": {"<segment>": {"share": number, "cagr": number, "why": "..."}}}}"""


def truth(view: dict) -> dict:
    root, v, n = view["root"], view["v"], view["n_fc"]
    rc = cagr(v[root][1], v[root][2], n)
    kids = [k for k in view["kids"].get(root, []) if k in v and v[k][1]]
    tot = sum(v[k][1] for k in kids) or 1
    geos = {k: (v[k][1] / tot, cagr(v[k][1], v[k][2], n)) for k in kids}
    segs = {}
    for dim, rows in (view["seg"].get(root) or {}).items():
        top = [(s, a, b) for s, p, a, b in rows if p is None and a]
        t = sum(a for _, a, _ in top)
        if len(top) >= 2 and t:
            segs[dim] = {s: (a / t, cagr(a, b, n)) for s, a, b in top}
    return {"root": root, "cagr": rc, "geos": geos, "segs": segs}


def ask(llm, name: str, tr: dict, pb: dict | None, arm: str) -> dict:
    gnames = list(tr["geos"])
    dims = {d: list(s) for d, s in tr["segs"].items()}
    house = ""
    if pb:
        house = guidance(pb, "regional", extra_decisions=tuple(DECISIONS))
    user = (f"MARKET: {name}\nTOP-LEVEL GEOGRAPHY: {tr['root']}, split into (exact names): {gnames}\n"
            f"SEGMENTATION (exact names): {json.dumps(dims, ensure_ascii=False)}" + house)

    def check(o):
        g = canon_keys(o.get("geographies") or {}, gnames)
        if any(num((g.get(k) or {}).get("share")) is None or num((g.get(k) or {}).get("cagr")) is None for k in gnames):
            return f"every geography needs share and cagr: {gnames}"
        sg = canon_keys(o.get("segments") or {}, list(dims))
        for d, ss in dims.items():
            dd = canon_keys(sg.get(d) or {}, ss)
            if any(num((dd.get(s) or {}).get("share")) is None or num((dd.get(s) or {}).get("cagr")) is None for s in ss):
                return f"dimension {d!r} needs share and cagr for every segment: {ss}"
            sg[d] = dd
        if num(o.get("global_cagr")) is None:
            return "global_cagr missing"
        o["geographies"], o["segments"] = g, sg
        return None
    return llm.json(f"mimic:{arm}:{name[:40]}", ANALYST, user, check=check, temperature=0.3, retries=4)


def score(tr: dict, out: dict) -> dict:
    gc = num(out["global_cagr"])
    g = out["geographies"]
    gs = sum(num(g[k]["share"]) for k in tr["geos"]) or 1
    r = {"global_cagr_err": abs(gc - tr["cagr"]) if tr["cagr"] is not None else None}
    r["geo_share_mae"] = mean(abs(num(g[k]["share"]) / gs - t[0]) for k, t in tr["geos"].items()) if tr["geos"] else None
    gaps = [abs((num(g[k]["cagr"]) - gc) - (t[1] - tr["cagr"])) for k, t in tr["geos"].items() if t[1] is not None]
    r["geo_gap_mae"] = mean(gaps) if gaps else None
    sh, pr, rk = [], [], []
    for d, ss in tr["segs"].items():
        o = out["segments"][d]
        tot = sum(num(o[s]["share"]) for s in ss) or 1
        sh += [abs(num(o[s]["share"]) / tot - t[0]) for s, t in ss.items()]
        pr += [abs((num(o[s]["cagr"]) - gc) - (t[1] - tr["cagr"])) for s, t in ss.items() if t[1] is not None]
        ok = [s for s, t in ss.items() if t[1] is not None]
        c = _spearman([num(o[s]["cagr"]) for s in ok], [ss[s][1] for s in ok])
        if c is not None:
            rk.append(c)
    r["seg_share_mae"] = mean(sh) if sh else None
    r["seg_premium_mae"] = mean(pr) if pr else None
    r["seg_rank_corr"] = mean(rk) if rk else None
    return r


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    syslog.setup(ROOT / "system.log", "mimic")
    pb = load_playbook(OUT / "opinion_playbook_train.json")
    lib = json.loads((OUT / "library.json").read_text(encoding="utf-8"))
    cases = []
    for m in lib:
        if m["split"] != "test":
            continue
        tr = truth(view_from_me(parse_me(ROOT / m["me_file"])))
        if tr["geos"] or tr["segs"]:
            cases.append({"name": m["name"], "kind": "held-out corpus", "truth": tr})
    tax = Taxonomy(cfg.taxonomy_path)
    cases.append({"name": cfg.market_name, "kind": "gearbox (never in corpus)", "truth": truth(view_from_human(tax, cmp.read_human(tax)))})
    syslog.event("mimic.start", cases=len(cases), playbook_markets=pb["n_markets"])

    llm = LLM(cfg)
    pb_v1 = {k: v for k, v in pb.items() if k != "calibration"}      # the playbook as first distilled
    arms = {"A_no_playbook": None, "B_playbook": pb_v1, "C_playbook_calibrated": pb}
    jobs = [(c, arm) for c in cases for arm in arms]
    outs = run_parallel(lambda c, arm: ask(llm, c["name"], c["truth"], arms[arm], arm), jobs, cfg.llm_workers)
    res = []
    for (c, arm), o in zip(jobs, outs):
        if o:
            s = score(c["truth"], o)
            res.append({"market": c["name"], "kind": c["kind"], "arm": arm, **s, "answer": o})
            syslog.event("mimic.score", market=c["name"], arm=arm, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()})
    (OUT / "mimic_test.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")

    metrics = ["global_cagr_err", "geo_share_mae", "geo_gap_mae", "seg_share_mae", "seg_premium_mae", "seg_rank_corr"]
    fmt = lambda k, v: "n/a" if v is None else (f"{v:+.2f}" if k == "seg_rank_corr" else f"{v * 100:.2f} pp")

    def avg(rows, k):
        v = [r[k] for r in rows if r[k] is not None]
        return mean(v) if v else None
    L = ["# Mimic test - does the house playbook make an analyst think like the team?", "",
         f"Playbook distilled from {pb['n_markets']} TRAIN markets only; tested on {len(cases) - 1} held-out markets + the gearbox. "
         "Lower is better except rank correlation (higher = segment growth ordering closer to the human's).", "",
         "| Group | Arm | " + " | ".join(metrics) + " |", "|---|---|" + "---|" * len(metrics)]
    for grp, sel in (("held-out corpus", lambda r: r["kind"] == "held-out corpus"), ("gearbox", lambda r: r["kind"] != "held-out corpus")):
        for arm in ("A_no_playbook", "B_playbook", "C_playbook_calibrated"):
            rows = [r for r in res if sel(r) and r["arm"] == arm]
            L.append(f"| {grp} | {arm} | " + " | ".join(fmt(k, avg(rows, k)) for k in metrics) + " |")
    L += ["", "## Per market", "", "| Market | Arm | " + " | ".join(metrics) + " |", "|---|---|" + "---|" * len(metrics)]
    for r in res:
        L.append(f"| {r['market'][:50]} | {r['arm']} | " + " | ".join(fmt(k, r[k]) for k in metrics) + " |")
    (OUT / "mimic_test.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:9]))
    syslog.event("mimic.end", llm_live=llm.usage["calls"], llm_cached=llm.usage["cached"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
