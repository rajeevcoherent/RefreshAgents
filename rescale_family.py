"""Tie sibling markets (one parent market split by component) to one consistent 2025 total.

    python rescale_family.py --total 3180 --basis "..." DIR=0.68 DIR=0.19 DIR=0.13 --out outputs/STM_rescaled [--dry]

Each finished run is rescaled by one factor: global 2025 value -> total x share. Every region, country and segment
value scales by the same factor; every share, CAGR and price is unchanged (verified by code before writing).
Every plain-language note is rewritten (old notes quote the old US$ values); --reuse-notes keeps an earlier
rescaled sheet's notes and rewrites only those quoting a pre-rescale value
(--dry: no LLM calls, notes left empty - for checking the numbers at US$ 0).
Output: a new folder; the original sheets are not modified.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import sys
from dataclasses import replace
from pathlib import Path

from analyst_fleet import compute, syslog
from analyst_fleet.config import Config
from analyst_fleet.llm import LLM
from analyst_fleet.taxonomy import Taxonomy
from analyst_fleet.writer import dump_state, summary_md, write_workbook
from growth_correction import assert_locked, plain_from_workbook

ROOT = Path(__file__).resolve().parent


def locked_view(state: dict) -> dict:
    n = state["numbers"]
    return {"share": n["geo"]["share"], "cagr": n["geo"]["cagr"], "seg": n["seg"]["shares"], "asp": n["price"]["asp"]}


def rescale(src: Path, target: float, share: float, total: float, basis: str, out_root: Path, dry: bool,
            reuse: bool = False) -> None:
    spec = json.loads((src / "spec.json").read_text(encoding="utf-8"))
    out_dir = out_root / src.name
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in ("spec.json", "taxonomy.json"):
        (out_dir / f).write_text((src / f).read_text(encoding="utf-8"), encoding="utf-8")
    cfg = replace(Config(), market_name=spec["title"], taxonomy_path=out_dir / "taxonomy.json", generic=True,
                  plain_language=not dry)
    llm = LLM(cfg)                                   # with plain_language off (--dry) the writer makes no LLM call
    llm.run_label = f"rescale: {spec['title']}"
    saved = json.loads((src / "state.json").read_text(encoding="utf-8"))
    tax = Taxonomy(out_dir / "taxonomy.json")
    state = {**saved, "cfg": cfg, "tax": tax, "llm": llm, "oem": {}, "segment_definitions": spec["segment_notes"],
             "scope_rules": spec["scope_rules"]}
    state["numbers"] = compute.compute_all(state)
    before, old_val = copy.deepcopy(locked_view(state)), copy.deepcopy(state["numbers"]["geo"]["value"])

    sz = state["sizing"]
    g0 = sz["global_value_2025"]
    factor = target / g0
    note = (f"[Rescaled to one consistent total: {basis}. This market = {share:.0%} of US$ {total:,.0f} Mn = "
            f"US$ {target:,.1f} Mn (independent estimate was US$ {g0:,.1f} Mn, factor {factor:.4f}); all shares, CAGRs "
            f"and prices unchanged.] ")
    sz["rescale"] = {"original_usd_mn": g0, "target_usd_mn": target, "factor": factor, "basis": basis,
                     "family_total_usd_mn": total, "family_share": share}
    sz["global_value_2025"] = target
    # growth text quotes the pre-rescale global value: mark it so no note presents it as the current base
    nums = sorted({f"{g0:,.2f}", f"{g0:,.1f}", f"{g0:,.0f}", f"{g0:.2f}", f"{g0:.1f}", f"{g0:.0f}"}, key=len, reverse=True)
    bns = sorted({f"{g0 / 1000:.2f}", f"{g0 / 1000:.1f}"}, key=len, reverse=True)
    old_re = re.compile(r"(?:US\$|USD|\$)\s?(?:(?:%s)\s?(?:Mn|mn|million|M)\b|(?:%s)\s?(?:Bn|bn|billion|B)\b)"
                        % ("|".join(map(re.escape, nums)), "|".join(map(re.escape, bns))))
    mark = f" (pre-rescale estimate; the sheet's 2025 value is US$ {target:,.1f} Mn)"

    def mark_old(x):
        if isinstance(x, str):
            return old_re.sub(lambda mt: mt.group(0) + mark, x)
        if isinstance(x, dict):
            return {kk: mark_old(vv) for kk, vv in x.items()}
        if isinstance(x, list):
            return [mark_old(vv) for vv in x]
        return x
    for k in [k for k in sz if k not in ("rescale", "global_value_2025")]:
        sz[k] = mark_old(sz[k])
    for k in ("step2", "step3"):
        if isinstance(sz.get(k), dict) and sz[k].get("reasoning"):
            sz[k]["reasoning"] = note + sz[k]["reasoning"]
    state["numbers"] = compute.compute_all(state)

    assert_locked(before, locked_view(state))
    new_val = state["numbers"]["geo"]["value"]
    for k, v in old_val.items():
        assert abs(new_val[k] - v * factor) <= 1e-6 * max(1.0, abs(v)), f"value of {k} did not scale by the factor"
    # analysts' reasoning text quotes 2025 geography values in many formats ("US$ 363.23 Mn", "US$482.64 million",
    # "$83.53 million", "US$ 1.86 Bn"): swap each for its rescaled value
    geo_rx = []
    for k, v in old_val.items():
        if v < 1:
            continue
        nums = {f"{v:,.2f}", f"{v:.2f}"} | ({f"{v:,.1f}", f"{v:.1f}"} if v >= 20 else set())
        rx = r"(?:US\$|USD|\$)\s?(%s)\s?(?:Mn|mn|million|M)\b" % "|".join(map(re.escape, sorted(nums, key=len, reverse=True)))
        if v >= 1000:
            rx += r"|(?:US\$|USD|\$)\s?(%s)\s?(?:Bn|bn|billion|B)\b" % re.escape(f"{v / 1000:.2f}")
        geo_rx.append((re.compile(rx), new_val[k]))

    def swap(mt, nv):
        num_ = mt.group(1) or mt.group(2)
        dec = len(num_.split(".")[1]) if "." in num_ else 0
        return f"US$ {nv:,.{max(dec, 1)}f} Mn" if mt.group(1) else f"US$ {nv / 1000:,.2f} Bn"
    skip = {"sizing", "facts", "log", "numbers", "plain_texts", "cfg", "tax", "llm", "oem"}

    def fix(x):
        if isinstance(x, str):
            for rx_, nv in geo_rx:
                x = rx_.sub(lambda mt: swap(mt, nv), x)
            return x
        if isinstance(x, dict):
            return {kk: fix(vv) for kk, vv in x.items()}
        if isinstance(x, list):
            return [fix(vv) for vv in x]
        return x
    for key in [k for k in state if k not in skip]:
        state[key] = fix(state[key])
    state["numbers"] = compute.compute_all(state)
    assert_locked(before, locked_view(state))
    xlsx = out_dir / f"Input Sheet - {spec['title']}.xlsx"
    # notes: with --reuse-notes, keep the notes of an earlier rescaled sheet unless they quote any pre-rescale US$
    # value (those are rewritten); otherwise rewrite every note (old notes quote the old US$ values)
    keep = {}
    if reuse and xlsx.exists():
        from analyst_fleet.cell_reasoning import explain_all
        prev = plain_from_workbook(xlsx, explain_all(state, include_asp=True))
        keep = {k_: e for k_, e in prev.items()
                if not any(rx_.search(e["text"]) for rx_, _ in geo_rx) and not old_re.search(e["text"])}
        print(f"  notes reused {len(keep)}, to rewrite {len(prev) - len(keep)} (quote a pre-rescale value)")
    state["plain_texts"] = keep
    state["log"] = (state.get("log") or []) + [note.strip()]
    write_workbook(state, xlsx)
    assert_locked(before, locked_view(state))
    dump_state(state, out_dir / "state.json")
    (out_dir / "summary.md").write_text(summary_md(state), encoding="utf-8")
    cost = llm.usage["cost_usd"]
    print(f"{spec['title']}: US$ {g0:,.1f} Mn -> US$ {target:,.1f} Mn (x {factor:.4f}); shares/CAGRs/prices verified "
          f"unchanged; cost US$ {cost:.3f}\n  {xlsx}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("members", nargs="+", help="RUN_DIR=SHARE")
    ap.add_argument("--total", type=float, required=True, help="family 2025 total, US$ Mn")
    ap.add_argument("--basis", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reuse-notes", action="store_true",
                    help="keep notes of the existing rescaled sheet in --out; rewrite only those quoting pre-rescale values")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    syslog.setup(ROOT / "system.log", "rescale")
    members = [(Path(m.rsplit("=", 1)[0]), float(m.rsplit("=", 1)[1])) for m in args.members]
    assert abs(sum(s for _, s in members) - 1) < 1e-6, "shares must add to 1"
    for d, s in members:
        rescale(d, args.total * s, s, args.total, args.basis, args.out, args.dry, args.reuse_notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
