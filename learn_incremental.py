"""Add NEW past ME workbooks to the learned corpus without re-learning the markets already in it.

    python learn_incremental.py              # scans every month-named folder (April/, June/, ...)
    python learn_incremental.py --dry-run    # list what would be added, no LLM calls, nothing written

What it does (and does not) touch in data/corpus/:
  library.json   existing records kept verbatim (also those whose ME file is no longer on disk);
                 markets not yet in it are parsed, featurised and classified, then appended
  rules.json     the existing rules are NOT re-proposed; they are re-verified (code only) on the
                 enlarged corpus, so their support / kept-weak-rejected status reflects the new markets
  eval.json, stats_train.json, playbook.md   recomputed in code on the enlarged corpus
  opinions_incremental.json   reconstructed opinions for the new markets only
  opinion_playbook.json / _train.json   existing heuristics, calibration and disabled_decisions are kept
                 word for word; new markets can only (a) be added as support to an existing heuristic,
                 (b) add new heuristics, (c) add pitfalls. Stats are recomputed on the enlarged corpus.
The previous versions are tracked in git: `git checkout data/corpus/` undoes a run.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.config import Config
from analyst_fleet.corpus.build import scan
from analyst_fleet.corpus.features import features, segment_names, view_from_me
from analyst_fleet.corpus.llm_tasks import classify_market, extract_method, run_parallel
from analyst_fleet.corpus.opinion import (STAGES, dossier, opinion_stats, region_price_index, reverse_engineer)
from analyst_fleet.corpus.priors import Library, describe, loo_eval, split_of, verify_rules
from analyst_fleet.llm import LLM
from learn_from_corpus import playbook
from learn_opinions import to_md

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "corpus"
MONTHS = re.compile(r"(?i)^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*( \d{4})?$")
KEEP = ("id", "family", "month", "name", "me_file", "versions", "root", "unit", "split", "class", "features",
        "segments", "method")

UPDATE_SYSTEM = """You are the research director maintaining the HOUSE OPINION PLAYBOOK for stage {stage}.
The playbook was distilled from earlier market estimates and is NOT to be rewritten. You are given its
current heuristics and the opinions reconstructed from NEW market estimates (tagged with market and
category), plus code-computed statistics over all markets.

Your only jobs:
 1. support: for each existing heuristic id, list the NEW markets whose opinions genuinely follow it
    (same condition, same direction, magnitude within its range). Omit ids with no new support.
 2. new_heuristics: only for patterns in the new opinions that NO existing heuristic covers, written
    as "when <qualitative condition>, set/shift <number> by <typical magnitude>, because <mechanism>",
    grounded in the opinions/statistics. 0-4 items; zero is a fine answer.
 3. new_pitfalls: mistakes the new markets reveal that the current pitfalls miss. 0-3 items.

Decisions in this stage: {decisions}

Return json:
{{"support": {{"<existing id>": ["new market names"]}},
 "new_heuristics": [{{"decision": "<one of the decisions>", "condition": "...", "action": "direction + typical magnitude",
                     "why": "mechanism", "kind": "mechanism" | "house_convention",
                     "markets": ["new market names"], "caveat": "when NOT to apply"}}],
 "new_pitfalls": ["..."]}}"""


def update_stage(llm, stage: str, block: dict, records: list[dict], stats: dict, tag: str) -> dict:
    decisions = STAGES[stage]
    ids = {h["id"] for h in block["heuristics"]}
    rows = [r for r in records if r["decision"] in decisions]
    current = [{k: h.get(k) for k in ("id", "decision", "condition", "action", "kind")} for h in block["heuristics"]]

    def check(o: dict) -> str | None:
        if not isinstance(o.get("support"), dict) or not isinstance(o.get("new_heuristics"), list):
            return "need support (object) and new_heuristics (list)"
        bad = [k for k in o["support"] if k not in ids]
        if bad:
            return f"support keys must be existing ids, not {bad}"
        for h in o["new_heuristics"]:
            if h.get("decision") not in decisions:
                return f"new heuristic decision must be one of {decisions}"
            if len(str(h.get("action", ""))) < 15 or len(str(h.get("why", ""))) < 20:
                return "new heuristics need a specific action and why"
        return None

    user = (f"STATISTICS:\n{json.dumps(stats, indent=0)[:20000]}\n\nCURRENT HEURISTICS:\n{json.dumps(current, ensure_ascii=False)}\n\n"
            f"CURRENT PITFALLS:\n{json.dumps(block.get('pitfalls') or [], ensure_ascii=False)}\n\nNEW OPINIONS ({len(rows)}):\n"
            + "\n".join(f"- [{r['market']} | {r['category']}] {r['decision']} ({r['factor']}): {r['what']} -> {r['why']}" for r in rows)[:60000])
    return llm.json(f"opinion.update:{stage}:{tag}", UPDATE_SYSTEM.format(stage=stage, decisions=decisions), user,
                    check=check, temperature=0.2, retries=4)


def merge_stage(stage: str, block: dict, upd: dict, new_names: set[str]) -> tuple[dict, dict]:
    """Code-side merge: existing heuristic text is never changed."""
    out = json.loads(json.dumps(block))
    log = {"support_added": 0, "heuristics_added": [], "pitfalls_added": 0}
    for h in out["heuristics"]:
        add = [m for m in upd["support"].get(h["id"], []) if m in new_names and m not in h["markets"]]
        h["markets"] += add
        h["n_markets"] = len(h["markets"])
        log["support_added"] += len(add)
    nxt = max(int(h["id"].split("-")[-1]) for h in out["heuristics"]) + 1
    for h in upd.get("new_heuristics") or []:
        h["markets"] = [m for m in h.get("markets") or [] if m in new_names]
        if not h["markets"]:
            continue                       # a new heuristic must be backed by at least one new market
        h["id"], h["n_markets"], h["added"] = f"{stage}-{nxt}", len(h["markets"]), "incremental"
        nxt += 1
        out["heuristics"].append(h)
        log["heuristics_added"].append(h["id"])
    for p in upd.get("new_pitfalls") or []:
        if p and p not in out.get("pitfalls", []):
            out.setdefault("pitfalls", []).append(p)
            log["pitfalls_added"] += 1
    return out, log


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    dry = "--dry-run" in argv
    cfg = Config()
    syslog.setup(ROOT / "system.log", "incremental")
    old = json.loads((OUT / "library.json").read_text(encoding="utf-8"))
    known = {m["id"] for m in old}
    folders = sorted(p for p in ROOT.iterdir() if p.is_dir() and MONTHS.match(p.name))
    new = [m for m in scan(folders) if m["id"] not in known]
    for m in new:
        m["features"] = features(view_from_me(m["parsed"]))
        m["segments"] = segment_names(m["parsed"])
        m["root"] = m["parsed"]["root"]
        m["unit"] = m["parsed"]["geos"][m["root"]].get("unit", "")
        m["split"] = split_of(m["family"])
    new = [m for m in new if m["features"]]
    print(f"library: {len(old)} existing markets kept as-is; {len(new)} new markets to add")
    for m in new:
        print(f"  + [{m['split']}] {m['id']}")
    syslog.event("incremental.start", existing=len(old), new=len(new))
    if dry or not new:
        return 0

    llm = LLM(cfg)
    # ---- 1. classify + sizing method for the NEW markets only
    for m, c in zip(new, run_parallel(lambda n, s: classify_market(llm, n, s), [(m["name"], m["segments"]) for m in new],
                                      cfg.llm_workers)):
        m["class"] = c or {}
    with_notes = [m for m in new if len(m["secondary"]) >= 5]
    for m, r in zip(with_notes, run_parallel(lambda n, l: extract_method(llm, n, l),
                                             [(m["name"], m["secondary"]) for m in with_notes], cfg.llm_workers)):
        m["method"] = r
    markets = old + [{k: m.get(k) for k in KEEP} for m in new]

    # ---- 2. existing rules re-verified on the enlarged corpus (no new rules proposed)
    rules_old = json.loads((OUT / "rules.json").read_text(encoding="utf-8"))
    rules = verify_rules(rules_old, markets)
    status_changes = [(a.get("id"), a["status"], b["status"]) for a, b in zip(rules_old, rules) if a["status"] != b["status"]]

    # ---- 3. code-only recomputation
    ev = loo_eval(Library(markets))
    stats_train = describe([m for m in markets if m["split"] == "train"])

    # ---- 4. opinions for the NEW markets, merged into the existing playbooks
    by_id = {m["id"]: m for m in new}
    for m in new:
        p = m["parsed"]
        m["dossier"] = dossier(view_from_me(p), region_price_index(p))
        m["dims"] = [d["title"] for d in p["geos"][p["root"]]["dims"]]
    res = run_parallel(lambda m, _n: reverse_engineer(llm, m), [(m, m["name"]) for m in new], cfg.llm_workers)
    records, per_market = [], {}
    for m, r in zip(new, res):
        if not r:
            continue
        per_market[m["id"]] = {"name": m["name"], "split": m["split"], "dimension_roles": r.get("dimension_roles"),
                               "opinions": r["opinions"], "style": r.get("style"), "dossier": m["dossier"]}
        for o in r["opinions"]:
            if o.get("generalisable", True):
                records.append({"market": m["name"], "split": m["split"], "category": (m.get("class") or {}).get("category", "?"),
                                **{k: o.get(k, "") for k in ("decision", "factor", "what", "why")}})
    (OUT / "opinions_incremental.json").write_text(json.dumps(per_market, indent=1, ensure_ascii=False), encoding="utf-8")

    report = {"new_markets": [by_id[i]["name"] for i in per_market], "rule_status_changes": status_changes, "playbooks": {}}
    for tag, fn in (("all", "opinion_playbook.json"), ("train", "opinion_playbook_train.json")):
        pb = json.loads((OUT / fn).read_text(encoding="utf-8"))
        pool_new = [m for m in new if m["id"] in per_market and (tag == "all" or m["split"] == "train")]
        names = {m["name"] for m in pool_new}
        recs = [r for r in records if r["market"] in names]
        pool = [m for m in markets if tag == "all" or m["split"] == "train"]
        stats = opinion_stats(pool)
        logs = {}
        if recs:
            upds = dict(zip(STAGES, run_parallel(lambda st, _t: update_stage(llm, st, pb["stages"][st], recs, stats, tag),
                                                 [(st, tag) for st in STAGES], 3)))
            for st in STAGES:
                if upds.get(st):
                    pb["stages"][st], logs[st] = merge_stage(st, pb["stages"][st], upds[st], names)
        pb["markets"] = sorted(set(pb["markets"]) | names)
        pb["n_markets"] = len(pb["markets"])
        pb["stats"] = stats                      # calibration and disabled_decisions untouched
        (OUT / fn).write_text(json.dumps(pb, indent=1, ensure_ascii=False), encoding="utf-8")
        if tag == "all":
            (OUT / "opinion_playbook.md").write_text(to_md(pb), encoding="utf-8")
        report["playbooks"][tag] = {"n_markets": pb["n_markets"], "stages": logs}

    # ---- 5. write corpus files
    (OUT / "library.json").write_text(json.dumps(markets, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "rules.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "eval.json").write_text(json.dumps(ev, indent=1), encoding="utf-8")
    (OUT / "stats_train.json").write_text(json.dumps(stats_train, indent=1), encoding="utf-8")
    (OUT / "playbook.md").write_text(playbook(markets, rules, ev, describe(markets)), encoding="utf-8")
    report["llm"] = {"live": llm.usage["calls"], "cached": llm.usage["cached"]}
    syslog.event("incremental.end", **{k: v for k, v in report.items() if k != "playbooks"})
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
