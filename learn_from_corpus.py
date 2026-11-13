"""Learn a house playbook from past human ME / Input Sheet workbooks.

    python learn_from_corpus.py                 # scans April/ May/ July/ (any month-named folder)
    python learn_from_corpus.py Aug Sep          # or the folders you name

Writes data/corpus/:
  library.json   one record per market: classification, scale-free features, sizing method
  rules.json     candidate rules with train/test support and status (kept / weak / rejected)
  eval.json      leave-one-out: do analog priors predict an unseen market better than the corpus median?
  playbook.md    the readable version (bands by category, kept rules, sizing lessons, eval)
Every step is logged to system.log. Add more folders later and re-run: parsing is cheap and
LLM steps are cached, so only new markets cost calls.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.config import Config
from analyst_fleet.corpus.build import scan
from analyst_fleet.corpus.features import features, segment_names, view_from_me
from analyst_fleet.corpus.llm_tasks import (CATEGORIES, CUSTOMERS, OFFERINGS, classify_market, extract_method,
                                            propose_rules, run_parallel)
from analyst_fleet.corpus.priors import PRIOR_FEATURES, Library, describe, loo_eval, split_of, verify_rules
from analyst_fleet.llm import LLM

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "corpus"
MONTHS = re.compile(r"(?i)^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*( \d{4})?$")
FEATURE_DOC = (ROOT / "analyst_fleet" / "corpus" / "features.py").read_text(encoding="utf-8").split('"""')[1]


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    syslog.setup(ROOT / "system.log", "corpus")
    folders = [ROOT / a for a in argv] if argv else sorted(p for p in ROOT.iterdir() if p.is_dir() and MONTHS.match(p.name))
    syslog.event("corpus.start", folders=[f.name for f in folders])
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 1. parse + features (code)
    markets = scan(folders)
    for m in markets:
        m["features"] = features(view_from_me(m["parsed"]))
        m["segments"] = segment_names(m["parsed"])
        m["root"] = m["parsed"]["root"]
        m["unit"] = m["parsed"]["geos"][m["root"]].get("unit", "")
        m["split"] = split_of(m["family"])
    markets = [m for m in markets if m["features"]]
    print(f"{len(markets)} markets parsed from {[f.name for f in folders]}")

    # ---- 2. classify + sizing methods (LLM, fast tier, cached)
    llm = LLM(cfg)
    syslog.event("batch.classify", n=len(markets))
    for m, c in zip(markets, run_parallel(lambda n, s: classify_market(llm, n, s),
                                          [(m["name"], m["segments"]) for m in markets], cfg.llm_workers)):
        m["class"] = c or {}
    with_notes = [m for m in markets if len(m["secondary"]) >= 5]
    syslog.event("batch.methods", n=len(with_notes))
    for m, r in zip(with_notes, run_parallel(lambda n, l: extract_method(llm, n, l),
                                             [(m["name"], m["secondary"]) for m in with_notes], cfg.llm_workers)):
        m["method"] = r
    print("categories:", dict(Counter(m["class"].get("category") for m in markets)))

    # ---- 3. rules: proposed from TRAIN markets only, verified on train and held-out test
    train = [m for m in markets if m["split"] == "train"]
    stats = describe(train)
    feat_names = sorted({k for m in train for k in m["features"]})
    rules = propose_rules(llm, [f"{k}" for k in feat_names] + ["", FEATURE_DOC], stats,
                          {"category": CATEGORIES, "offering": OFFERINGS, "customer": CUSTOMERS})
    rules = verify_rules(rules, markets)
    for r in rules:
        syslog.event("restriction.rule", id=r.get("id"), status=r["status"], kind=r.get("kind"),
                     train=f"{r['train_support']}/{r['train_n']}", test=f"{r['test_support']}/{r['test_n']}",
                     statement=r.get("statement", ""))

    # ---- 4. leave-one-out: does the library generalise to markets it has not seen?
    lib = Library(markets)
    ev = loo_eval(lib)
    syslog.event("corpus.eval", features=len(ev),
                 mean_gain=round(sum(v["gain"] for v in ev.values()) / max(len(ev), 1), 3),
                 mean_band_coverage=round(sum(v["band_coverage"] for v in ev.values()) / max(len(ev), 1), 3))

    # ---- 5. write
    keep = ("id", "family", "month", "name", "me_file", "versions", "root", "unit", "split", "class", "features",
            "segments", "method")
    (OUT / "library.json").write_text(json.dumps([{k: m.get(k) for k in keep} for m in markets], indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    (OUT / "rules.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "eval.json").write_text(json.dumps(ev, indent=1), encoding="utf-8")
    (OUT / "stats_train.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    (OUT / "playbook.md").write_text(playbook(markets, rules, ev, describe(markets)), encoding="utf-8")
    syslog.event("corpus.end", markets=len(markets), rules_kept=sum(r["status"] == "kept" for r in rules),
                 rules_weak=sum(r["status"] == "weak" for r in rules), rules_rejected=sum(r["status"] == "rejected" for r in rules),
                 llm_live=llm.usage["calls"], llm_cached=llm.usage["cached"])
    print(f"rules kept {sum(r['status'] == 'kept' for r in rules)} / {len(rules)}; LLM live {llm.usage['calls']} cached {llm.usage['cached']}")
    print(f"wrote {OUT}")
    return 0


def _pct(x: float, f: str) -> str:
    return f"{x:+.2%}" if ("gap" in f or "minus" in f) else f"{x:.1%}" if ("share" in f or "cagr" in f or "sd" in f
                                                                         or "dev" in f or "spread" in f or "range" in f) else f"{x:.2f}"


def playbook(markets, rules, ev, stats) -> str:
    L = ["# House playbook learned from past ME workbooks", "",
         f"{len(markets)} markets ({', '.join(sorted({m['month'] for m in markets}))}); "
         f"train {sum(m['split'] == 'train' for m in markets)} / held-out test {sum(m['split'] == 'test' for m in markets)} "
         "(split by market folder). Only scale-free quantities are learned - shares, growth spreads, history vs forecast - "
         "never a market's absolute size.", "",
         "## Markets by category", ""]
    for c, n in Counter((m.get("class") or {}).get("category", "?") for m in markets).most_common():
        L.append(f"- {c}: {n} - " + ", ".join(m["name"] for m in markets if (m.get("class") or {}).get("category") == c))
    L += ["", "## Corpus bands (p10 / median / p90, n markets)", "", "| Feature | p10 | median | p90 | n |", "|---|---|---|---|---|"]
    for f in PRIOR_FEATURES:
        if f in stats["overall"]:
            a, b, c, n = stats["overall"][f]
            L.append(f"| {f} | {_pct(a, f)} | {_pct(b, f)} | {_pct(c, f)} | {n} |")
    L += ["", "Directional frequencies:", *[f"- {k}: {v:.0%}" for k, v in stats["directional"].items() if v is not None]]
    for kind, title in (("economics", "Rules - market economics"), ("house_convention", "Rules - house conventions")):
        L += ["", f"## {title}", "", "| id | status | support (train / test) | rule | why |", "|---|---|---|---|---|"]
        for r in sorted((r for r in rules if r.get("kind") == kind), key=lambda r: ({"kept": 0, "weak": 1}.get(r["status"], 2), r.get("id", ""))):
            L.append(f"| {r.get('id')} | {r['status']} | {r['train_support']} (n={r['train_n']}) / {r['test_support']} (n={r['test_n']}) "
                     f"| {r.get('statement', '')} | {r.get('rationale', '')} |")
    L += ["", "## How analysts sized the base year (from their Secondary / Base Number sheets)", ""]
    for m in markets:
        mt = m.get("method")
        if mt:
            L += [f"### {m['name']} - {mt.get('sizing_method')} ({mt.get('quality', '?')} quality notes)", mt.get("method_summary", ""),
                  *[f"- lesson: {x}" for x in mt.get("transferable_lessons") or []], ""]
    L += ["## Does it generalise? Leave-one-out on unseen markets", "",
          "Each market is predicted from the others (its own folder excluded). 'gain' = error reduction of the analog "
          "prior vs the plain corpus median; 'coverage' = how often the truth fell inside the analog p10-p90 band (~0.8 is calibrated).", "",
          "| Feature | n | MAE corpus median | MAE analogs | gain | coverage |", "|---|---|---|---|---|---|"]
    for f, v in ev.items():
        L.append(f"| {f} | {v['n']} | {_pct(v['mae_corpus_median'], f)} | {_pct(v['mae_analogs'], f)} | {v['gain']:+.0%} | {v['band_coverage']:.0%} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
