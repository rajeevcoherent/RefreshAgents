"""Generate the Input Sheet with the opinionated-analyst fleet.

    python run_input_sheet.py                      # blind run (publisher's own report excluded)
    python run_input_sheet.py --allow-publisher    # let the publisher's report into the evidence
    python run_input_sheet.py --no-web --passes 1  # offline / quick

Outputs go to outputs/<run_id>/: the Input Sheet xlsx, state.json, summary.md, run.log.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from analyst_fleet.config import Config
from analyst_fleet.evidence.definitions import definitions_text, scope_rules, segment_definitions
from analyst_fleet.evidence.oem import load_oem
from analyst_fleet.evidence.web import Web
from analyst_fleet.graph import build_graph
from analyst_fleet.llm import LLM
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.writer import dump_state, summary_md, write_workbook
from analyst_fleet import syslog


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--allow-publisher", action="store_true", help="do not block the publisher's own report")
    ap.add_argument("--no-web", action="store_true", help="skip web research")
    ap.add_argument("--passes", type=int, default=None, help="max review passes (default 2)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-opinions", action="store_true", help="do not give analysts the learned house judgement")
    args = ap.parse_args()

    cfg = Config(blind=not args.allow_publisher, web=not args.no_web, use_opinions=not args.no_opinions)
    if args.passes:
        cfg.max_passes = args.passes
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S") + ("-blind" if cfg.blind else "-open")
    out_dir = cfg.out_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(out_dir / "run.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    for noisy in ("httpx", "openai", "primp", "ddgs", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    syslog.setup(cfg.out_dir.parent / "system.log", run_id)
    syslog.event("run.start", run_id=run_id, blind=cfg.blind, web=cfg.web, max_passes=cfg.max_passes,
                 reasoning_model=cfg.reasoning_model, fast_model=cfg.fast_model, opinions=cfg.use_opinions, blocklist=list(cfg.blocklist))
    tax = Taxonomy(cfg.taxonomy_path)
    xlsx = out_dir / f"Input Sheet - {tax.market_name}.xlsx"

    def write_node(state):
        write_workbook(state, xlsx)
        dump_state(state, out_dir / "state.json")
        (out_dir / "summary.md").write_text(summary_md(state), encoding="utf-8")
        return {"out_path": str(xlsx)}

    state = {
        "cfg": cfg, "tax": tax, "llm": LLM(cfg), "web": Web(cfg), "oem": load_oem(cfg.oem_dir, tax),
        "definitions_text": definitions_text(cfg.definitions_docx),
        "oem_definitions_text": definitions_text(cfg.oem_definitions_docx),
        "segment_definitions": segment_definitions(cfg.definitions_docx),
        "scope_rules": scope_rules(cfg.definitions_docx, cfg.oem_definitions_docx),
        "pass_n": 1, "log": [],
    }
    try:
        final = build_graph(write_node).invoke(state, config={"recursion_limit": 60})
    except Exception as e:
        syslog.event("run.failed", error=f"{type(e).__name__}: {e}")
        raise
    u = final["llm"].usage
    syslog.event("run.end", out=final["out_path"], llm_live=u["calls"], llm_cached=u["cached"],
                 prompt_tokens=u["prompt_tokens"], completion_tokens=u["completion_tokens"],
                 sources_blocked=final.get("blocked_sources", 0))
    print(f"\nInput Sheet: {final['out_path']}")
    print(f"LLM calls live/cached: {u['calls']}/{u['cached']}; tokens {u['prompt_tokens']:,} in / {u['completion_tokens']:,} out")
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
