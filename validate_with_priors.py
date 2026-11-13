"""Reference-free validation: judge a run's Input Sheet against priors learned from past human sheets.

    python learn_from_corpus.py                     # once (and again whenever new month folders arrive)
    python validate_with_priors.py outputs/<run_id>

Needs no human sheet for the market. Writes into the run folder:
  Input Sheet - <market> (prior-validated).xlsx   ('Validation' sheet + reasoning next to every moved number)
  state_priorval.json, priors_validation.md
If a human sheet for this market happens to exist (the gearbox one does), it is used ONLY afterwards
to score the result (comparison_priors.md) and to check whether the human sheet itself sits inside the
learned bands - it never feeds the validation.
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.compute import compute_all
from analyst_fleet.config import Config
from analyst_fleet.corpus.features import features
from analyst_fleet.corpus.priors import Library
from analyst_fleet.evidence.definitions import scope_rules, segment_definitions
from analyst_fleet.evidence.oem import load_oem
from analyst_fleet.llm import LLM
from analyst_fleet.prior_validator import adjudicate, apply, find_items, target_record, view_from_human
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.validator import finalize_notes
from analyst_fleet.writer import dump_state, write_workbook
import compare_with_human as cmp

CORPUS = Path(__file__).resolve().parent / "data" / "corpus"


def main(run_dir: Path) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(run_dir / "validate_priors.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    tax = Taxonomy(cfg.taxonomy_path)
    syslog.setup(cfg.out_dir.parent / "system.log", f"{run_dir.name}:priors")
    lib_ms = json.loads((CORPUS / "library.json").read_text(encoding="utf-8"))
    rules = json.loads((CORPUS / "rules.json").read_text(encoding="utf-8"))
    lib = Library(lib_ms)
    syslog.event("validate.start", mode="priors", run=run_dir.name, corpus_markets=len(lib.markets),
                 rules_kept=sum(r["status"] == "kept" for r in rules))

    saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    best = saved.get("best_pass")
    review = next((r for r in saved.get("reviews") or [] if r.get("pass") == best), {})
    state = {**saved, "cfg": cfg, "tax": tax, "llm": LLM(cfg), "oem": load_oem(cfg.oem_dir, tax), "segment_definitions": segment_definitions(cfg.definitions_docx), "scope_rules": scope_rules(cfg.definitions_docx, cfg.oem_definitions_docx), "review": review}
    state.pop("validation", None)
    state["numbers"] = compute_all(state)

    target = target_record(state)
    analogs = [(round(s, 2), m["name"]) for s, m in lib.similar(target)[:10]]
    syslog.event("validate.target", classification=target["class"], analogs=analogs)
    items, chk = find_items(state, lib, rules, target)
    syslog.event("validate.items", mode="priors", n=len(items), by_kind=dict(Counter(i["kind"] for i in items)),
                 outside=[f for f, c in chk["checks"].items() if c["outside"]])
    print(f"target: {target['class'].get('category')} / {target['class'].get('offering')}; analogs: {[a[1] for a in analogs[:5]]}")
    print(f"{len(chk['checks'])} features checked, {len(items)} outside the learned bands: {[i['id'] for i in items]}")

    verdicts = adjudicate(state, items, target) if items else {}
    upd, records = apply(state, items, verdicts)
    for r in records:
        r["prior"] = r["human"]
    vstate = {**state, **upd, "validation": records, "validation_mode": "priors",
              "log": (state.get("log") or []) + [f"prior validator: {len(records)} items adjudicated against corpus priors "
                                                  f"({len(lib.markets)} past markets)"]}
    vstate["numbers"] = compute_all(vstate)
    finalize_notes(vstate, records)
    after = features(__import__("analyst_fleet.prior_validator", fromlist=["view_from_state"]).view_from_state(vstate))

    # ---- held-out check: does the (never used) human sheet sit inside the learned bands?
    human_feats = {}
    try:
        human_feats = features(view_from_human(tax, cmp.read_human(tax)))
    except Exception as e:                 # no human sheet for this market: that is the normal case
        syslog.event("validate.no_human", reason=str(e)[:200])
    rows = []
    for f, c in chk["checks"].items():
        h = human_feats.get(f)
        rows.append({"feature": f, **c, "agent_after": after.get(f), "human": h,
                     "human_inside": (c["lo"] <= h <= c["hi"]) if h is not None else None})
    vstate["prior_check"] = rows

    name = f"Input Sheet - {tax.market_name} (prior-validated).xlsx"
    write_workbook(vstate, run_dir / name)
    dump_state(vstate, run_dir / "state_priorval.json")

    # ---- report
    g0, g = state["numbers"]["geo"], vstate["numbers"]["geo"]
    tally = Counter(r.get("verdict") for r in records)
    hin = [r for r in rows if r["human"] is not None]
    pf = lambda f, x: "n/a" if x is None else f"{x:.2f}" if f in ("seg.n", "seg.hhi", "seg.small_fast") else (f"{x * 100:+.2f} pp" if ("gap" in f or "sd" in f or "spread" in f or "dev" in f)
                                              else f"{x:.1%}" if "share" in f else f"{x:.2%}")
    L = [f"# Prior-based validation of {run_dir.name} (no human sheet used)", "",
         f"- Corpus: {len(lib.markets)} past markets, {sum(r['status'] == 'kept' for r in rules)} verified rules",
         f"- Target classified as **{target['class'].get('category')} / {target['class'].get('offering')}**; "
         f"closest analogs: {', '.join(a[1] for a in analogs[:6])}",
         f"- Features checked: {len(rows)}; outside the learned band: {len(items)} -> verdicts: agent {tally['agent']}, "
         f"prior {tally['prior']}, between {tally['between']}",
         f"- Global 2025 {g0['value'][tax.root]:.1f} -> {g['value'][tax.root]:.1f} US$ Mn (absolute size is never set by priors); "
         f"CAGR {g0['cagr'][tax.root]:.2%} -> {g['cagr'][tax.root]:.2%}"]
    if hin:
        L.append(f"- Held-out check: the human gearbox sheet (never used above) sits inside the learned band on "
                 f"**{sum(r['human_inside'] for r in hin)} of {len(hin)}** features; the agent sheet did on "
                 f"{sum(not r['outside'] for r in hin)} of {len(hin)}")
    L += ["", "| Feature | Band p10 - p90 (source, n) | Agent before | Agent after | Human (held out) |", "|---|---|---|---|---|"]
    for r in rows:
        flag = " **outside**" if r["outside"] else ""
        L.append(f"| {r['feature']} | {pf(r['feature'], r['p10'])} - {pf(r['feature'], r['p90'])} ({r['source']}, {r['n']}) | "
                 f"{pf(r['feature'], r['agent'])}{flag} | {pf(r['feature'], r['agent_after'])} | {pf(r['feature'], r['human'])} |")
    for r in records:
        L += ["", f"## {r['id']} - verdict: {r.get('verdict')} (weight on prior {r['weight_on_human']:.2f})",
              f"- Why the agent may be wrong: {r.get('why_agent_may_be_wrong', '')}",
              f"- Why the prior may not apply: {r.get('why_human_may_be_wrong', '')}",
              f"- Final-number reasoning (also written next to the number in the sheet): {r.get('validated_reasoning', '')}"]
    (run_dir / "priors_validation.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:8]))
    syslog.event("validate.end", mode="priors", items=len(records), verdicts=dict(tally),
                 human_inside=f"{sum(r['human_inside'] for r in hin)}/{len(hin)}" if hin else "n/a",
                 global_cagr=round(g["cagr"][tax.root], 4), llm_live=state["llm"].usage["calls"],
                 llm_cached=state["llm"].usage["cached"])
    if human_feats:
        cmp.main(run_dir, "state_priorval.json", "_priors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
