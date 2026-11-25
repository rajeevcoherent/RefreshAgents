"""LangGraph orchestration.

 scope -> research -> sizing -> regional -> countries -> segments -> pricing -> compute -> review
                                                                                  ^          |
                                                         refine (targeted redo) --+-- if score < min and passes left
 review -> finalize (restore best-scoring pass) -> write -> END
"""
from __future__ import annotations

import copy
import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import compute
from .syslog import event
from .agents.geo import countries_node, regional_node
from .agents.pricing import pricing_node
from .agents.research import research_node, scope_node
from .agents.review import review
from .agents.segments import segments_node
from .agents.sizing import sizing_node as _sizing_gearbox
from .agents.sizing_generic import sizing_node as _sizing_generic

log = logging.getLogger("fleet.graph")

def sizing_node(state: dict) -> dict:
    """Gearbox installed-base sizing, or the generic volume x price sizing for scope-sheet markets."""
    return (_sizing_generic if getattr(state["cfg"], "generic", False) else _sizing_gearbox)(state)


RAW_KEYS = ("sizing", "regional_raw", "country_raw", "segments_raw", "pricing_raw")


class FleetState(TypedDict, total=False):
    cfg: Any
    tax: Any
    llm: Any
    web: Any
    oem: Any
    definitions_text: str
    oem_definitions_text: str
    segment_definitions: dict
    scope_rules: str
    scope: dict
    evidence: list
    facts: list
    blocked_sources: int
    sizing: dict
    regional_raw: dict
    country_raw: dict
    segments_raw: dict
    pricing_raw: dict
    numbers: dict
    review: dict
    reviews: list
    critique: dict
    pass_n: int
    snapshots: list
    best_pass: int
    out_path: str
    prior_check: dict
    validation: list
    validation_mode: str
    log: list


def compute_node(state: FleetState) -> dict:
    numbers = compute.compute_all(state)
    hard = [f for f in numbers["flags"] if f["severity"] == "hard"]
    if hard:
        log.warning("hard flags: %s", hard[:5])
    n = state.get("pass_n", 1)
    for a in numbers["adjustments"]:
        event("restriction.adjust", pass_n=n, stage=a["stage"], kind=a["kind"], scope=a["scope"], detail=a["detail"])
    for f in numbers["flags"]:
        event("restriction.flag", pass_n=n, severity=f["severity"], check=f["check"], scope=f["scope"], message=f["message"])
    event("compute", pass_n=n, global_value=round(numbers["geo"]["value"][state["tax"].root], 2),
          global_cagr=round(numbers["geo"]["cagr"][state["tax"].root], 4),
          adjustments=len(numbers["adjustments"]), flags=len(numbers["flags"]), hard=len(hard))
    return {"numbers": numbers}


def review_node(state: FleetState) -> dict:
    rv = review(state, state["numbers"])
    n = state.get("pass_n", 1)
    snap = {"pass": n, "overall": float(rv.get("overall") or 0), "review": rv,
            **{k: copy.deepcopy(state.get(k)) for k in RAW_KEYS}}
    log.info("pass %d review overall %.1f, %d issues", n, snap["overall"], len(rv.get("issues") or []))
    event("review", pass_n=n, overall=snap["overall"], scores=rv.get("scores"), n_issues=len(rv.get("issues") or []))
    for i in rv.get("issues") or []:
        event("review.issue", pass_n=n, target=i.get("target"), problem=i.get("problem"), instruction=i.get("instruction"))
    return {"review": rv, "reviews": (state.get("reviews") or []) + [{"pass": n, **rv}],
            "snapshots": (state.get("snapshots") or []) + [snap],
            "log": state.get("log", []) + [f"review pass {n}: overall {snap['overall']:.1f}"]}


def _route(state: FleetState) -> str:
    cfg = state["cfg"]
    rv = state["review"]
    if state.get("pass_n", 1) >= cfg.max_passes:
        return "finalize"
    hard = [f for f in state["numbers"]["flags"] if f["severity"] == "hard"]
    if float(rv.get("overall") or 0) >= cfg.min_review_score and not hard:
        return "finalize"
    return "refine" if rv.get("issues") else "finalize"


def refine_node(state: FleetState) -> dict:
    tax = state["tax"]
    issues = (state["review"].get("issues") or [])[:8]
    critique: dict[str, str] = {}
    for i in issues:
        t = str(i.get("target", "")).strip()
        critique[t] = (critique.get(t, "") + f"\n- Problem: {i.get('problem', '')}\n  Instruction: {i.get('instruction', '')}").strip()
    upd: dict[str, Any] = {"critique": critique, "pass_n": state.get("pass_n", 1) + 1}
    st = {**state, **upd}
    if "sizing" in critique:
        st.update(sizing_node(st))
    if "regional" in critique:
        st.update(regional_node(st))
    parents = [t.split(":", 1)[1] for t in critique if t.startswith("country:") and t.split(":", 1)[1] in tax.parent_nodes]
    if parents:
        st.update(countries_node(st, only=parents))
    seg_keys = [t.split(":", 1)[1] for t in critique if t.startswith("segments:")]
    valid = {f"{g}||{d.title}" for g in tax.leaf_groups for d in tax.dimensions}
    seg_keys = [k for k in seg_keys if k in valid]
    if seg_keys:
        st.update(segments_node(st, only=seg_keys))
    if "pricing" in critique:
        st.update(pricing_node(st))
    redone = list(critique)
    event("batch.refine", pass_n=upd["pass_n"], targets=redone,
          ignored=[t for t in critique if t.startswith("country:") and t.split(":", 1)[1] not in tax.parent_nodes]
          + [t for t in critique if t.startswith("segments:") and t.split(":", 1)[1] not in valid])
    return {**{k: st.get(k) for k in RAW_KEYS}, **upd,
            "log": state.get("log", []) + [f"refine pass {upd['pass_n']}: redid {redone}"]}


def finalize_node(state: FleetState) -> dict:
    snaps = state.get("snapshots") or []
    best = max(snaps, key=lambda s: s["overall"]) if snaps else None
    if not best:
        return {}
    upd = {k: best[k] for k in RAW_KEYS}
    numbers = compute.compute_all({**state, **upd})
    event("finalize", kept_pass=best["pass"], overall=best["overall"],
          passes=[(s["pass"], s["overall"]) for s in snaps])
    return {**upd, "numbers": numbers, "review": best["review"], "best_pass": best["pass"],
            "log": state.get("log", []) + [f"finalize: kept pass {best['pass']} (overall {best['overall']:.1f})"]}


def _wrap(fn):
    def node(state):
        return fn(state)
    node.__name__ = fn.__name__
    return node


def build_graph(write_fn):
    g = StateGraph(FleetState)
    g.add_node("scope", _wrap(scope_node))
    g.add_node("research", _wrap(research_node))
    g.add_node("sizing", _wrap(sizing_node))
    g.add_node("regional", _wrap(regional_node))
    g.add_node("countries", lambda s: countries_node(s))
    g.add_node("segments", lambda s: segments_node(s))
    g.add_node("pricing", _wrap(pricing_node))
    g.add_node("compute", compute_node)
    g.add_node("review", review_node)
    g.add_node("refine", refine_node)
    g.add_node("finalize", finalize_node)
    g.add_node("write", write_fn)
    g.set_entry_point("scope")
    for a, b in [("scope", "research"), ("research", "sizing"), ("sizing", "regional"), ("regional", "countries"),
                 ("countries", "segments"), ("segments", "pricing"), ("pricing", "compute"), ("compute", "review"),
                 ("refine", "compute"), ("finalize", "write"), ("write", END)]:
        g.add_edge(a, b)
    g.add_conditional_edges("review", _route, {"refine": "refine", "finalize": "finalize"})
    return g.compile()
