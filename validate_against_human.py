"""Validator agent: adjudicate the agent fleet's Input Sheet against the human ME workbooks.

    python validate_against_human.py outputs/<run_id>

For every stark disagreement (global size/CAGR, region and country shares/CAGRs, segment
mix per country group) an independent validation lead decides whether the agent or the
human number is more defensible, says exactly why the other is likely wrong, and sets a
weight on the human number. Code moves the number (agent + w x (human - agent)),
re-runs the normal arithmetic and writes, next to the untouched blind sheet:
  Input Sheet - <market> (validated).xlsx   (adds a 'Validation' sheet)
  state_validated.json, validation.md, comparison_validated.md/.xlsx
Every verdict is also written to system.log.
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
from analyst_fleet.evidence.definitions import scope_rules, segment_definitions
from analyst_fleet.evidence.oem import load_oem
from analyst_fleet.llm import LLM
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.validator import adjudicate, apply, finalize_notes, find_items
from analyst_fleet.writer import dump_state, write_workbook
import compare_with_human as cmp


def main(run_dir: Path) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(run_dir / "validate.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    tax = Taxonomy(cfg.taxonomy_path)
    syslog.setup(cfg.out_dir.parent / "system.log", f"{run_dir.name}:validate")
    syslog.event("validate.start", run=run_dir.name)

    saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    best = saved.get("best_pass")
    review = next((r for r in saved.get("reviews") or [] if r.get("pass") == best), {})
    state = {**saved, "cfg": cfg, "tax": tax, "llm": LLM(cfg), "oem": load_oem(cfg.oem_dir, tax), "segment_definitions": segment_definitions(cfg.definitions_docx), "scope_rules": scope_rules(cfg.definitions_docx, cfg.oem_definitions_docx), "review": review}
    state["numbers"] = compute_all(state)

    human = cmp.read_human(tax)
    items = find_items(state, human, cmp.human_mix)
    syslog.event("validate.items", n=len(items), by_kind=dict(Counter(i["kind"] for i in items)))
    print(f"{len(items)} stark disagreements: {dict(Counter(i['kind'] for i in items))}")

    verdicts = adjudicate(state, items)
    upd, records = apply(state, items, verdicts, human, cmp.human_mix)
    vstate = {**state, **upd, "validation": records,
              "log": (state.get("log") or []) + [f"validator: {len(records)} items adjudicated against the human sheet"]}
    vstate["numbers"] = compute_all(vstate)
    finalize_notes(vstate, records)
    for a in vstate["numbers"]["adjustments"]:
        syslog.event("restriction.adjust", stage=f"validated/{a['stage']}", kind=a["kind"], scope=a["scope"], detail=a["detail"])

    name = f"Input Sheet - {tax.market_name} (validated).xlsx"
    write_workbook(vstate, run_dir / name)
    dump_state(vstate, run_dir / "state_validated.json")

    # ---- report
    g = vstate["numbers"]["geo"]
    tally = Counter(r.get("verdict") for r in records)
    lines = [f"# Validation of {run_dir.name} against the human ME", "",
             f"- Disputed items: {len(records)} - verdicts: agent {tally['agent']}, human {tally['human']}, between {tally['between']}",
             f"- Global 2025: agent {state['numbers']['geo']['value'][tax.root]:.1f} -> validated **{g['value'][tax.root]:.1f}** US$ Mn "
             f"(human {human[tax.root]['v25']:.1f}); CAGR {state['numbers']['geo']['cagr'][tax.root]:.2%} -> **{g['cagr'][tax.root]:.2%}**", ""]
    for r in records:
        if r["kind"] in ("global_value", "global_cagr") or r["kind"] == "geo_share" and tax.parent_of.get(r["node"]) == tax.root:
            f = (lambda v: f"{v:,.1f}") if r["kind"] == "global_value" else (lambda v: f"{v:.2%}")
            lines += [f"## {r['id']} - verdict: {r.get('verdict')} (weight on human {r['weight_on_human']:.2f})",
                      f"agent {f(r['agent'])} | human {f(r['human'])} | validated {f(r['validated'])}", "",
                      f"- Why the agent may be wrong: {r.get('why_agent_may_be_wrong', '')}",
                      f"- Why the human may be wrong: {r.get('why_human_may_be_wrong', '')}",
                      f"- Decisive: {'; '.join(r.get('decisive_factors') or [])}",
                      f"- Validated reasoning: {r.get('validated_reasoning', '')}", ""]
    lines.append("All items, including countries and segments, are in the 'Validation' sheet of the validated workbook.")
    (run_dir / "validation.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:6]))
    syslog.event("validate.end", items=len(records), verdicts=dict(tally),
                 global_value=round(g["value"][tax.root], 2), global_cagr=round(g["cagr"][tax.root], 4),
                 llm_live=state["llm"].usage["calls"], llm_cached=state["llm"].usage["cached"])

    cmp.main(run_dir, "state_validated.json", "_validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
