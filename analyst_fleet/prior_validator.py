"""Reference-free validator: judges the agent's Input Sheet against priors learned from past human
sheets (data/corpus), so it works for markets that have no human sheet.

1. The target market is classified like the corpus markets and its analogs are retrieved.
2. Code computes the agent sheet's scale-free features (regional shares, growth spreads, segment
   concentration, country-mix spread ...) and compares each with its prior band. Anything outside
   the dispute band (analog p10-p90 widened by 1/4 of its width; 81% coverage on held-out markets)
   becomes an item, with the analogs, the verified rules that apply and the agent's own reasoning.
3. The validation lead decides per item: keep the agent's number (market-specific evidence beats
   the pattern), move to the prior, or in between, and writes the reasoning for the final number.
4. Code moves numbers towards the nearest edge of the prior's interquartile range (not the median:
   inside the typical range is enough), re-runs the normal arithmetic, and writes the reasoning
   into the moved number's own reasoning field.
"""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .agents.common import PERSONA, canon_keys, dump, fmt_facts, num, select_facts
from .corpus.features import canon_country, features, macro_of
from .corpus.llm_tasks import classify_market
from .corpus.priors import Library, rule_applies, rule_holds
from .syslog import event
from .validator import _note, apply as apply_geo

LABEL = "Validated against corpus priors"


# ----------------------------------------------------------------------------- views of a sheet
def _desc_leaves(tax, node: str) -> list[str]:
    kids = tax.children_of.get(node) or []
    return [node] if not kids else [l for k in kids for l in _desc_leaves(tax, k)]


def _seg_rows(tax, numbers: dict, node: str, n: int = 7) -> dict[str, list]:
    """Absolute 2025 / 2032 segment values for any node, summed bottom-up from its leaves."""
    geo, seg = numbers["geo"], numbers["seg"]
    out = {}
    for dim in tax.dimensions:
        acc: dict[str, list[float]] = {}
        for leaf in _desc_leaves(tax, node):
            sh, cg = seg["shares"][dim.title].get(leaf), seg["seg_cagr"][dim.title].get(leaf)
            if not sh:
                continue
            v = geo["value"][leaf]
            for s, parent in dim.rows:
                a = v * sh.get(s, 0) * (sh.get(parent, 1) if parent else 1)
                b = a * (1 + (cg or {}).get(s, geo["cagr"][leaf])) ** n
                acc.setdefault(s, [0.0, 0.0])
                acc[s][0] += a
                acc[s][1] += b
        out[dim.title] = [(s, parent, *acc.get(s, (0.0, 0.0))) for s, parent in dim.rows]
    return out


def view_from_state(state: dict) -> dict[str, Any]:
    tax, numbers = state["tax"], state["numbers"]
    geo = numbers["geo"]
    v = {g: (None, geo["value"][g], geo["value"][g] * (1 + geo["cagr"][g]) ** 7) for g in geo["value"]}
    kids = {p: list(tax.children_of[p]) for p in tax.parent_nodes}
    seg = {g: _seg_rows(tax, numbers, g) for g in [tax.root] + list(tax.leaves)}
    return {"root": tax.root, "v": v, "n_fc": 7, "kids": kids, "seg": seg, "price_cagr": None}


def view_from_human(tax, hum: dict) -> dict[str, Any]:
    """The held-out human gearbox sheet in the same form (history 2020-25 = 5 years)."""
    v = {}
    for g in [tax.root] + [k for p in tax.parent_nodes for k in tax.children_of[p]]:
        h = hum.get(g)
        p = tax.parent_of.get(g)
        blk = (hum.get(p) or {}).get("children", {}).get(g) if p else None
        if h:
            scale = (blk / h["v25"]) if blk and h["v25"] else 1.0      # parent block wins (stale regional files)
            v[g] = (h.get("v20", 0) * scale or None, h["v25"] * scale, h["v32"] * scale)
    kids = {p: [k for k in tax.children_of[p] if k in v] for p in tax.parent_nodes if p in v}
    seg = {}
    for g in [tax.root] + list(tax.leaves):
        h = hum.get(g)
        if h and h["seg"]:
            seg[g] = {d.title: [(s, parent, *h["seg"].get(s, (0.0, 0.0))) for s, parent in d.rows] for d in tax.dimensions}
    return {"root": tax.root, "v": v, "n_fc": 7, "n_hist": 5, "kids": kids, "seg": seg, "price_cagr": None}


# ----------------------------------------------------------------------------- items
def target_record(state: dict) -> dict:
    tax, cfg = state["tax"], state["cfg"]
    segs = [d.title for d in tax.dimensions] + [s for d in tax.dimensions for s in d.all_segments]
    cls = classify_market(state["llm"], cfg.market_name, segs)
    return {"name": cfg.market_name, "family": None, "class": cls, "segments": segs}


def _iqr_target(x: float, b: dict) -> float:
    return min(max(x, b["p25"]), b["p75"])


def find_items(state: dict, lib: Library, rules: list[dict], target: dict) -> tuple[list[dict], dict]:
    tax, numbers = state["tax"], state["numbers"]
    geo = numbers["geo"]
    feats = features(view_from_state(state))
    kept = [r for r in rules if r["status"] == "kept"]
    pseudo = {"class": target["class"], "features": feats}
    root_c = geo["cagr"][tax.root]
    raw_by_parent = {tax.root: state.get("regional_raw")} | dict(state.get("country_raw") or {})

    def reason(node):
        p = tax.parent_of.get(node)
        return (((raw_by_parent.get(p) or {}).get("items") or {}).get(node) or {}).get("reasoning", "") if p else \
            state["sizing"]["step1"].get("cagr_reasoning", "")

    def rules_for(f):
        return [{"id": r["id"], "kind": r["kind"], "statement": r["statement"], "support": r["support"],
                 "holds_for_agent": rule_holds(r, feats)}
                for r in kept if (r["lhs"] == f or r.get("rhs") == f) and rule_applies(r, pseudo)]

    items, checks = [], {}
    for f, x in feats.items():
        if "_is_" in f:                       # binary descriptors are reported via their rules, not banded
            continue
        b = lib.band(target, f)
        if not b or b["n"] < 5:
            continue
        out = not (b["lo"] <= x <= b["hi"])
        checks[f] = {"agent": x, **{k: b[k] for k in ("p10", "p25", "p50", "p75", "p90", "lo", "hi", "n", "source")}, "outside": out}
        if not out:
            continue
        base = {"feature": f, "band": {k: b[k] for k in ("p10", "p25", "p50", "p75", "p90", "n", "source")},
                "analogs": b["markets"], "rules": rules_for(f)}
        if f == "growth.fcst_cagr":
            items.append({**base, "id": "global_cagr", "group": "global", "kind": "global_cagr", "node": tax.root,
                          "agent": root_c, "human": _iqr_target(root_c, b), "unit": "CAGR 2025-32", "agent_reasoning": reason(tax.root)})
        elif f.startswith("region.share.") or f.startswith("region.cagr_gap."):
            m = f.rsplit(".", 1)[1]
            nodes = [k for k in tax.children_of[tax.root] if macro_of(k) == m]
            if len(nodes) != 1:
                continue
            k = nodes[0]
            if f.startswith("region.share."):
                items.append({**base, "id": f"share|{k}", "group": "regions", "kind": "geo_share", "node": k, "parent": tax.root,
                              "agent": geo["share"][k], "human": _iqr_target(geo["share"][k], b), "unit": f"share of {tax.root}",
                              "agent_reasoning": reason(k)})
            else:
                items.append({**base, "id": f"cagr|{k}", "group": "regions", "kind": "geo_cagr", "node": k, "parent": tax.root,
                              "agent": geo["cagr"][k], "human": root_c + _iqr_target(x, b), "unit": "CAGR 2025-32", "band_offset": root_c,
                              "agent_reasoning": reason(k)})
        elif f.startswith("country.share."):
            cn = f.split(".", 2)[2]
            nodes = [k for p in tax.parent_nodes if p != tax.root for k in tax.children_of[p] if canon_country(k) == cn
                     and macro_of(p)]
            if len(nodes) != 1:
                continue
            k = nodes[0]
            items.append({**base, "id": f"share|{k}", "group": f"countries:{tax.parent_of[k]}", "kind": "geo_share", "node": k,
                          "parent": tax.parent_of[k], "agent": geo["share"][k], "human": _iqr_target(geo["share"][k], b),
                          "unit": f"share of {tax.parent_of[k]}", "agent_reasoning": reason(k)})
        elif f in ("seg.cagr_spread", "seg.cagr_dev"):
            if any(i["kind"] == "seg_premium_scale" for i in items):
                continue
            samples = []
            for key, raw in list((state.get("segments_raw") or {}).items())[:4]:
                for s, it in list((raw.get("segments") or {}).items())[:3]:
                    samples.append(f"{key} / {s}: premium {num(it.get('growth_premium')) or 0:+.2%} - {str(it.get('growth_reasoning', ''))[:300]}")
            items.append({**base, "id": "segments|growth_spread", "group": "segments", "kind": "seg_premium_scale", "node": tax.root,
                          "agent": x, "human": _iqr_target(x, b), "unit": f"{f} (pp)", "agent_reasoning": "\n".join(samples)})
        elif f == "seg.country_mix_sd":
            items.append({**base, "id": "segments|country_mix", "group": "segments", "kind": "seg_country_mix", "node": tax.root,
                          "agent": x, "human": _iqr_target(x, b), "unit": "std of segment share across countries",
                          "agent_reasoning": "Country analysts set each country's mix individually with a written 'why' per cell "
                                             "(Country Segment Reasoning sheet)."})
        elif f == "country.cagr_gap_sd":
            items.append({**base, "id": "countries|cagr_spread", "group": "countries", "kind": "country_cagr_spread",
                          "node": tax.root, "agent": x, "human": _iqr_target(x, b),
                          "unit": "std of country CAGR around its region", "agent_reasoning": "see the country rows of Geography Reasoning"})
    return items, {"features": feats, "checks": checks}


# ----------------------------------------------------------------------------- adjudicate
PRIOR_SYSTEM = PERSONA.split("How you work:")[0] + """
ROLE: You are the independent VALIDATION LEAD. There is no human reference sheet for this market.
Instead you have the house's track record: priors learned from dozens of past market estimates
(bands of shares and growth spreads across similar markets, and rules verified on held-out markets).
For every item the agent's number sits outside the band seen in comparable markets. Decide:
 - "agent": the agent's reasoning gives market-specific evidence that this market really differs
   from the analogs (e.g. the installed base, regulation or demand really is located elsewhere);
 - "prior": the agent's reasoning does not justify the outlier (generic, news-driven, double-counted,
   or contradicted by the analogs' structure) - pitch towards the prior;
 - "between": partly justified.
Weigh how comparable the analogs are (band 'source': analogs > category > all) and the rule kind:
'economics' rules describe how markets behave; 'house_convention' rules describe how the team builds
sheets - follow them only when the agent's deviation is not backed by evidence.
Pinpoint WHY the losing side is wrong (which assumption, by how much). Generic statements are rejected.
verdict "agent" -> weight_on_prior 0.0-0.3; "prior" -> 0.7-1.0; "between" -> 0.3-0.7.
The 'validated_reasoning' is printed next to the final number in the client sheet: 3-5 sentences that
explain the final value on its own merits (market mechanism + why it differs or not from analogs).
Do NOT quote the final value as a number - the arithmetic re-normalises shares afterwards and code
inserts the exact final number next to your text.

Return json:
{"items": {"<item id>": {"verdict": "agent" | "prior" | "between", "weight_on_prior": number,
   "why_agent_may_be_wrong": "specific", "why_prior_may_not_apply": "specific",
   "decisive_factors": ["..."], "evidence": ["W#", "analogs", "rule R#", "agent reasoning"],
   "validated_reasoning": "...", "confidence": "High" | "Medium" | "Low"} ...every item id...}}"""


def _check(ids: list[str]):
    def check(out: dict) -> str | None:
        items = canon_keys(out.get("items") or {}, ids)
        miss = [i for i in ids if i not in items]
        if miss:
            return f"items missing (exact ids): {miss}"
        for i in ids:
            it = items[i] or {}
            v, w = it.get("verdict"), num(it.get("weight_on_prior"))
            if v not in ("agent", "prior", "between") or w is None or not (0 <= w <= 1):
                return f"{i}: verdict must be agent/prior/between and weight_on_prior a number 0-1"
            lo, hi = {"agent": (0, 0.3), "prior": (0.7, 1), "between": (0.3, 0.7)}[v]
            if not (lo - 1e-9 <= w <= hi + 1e-9):
                return f"{i}: weight_on_prior {w} inconsistent with verdict '{v}' (allowed {lo}-{hi})"
            if len(str(it.get("why_agent_may_be_wrong", ""))) < 30 or len(str(it.get("why_prior_may_not_apply", ""))) < 30 \
                    or len(str(it.get("validated_reasoning", ""))) < 80:
                return f"{i}: specific why_agent_may_be_wrong, why_prior_may_not_apply and a full validated_reasoning are required"
        return None
    return check


def adjudicate(state: dict, items: list[dict], target: dict) -> dict[str, dict]:
    llm = state["llm"]
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["group"], []).append(it)

    def fmt(it, v):
        return f"{v:.2%}" if "share" in it["unit"] or "CAGR" in it["unit"] else f"{v * 100:.2f} pp"

    def run(g: str, its: list[dict]) -> dict:
        geos = list({it["node"] for it in its} | {it.get("parent") or "" for it in its} - {""})
        facts = select_facts(state.get("facts", []), themes=("regional", "segment", "installed_base", "current_events"),
                             geos=geos, limit=30)
        blocks = []
        for it in its:
            off = it.get("band_offset", 0.0)        # gap bands shown as absolute CAGRs (parent + gap)
            b = {k: (v + off if isinstance(v, float) and k.startswith("p") else v) for k, v in it["band"].items()}
            blocks.append(
                f"ITEM {it['id']}  ({it['unit']}; feature {it['feature']})\n"
                f"  AGENT: {fmt(it, it['agent'])}   PRIOR BAND ({b['source']}, n={b['n']}): p10 {fmt(it, b['p10'])} | "
                f"p25 {fmt(it, b['p25'])} | median {fmt(it, b['p50'])} | p75 {fmt(it, b['p75'])} | p90 {fmt(it, b['p90'])}\n"
                f"  IF MOVED, TARGET (nearest edge of p25-p75): {fmt(it, it['human'])}\n"
                f"  ANALOG MARKETS (name, value, similarity): {dump(it['analogs'])}\n"
                f"  VERIFIED RULES: {dump(it['rules']) or 'none'}\n"
                f"  AGENT REASONING: {it['agent_reasoning'][:1800] or '(none recorded)'}")
        user = (f"MARKET: {state['cfg'].market_name} (base year 2025, CAGR 2025-2032)\n"
                f"CLASSIFICATION: {dump(target['class'])}\nSCOPE: {state['scope'].get('scope_statement', '')}\n\n"
                f"DISPUTED ITEMS ({g}):\n\n" + "\n\n".join(blocks) + f"\n\nWEB EVIDENCE (verified quotes):\n{fmt_facts(facts)}")
        event("validator.batch", mode="priors", group=g, items=[it["id"] for it in its])
        return llm.json(f"prior_validator:{g}", PRIOR_SYSTEM, user, check=_check([it["id"] for it in its]),
                        temperature=0.2, retries=4)["items"]

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=state["cfg"].llm_workers) as ex:
        futs = {g: ex.submit(run, g, its) for g, its in groups.items()}
        for g, f in futs.items():
            out.update(canon_keys(f.result(), [it["id"] for it in groups[g]]))
    return out


# ----------------------------------------------------------------------------- apply
def apply(state: dict, items: list[dict], verdicts: dict[str, dict]) -> tuple[dict, list[dict]]:
    tax, geo = state["tax"], state["numbers"]["geo"]
    as_human = {i: {**v, "weight_on_human": v.get("weight_on_prior"),
                    "why_human_may_be_wrong": v.get("why_prior_may_not_apply")} for i, v in verdicts.items()}
    geo_items = [it for it in items if it["kind"] in ("global_cagr", "geo_share", "geo_cagr")]
    upd, records = apply_geo(state, geo_items, as_human, {}, None, label=LABEL)
    upd["segments_raw"] = upd["segments_raw"] or copy.deepcopy(state.get("segments_raw"))
    for it in items:
        if it in geo_items:
            continue
        v = as_human.get(it["id"]) or {}
        w = float(num(v.get("weight_on_human")) or 0.0)
        factor = it["human"] / it["agent"] if it["agent"] else 1.0
        lam = 1 - w * (1 - factor)                  # 1 = keep, factor = full move to the prior edge
        val = it["agent"] * lam
        rec = {**{k: it[k] for k in ("id", "kind", "node", "unit", "agent", "human")}, "weight_on_human": w, "validated": val,
               **{k: v.get(k) for k in ("verdict", "why_agent_may_be_wrong", "why_human_may_be_wrong", "decisive_factors",
                                        "evidence", "validated_reasoning", "confidence")}}
        records.append(rec)
        event("validator.verdict", mode="priors", item=it["id"], verdict=v.get("verdict"), weight_on_prior=round(w, 3),
              agent=round(it["agent"], 5), prior=round(it["human"], 5), validated=round(val, 5), scale=round(lam, 3))
        if abs(lam - 1) < 1e-9:
            continue
        note = (f"[{LABEL}: {it['unit']} {it['agent'] * 100:.2f} -> {{FINAL:{it['id']}}}, scale x{lam:.2f} "
                f"(verdict {v.get('verdict')}). {v.get('validated_reasoning', '')}] ")
        if it["kind"] == "seg_premium_scale":
            for raw in upd["segments_raw"].values():
                for s, si in (raw.get("segments") or {}).items():
                    if num(si.get("growth_premium")) is not None:
                        si["growth_premium"] = num(si["growth_premium"]) * lam
                        si["growth_reasoning"] = note + str(si.get("growth_reasoning", ""))
                for c in (raw.get("countries") or {}).values():
                    ov = c.get("growth_premium_overrides") or {}
                    for s in list(ov):
                        if num(ov[s]) is not None:
                            ov[s] = num(ov[s]) * lam
        elif it["kind"] == "seg_country_mix":
            for raw in upd["segments_raw"].values():
                gv = {s: num(si.get("group_share_view")) for s, si in (raw.get("segments") or {}).items()}
                for c in (raw.get("countries") or {}).values():
                    sh = c.get("shares") or {}
                    for s in list(sh):
                        if gv.get(s) is not None and num(sh[s]) is not None:
                            sh[s] = gv[s] + lam * (num(sh[s]) - gv[s])
                            why = c.setdefault("why", {})
                            why[s] = f"[{LABEL}: pulled x{lam:.2f} towards the group view] " + str(why.get(s, ""))
        elif it["kind"] == "country_cagr_spread":
            upd["country_raw"] = upd.get("country_raw") or copy.deepcopy(state.get("country_raw"))
            for p, raw in upd["country_raw"].items():
                if p not in geo["cagr"]:
                    continue
                for k, ci in (raw.get("items") or {}).items():
                    if k in geo["cagr"]:
                        ci["cagr"] = geo["cagr"][p] + lam * (geo["cagr"][k] - geo["cagr"][p])
                        ci["reasoning"] = note + str(ci.get("reasoning", ""))
    return upd, records
