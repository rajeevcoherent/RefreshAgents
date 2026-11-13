"""Compare an agent-generated Input Sheet run against the human aftermarket ME workbooks.

    python compare_with_human.py outputs/<run_id>

Reads the human ME workbooks in the project root (never used during generation) and
the run's state.json, and writes comparison.xlsx + comparison.md into the run folder:
  - geography: 2025 value, share of parent, CAGR 2025-32 (human computed from its yearly values)
  - segments: base-year share (share-of-parent convention) and segment CAGR per country
  - pricing: the human aftermarket ME carries no price/volume (all zero), so the agent's
    service price is checked against OEM new-unit prices instead (ratio), for sanity only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from analyst_fleet.config import Config
from analyst_fleet.taxonomy import Taxonomy

ROOT = Path(__file__).resolve().parent
Y0 = 4          # column E (2020) zero-based in values_only rows
I25, I32 = Y0 + 5, Y0 + 12


def _n(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_human(tax: Taxonomy) -> dict[str, dict]:
    """geo -> {'v25', 'v32', 'seg': {seg: (v25, v32)}, 'children': {child: v25}}"""
    seg_names = {_n(s).lower(): s for d in tax.dimensions for s in d.all_segments}
    out: dict[str, dict] = {}
    for f in sorted(ROOT.glob("*Aftermarket Market.xlsx")):
        wb = load_workbook(f, read_only=True, data_only=True)
        for ws in wb.worksheets:
            name = _n(ws.title)
            if name in out:
                continue
            rows = list(ws.iter_rows(min_row=1, max_row=80, max_col=17, values_only=True))
            rec = {"seg": {}, "children": {}, "file": f.name}
            in_block, first = False, None
            for r in rows:
                c, d = _n(r[2]), _n(r[3])
                if "Value in" in d and "v25" not in rec:
                    rec["v25"], rec["v32"] = float(r[I25] or 0), float(r[I32] or 0)
                    rec["v20"] = float(r[Y0] or 0)
                    continue
                if c.startswith("Region_"):
                    in_block = True
                    continue
                if c in ("Price", "Volume"):
                    break
                if in_block:
                    if not d or d == "0" or not isinstance(r[I25], (int, float)):
                        continue
                    if first is None:
                        first = d
                        continue
                    rec["children"][d] = float(r[I25])
                    continue
                key = seg_names.get(d.lower())
                if key and key not in rec["seg"] and isinstance(r[I25], (int, float)):
                    rec["seg"][key] = (float(r[I25]), float(r[I32] or 0))
            if "v25" in rec:
                out[name] = rec
        wb.close()
    return out


def cagr(a, b, n=7):
    return (b / a) ** (1 / n) - 1 if a and b and a > 0 and b > 0 else None


def human_mix(dim, segvals: dict) -> dict[str, float]:
    tot = sum(segvals.get(s, (0, 0))[0] for s in dim.top) or 1.0
    out = {}
    for s in dim.top:
        out[s] = segvals.get(s, (0, 0))[0] / tot
        kids = dim.children.get(s, ())
        kt = sum(segvals.get(k, (0, 0))[0] for k in kids) or 1.0
        for k in kids:
            out[k] = segvals.get(k, (0, 0))[0] / kt
    return out


def main(run_dir: Path, state_file: str = "state.json", tag: str = "") -> int:
    cfg = Config()
    tax = Taxonomy(cfg.taxonomy_path)
    st = json.loads((run_dir / state_file).read_text(encoding="utf-8"))
    geo, seg, price = st["numbers"]["geo"], st["numbers"]["seg"], st["numbers"]["price"]
    hum = read_human(tax)

    wb = Workbook()
    red = PatternFill("solid", fgColor="FFC7CE")
    green = PatternFill("solid", fgColor="C6EFCE")

    # ---------------------------------------------------------------- geography
    ws = wb.active
    ws.title = "Geography"
    ws.append(["Geography", "Parent", "Human 2025 US$ Mn", "Agent 2025 US$ Mn", "Value diff %",
               "Human share of parent", "Agent share of parent", "Share diff (pp)",
               "Human CAGR 25-32", "Agent CAGR 25-32", "CAGR diff (pp)"])
    geo_rows = []
    nodes = [tax.root] + [k for p in tax.parent_nodes for k in tax.children_of[p]]
    for g in nodes:
        h = hum.get(g)
        p = tax.parent_of.get(g)
        # value: the parent's block wins (the Global file is internally consistent; one
        # regional workbook - NA - has stale country sheets); fall back to the own sheet
        hv = hum[p]["children"].get(g) if (p and hum.get(p) and g in hum[p]["children"]) else (h["v25"] if h else None)
        hp = hum.get(p, {}).get("children", {}).get(g) if p else None
        hshare = hp / hum[p]["v25"] if (hp is not None and hum.get(p) and hum[p]["v25"]) else (1.0 if not p else None)
        hc = cagr(h["v25"], h["v32"]) if h else None
        av, ash, ac = geo["value"].get(g), geo["share"].get(g), geo["cagr"].get(g)
        row = [g, p or "", hv, av, (av / hv - 1) if hv else None, hshare, ash,
               (ash - hshare) * 100 if hshare is not None and ash is not None else None,
               hc, ac, (ac - hc) * 100 if hc is not None and ac is not None else None]
        ws.append(row)
        geo_rows.append(row)
    for r in ws.iter_rows(min_row=2):
        r[2].number_format = r[3].number_format = "#,##0.00"
        r[4].number_format = "0.0%"
        r[5].number_format = r[6].number_format = "0.0%"
        r[8].number_format = r[9].number_format = "0.00%"
        for c, lim in ((r[4], 0.25), (r[7], 5), (r[10], 1.5)):
            if isinstance(c.value, float):
                v = abs(c.value)
                c.fill = green if v <= lim else (red if v > 2 * lim else PatternFill())

    # ---------------------------------------------------------------- segments
    ss = wb.create_sheet("Segments")
    ss.append(["Geography", "Dimension", "Segment", "Human share", "Agent share", "Diff (pp)",
               "Human seg CAGR", "Agent seg CAGR", "CAGR diff (pp)"])
    seg_err: dict[str, list[tuple[float, float]]] = {}
    cagr_err: list[float] = []
    for d in tax.dimensions:
        for g in tax.leaves:
            h = hum.get(g)
            if not h or not h["seg"]:
                continue
            hm = human_mix(d, h["seg"])
            am = seg["shares"][d.title].get(g, {})
            for s, parent in d.rows:
                hsc = cagr(*h["seg"].get(s, (0, 0)))
                asc = seg["seg_cagr"][d.title].get(g, {}).get(s)
                diff = (am.get(s, 0) - hm.get(s, 0)) * 100
                ss.append([g, d.title, s, hm.get(s), am.get(s), diff, hsc, asc,
                           (asc - hsc) * 100 if hsc is not None and asc is not None else None])
                if parent is None:
                    seg_err.setdefault(d.title, []).append((abs(diff), geo["value"].get(g, 0)))
                if hsc is not None and asc is not None:
                    cagr_err.append(abs(asc - hsc) * 100)
    for r in ss.iter_rows(min_row=2):
        r[3].number_format = r[4].number_format = "0.0%"
        r[6].number_format = r[7].number_format = "0.00%"

    # ---------------------------------------------------------------- pricing
    ps = wb.create_sheet("Pricing")
    ps.append(["Note", "The human aftermarket ME has no price or volume (all zero). The agent's price = aftermarket "
                       "revenue per installed gearbox per year; shown against OEM new-unit price for sanity."])
    ps.append([])
    ps.append(["Application", "Agent global avg US$/unit-yr", "OEM new-unit price US$", "Ratio (annual service / new price)"])
    sz = st["sizing"]["bottom_up"]["by_application"]
    for a, r in sz.items():
        ps.append([a, r["spend_per_unit_usd"], r["oem_asp_usd"], r["spend_pct"]])
    ps.append([])
    ps.append(["Implied serviced installed base (units)", price["total_units"]])
    ps.append(["Sizing installed base (units)", st["sizing"]["bottom_up"]["installed_units"]])
    for c in "ABCD":
        ps.column_dimensions[c].width = 40
    for w in (ws, ss):
        w.column_dimensions["A"].width = 26
        w.column_dimensions["C"].width = 30
        w.freeze_panes = "B2"
        for c in w[1]:
            c.font = Font(bold=True)
    wb.save(run_dir / f"comparison{tag}.xlsx")

    # ---------------------------------------------------------------- summary
    def mae(rows, idx, filt=lambda r: True):
        v = [abs(r[idx]) for r in rows if isinstance(r[idx], float) and filt(r)]
        return sum(v) / len(v) if v else float("nan")

    g = geo_rows[0]
    reg = [r for r in geo_rows if r[1] == tax.root]
    leaf = [r for r in geo_rows if r[0] in tax.leaves]
    lines = [f"# Comparison vs human ME - {run_dir.name}{tag}", "",
             f"- Global 2025: human {g[2]:.1f} vs agent {g[3]:.1f} US$ Mn ({g[4]:+.1%}); CAGR human {g[8]:.2%} vs agent {g[9]:.2%}",
             f"- Region share MAE: {mae(reg, 7):.1f} pp; region CAGR MAE: {mae(reg, 10):.2f} pp",
             f"- Country share-of-parent MAE: {mae(leaf, 7):.1f} pp; country value MAPE: {mae(leaf, 4):.0%}; country CAGR MAE: {mae(leaf, 10):.2f} pp"]
    for dim, errs in seg_err.items():
        wsum = sum(w for _, w in errs) or 1
        lines.append(f"- {dim} top-level share error (value-weighted MAE across countries): "
                     f"{sum(e * w for e, w in errs) / wsum:.1f} pp")
    if cagr_err:
        lines.append(f"- Segment CAGR MAE: {sum(cagr_err) / len(cagr_err):.2f} pp")
    lines += ["", "| Region | Human share | Agent share | Human CAGR | Agent CAGR | Value diff |", "|---|---|---|---|---|---|"]
    for r in reg:
        lines.append(f"| {r[0]} | {r[5]:.1%} | {r[6]:.1%} | {r[8]:.2%} | {r[9]:.2%} | {r[4]:+.0%} |")
    issues = []
    for p in tax.parent_nodes:
        for k in tax.children_of[p]:
            blk, own = hum.get(p, {}).get("children", {}).get(k), hum.get(k, {}).get("v25")
            if blk is not None and own is not None and abs(blk - own) > 0.01 * max(own, 1e-9):
                issues.append(f"- {k}: the {p} sheet says {blk:.2f} but {k}'s own sheet ({hum[k]['file']}) says "
                              f"{own:.2f} US$ Mn; the parent figure is used for value/share, the own sheet for mix and CAGR")
    if issues:
        lines += ["", "## Human answer-key inconsistencies", *issues]
    (run_dir / f"comparison{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), *(sys.argv[2:4])))
