"""Rewrite a run's Input Sheet from its saved state (no analyst calls; only the plain-language editor runs).

    python rebuild_workbook.py outputs/<run_id> [state.json | state_priorval.json | state_validated.json]
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.compute import compute_all
from analyst_fleet.config import Config
from analyst_fleet.evidence.definitions import scope_rules, segment_definitions
from analyst_fleet.evidence.oem import load_oem
from analyst_fleet.llm import LLM
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.writer import write_workbook

SUFFIX = {"state.json": "", "state_priorval.json": " (prior-validated)", "state_validated.json": " (validated)"}


def main(run_dir: Path, state_file: str = "state.json") -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    tax = Taxonomy(cfg.taxonomy_path)
    syslog.setup(cfg.out_dir.parent / "system.log", f"{run_dir.name}:rebuild")
    saved = json.loads((run_dir / state_file).read_text(encoding="utf-8"))
    best = saved.get("best_pass")
    review = next((r for r in saved.get("reviews") or [] if r.get("pass") == best), {})
    state = {**saved, "cfg": cfg, "tax": tax, "llm": LLM(cfg), "oem": load_oem(cfg.oem_dir, tax), "review": review,
             "segment_definitions": segment_definitions(cfg.definitions_docx),
             "scope_rules": scope_rules(cfg.definitions_docx, cfg.oem_definitions_docx)}
    state["numbers"] = compute_all(state)
    out = run_dir / f"Input Sheet - {tax.market_name}{SUFFIX.get(state_file, '')}.xlsx"
    write_workbook(state, out)
    syslog.event("rebuild.end", file=out.name, llm_live=state["llm"].usage["calls"], llm_cached=state["llm"].usage["cached"])
    print(f"wrote {out}  (LLM live {state['llm'].usage['calls']}, cached {state['llm'].usage['cached']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), *(sys.argv[2:3])))
