"""Learn HOW past analysts form opinions (run after learn_from_corpus.py).

    python learn_opinions.py

For every corpus market: a code-computed dossier of its decisions -> the reasoning model reconstructs
the opinion and qualitative factor behind each decision. Opinions are then distilled into stage
heuristics for 2A / 2B / 2C (system.md section 7.5):
  data/corpus/opinion_playbook_train.json   distilled from the TRAIN split only (for the mimic test)
  data/corpus/opinion_playbook.json         distilled from all markets (used by the fleet)
  data/corpus/opinion_playbook.md           readable version
  data/corpus/opinions.json                 every reconstructed opinion, per market
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.config import Config
from analyst_fleet.corpus.build import scan
from analyst_fleet.corpus.features import view_from_me
from analyst_fleet.corpus.llm_tasks import run_parallel
from analyst_fleet.corpus.opinion import STAGES, distil, dossier, opinion_stats, region_price_index, reverse_engineer
from analyst_fleet.llm import LLM

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "corpus"
MONTHS = re.compile(r"(?i)^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*( \d{4})?$")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    syslog.setup(ROOT / "system.log", "opinions")
    lib = {m["id"]: m for m in json.loads((OUT / "library.json").read_text(encoding="utf-8"))}
    folders = sorted(p for p in ROOT.iterdir() if p.is_dir() and MONTHS.match(p.name))
    markets = []
    for s in scan(folders):
        m = lib.get(s["id"])
        if not m:
            continue
        p = s["parsed"]
        m["dossier"] = dossier(view_from_me(p), region_price_index(p))
        m["dims"] = [d["title"] for d in p["geos"][p["root"]]["dims"]]
        markets.append(m)
    syslog.event("opinion.start", markets=len(markets))
    print(f"{len(markets)} markets with dossiers")

    llm = LLM(cfg)
    res = run_parallel(lambda m, _n: reverse_engineer(llm, m), [(m, m["name"]) for m in markets], cfg.llm_workers)
    records, per_market = [], {}
    for m, r in zip(markets, res):
        if not r:
            continue
        per_market[m["id"]] = {"name": m["name"], "split": m["split"], "dimension_roles": r.get("dimension_roles"),
                               "opinions": r["opinions"], "style": r.get("style"), "dossier": m["dossier"]}
        for o in r["opinions"]:
            if o.get("generalisable", True):
                records.append({"market": m["name"], "split": m["split"], "category": (m.get("class") or {}).get("category", "?"),
                                **{k: o.get(k, "") for k in ("decision", "factor", "what", "why")}})
        syslog.event("opinion.market", id=m["id"], opinions=len(r["opinions"]), roles=r.get("dimension_roles"))
    (OUT / "opinions.json").write_text(json.dumps(per_market, indent=1, ensure_ascii=False), encoding="utf-8")

    for tag, pool in (("train", [m for m in markets if m["split"] == "train"]), ("all", markets)):
        names = {m["name"] for m in pool}
        recs = [r for r in records if r["market"] in names]
        stats = opinion_stats(pool)
        stages = dict(zip(STAGES, run_parallel(lambda st, _t: distil(llm, st, recs, stats, tag),
                                               [(st, tag) for st in STAGES], 3)))
        pb = {"tag": tag, "n_markets": len(pool), "markets": sorted(names), "stages": stages, "stats": stats}
        fn = "opinion_playbook.json" if tag == "all" else "opinion_playbook_train.json"
        (OUT / fn).write_text(json.dumps(pb, indent=1, ensure_ascii=False), encoding="utf-8")
        if tag == "all":
            (OUT / "opinion_playbook.md").write_text(to_md(pb), encoding="utf-8")
    syslog.event("opinion.end", opinions=len(records), llm_live=llm.usage["calls"], llm_cached=llm.usage["cached"])
    print(f"{len(records)} opinions; LLM live {llm.usage['calls']} cached {llm.usage['cached']}")
    return 0


def to_md(pb: dict) -> str:
    L = [f"# House opinion playbook ({pb['n_markets']} past markets)", "",
         "How the team's analysts turn qualitative factors into numbers. Reasoning habits with typical magnitudes - "
         "not targets. 'house habit' = how sheets are built rather than how markets behave.", ""]
    title = {"2A": "2A - country totals, regional split, growth", "2B": "2B - use-case / application split",
             "2C": "2C - product types inside use cases, sub-segments, price by region"}
    for st, blk in pb["stages"].items():
        L += [f"## {title[st]}", "", f"**Method.** {blk['method']}", ""]
        for h in blk["heuristics"]:
            L.append(f"- **{h['id']}** ({h['decision']}, {'house habit' if h.get('kind') == 'house_convention' else 'mechanism'}, "
                     f"{h.get('n_markets', 0)} markets): when {h['condition']} -> {h['action']}. *Why:* {h['why']}"
                     + (f" *Not when:* {h['caveat']}" if h.get("caveat") else ""))
        if blk.get("pitfalls"):
            L += ["", "Pitfalls:", *[f"- {p}" for p in blk["pitfalls"]]]
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
