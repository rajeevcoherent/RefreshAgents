"""Cell-by-cell comparison of an agent Input Sheet with the human one, at identical cell addresses.

    python cell_audit.py outputs/<run_id> [state file, default state.json]

There is no human gearbox Input Sheet - only the human ME workbooks computed from it. The human's
implied Input Sheet is rebuilt from those MEs in exactly our Data layout (segment shares per country,
segment CAGRs, country CAGRs, sizing-block share / CAGR / value), then every value cell is compared.
Writes outputs/<run_id>/Cell Audit - <market>.xlsx and cell_audit.md:
  Summary         error by number type, dimension and geography level, share of cells within tolerance
  Agent Data / Human Data (implied) / Difference   the three grids, same cells, differences coloured
  Cell by Cell    every cell: agent, human, difference, status + approach, derivation, reasoning,
                  adjustments, validator notes, heuristics, evidence, confidence
  Biggest gaps    the largest differences per number type, with the agent's argument next to them
Tolerances (green / amber / red): shares 2 / 5 pp, CAGRs 0.5 / 1.5 pp, values 15% / 35%.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from analyst_fleet.cell_reasoning import address, data_layout, explain_all, value_of
from analyst_fleet.compute import compute_all
from analyst_fleet.config import Config
from analyst_fleet.evidence.definitions import scope_rules, segment_definitions
from analyst_fleet.evidence.oem import load_oem
from analyst_fleet.llm import LLM
from analyst_fleet.taxonomy import Taxonomy
import compare_with_human as cmp

FILL = {"green": PatternFill("solid", fgColor="C6EFCE"), "amber": PatternFill("solid", fgColor="FFEB9C"),
        "red": PatternFill("solid", fgColor="FFC7CE")}
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")
TOL = {"share": (0.02, 0.05), "cagr": (0.005, 0.015), "value": (0.15, 0.35)}


def family(kind: str) -> str:
    return "value" if kind == "size_value" else "cagr" if "cagr" in kind else "share"


def human_numbers(tax, hum: dict) -> dict:
    geo = {"value": {}, "share": {}, "cagr": {}}
    for g in [tax.root] + [k for p in tax.parent_nodes for k in tax.children_of[p]]:
        h, p = hum.get(g), tax.parent_of.get(g)
        blk = (hum.get(p) or {}).get("children", {}).get(g) if p else None
        v = blk if blk is not None else (h["v25"] if h else None)
        if v is not None:
            geo["value"][g] = v
        if p is None:
            geo["share"][g] = 1.0
        elif blk is not None and hum.get(p) and hum[p]["v25"]:
            geo["share"][g] = blk / hum[p]["v25"]
        if h:
            c = cmp.cagr(h["v25"], h["v32"])
            if c is not None:
                geo["cagr"][g] = c
    shares, seg_cagr = {}, {}
    for d in tax.dimensions:
        shares[d.title], seg_cagr[d.title] = {}, {}
        for g in tax.leaves:
            h = hum.get(g)
            if not h or not h["seg"]:
                continue
            shares[d.title][g] = cmp.human_mix(d, h["seg"])
            seg_cagr[d.title][g] = {s: c for s in d.all_segments if (c := cmp.cagr(*h["seg"].get(s, (0, 0)))) is not None}
    return {"geo": geo, "seg": {"shares": shares, "seg_cagr": seg_cagr}}


def diff_status(kind: str, a, h):
    if a is None or h is None:
        return None, None
    f = family(kind)
    d = (a / h - 1) if f == "value" else (a - h)
    if f == "value" and not h:
        return None, None
    g, am = TOL[f]
    return d, ("green" if abs(d) <= g else "amber" if abs(d) <= am else "red")


def main(run_dir: Path, state_file: str = "state.json") -> int:
    cfg = Config()
    tax = Taxonomy(cfg.taxonomy_path)
    saved = json.loads((run_dir / state_file).read_text(encoding="utf-8"))
    state = {**saved, "cfg": cfg, "tax": tax, "llm": LLM(cfg), "oem": load_oem(cfg.oem_dir, tax), "segment_definitions": segment_definitions(cfg.definitions_docx), "scope_rules": scope_rules(cfg.definitions_docx, cfg.oem_definitions_docx), "review": {}}
    state["numbers"] = compute_all(state)
    hn = human_numbers(tax, cmp.read_human(tax))
    rows = explain_all(state)
    cells, labels = data_layout(tax)
    for r, c in zip(rows, cells):
        r["human"] = value_of(hn, c)
        r["diff"], r["status"] = diff_status(r["kind"], r["value"], r["human"])

    wb = Workbook()
    # ---------------------------------------------------------------- summary
    sm = wb.active
    sm.title = "Summary"
    sm.append([f"Cell-by-cell audit - {tax.market_name} - run {run_dir.name} ({state_file})"])
    sm["A1"].font = BOLD
    sm.append(["Human sheet = implied Input Sheet rebuilt from the human ME workbooks (same cell layout). Tolerances "
               "green/amber/red: shares 2/5 pp, CAGRs 0.5/1.5 pp, values 15%/35%. Differences are agent minus human."])
    sm.append([])
    head = ["Group", "Cells compared", "Mean abs diff", "Median abs diff", "Green", "Amber", "Red", "% within amber"]
    sm.append(head)
    for c in sm[sm.max_row]:
        c.font = BOLD
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["diff"] is None:
            continue
        lvl = "global" if r["geo"] == tax.root else ("region" if r["parent_geo"] == tax.root else
                                                     "country" if r["geo"] in tax.leaves else "sub-region")
        groups[f"{r['kind']}"].append(r)
        if r["dim"]:
            groups[f"{r['kind']} | {r['dim']}"].append(r)
            groups[f"{r['kind']} | {r['dim']} | {'sub-segments' if r['parent_seg'] else 'top level'}"].append(r)
        else:
            groups[f"{r['kind']} | {lvl}"].append(r)
    md = [f"# Cell-by-cell audit - {run_dir.name} ({state_file})", "",
          "| Group | Cells | Mean abs diff | Green | Amber | Red |", "|---|---|---|---|---|---|"]
    for gname in sorted(groups):
        rs = groups[gname]
        f = family(rs[0]["kind"])
        ad = [abs(r["diff"]) for r in rs]
        cnt = {k: sum(r["status"] == k for r in rs) for k in ("green", "amber", "red")}
        fmtd = (lambda x: f"{x:.1%}") if f == "value" else (lambda x: f"{x * 100:.2f} pp")
        sm.append([gname, len(rs), fmtd(mean(ad)), fmtd(median(ad)), cnt["green"], cnt["amber"], cnt["red"],
                   f"{(cnt['green'] + cnt['amber']) / len(rs):.0%}"])
        if "|" not in gname or gname.count("|") == 1:
            md.append(f"| {gname} | {len(rs)} | {fmtd(mean(ad))} | {cnt['green']} | {cnt['amber']} | {cnt['red']} |")
    sm.column_dimensions["A"].width = 70
    no_h = sum(r["human"] is None for r in rows)
    sm.append([])
    sm.append([f"{len(rows)} value cells in the agent sheet; {len(rows) - no_h} have a human counterpart "
               f"({no_h} without: geographies or segments the human ME does not carry)."])
    sm.append([f"Cells with written reasoning: {sum(not str(r['reasoning']).startswith('(no analyst') for r in rows)} of {len(rows)}"])

    # ---------------------------------------------------------------- grids
    def grid(title: str, getter, colour: bool = False):
        ws = wb.create_sheet(title)
        ws.cell(1, 2, "Market Name").font = BOLD
        ws.cell(1, 3, tax.market_name)
        b2 = labels["b2"]
        ws.cell(1, b2, "Segment CAGR 2025-2032 (row 2 = country CAGR)").font = BOLD
        for ci, g in enumerate(tax.leaves):
            ws.cell(3, 3 + ci, g).font = BOLD
            ws.cell(3, b2 + 1 + ci, g).font = BOLD
        for r_, t in labels["dim_rows"]:
            ws.cell(r_, 2, t).font = BOLD
            ws.cell(r_, b2, t).font = BOLD
        for r_, s, parent in labels["seg_rows"]:
            for col in (2, b2):
                ws.cell(r_, col, s).alignment = Alignment(indent=1 if parent else 0)
        h = labels["size_header"]
        for ci, t in enumerate(["Region", "Market Share", "Expected CAGR", "Value", "Parent"], start=2):
            ws.cell(h, ci, t).font = BOLD
        for r_, name, parent, bold in labels["size_rows"]:
            ws.cell(r_, 2, name).font = Font(bold=bold)
            if parent:
                ws.cell(r_, 6, parent)
        for r in rows:
            v = getter(r)
            c = ws.cell(r["row"], r["col"], v)
            fam = family(r["kind"])
            c.number_format = ("+0.0%;-0.0%" if colour else "#,##0.00") if fam == "value" else \
                ("+0.00%;-0.00%" if colour else "0.00%") if fam == "cagr" else ("+0.0%;-0.0%" if colour else "0.0%")
            if colour and r["status"]:
                c.fill = FILL[r["status"]]
        ws.column_dimensions["B"].width = 40
        ws.column_dimensions[get_column_letter(b2)].width = 40
        ws.freeze_panes = "C4"

    grid("Agent Data", lambda r: r["value"])
    grid("Human Data (implied)", lambda r: r["human"])
    grid("Difference", lambda r: r["diff"], colour=True)

    # ---------------------------------------------------------------- cell by cell
    cb = wb.create_sheet("Cell by Cell")
    heads = ["Cell", "Number type", "Geography", "Parent geography", "Dimension", "Segment", "Parent segment", "Agent",
             "Human (implied)", "Difference", "Status", "Approach", "How the agent's number was formed", "Analyst reasoning",
             "Code adjustments", "Validator", "House heuristics cited", "Evidence", "Confidence", "Scope basis (client definition)"]
    widths = [8, 12, 20, 18, 18, 30, 16, 10, 10, 10, 8, 36, 70, 90, 36, 36, 14, 16, 10, 60]
    for i, (h_, w) in enumerate(zip(heads, widths), start=1):
        cb.cell(1, i, h_).font = BOLD
        cb.column_dimensions[get_column_letter(i)].width = w
    cb.freeze_panes = "C2"
    for i, r in enumerate(rows, start=2):
        vals = [r["cell"], r["kind"], r["geo"], r["parent_geo"], r["dim"], r["seg"], r["parent_seg"], r["value"], r["human"],
                r["diff"], r["status"] or "no human value", r["approach"], r["method"], r["reasoning"], r["adjustments"],
                r["validator"], r["heuristics"], r["evidence"], r["confidence"], r["scope"]]
        for j, v in enumerate(vals, start=1):
            cb.cell(i, j, v)
        fam = family(r["kind"])
        nf = "#,##0.00" if fam == "value" else "0.00%" if fam == "cagr" else "0.0%"
        cb.cell(i, 8).number_format = cb.cell(i, 9).number_format = nf
        cb.cell(i, 10).number_format = "+0.0%" if fam == "value" else "+0.00%"
        if r["status"]:
            cb.cell(i, 11).fill = FILL[r["status"]]
        for j in (12, 13, 14, 15, 16):
            cb.cell(i, j).alignment = WRAP
    cb.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{len(rows) + 1}"

    # ---------------------------------------------------------------- biggest gaps
    bg = wb.create_sheet("Biggest gaps")
    for i, (h_, w) in enumerate(zip(["Cell", "Type", "Geography", "Dimension", "Segment", "Agent", "Human", "Difference",
                                     "How the agent's number was formed", "Agent's reasoning"], [8, 12, 20, 18, 30, 10, 10, 10, 70, 100]), 1):
        bg.cell(1, i, h_).font = BOLD
        bg.column_dimensions[get_column_letter(i)].width = w
    gi = 2
    md += ["", "## Largest gaps (agent vs human) with the agent's argument", ""]
    for kind in ("size_value", "size_share", "size_cagr", "seg_share", "seg_cagr"):
        cand = [r for r in rows if r["kind"] == kind and r["diff"] is not None]
        # weight segment cells by the country's size so tiny countries do not dominate
        key = (lambda r: abs(r["diff"]) * (state["numbers"]["geo"]["value"].get(r["geo"], 0) ** 0.5)) if kind.startswith("seg") \
            else (lambda r: abs(r["diff"]))
        for r in sorted(cand, key=key, reverse=True)[:12]:
            fam = family(kind)
            for j, v in enumerate([r["cell"], kind, r["geo"], r["dim"], r["seg"], r["value"], r["human"], r["diff"],
                                   r["method"], r["reasoning"]], 1):
                bg.cell(gi, j, v)
            nf = "#,##0.00" if fam == "value" else "0.00%" if fam == "cagr" else "0.0%"
            bg.cell(gi, 6).number_format = bg.cell(gi, 7).number_format = nf
            bg.cell(gi, 8).number_format = "+0.0%" if fam == "value" else "+0.00%"
            bg.cell(gi, 8).fill = FILL[r["status"]]
            for j in (9, 10):
                bg.cell(gi, j).alignment = WRAP
            gi += 1
        top = sorted(cand, key=key, reverse=True)[:3]
        for r in top:
            fv = (lambda x: f"{x:,.2f}") if kind == "size_value" else (lambda x: f"{x:.2%}")
            md.append(f"- **{r['cell']} {kind} {r['geo']} {r['seg']}**: agent {fv(r['value'])} vs human {fv(r['human'])}. "
                      f"{r['method'][:220]} Reason: {str(r['reasoning'])[:260]}")
    out = run_dir / f"Cell Audit - {tax.market_name}{'' if state_file == 'state.json' else ' (' + state_file.replace('.json', '') + ')'}.xlsx"
    wb.save(out)
    (run_dir / f"cell_audit{'' if state_file == 'state.json' else '_' + state_file.replace('.json', '')}.md").write_text(
        "\n".join(md), encoding="utf-8")
    from analyst_fleet.cell_reasoning import methodology
    tagm = "" if state_file == "state.json" else "_" + state_file.replace(".json", "")
    meth = [f"# Research methodology - {tax.market_name}", "", f"Run {run_dir.name} ({state_file}). Every value cell of the "
            "Input Sheet's Data sheet has its own approach, derivation, reasoning, adjustments and evidence in the "
            "'Cell Reasoning' sheet (and as a comment on the cell).", ""]
    for h_, p_ in methodology(state):
        meth += [f"## {h_}", "", p_, ""]
    (run_dir / f"research_methodology{tagm}.md").write_text("\n".join(meth), encoding="utf-8")
    print("\n".join(md[:30]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), *(sys.argv[2:3])))
