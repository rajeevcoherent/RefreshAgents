"""Validator agent: adjudicates between the agent fleet's numbers and a human reference.

The human sheet carries numbers but no reasoning, so for every stark disagreement the
code first reverse-engineers what the human number IMPLIES (aftermarket/OEM ratio,
service spend per installed gearbox, share vs the OEM installed-flow signal, growth vs
the human's own 2020-25 history). An independent validation lead (DeepSeek) then weighs
the agent's written reasoning against the human's implied assumptions, the OEM data and
the web evidence, and returns a verdict with explicit reasons why each side may be wrong.

Code applies the verdict:  validated = agent + w_human x (human - agent)
and then re-runs the normal arithmetic (normalisation, CAGR reconciliation), so the
validated sheet stays internally consistent. Nothing is copied silently: every moved
number has a row in the Validation sheet and an event in system.log.
"""
from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .agents.common import PERSONA, canon_keys, dump, fmt_facts, num, select_facts
from .syslog import event

log = logging.getLogger("fleet.validator")

# what counts as a stark difference (absolute unless noted)
THRESH = {
    "global_value_rel": 0.15,    # 15% relative
    "cagr_pp": 0.0075,           # 0.75 pp (global/region); countries use 1.25 pp
    "country_cagr_pp": 0.0125,
    "region_share_pp": 0.03,     # 3 pp of global
    "country_share_pp": 0.05,    # 5 pp of parent
    "segment_pp": 0.05,          # 5 pp of the dimension / parent segment
}

VALIDATOR_SYSTEM = PERSONA.split("How you work:")[0] + """
ROLE: You are the independent VALIDATION LEAD. Two teams sized the same market:
 - the AGENT team, whose numbers come with written reasoning (given), and
 - a HUMAN analyst team, whose numbers come with NO reasoning. For each human number the
   code shows what it IMPLIES (ratios to the OEM market, spend per installed gearbox,
   share vs the OEM signal, growth vs the human's own 2020-25 history).
For every disputed item decide which number is more defensible.

Rules:
 - Do not favour either side by default. The human team is experienced but unexplained;
   the agent team is explained but can be systematically biased (e.g. over-counting
   non-gear service, over-weighting news-driven growth, under-weighting small segments).
 - Judge on physics and economics: installed base and its age, service intensity of a
   gearbox (a gearbox is far less service-intensive than the turbine or compressor it
   sits in), utilisation, prices, capacity additions, geopolitics, and consistency with
   the OEM market data. Use the web evidence where it is relevant.
 - Pinpoint concretely WHY the losing number is wrong (which assumption is implausible,
   by how much, and what evidence contradicts it). Generic statements are not accepted.
 - verdict "agent" -> weight_on_human 0.0-0.3; "human" -> 0.7-1.0; "between" -> 0.3-0.7.

Return json:
{"items": {"<item id>": {
   "verdict": "agent" | "human" | "between",
   "weight_on_human": number,
   "why_agent_may_be_wrong": "specific",
   "why_human_may_be_wrong": "specific (reason from the implied assumptions)",
   "decisive_factors": ["..."],
   "evidence": ["W#", "OEM", "agent reasoning", "human implied assumption"],
   "validated_reasoning": "3-5 sentences a client could read explaining the final number",
   "confidence": "High" | "Medium" | "Low"} ...every item id...}}"""


def _check(ids: list[str]):
    def check(out: dict) -> str | None:
        items = canon_keys(out.get("items") or {}, ids)
        miss = [i for i in ids if i not in items]
        if miss:
            return f"items missing (exact ids): {miss}"
        for i in ids:
            it = items[i] or {}
            v, w = it.get("verdict"), num(it.get("weight_on_human"))
            if v not in ("agent", "human", "between") or w is None or not (0 <= w <= 1):
                return f"{i}: verdict must be agent/human/between and weight_on_human a number 0-1"
            lo, hi = {"agent": (0, 0.3), "human": (0.7, 1), "between": (0.3, 0.7)}[v]
            if not (lo - 1e-9 <= w <= hi + 1e-9):
                return f"{i}: weight_on_human {w} is inconsistent with verdict '{v}' (allowed {lo}-{hi})"
            if len(str(it.get("why_agent_may_be_wrong", ""))) < 30 or len(str(it.get("why_human_may_be_wrong", ""))) < 30:
                return f"{i}: both why_agent_may_be_wrong and why_human_may_be_wrong need specific reasons"
        return None
    return check


def _cagr(a, b, n):
    return (b / a) ** (1 / n) - 1 if a and b and a > 0 and b > 0 else None


# ---------------------------------------------------------------------------------- items
def find_items(state: dict, human: dict, human_mix) -> list[dict[str, Any]]:
    """Every stark disagreement, with the code-computed context for both sides."""
    tax, oem, numbers, sz = state["tax"], state["oem"], state["numbers"], state["sizing"]
    geo, seg = numbers["geo"], numbers["seg"]
    items: list[dict[str, Any]] = []
    g_o = oem["Global"]
    hroot = human[tax.root]
    h_cagr = _cagr(hroot["v25"], hroot["v32"], 7)
    ib = sz["bottom_up"]["installed_units"]
    avg_price = sum(r["installed_units"] * r["oem_asp_usd"] for r in sz["bottom_up"]["by_application"].values()) / ib

    # ---- global value / CAGR
    a_v, h_v = geo["value"][tax.root], hroot["v25"]
    if abs(a_v / h_v - 1) > THRESH["global_value_rel"]:
        items.append({"id": "global_value", "group": "global", "kind": "global_value", "node": tax.root,
                      "agent": a_v, "human": h_v, "unit": "US$ Mn 2025",
                      "context": {
                          "OEM market 2025 (US$ Mn)": round(g_o.v(2025), 1),
                          "agent aftermarket/OEM ratio": round(a_v / g_o.v(2025), 2),
                          "human aftermarket/OEM ratio": round(h_v / g_o.v(2025), 2),
                          "installed base used by agent (units)": round(ib),
                          "agent implied service spend per installed gearbox per year (US$)": round(a_v * 1e6 / ib),
                          "human implied service spend per installed gearbox per year (US$, same installed base)": round(h_v * 1e6 / ib),
                          "average new-unit price of that installed base (US$)": round(avg_price),
                          "agent implied annual spend as % of new-unit price": f"{a_v * 1e6 / ib / avg_price:.2%}",
                          "human implied annual spend as % of new-unit price": f"{h_v * 1e6 / ib / avg_price:.2%}",
                          "agent sizing routes": {"bottom_up": round(sz["bottom_up"]["total_usd_mn"], 1),
                                                  "top_down": round(sz["top_down_usd_mn"], 1),
                                                  "cited_median": sz["cited_median_usd_mn"] and round(sz["cited_median_usd_mn"], 1),
                                                  "weights": sz["weights"]},
                          "agent installed-base parameters": sz["step1"]["installed_base"],
                          "agent spend % by application": {a: r["spend_pct"] for a, r in sz["bottom_up"]["by_application"].items()},
                      },
                      "agent_reasoning": sz["step2"].get("reasoning", "") + " | " +
                      " ".join(f"{a}: {v.get('reasoning', '')}" for a, v in sz["step1"]["spend_pct_of_new_unit_price"].items())[:2500]})
    a_c = geo["cagr"][tax.root]
    if h_cagr is not None and abs(a_c - h_cagr) > THRESH["cagr_pp"]:
        items.append({"id": "global_cagr", "group": "global", "kind": "global_cagr", "node": tax.root,
                      "agent": a_c, "human": h_cagr, "unit": "CAGR 2025-32",
                      "context": {"OEM CAGR 2025-32": f"{g_o.cagr(2025, 2032):.2%}",
                                  "human 2020-25 historical CAGR": f"{_cagr(hroot['v20'], hroot['v25'], 5):.2%}",
                                  "OEM 2020-25 historical CAGR": f"{g_o.cagr(2020, 2025):.2%}"},
                      "agent_reasoning": sz["step1"].get("cagr_reasoning", "")})

    # ---- geography: every parent's children
    raw_by_parent = {tax.root: state.get("regional_raw")} | dict(state.get("country_raw") or {})
    for p in tax.parent_nodes:
        hp = human.get(p)
        if not hp:
            continue
        group = "regions" if p == tax.root else f"countries:{p}"
        is_region = p == tax.root
        sh_t = THRESH["region_share_pp"] if is_region else THRESH["country_share_pp"]
        cg_t = THRESH["cagr_pp"] if is_region else THRESH["country_cagr_pp"]
        po = oem.get(p)
        for k in tax.children_of[p]:
            hv = hp["children"].get(k)
            if hv is None:
                continue
            h_sh = hv / hp["v25"]
            a_sh = geo["share"][k]
            hk = human.get(k, {})
            h_kc = _cagr(hk.get("v25"), hk.get("v32"), 7)
            ko = oem.get(k)
            ctx = {"OEM share of parent 2025": (f"{ko.v(2025) / po.v(2025):.1%}" if ko and po and not ko.scale_uncertain
                                                and not po.scale_uncertain else "n/a"),
                   "human aftermarket/OEM ratio": (round(hv / ko.v(2025), 2) if ko and not ko.scale_uncertain and ko.v(2025) else "n/a"),
                   "agent aftermarket/OEM ratio": (round(geo["value"][k] / ko.v(2025), 2) if ko and not ko.scale_uncertain and ko.v(2025) else "n/a"),
                   "human 2020-25 historical CAGR": (f"{_cagr(hk.get('v20'), hk.get('v25'), 5):.2%}" if hk.get("v20") else "n/a"),
                   "OEM CAGR 2025-32": (f"{ko.cagr(2025, 2032):.2%}" if ko and ko.cagr(2025, 2032) is not None else "n/a")}
            reason = ((raw_by_parent.get(p) or {}).get("items", {}).get(k) or {}).get("reasoning", "")
            if abs(a_sh - h_sh) > sh_t:
                items.append({"id": f"share|{k}", "group": group, "kind": "geo_share", "node": k, "parent": p,
                              "agent": a_sh, "human": h_sh, "unit": f"share of {p}", "context": ctx, "agent_reasoning": reason})
            if h_kc is not None and abs(geo["cagr"][k] - h_kc) > cg_t:
                items.append({"id": f"cagr|{k}", "group": group, "kind": "geo_cagr", "node": k, "parent": p,
                              "agent": geo["cagr"][k], "human": h_kc, "unit": "CAGR 2025-32", "context": ctx,
                              "agent_reasoning": reason})

    # ---- segment mix per leaf group (compared at the group's node level)
    for gname in tax.leaf_groups:
        h = human.get(gname)
        if not h or not h["seg"]:
            continue
        for dim in tax.dimensions:
            hm = human_mix(dim, h["seg"])
            am = seg["rollup"][dim.title].get(gname, {})
            raw = (state.get("segments_raw") or {}).get(f"{gname}||{dim.title}", {})
            om = oem[gname].mix(state["tax"], dim.title) if gname in oem and not oem[gname].scale_uncertain else {}
            for s, parent in dim.rows:
                if s in am and abs(am[s] - hm.get(s, 0)) > THRESH["segment_pp"]:
                    items.append({"id": f"seg|{gname}|{dim.title}|{s}", "group": f"segments:{gname}||{dim.title}",
                                  "kind": "segment_share", "node": gname, "dimension": dim.title, "segment": s,
                                  "agent": am[s], "human": hm.get(s, 0),
                                  "unit": f"share of {parent}" if parent else f"share of {dim.title}",
                                  "context": {"OEM mix (recent new installations)": (f"{om[s]:.1%}" if s in om else "n/a")},
                                  "agent_reasoning": ((raw.get("segments") or {}).get(s) or {}).get("reasoning", "")})
    return items


# ---------------------------------------------------------------------------------- adjudicate
def adjudicate(state: dict, items: list[dict]) -> dict[str, dict]:
    llm = state["llm"]
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["group"], []).append(it)

    def run(gname: str, its: list[dict]) -> dict:
        geos = list({it["node"] for it in its} | {it.get("parent") or "" for it in its} - {""})
        facts = select_facts(state.get("facts", []), themes=("global_size", "regional", "segment", "installed_base",
                                                              "current_events", "pricing"), geos=geos, limit=40)
        blocks = []
        for it in its:
            fmt = (lambda v: f"{v:.2%}") if it["kind"] != "global_value" else (lambda v: f"{v:,.1f}")
            blocks.append(f"ITEM {it['id']}  ({it['unit']})\n  AGENT: {fmt(it['agent'])}   HUMAN: {fmt(it['human'])}\n"
                          f"  CODE-COMPUTED CONTEXT: {dump(it['context'])}\n"
                          f"  AGENT REASONING: {it['agent_reasoning'][:1800] or '(none recorded)'}")
        user = (f"MARKET: {state['cfg'].market_name} (base year 2025, CAGR 2025-2032)\n"
                f"SCOPE: {state['scope'].get('scope_statement', '')}\n\nDISPUTED ITEMS ({gname}):\n\n" + "\n\n".join(blocks)
                + f"\n\nWEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}")
        event("validator.batch", group=gname, items=[it["id"] for it in its])
        return llm.json(f"validator:{gname}", VALIDATOR_SYSTEM, user, check=_check([it["id"] for it in its]),
                        temperature=0.2, retries=4)["items"]

    out: dict[str, dict] = {}
    # global first is not required (verdicts are independent); run all in parallel
    with ThreadPoolExecutor(max_workers=state["cfg"].llm_workers) as ex:
        futs = {g: ex.submit(run, g, its) for g, its in groups.items()}
        for g, f in futs.items():
            out.update(f.result())
    return out


# ---------------------------------------------------------------------------------- apply
def _note(rec: dict, label: str) -> str:
    fmt = (lambda v: f"{v:,.1f}") if rec["kind"] == "global_value" else (lambda v: f"{v:.2%}")
    return (f"[{label}: {rec['unit']} {fmt(rec['agent'])} -> {{FINAL:{rec['id']}}} (verdict {rec.get('verdict')}, "
            f"weight {rec['weight_on_human']:.2f}). {rec.get('validated_reasoning') or ''}] ")


def finalize_notes(vstate: dict, records: list[dict]) -> None:
    """After compute_all: put the number that is actually in the sheet (post normalisation /
    reconciliation) into every validator note, so the reasoning always matches the final value."""
    import re as _re
    geo, tax = vstate["numbers"]["geo"], vstate["tax"]
    final = {}
    for r in records:
        k = r["kind"]
        v = (geo["value"][tax.root] if k == "global_value" else geo["cagr"][tax.root] if k == "global_cagr"
             else geo["share"].get(r["node"]) if k == "geo_share" else geo["cagr"].get(r["node"]) if k == "geo_cagr" else None)
        r["final"] = v if v is not None else r.get("validated")
        final[r["id"]] = (f"{r['final']:,.1f}" if k == "global_value" else f"{r['final']:.2%}") if k in (
            "global_value", "global_cagr", "geo_share", "geo_cagr") else f"{r['final'] * 100:.2f} pp"
    pat = _re.compile(r"\{FINAL:([^}]+)\}")

    def fix(o):
        if isinstance(o, str):
            return pat.sub(lambda m: final.get(m.group(1), "see Validation sheet"), o)
        if isinstance(o, dict):
            return {k: fix(v) for k, v in o.items()}
        if isinstance(o, list):
            return [fix(v) for v in o]
        return o
    for key in ("sizing", "regional_raw", "country_raw", "segments_raw"):
        if vstate.get(key) is not None:
            vstate[key] = fix(vstate[key])


def apply(state: dict, items: list[dict], verdicts: dict[str, dict], human: dict, human_mix,
          label: str = "Validated against the human sheet") -> tuple[dict, list[dict]]:
    """Return updated raw analyst outputs + one record per item. Every moved number also gets the
    validator's reasoning written into its own reasoning field, so the sheet explains the final value."""
    tax = state["tax"]
    upd = {k: copy.deepcopy(state.get(k)) for k in ("sizing", "regional_raw", "country_raw", "segments_raw")}
    records = []
    for it in items:
        v = verdicts.get(it["id"]) or {}
        w = float(num(v.get("weight_on_human")) or 0.0)
        val = it["agent"] + w * (it["human"] - it["agent"])
        rec = {**{k: it[k] for k in ("id", "kind", "node", "unit", "agent", "human")}, "weight_on_human": w,
               "validated": val, **{k: v.get(k) for k in ("verdict", "why_agent_may_be_wrong", "why_human_may_be_wrong",
                                                        "decisive_factors", "evidence", "validated_reasoning", "confidence")}}
        records.append(rec)
        event("validator.verdict", item=it["id"], verdict=v.get("verdict"), weight_on_human=round(w, 3),
              agent=round(it["agent"], 5), human=round(it["human"], 5), validated=round(val, 5))
        k = it["kind"]
        moved = abs(val - it["agent"]) > 1e-12
        if k == "global_value":
            upd["sizing"]["global_value_2025"] = val
            if moved:
                upd["sizing"]["step2"]["reasoning"] = _note(rec, label) + upd["sizing"]["step2"].get("reasoning", "")
        elif k == "global_cagr":
            upd["sizing"]["global_cagr"] = val
            if moved:
                upd["sizing"]["step1"]["cagr_reasoning"] = _note(rec, label) + upd["sizing"]["step1"].get("cagr_reasoning", "")
        elif k in ("geo_share", "geo_cagr"):
            p = it["parent"]
            holder = upd["regional_raw"] if p == tax.root else upd["country_raw"][p]
            node_item = holder["items"][it["node"]]
            node_item["share" if k == "geo_share" else "cagr"] = val
            node_item.setdefault("validation", []).append(rec)
            if moved:
                node_item["reasoning"] = _note(rec, label) + node_item.get("reasoning", "")
        elif k == "segment_share":
            # move every country in the group by the same weight towards its own human mix
            dim = tax.dimension(it["dimension"])
            raw = upd["segments_raw"][f"{it['node']}||{dim.title}"]
            for c in tax.leaf_groups[it["node"]]:
                hc = human.get(c)
                if not hc or not hc["seg"]:
                    continue
                hm = human_mix(dim, hc["seg"])
                sh = raw["countries"][c]["shares"]
                a = float(num(sh.get(it["segment"])) or 0.0)
                sh[it["segment"]] = a + w * (hm.get(it["segment"], 0.0) - a)
            sgi = raw["segments"].setdefault(it["segment"], {})
            sgi.setdefault("validation", []).append(rec)
            if moved:
                sgi["reasoning"] = _note(rec, label) + sgi.get("reasoning", "")
                for c in tax.leaf_groups[it["node"]]:
                    why = raw["countries"].get(c, {}).setdefault("why", {})
                    why[it["segment"]] = f"[{label}: share moved towards the reference, weight {w:.2f}] " + str(why.get(it["segment"], ""))
    return upd, records
