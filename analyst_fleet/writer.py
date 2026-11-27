"""Write the Input Sheet workbook (human layout) + reasoning/audit sheets.

Data sheet layout (mirrors the human Input Sheet, numbers only - no formulas, so any
reader with data_only=True sees the values):
  B1/C1         Market Name
  row 3         C.. leaf geographies (band 1: base-year shares)
  rows 4..      'By <dimension>' then segments; sub-segments indented (share of parent)
  band 2        same rows, to the right: segment CAGR 2025-2032 per country (row 2 = country CAGR)
  sizing block  B Region | C Market Share (of parent) | D Expected CAGR | E Value 2025 (US$ Mn) | F Parent
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as L

BOLD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
CONF_FILL = {"High": "C6EFCE", "Medium": "FFEB9C", "Low": "FFC7CE"}
WRAP = Alignment(wrap_text=True, vertical="top")
PCT = "0.0%"
PCT2 = "0.00%"


def _j(x) -> str:
    if isinstance(x, list):
        return "; ".join(str(i) for i in x)
    return "" if x is None else str(x)


def _header(ws, row: int, heads: list[str], widths: list[int] | None = None) -> None:
    for i, h in enumerate(heads, start=1):
        c = ws.cell(row, i, h)
        c.font, c.fill = BOLD, HEAD_FILL
        if widths:
            ws.column_dimensions[L(i)].width = widths[i - 1]
    ws.freeze_panes = ws.cell(row + 1, 1)


def _conf(cell, val) -> None:
    if val in CONF_FILL:
        cell.fill = PatternFill("solid", fgColor=CONF_FILL[val])


def write_workbook(state: dict, path: Path) -> Path:
    tax, cfg, numbers = state["tax"], state["cfg"], state["numbers"]
    geo, seg, price = numbers["geo"], numbers["seg"], numbers["price"]
    leaves = tax.leaves
    wb = Workbook()

    # ================================================================ Data
    ws = wb.active
    ws.title = "Data"
    ws.cell(1, 2, "Market Name").font = BOLD
    ws.cell(1, 3, tax.market_name)
    ws.cell(3, 1, "Segment Name in B Column Only")
    ws.cell(3, 2, "Segment").font = BOLD
    for ci, g in enumerate(leaves, start=3):
        ws.cell(3, ci, g).font = BOLD
    b2 = 3 + len(leaves) + 3                      # band-2 label column
    ws.cell(1, b2, "Segment CAGR 2025-2032 (row 2 = country CAGR)").font = BOLD
    for ci, g in enumerate(leaves, start=b2 + 1):
        ws.cell(3, ci, g).font = BOLD
        ws.cell(2, ci, round(geo["cagr"][g], 6)).number_format = PCT2
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions[L(b2)].width = 44
    r = 4
    for dim in tax.dimensions:
        ws.cell(r, 2, dim.title).font = BOLD
        ws.cell(r, b2, dim.title).font = BOLD
        r += 1
        for s, parent in dim.rows:
            for col in (2, b2):
                c = ws.cell(r, col, s)
                c.alignment = Alignment(indent=1 if parent else 0)
            for ci, g in enumerate(leaves):
                ws.cell(r, 3 + ci, round(seg["shares"][dim.title][g][s], 6)).number_format = PCT
                ws.cell(r, b2 + 1 + ci, round(seg["seg_cagr"][dim.title][g][s], 6)).number_format = PCT2
            r += 1
        r += 1
    r = max(r + 2, 36)
    ws.cell(r, 1, "Sizing block (share of parent, CAGR 2025-2032, value 2025 US$ Mn)")
    for ci, h in enumerate(["Region", "Market Share", "Expected CAGR", "Value", "Parent"], start=2):
        ws.cell(r, ci, h).font = BOLD
    r += 1

    def put(row, name, parent, bold=False):
        ws.cell(row, 2, name).font = Font(bold=bold)
        ws.cell(row, 3, round(geo["share"][name], 6)).number_format = PCT
        ws.cell(row, 4, round(geo["cagr"][name], 6)).number_format = PCT2
        ws.cell(row, 5, round(geo["value"][name], 4)).number_format = "#,##0.00"
        if parent:
            ws.cell(row, 6, parent)

    for reg in tax.regions:
        put(r, reg, tax.root, bold=True)
        r += 1
    put(r, tax.root, None, bold=True)
    r += 2
    for p in tax.parent_nodes[1:]:
        put(r, p, tax.parent_of[p], bold=True)
        r += 1
        for k in tax.children_of[p]:
            put(r, k, p)
            r += 1
        r += 1

    # ================================================================ reasoning for every value cell
    from openpyxl.comments import Comment
    from .cell_reasoning import explain_all, methodology
    cell_rows = explain_all(state, include_asp=True)
    plain: dict[str, str] = {}
    saved = state.get("plain_texts") or {}               # {cell: {"text", "value"}} from an earlier write of this run
    key_of = lambda cr: f"{cr['sheet']}!{cr['cell']}"
    same = lambda cr: (key_of(cr) in saved and saved[key_of(cr)].get("text") and saved[key_of(cr)].get("value") is not None
                       and cr.get("value") is not None and abs(float(saved[key_of(cr)]["value"]) - float(cr["value"])) < 1e-9)
    plain = {key_of(cr): saved[key_of(cr)]["text"] for cr in cell_rows if same(cr)}
    todo = [cr for cr in cell_rows if not same(cr)]
    if todo and getattr(cfg, "plain_language", True) and state.get("llm") is not None:
        from .plain_language import plain_texts
        plain.update(plain_texts(state, todo))
    state["plain_texts"] = {key_of(cr): {"text": plain[key_of(cr)], "value": cr.get("value")}
                            for cr in cell_rows if plain.get(key_of(cr))}
    for cr in cell_rows:
        cr["plain"] = plain.get(f"{cr['sheet']}!{cr['cell']}", "")

    def annotate(sheet, rows):
        for i, cr in rows:
            c = sheet.cell(cr["row"], cr["col"])
            if c.value is None:                   # layout drift would be a bug: the audit relies on it
                raise RuntimeError(f"cell layout mismatch at {cr['sheet']}!{cr['cell']} ({cr['kind']} {cr['geo']} {cr['seg']})")
            calc = cr.get("calc") or cr["method"]
            if len(calc) > 1400:                          # long chains (global value): keep the last steps in the note
                calc = "...\n" + "\n".join(calc.split("\n")[-4:])
            txt = ((cr["plain"] + "\n\n") if cr["plain"] else "") + "CALCULATION:\n" + calc \
                + (f"\nASSUMPTIONS: {cr['assumptions'][:500]}" if cr.get("assumptions") else "") \
                + f"\n(full detail: 'Cell Reasoning' row {i})"
            cm = Comment(txt, "analyst fleet")
            cm.width, cm.height = 620, 420
            c.comment = cm

    numbered = list(enumerate(cell_rows, start=2))
    annotate(ws, [(i, cr) for i, cr in numbered if cr["sheet"] == "Data"])

    # ================================================================ ASP
    dim = tax.priced_dimension
    aws = wb.create_sheet("ASP")
    aws.cell(1, 2, "Market Name").font = BOLD
    aws.cell(1, 3, tax.market_name)
    unit = (state.get("pricing_raw") or {}).get("unit_definition") or state["sizing"].get("volume_unit") or \
        "US$ of aftermarket revenue per installed gearbox per year"
    aws.cell(2, 4, f"Price Table - US$ per unit; unit = {unit}").font = BOLD
    aws.cell(3, 2, dim.title).font = BOLD
    for ci, g in enumerate(leaves, start=3):
        aws.cell(3, ci, g).font = BOLD
    aws.cell(4, 2, dim.title).font = BOLD
    aws.column_dimensions["B"].width = 44
    ar = 5
    for s, parent in dim.rows:
        c = aws.cell(ar, 2, s)
        c.alignment = Alignment(indent=1 if parent else 0)
        for ci, g in enumerate(leaves, start=3):
            aws.cell(ar, ci, round(price["asp"][g].get(s, 0.0), 2)).number_format = "#,##0"
        ar += 1
    annotate(aws, [(i, cr) for i, cr in numbered if cr["sheet"] == "ASP"])

    # ================================================================ Market Value
    mv = wb.create_sheet("Market Value")
    mv.cell(2, 3, "Value").font = BOLD
    for ci, h in enumerate(["Regions", "USD Mn", "Share", "CAGR", "Confidence"], start=2):
        mv.cell(3, ci, h).font = BOLD
    mr = 4
    reg_items = (state.get("regional_raw") or {}).get("items", {})
    for reg in tax.regions:
        mv.cell(mr, 2, reg)
        mv.cell(mr, 3, round(geo["value"][reg], 2))
        mv.cell(mr, 4, round(geo["share"][reg], 4)).number_format = PCT
        mv.cell(mr, 5, round(geo["cagr"][reg], 4)).number_format = PCT2
        conf = (reg_items.get(reg) or {}).get("confidence", "")
        _conf(mv.cell(mr, 6, conf), conf)
        mr += 1
    mv.cell(mr, 2, "Global").font = BOLD
    mv.cell(mr, 3, round(geo["value"][tax.root], 2))
    mv.cell(mr, 5, round(geo["cagr"][tax.root], 4)).number_format = PCT2
    conf = state["sizing"]["step2"].get("confidence", "")
    _conf(mv.cell(mr, 6, conf), conf)
    mv.column_dimensions["B"].width = 26

    # ================================================================ Geography reasoning
    gr = wb.create_sheet("Geography Reasoning")
    _header(gr, 1, ["Geography", "Parent", "Share of parent", "Value 2025 (US$ Mn)", "CAGR 2025-32 (final)",
                    "CAGR (analyst)", "Reasoning", "Key drivers", "Restraints", "Geopolitical / regulatory factors",
                    "Evidence", "Confidence", "Analyst"],
            [24, 22, 11, 12, 11, 11, 90, 40, 36, 40, 14, 11, 22])
    sz = state["sizing"]
    s1, s2 = sz["step1"], sz["step2"]
    row = [tax.root, "", 1.0, geo["value"][tax.root], geo["cagr"][tax.root], s1.get("global_cagr"),
           f"VALUE: {s2.get('reasoning', '')}\nGROWTH: {s1.get('cagr_reasoning', '')}",
           _j(s1.get("drivers")), _j(s1.get("restraints")), _j(s1.get("geopolitics")),
           "see Sizing sheet", s2.get("confidence", ""), "Market-sizing analyst"]
    gr.append(row)
    raw_by_parent = {tax.root: state.get("regional_raw")} | dict(state.get("country_raw") or {})
    for p in tax.parent_nodes:
        items = (raw_by_parent.get(p) or {}).get("items", {})
        who = "Regional analyst" if p == tax.root else f"Country analyst ({p})"
        for k in tax.children_of[p]:
            it = items.get(k, {})
            gr.append([k, p, geo["share"][k], geo["value"][k], geo["cagr"][k], geo["analyst_cagr"].get(k),
                       it.get("reasoning", ""), _j(it.get("drivers")), _j(it.get("restraints")),
                       _j(it.get("geopolitics")), _j(it.get("evidence")), it.get("confidence", ""), who])
    for rr in gr.iter_rows(min_row=2):
        rr[2].number_format, rr[3].number_format = PCT, "#,##0.00"
        rr[4].number_format = rr[5].number_format = PCT2
        for c in rr[6:11]:
            c.alignment = WRAP
        _conf(rr[11], rr[11].value)
    gr.append([])
    gr.append(["Overviews"])
    for p in tax.parent_nodes:
        ov = (raw_by_parent.get(p) or {}).get("overview")
        if ov:
            gr.append([p, "", "", "", "", "", ov])
            gr.cell(gr.max_row, 7).alignment = WRAP

    # ================================================================ Segment reasoning (group level)
    sr = wb.create_sheet("Segment Reasoning")
    _header(sr, 1, ["Country group", "Dimension", "Segment", "Parent segment", "Group share (value-weighted)",
                    "Growth premium vs country (pp)", "Reasoning", "Growth reasoning", "Key drivers", "Restraints",
                    "Geopolitical / regulatory factors", "Evidence", "Confidence"],
            [22, 20, 34, 16, 12, 12, 80, 50, 36, 32, 34, 12, 11])
    for group in tax.leaf_groups:
        for d in tax.dimensions:
            raw = (state.get("segments_raw") or {}).get(f"{group}||{d.title}", {})
            roll = seg["rollup"][d.title].get(group, {})
            for s, parent in d.rows:
                it = (raw.get("segments") or {}).get(s, {})
                sr.append([group, d.title, s, parent or "", roll.get(s), (it.get("growth_premium") or 0) * 100,
                           it.get("reasoning", ""), it.get("growth_reasoning", ""), _j(it.get("drivers")),
                           _j(it.get("restraints")), _j(it.get("geopolitics")), _j(it.get("evidence")),
                           it.get("confidence", "")])
            if raw.get("overview"):
                sr.append([group, d.title, "(overview)", "", None, None, raw["overview"]])
    for rr in sr.iter_rows(min_row=2):
        rr[4].number_format = PCT
        rr[5].number_format = "0.00"
        for c in rr[6:11]:
            c.alignment = WRAP
        _conf(rr[12], rr[12].value)

    # ================================================================ Country x segment reasoning
    cs = wb.create_sheet("Country Segment Reasoning")
    _header(cs, 1, ["Country", "Group", "Dimension", "Segment", "Parent segment", "Share (of parent)",
                    "Share of country total", "Value 2025 (US$ Mn)", "Segment CAGR 2025-32", "Why this share"],
            [22, 22, 20, 34, 16, 11, 11, 12, 11, 90])
    from .compute import _absolute
    for d in tax.dimensions:
        for group, members in tax.leaf_groups.items():
            raw = (state.get("segments_raw") or {}).get(f"{group}||{d.title}", {})
            for c in members:
                sh = seg["shares"][d.title][c]
                ab = _absolute(d, sh)
                why = ((raw.get("countries") or {}).get(c) or {}).get("why") or {}
                for s, parent in d.rows:
                    cs.append([c, group, d.title, s, parent or "", sh[s], ab[s], ab[s] * geo["value"][c],
                               seg["seg_cagr"][d.title][c][s], why.get(s, "")])
    for rr in cs.iter_rows(min_row=2):
        rr[5].number_format = rr[6].number_format = PCT
        rr[7].number_format = "#,##0.000"
        rr[8].number_format = PCT2
        rr[9].alignment = WRAP

    # ================================================================ Pricing reasoning
    pz = wb.create_sheet("Pricing Reasoning")
    pr = state.get("pricing_raw") or {}
    pz.append(["Unit definition", pr.get("unit_definition", "")])
    pz.append(["Price trend 2025-32 (annual)", (pr.get("price_trend") or {}).get("annual_change"),
               (pr.get("price_trend") or {}).get("reasoning", "")])
    pz.append(["Overview", pr.get("overview", "")])
    pz.append(["Index normalisation factor", price["index_scale"]])
    pz.append([])
    pz.append(["Application", "Global average US$/unit-yr", "Basis", "Note"])
    gen = state["sizing"].get("mode") == "generic"
    for a, r_ in state["sizing"]["bottom_up"]["by_application"].items():
        pz.append([a, r_["spend_per_unit_usd"], "sizing analyst's price per unit (bottom-up build)" if gen else
                   f"{r_['spend_pct']:.1%} of OEM new-unit price US$ {r_['oem_asp_usd']:,.0f}",
                   (pr.get("application_notes") or {}).get(a, "")])
    pz.append([])
    hr = pz.max_row + 1
    heads = ["Country", "Analyst index", "Normalised index"] + [s for s, _ in dim.rows] + ["Reasoning"]
    for ci, h in enumerate(heads, start=1):
        pz.cell(hr, ci, h).font = BOLD
    for c in leaves:
        it = (pr.get("country_index") or {}).get(c, {})
        pz.append([c, price["index_raw"][c], price["index"][c]] + [price["asp"][c].get(s, 0) for s, _ in dim.rows]
                  + [it.get("reasoning", "")])
    pz.column_dimensions["A"].width = 26
    pz.column_dimensions[L(len(heads))].width = 70

    # ================================================================ Sizing
    sw = wb.create_sheet("Sizing")
    sw.column_dimensions["A"].width = 40
    sw.column_dimensions["B"].width = 18
    sw.column_dimensions["G"].width = 80
    sw.append(["GLOBAL 2025 ANCHOR (US$ Mn)", sz["global_value_2025"]])
    sw.append(["Global CAGR 2025-32", sz["global_cagr"]])
    sw.append(["Triangulation weights", json.dumps({k: round(v, 3) for k, v in sz["weights"].items()})])
    sw.append(["Triangulation reasoning", s2.get("reasoning", "")])
    sw.append([])
    sw.append(["Route (independent estimate, BEFORE rescaling - see row 4)" if sz.get("rescale") else "Route", "US$ Mn"])
    if sz.get("mode") == "generic":
        _generic_sizing_rows(sw, sz)
    else:
        _gearbox_sizing_rows(sw, sz, s1)
    sw.append(["Cited figure", "Figure US$ Mn", "Year", "Scope factor", "2025 in-scope US$ Mn", "", "Reasoning"])
    for c in sz["cited"]:
        sw.append([c.get("evidence"), c.get("figure_usd_mn"), c.get("year"), c.get("scope_factor"),
                   c.get("value_2025_in_scope_usd_mn"), "", c.get("reasoning", "")])
    for rr in sw.iter_rows():
        if len(rr) >= 7:
            rr[6].alignment = WRAP
    _rest_of_workbook(wb, state, tax, cfg, path, numbers, cell_rows)
    return path


def _generic_sizing_rows(sw, sz) -> None:
    s1, td = sz["step1"], sz.get("top_down_parent") or {}
    sw.append(["A. Bottom-up: volume x price per segment", sz["bottom_up"]["total_usd_mn"]])
    sw.append(["B. Top-down: parent market x share", sz["top_down_usd_mn"],
               f"{td.get('parent_market')} = US$ {td.get('parent_value_2025_usd_mn')} Mn x share {td.get('share_of_parent')}",
               "", "", "", td.get("reasoning", "")])
    sw.append(["C. Cited figures (median, 2025 in-scope)", sz["cited_median_usd_mn"]])
    sw.append([])
    sw.append(["Volume unit", sz.get("volume_unit", "")])
    sw.append(["Segment", "Volume 2025", "Price US$/unit", "", "", "Value US$ Mn", "Volume build (product of the chain) / reasoning"])
    for a, r_ in sz["bottom_up"]["by_application"].items():
        it = (s1.get("segments") or {}).get(a) or {}
        chain = " x ".join(f"{e.get('label')} {e.get('value')} [{e.get('evidence', 'analyst')}]" for e in r_.get("volume_build") or [])
        sw.append([a, r_["installed_units"], r_["spend_per_unit_usd"], "", "", r_["value_usd_mn"],
                   f"{chain}. PRICE: {it.get('price_reasoning', '')} WHY: {it.get('reasoning', '')}"])
    sw.append(["Total", sz["bottom_up"]["installed_units"], "", "", "", sz["bottom_up"]["total_usd_mn"]])
    sw.append([])


def _gearbox_sizing_rows(sw, sz, s1) -> None:
    sw.append(["A. Bottom-up: installed base x spend per unit", sz["bottom_up"]["total_usd_mn"]])
    sw.append(["B. Top-down: OEM 2025 x aftermarket/OEM ratio", sz["top_down_usd_mn"],
               f"OEM 2025 = {sz['oem_2025_usd_mn']:.1f}; ratio = {s1['aftermarket_to_oem_ratio']['value']}",
               "", "", "", s1["aftermarket_to_oem_ratio"].get("reasoning", "")])
    sw.append(["C. Cited figures (median, 2025 in-scope)", sz["cited_median_usd_mn"]])
    sw.append([])
    ib = s1["installed_base"]
    sw.append(["Installed-base parameters", "", "", "", "", "", ib.get("reasoning", "")])
    sw.append(["service_life_years", ib.get("service_life_years")])
    sw.append(["pre2020_unit_growth", ib.get("pre2020_unit_growth")])
    sw.append(["active_fraction", ib.get("active_fraction")])
    sw.append([])
    sw.append(["Application", "Installed units", "OEM new-unit price US$", "Spend % of price", "Spend US$/unit-yr",
               "Value US$ Mn", "Reasoning"])
    for a, r_ in sz["bottom_up"]["by_application"].items():
        sw.append([a, r_["installed_units"], r_["oem_asp_usd"], r_["spend_pct"], r_["spend_per_unit_usd"],
                   r_["value_usd_mn"], s1["spend_pct_of_new_unit_price"][a].get("reasoning", "")])
    sw.append(["Total", sz["bottom_up"]["installed_units"], "", "", "", sz["bottom_up"]["total_usd_mn"]])
    sw.append([])
    comps = ("parts_and_internals", "overhaul_and_repair_services", "inspection_monitoring_lubrication",
             "replacement_gearboxes", "upgrades_modernisation")
    if any((s1["spend_pct_of_new_unit_price"].get(a) or {}).get("components") for a in sz["bottom_up"]["by_application"]):
        sw.append(["In-scope spend build-up (annual, fraction of the new gearbox ASSEMBLY price; code adds the components)"])
        sw.append(["Application", *comps, "Total", "Excluded (out of scope per client definition)"])
        for a in sz["bottom_up"]["by_application"]:
            it = s1["spend_pct_of_new_unit_price"].get(a) or {}
            cp = it.get("components") or {}
            sw.append([a, *[cp.get(k) for k in comps], it.get("value"), it.get("excluded", "")])
            for c in sw[sw.max_row][1:7]:
                c.number_format = "0.00%"
            sw.cell(sw.max_row, 8).alignment = WRAP
        sw.append([])


def _rest_of_workbook(wb, state, tax, cfg, path, numbers, cell_rows) -> None:
    from .cell_reasoning import methodology
    # ================================================================ Evidence
    ev = wb.create_sheet("Evidence")
    _header(ev, 1, ["Id", "Theme", "Domain", "Title", "URL", "Query", "Text (snippet / extracted passage)"],
            [6, 14, 24, 40, 50, 40, 100])
    for e in state.get("evidence") or []:
        ev.append([e["id"], e["theme"], e["domain"], e["title"], e["url"], e["query"], e["text"][:1500]])
    fx = wb.create_sheet("Facts")
    _header(fx, 1, ["Fact", "Source id", "Theme", "Claim", "Verbatim quote", "Geo", "Year", "Metric", "Relevance", "URL"],
            [6, 8, 14, 70, 60, 16, 8, 20, 9, 50])
    for f in state.get("facts") or []:
        fx.append([f["fid"], f["id"], f["theme"], f["claim"], f["quote"], f.get("geo"), f.get("year"),
                   f.get("metric"), f.get("relevance"), f["url"]])

    # ================================================================ Checks & Review
    ck = wb.create_sheet("Checks & Review")
    ck.column_dimensions["A"].width = 18
    ck.column_dimensions["B"].width = 26
    ck.column_dimensions["C"].width = 34
    ck.column_dimensions["D"].width = 100
    rv = state.get("review") or {}
    ck.append(["Executive summary", "", "", rv.get("executive_summary", "")])
    ck.cell(1, 4).alignment = WRAP
    ck.append(["Selected pass", state.get("best_pass"), "overall score", rv.get("overall")])
    ck.append([])
    ck.append(["Severity", "Check", "Scope", "Message"])
    for f in numbers["flags"]:
        ck.append([f["severity"], f["check"], f["scope"], f["message"]])
    ck.append([])
    ck.append(["Adjustment", "Stage", "Scope", "Detail"])
    for a in numbers["adjustments"]:
        ck.append([a["kind"], a["stage"], a["scope"], a["detail"]])
    ck.append([])
    for rvw in state.get("reviews") or []:
        ck.append([f"Review pass {rvw['pass']}", "overall", rvw.get("overall"), json.dumps(rvw.get("scores"))])
        for i in rvw.get("issues") or []:
            ck.append(["", "issue", i.get("target"), f"{i.get('problem', '')} -> {i.get('instruction', '')}"])
            ck.cell(ck.max_row, 4).alignment = WRAP

    # ================================================================ Validation (agent vs human adjudication)
    if state.get("validation"):
        ref = "Corpus prior" if state.get("validation_mode") == "priors" else "Human"
        vs = wb.create_sheet("Validation", 3)
        _header(vs, 1, ["Item", "Level", "Geography / group", "Unit", "Agent", ref, "Verdict", f"Weight on {ref.lower()}",
                        "Validated", "Why the agent may be wrong",
                        "Why the prior may not apply here" if ref != "Human" else "Why the human may be wrong", "Decisive factors",
                        "Evidence", "Validated reasoning", "Confidence"],
                [34, 13, 22, 20, 11, 11, 9, 9, 11, 60, 60, 40, 18, 70, 11])
        for v in state["validation"]:
            vs.append([v["id"], v["kind"], v["node"], v["unit"], v["agent"], v.get("human", v.get("prior")), v.get("verdict"),
                       v["weight_on_human"], v["validated"], v.get("why_agent_may_be_wrong", ""),
                       v.get("why_human_may_be_wrong", ""), _j(v.get("decisive_factors")), _j(v.get("evidence")),
                       v.get("validated_reasoning", ""), v.get("confidence", "")])
        for rr in vs.iter_rows(min_row=2):
            fmt = "#,##0.0" if rr[1].value == "global_value" else ("0.00%" if "cagr" in str(rr[1].value) else "0.0%")
            for c in (rr[4], rr[5], rr[8]):
                c.number_format = fmt
            rr[7].number_format = "0.00"
            for c in rr[9:14]:
                c.alignment = WRAP
            _conf(rr[14], rr[14].value)
            rr[6].fill = PatternFill("solid", fgColor={"agent": "DDEBF7", "human": "FCE4D6", "prior": "FCE4D6",
                                                       "between": "EDEDED"}.get(rr[6].value, "FFFFFF"))

    # ================================================================ Cell reasoning + research methodology
    cw = wb.create_sheet("Cell Reasoning", 3)
    heads = ["Sheet", "Cell", "Number type", "Geography", "Parent geography", "Dimension", "Segment", "Parent segment",
             "Value", "Explanation (plain language)", "Calculation, step by step", "Assumptions behind it",
             "2A contribution (totals, geography, growth)", "2B contribution (use case / industry vertical)",
             "2C contribution (product type x application)", "Approach", "Analyst notes (detail)", "Code adjustments",
             "Validator", "House heuristics cited", "Evidence", "Confidence", "Scope basis (client definition)"]
    _header(cw, 1, heads, [7, 8, 13, 20, 18, 18, 30, 16, 11, 80, 90, 60, 60, 50, 50, 30, 60, 30, 30, 12, 16, 11, 50])
    for i, cr in enumerate(cell_rows, start=2):
        for j, v in enumerate([cr["sheet"], cr["cell"], cr["kind"], cr["geo"], cr["parent_geo"], cr["dim"], cr["seg"],
                               cr["parent_seg"], cr["value"], cr["plain"], cr.get("calc", ""), cr.get("assumptions", ""),
                               cr.get("c2A", ""), cr.get("c2B", ""), cr.get("c2C", ""), cr["approach"], cr["reasoning"],
                               cr["adjustments"], cr["validator"], cr["heuristics"], cr["evidence"], cr["confidence"],
                               cr["scope"]], start=1):
            cw.cell(i, j, v)
        rr = cw[i]
        rr[8].number_format = ("#,##0.00" if cr["kind"] == "size_value" else "#,##0" if cr["kind"] == "asp"
                               else "0.00%" if "cagr" in cr["kind"] else "0.0%")
        for c in list(rr[9:19]) + [rr[22]]:
            c.alignment = WRAP
        _conf(rr[21], cr["confidence"])
    cw.auto_filter.ref = f"A1:{L(len(heads))}{len(cell_rows) + 1}"
    ms = wb.create_sheet("Research Methodology", 4)
    ms.column_dimensions["A"].width = 38
    ms.column_dimensions["B"].width = 150
    ms.cell(1, 1, f"Research methodology - {tax.market_name}").font = BOLD
    for i, (h, p) in enumerate(methodology(state), start=3):
        ms.cell(i, 1, h).font = BOLD
        ms.cell(i, 2, p).alignment = WRAP

    # ================================================================ Run info
    ri = wb.create_sheet("Run Info")
    usage = state["llm"].usage
    for k, v in [("Market", tax.market_name), ("Base year", cfg.base_year), ("CAGR window", f"{cfg.base_year}-{cfg.forecast_end_year}"),
                 ("Generated", datetime.now().isoformat(timespec="seconds")),
                 ("Reasoning model", cfg.reasoning_model), ("Fast model", cfg.fast_model),
                 ("Blind mode (publisher's own report excluded)", cfg.blind),
                 ("Publisher sources blocked", state.get("blocked_sources", 0)),
                 ("Web sources", len(state.get("evidence") or [])), ("Verified facts", len(state.get("facts") or [])),
                 ("LLM calls (live / cached)", f"{usage['calls']} / {usage['cached']}"),
                 ("Tokens (prompt / completion)", f"{usage['prompt_tokens']:,} / {usage['completion_tokens']:,}"),
                 ("Volume unit", state["sizing"].get("volume_unit") if state["sizing"].get("mode") == "generic"
                  else "Th units = thousand installed gearboxes serviced per year (value / ASP x 1000)"),
                 ("Not produced", "Band 3 (CMI_Trend_NN curve tags) - requires the publisher's internal trend library")]:
        ri.append([k, str(v)])
    ri.column_dimensions["A"].width = 46
    ri.column_dimensions["B"].width = 80
    for line in state.get("log") or []:
        ri.append(["log", line])

    path.parent.mkdir(parents=True, exist_ok=True)
    # ================================================================ first page: what 2A / 2B / 2C stand for here
    st = getattr(tax, "strategies", {}) or {}
    if st:
        dfn = wb.create_sheet("2A-2B-2C Definitions", 0)
        for col, wdt in zip("ABCDE", (16, 34, 60, 110, 70)):
            dfn.column_dimensions[col].width = wdt
        dfn.cell(1, 1, f"{tax.market_name} - what 2A, 2B and 2C stand for in this market").font = Font(bold=True, size=13)
        _header(dfn, 3, ["Strategy", "Name", "Stands for", "In this market", "Produces in this sheet"])
        dfn.freeze_panes = None
        for i, code in enumerate(("2A", "2B", "2C"), start=4):
            d = st.get(code) or {}
            name = d.get("name", "") + (f"  [dimension: {d['dimension']}]" if d.get("dimension") else "")
            for j, v in enumerate([code, name, d.get("stands_for", ""), d.get("in_this_market", ""), d.get("produces", "")], 1):
                dfn.cell(i, j, v).alignment = WRAP
            dfn.cell(i, 1).font = Font(bold=True, size=12)
            dfn.row_dimensions[i].height = 150
        dfn.cell(8, 1, "How they combine").font = BOLD
        dfn.cell(8, 2, st.get("how_they_combine", "")).alignment = WRAP
        dfn.merge_cells(start_row=8, start_column=2, end_row=8, end_column=5)
        dfn.row_dimensions[8].height = 45
        dfn.cell(10, 1, "Where to read it").font = BOLD
        dfn.cell(10, 2, "Every number on the Data and ASP sheets has a note (hover over the cell) with a plain-language "
                        "explanation, the calculation step by step and the assumptions behind it. 'Cell Reasoning' holds the "
                        "same for every cell, plus what 2A, 2B and 2C each contributed. 'Research Methodology' describes the "
                        "whole build.").alignment = WRAP
        dfn.merge_cells(start_row=10, start_column=2, end_row=10, end_column=5)
        dfn.row_dimensions[10].height = 45
        wb.active = 0
    wb.save(path)


def dump_state(state: dict, path: Path) -> None:
    keep = {k: state.get(k) for k in ("scope", "sizing", "regional_raw", "country_raw", "segments_raw", "pricing_raw",
                                      "reviews", "best_pass", "facts", "evidence", "log", "validation", "validation_mode", "prior_check",
                                      "plain_texts", "growth_correction")}
    n = state["numbers"]
    keep["numbers"] = {"geo": n["geo"], "seg": {k: n["seg"][k] for k in ("shares", "seg_cagr", "rollup")},
                       "price": {k: n["price"][k] for k in ("base_usd", "index", "asp", "total_units")},
                       "flags": n["flags"], "adjustments": n["adjustments"]}
    keep["usage"] = state["llm"].usage
    keep["llm_calls"] = state["llm"].calls
    path.write_text(json.dumps(keep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def summary_md(state: dict) -> str:
    tax, n = state["tax"], state["numbers"]
    geo = n["geo"]
    sz = state["sizing"]
    rv = state.get("review") or {}
    lines = [f"# {tax.market_name} - agent-generated Input Sheet", "",
             f"- Global 2025: **US$ {geo['value'][tax.root]:.1f} Mn**, CAGR 2025-{state['cfg'].forecast_end_year}: **{geo['cagr'][tax.root]:.2%}**",
             f"- Sizing routes: bottom-up {sz['bottom_up']['total_usd_mn']:.1f}, top-down {sz['top_down_usd_mn']:.1f}, "
             f"cited median {sz['cited_median_usd_mn'] if sz['cited_median_usd_mn'] is None else round(sz['cited_median_usd_mn'], 1)}; "
             f"weights {', '.join(f'{k} {v:.2f}' for k, v in sz['weights'].items())}",
             f"- Reviewer: pass {state.get('best_pass')} kept, overall {rv.get('overall')}", "",
             "| Region | Share | 2025 US$ Mn | CAGR |", "|---|---|---|---|"]
    for r in tax.regions:
        lines.append(f"| {r} | {geo['share'][r]:.1%} | {geo['value'][r]:.1f} | {geo['cagr'][r]:.2%} |")
    flags = [f"- [{f['severity']}] {f['check']} - {f['scope']}: {f['message']}" for f in n["flags"]] or ["- none"]
    adj = [f"- {a['scope']}: {a['detail']}" for a in n["adjustments"]] or ["- none"]
    lines += ["", "## Executive summary", rv.get("executive_summary", ""), "", "## Flags", *flags,
              "", "## Code adjustments", *adj]
    return "\n".join(lines)
