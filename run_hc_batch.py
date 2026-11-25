"""Generate Input Sheets for every market in a client scope sheet (title / definition / segmentation / exclusions).

    python run_hc_batch.py "C:/path/HC titles for automation.xlsx"                  # every market
    python run_hc_batch.py "C:/path/HC titles for automation.xlsx" --only tirzepatide
    python run_hc_batch.py SHEET --list                                             # show parsed markets, no LLM

Each market runs the full analyst fleet in generic mode (no OEM workbooks; scope from the sheet) and writes
outputs/HC/<market-slug>/: Input Sheet - <market>.xlsx, taxonomy.json, spec.json, state.json, summary.md, run.log.
outputs/HC/index.md lists every market with its status and file.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from analyst_fleet import syslog
from analyst_fleet.config import Config
from analyst_fleet.corpus.opinion import classify_dims
from analyst_fleet.evidence.web import Web
from analyst_fleet.graph import build_graph
from analyst_fleet.llm import LLM, is_peak
from analyst_fleet.market_spec import load_sheet, strategies
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.writer import dump_state, summary_md, write_workbook

ROOT = Path(__file__).resolve().parent


def _same_spec(out_dir: Path, spec: dict) -> bool:
    """The saved run was made from this exact sheet row (title, definition, segmentation, exclusions)."""
    f = out_dir / "spec.json"
    if not f.exists():
        return False
    old = json.loads(f.read_text(encoding="utf-8"))
    keys = ("title", "definition", "segmentation_text", "exclusions")
    return all(old.get(k) == spec.get(k) for k in keys)       # geography of the saved run is kept as it was


def reuse_market(spec: dict, out_dir: Path) -> dict | None:
    """Already generated from this sheet row -> reuse it; no DeepSeek call. A missing workbook is rebuilt from the
    saved state in cache-only mode (plain-language notes are stored with the run), so this path never spends."""
    state_f = out_dir / "state.json"
    if not (state_f.exists() and _same_spec(out_dir, spec)):
        return None
    saved = json.loads(state_f.read_text(encoding="utf-8"))
    if not saved.get("numbers"):
        return None
    xlsx = out_dir / f"Input Sheet - {spec['title']}.xlsx"
    if not xlsx.exists():
        cfg = replace(Config(), market_name=spec["title"], taxonomy_path=out_dir / "taxonomy.json", generic=True,
                      cache_only=True)
        from analyst_fleet.compute import compute_all
        state = {**saved, "cfg": cfg, "tax": Taxonomy(out_dir / "taxonomy.json"), "llm": LLM(cfg), "oem": {},
                 "segment_definitions": spec["segment_notes"], "scope_rules": spec["scope_rules"]}
        state["numbers"] = compute_all(state)
        write_workbook(state, xlsx)
        dump_state(state, state_f)
    geo = saved["numbers"]["geo"]
    root = next(iter(spec["taxonomy"]["geography"]))
    rv = next((r.get("overall") for r in saved.get("reviews") or [] if r.get("pass") == saved.get("best_pass")), None)
    syslog.event("hc.market.reused", market=spec["title"], file=str(xlsx))
    return {"market": spec["title"], "status": "done (reused saved result - no DeepSeek spend)", "file": str(xlsx),
            "value_2025": geo["value"][root], "cagr": geo["cagr"][root], "review": rv, "cost_usd": 0.0, "minutes": 0.0}


def run_market(spec: dict, base_cfg: Config, out_root: Path, own_log: bool = True, priors: bool = True,
               force: bool = False) -> dict:
    out_dir = out_root / spec["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    if not force:
        try:
            r = reuse_market(spec, out_dir)
            if r:
                return r
        except Exception as e:                           # a broken saved run is regenerated, never silently trusted
            logging.getLogger("hc").warning("saved run for %s not reusable (%s); regenerating", spec["title"], e)
    handler = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"))
    if own_log:            # with --parallel the shared outputs/HC/batch.log is used (per-call trace keeps the market)
        logging.getLogger().addHandler(handler)
    t0 = time.time()
    try:
        tax_path = out_dir / "taxonomy.json"
        tax_raw = spec["taxonomy"]
        tax_path.write_text(json.dumps(tax_raw, indent=1, ensure_ascii=False), encoding="utf-8")
        cfg = replace(base_cfg, market_name=spec["title"], taxonomy_path=tax_path, generic=True)
        llm = LLM(cfg)
        # 2B / 2C roles of every dimension (fast model, cached) -> the 2A-2B-2C Definitions sheet
        roles = classify_dims(llm, cfg.market_name, {d["title"]: [s["name"] for s in d["segments"]]
                                                     for d in tax_raw["dimensions"]})
        tax_raw["strategies"] = strategies(tax_raw, roles)
        tax_path.write_text(json.dumps(tax_raw, indent=1, ensure_ascii=False), encoding="utf-8")
        (out_dir / "spec.json").write_text(json.dumps({k: v for k, v in spec.items() if k != "taxonomy"}, indent=1,
                                                      ensure_ascii=False), encoding="utf-8")
        tax = Taxonomy(tax_path)
        xlsx = out_dir / f"Input Sheet - {tax.market_name}.xlsx"

        def write_node(state):
            dump_state(state, out_dir / "state.json")     # first: a writer error must not lose the analysis
            if priors:                                     # house priors from 72 past MEs, before anything is written
                try:
                    from analyst_fleet.prior_check import run_priors
                    state = run_priors(state)
                    dump_state(state, out_dir / "state.json")
                except Exception as e:                     # validation must never cost the sheet
                    logging.getLogger("hc").exception("prior validation failed: %s", e)
                    syslog.event("priors.failed", market=spec["title"], error=f"{type(e).__name__}: {e}")
            write_workbook(state, xlsx)
            dump_state(state, out_dir / "state.json")
            (out_dir / "summary.md").write_text(summary_md(state), encoding="utf-8")
            return {"out_path": str(xlsx), "numbers": state["numbers"], **{k: state[k] for k in ("prior_check", "validation", "validation_mode") if k in state}}

        syslog.event("hc.market.start", market=spec["title"], leaves=len(tax.leaves),
                     dims=[d.title for d in tax.dimensions], roles=roles)
        state = {"cfg": cfg, "tax": tax, "llm": llm, "web": Web(cfg), "oem": {},
                 "definitions_text": spec["definitions_text"], "oem_definitions_text": "",
                 "segment_definitions": spec["segment_notes"], "scope_rules": spec["scope_rules"],
                 "pass_n": 1, "log": []}
        final = build_graph(write_node).invoke(state, config={"recursion_limit": 60})
        u = final["llm"].usage
        geo = final["numbers"]["geo"]
        res = {"market": spec["title"], "status": "done", "file": str(xlsx),
               "value_2025": geo["value"][tax.root], "cagr": geo["cagr"][tax.root],
               "review": next((r.get("overall") for r in final.get("reviews") or [] if r.get("pass") == final.get("best_pass")), None),
               "prior_items": len((final.get("prior_check") or {}).get("outside") or []),
               "llm_calls": u["calls"], "cost_usd": round(u.get("cost_usd", 0.0), 3),
               "reasoning_tokens": u.get("reasoning_tokens", 0), "cache_hit_tokens": u.get("cache_hit_tokens", 0),
               "minutes": round((time.time() - t0) / 60, 1)}
        syslog.event("hc.market.end", **res)
        return res
    except Exception as e:                               # one market failing must not stop the batch
        logging.getLogger("hc").exception("market failed: %s", spec["title"])
        syslog.event("hc.market.failed", market=spec["title"], error=f"{type(e).__name__}: {e}")
        return {"market": spec["title"], "status": f"failed: {type(e).__name__}: {e}", "file": "",
                "minutes": round((time.time() - t0) / 60, 1)}
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def write_index(out_root: Path, results: list[dict]) -> None:
    lines = ["# HC Input Sheets", "", "| Market | Status | 2025 US$ Mn | CAGR 2025-32 | Review | Minutes | API cost US$ | File |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['market']} | {r['status']} | {r.get('value_2025', 0):,.1f} | {r.get('cagr', 0):.2%} | "
                     f"{r.get('review', '')} | {r.get('minutes', '')} | {r.get('cost_usd', 'n/a')} | {r.get('file', '')} |")
    (out_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--only", default=None, help="run only markets whose title contains this text (comma-separated)")
    ap.add_argument("--list", action="store_true", help="print the parsed markets and exit")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--passes", type=int, default=None)
    ap.add_argument("--no-priors", action="store_true", help="skip the house-prior validation before writing")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if this market was already generated from the same sheet row "
                         "(default: reuse the saved result, no DeepSeek spend)")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "HC")
    ap.add_argument("--parallel", type=int, default=1,
                    help="markets run at the same time (default 1). DeepSeek allows 500 concurrent v4-pro requests per "
                         "account; each market uses up to ~12 at once")
    ap.add_argument("--offpeak", action="store_true",
                    help="start each market only in DeepSeek off-peak hours (half price; peak = 01:00-04:00 and "
                         "06:00-10:00 UTC Mon-Fri = 06:30-09:30 and 11:30-15:30 IST)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai", "primp", "ddgs", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    specs = load_sheet(args.sheet)
    if args.only:
        keys = [k.strip().lower() for k in args.only.split(",")]
        specs = [s for s in specs if any(k in s["title"].lower() for k in keys)]
    if args.list:
        for s in specs:
            t = s["taxonomy"]
            print(f"- {s['title']}  [{s['slug']}]  geography root: {next(iter(t['geography']))}; dimensions: "
                  + ", ".join(f"{d['title']} ({len(d['segments'])})" for d in t["dimensions"]))
        return 0
    base = Config(web=not args.no_web)
    if args.passes:
        base.max_passes = args.passes
    args.out.mkdir(parents=True, exist_ok=True)
    syslog.setup(ROOT / "system.log", "hc-batch")
    prev = {}
    if (args.out / "results.json").exists():
        prev = {r["market"]: r for r in json.loads((args.out / "results.json").read_text(encoding="utf-8"))}
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    par = max(1, args.parallel)
    if par > 1:
        bh = logging.FileHandler(args.out / "batch.log", encoding="utf-8")
        bh.setFormatter(logging.Formatter("%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(bh)

    def one(s):
        while args.offpeak and is_peak():
            print("DeepSeek peak hours - waiting 5 min for the off-peak (half price) window", flush=True)
            time.sleep(300)
        print(f"\n=== {s['title']} ===", flush=True)
        r = run_market(s, base, args.out, own_log=par == 1, priors=not args.no_priors, force=args.force)
        with lock:
            prev[s["title"]] = r
            results = list(prev.values())
            (args.out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
            write_index(args.out, results)
        print(f"-> {s['title']}: {r['status']}  {r.get('file', '')}", flush=True)

    with ThreadPoolExecutor(max_workers=par, thread_name_prefix="market") as ex:
        list(ex.map(one, specs))
    print(f"\nIndex: {args.out / 'index.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
