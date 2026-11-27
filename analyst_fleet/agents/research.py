"""Scope analyst + evidence researcher.

Scope: reads the client's definition document and states, in its own words, what is
in and out of the market and the value basis every later analyst must use.

Research: plans web queries like an analyst would, runs them, reads the most useful
pages and extracts numeric facts. A fact is kept only if its quote is found
verbatim in the source text (the LLM cannot invent a sourced number).
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from ..evidence.web import Web, relevant_passages
from ..syslog import event
from . import generic as G
from .common import PERSONA, domain_of, dump

log = logging.getLogger("fleet.research")

SCOPE_SYSTEM = PERSONA + """

TASK: You are the scoping analyst. Read the client's market definition documents and state the
BOUNDARY of the market every downstream analyst will use. You define WHAT is counted, never HOW
MUCH: no percentages, ratios, shares, intervals, multipliers, prices or money amounts anywhere in
your answer (those are set later by the sizing analysts from evidence). Years are allowed.
Stay with the documents. Every inclusion and exclusion carries its source:
  "client"  = stated in the client documents (paraphrase faithfully)
  "analyst" = your interpretation of a boundary the documents leave open - keep these few and say why.
Return json:
{
 "scope_statement": "one paragraph: what revenue is counted (boundary only)",
 "value_basis": "what a dollar in this market is (who pays whom, for what)",
 "volume_unit": "what one unit of volume means for an aftermarket",
 "inclusions": [{"item": "...", "source": "client" | "analyst", "why": "only for analyst items"}],
 "exclusions": [{"item": "...", "source": "client" | "analyst", "why": "only for analyst items"}],
 "allocation_rules": ["how work is allocated to applications and verticals, from the documents"],
 "boundary_risks": ["where analysts most often double count or over-scope, e.g. counting whole compressor aftermarket instead of the gear system"],
 "installed_base_logic": "qualitative: how the aftermarket relates to the installed base of OEM units (no numbers)",
 "key_demand_drivers": [..],
 "search_keywords": ["6-10 short phrases a researcher should search"]
}"""

_NUMERIC = re.compile(r"(\d\s*%|\bper ?cent\b|\b0\.\d|\b\d+(\.\d+)?\s*x\b|\b\d+(\.\d+)?\s*times\b|US\$\s*\d|USD\s*\d|\$\s*\d|"
                      r"\b\d{1,2}\s*(-|to|–)\s*\d{1,2}\s*(years?|yrs?)\b|\b\d{1,2}\s*(years?|yrs?)\b)", re.I)


def _check_scope(out: dict) -> str | None:
    """Scope = boundary only. Numbers that would anchor sizing are rejected and sent back."""
    for k in ("inclusions", "exclusions"):
        items = out.get(k)
        if not isinstance(items, list) or not items:
            return f"{k} must be a non-empty list of objects with item and source"
        for it in items:
            if not isinstance(it, dict) or it.get("source") not in ("client", "analyst") or not it.get("item"):
                return f"every {k} entry needs item and source ('client' or 'analyst')"
            if it["source"] == "analyst" and len(str(it.get("why", ""))) < 15:
                return f"analyst-added {k} entry '{str(it['item'])[:60]}' needs a why"
    text = json.dumps({k: v for k, v in out.items() if k != "search_keywords"}, ensure_ascii=False)
    hits = sorted({m.group(0) for m in _NUMERIC.finditer(text)})
    if hits:
        return ("the scope must define WHAT is counted, not HOW MUCH - remove every percentage, ratio, multiplier, "
                f"interval, price or money amount (found: {hits[:8]}); state boundaries only")
    return None


PLAN_SYSTEM = PERSONA + """

TASK: You are the research lead. Plan web searches that would let an analyst size this
aftermarket and split it by region, country, application and industry vertical, and set
growth rates and service prices. Cover these themes (theme names are fixed):
 global_size      - market size of THIS aftermarket and the closest adjacent markets
                    (gearbox repair/MRO, turbomachinery services, high-speed gear units, industrial gearbox market)
 installed_base   - fleet counts: LNG trains, compressor stations, steam/gas turbine fleet, ammonia plants, refineries
 pricing          - cost of gearbox overhaul / repair / spare gear sets / service contracts
 players          - service revenues or footprint of the main OEMs and independent repairers
 regional         - activity drivers per region and key countries (capex, LNG, petrochemical, fertilizer, power, pipelines)
 segment          - application / vertical specific drivers (IGC, turboexpanders, FPSO, cement WHR, steel blowers ...)
 current_events   - 2024-2026 events: LNG FIDs, sanctions, tariffs, plant closures, energy-security policy
Return json: {"queries": [{"q": "search string", "theme": "<theme>"}]} with 26-32 queries,
specific and varied (name countries, OEMs, equipment). Do not use any publisher name in a query."""

EXTRACT_SYSTEM = """You extract factual, numeric statements that are useful for sizing the
industrial high-speed helical gearbox aftermarket (and adjacent turbomachinery / gearbox
service markets, installed fleets, capacity additions, prices, growth rates).
For each item of text you receive, return facts as json:
{"facts": [{"item": "<item id>", "claim": "one-sentence restatement with the number, metric, geography and year",
            "quote": "the exact words copied verbatim from the text that contain the number (<=200 chars)",
            "geo": "geography or null", "year": 2024 or null, "metric": "short metric name",
            "relevance": 1-5}]}
Rules: copy quotes exactly (they are checked character by character); skip marketing fluff
without numbers; skip facts with relevance < 2; at most 6 facts per item."""

_FALLBACK_QUERIES = [
    ("high speed gearbox repair overhaul services market size", "global_size"),
    ("turbomachinery aftermarket services market size 2025", "global_size"),
    ("industrial gearbox aftermarket market size 2025", "global_size"),
    ("integrally geared compressor service bull gear pinion repair", "segment"),
    ("LNG trains global installed capacity 2025 mtpa", "installed_base"),
    ("gas compressor stations number pipeline network", "installed_base"),
    ("gearbox overhaul cost turbine generator reduction gear", "pricing"),
    ("Flender service revenue aftermarket share", "players"),
    ("Voith turbo BHS high speed gear service", "players"),
    ("Middle East LNG expansion Qatar North Field 2026", "current_events"),
    ("China petrochemical capacity additions 2025", "regional"),
    ("India fertilizer ammonia urea plant capacity 2025", "regional"),
]


def _generic(state: dict) -> bool:
    return bool(getattr(state["cfg"], "generic", False))


def scope_node(state: dict) -> dict:
    llm, cfg = state["llm"], state["cfg"]
    out = llm.json("scope", G.SCOPE if _generic(state) else SCOPE_SYSTEM,
                   f"MARKET: {cfg.market_name}\nBASE YEAR {cfg.base_year}, FORECAST TO {cfg.forecast_end_year}\n\n"
                   f"CLIENT DEFINITION DOCUMENT:\n{state['definitions_text']}"
                   + (f"\n\nRELATED OEM (new-unit) DEFINITION, for boundary only:\n{state['oem_definitions_text'][:3500]}"
                      if state.get("oem_definitions_text") else ""),
                   tier="fast", temperature=0.2, check=_check_scope, retries=4)
    event("scope.sources", client_inclusions=sum(i["source"] == "client" for i in out["inclusions"]),
          analyst_inclusions=[i["item"] for i in out["inclusions"] if i["source"] == "analyst"],
          client_exclusions=sum(i["source"] == "client" for i in out["exclusions"]),
          analyst_exclusions=[i["item"] for i in out["exclusions"] if i["source"] == "analyst"])
    return {"scope": out, "log": state.get("log", []) + ["scope: done"]}


def _plan(state: dict) -> list[tuple[str, str]]:
    llm = state["llm"]
    seg_names = [s for d in state["tax"].dimensions for s in d.all_segments]
    try:
        plan = llm.json("research_plan", G.PLAN if _generic(state) else PLAN_SYSTEM,
                        f"MARKET: {state['cfg'].market_name}\nSCOPE: {dump(state['scope'])}\n"
                        f"REGIONS/COUNTRIES: {', '.join(state['tax'].parent_nodes[1:])}; key countries "
                        f"{', '.join(state['tax'].leaves[:30])}\nSEGMENTS: {', '.join(seg_names)}",
                        tier="fast", temperature=0.4,
                        check=lambda o: None if isinstance(o.get("queries"), list) and len(o["queries"]) >= 12
                        else "need a 'queries' list with at least 12 items")
        qs = [(q["q"], q.get("theme", "regional")) for q in plan["queries"] if q.get("q")]
    except Exception as e:
        log.warning("query planner failed (%s); using fallback queries", e)
        qs = []
    seen, out = set(), []
    for q, t in qs + (_generic_fallback(state) if _generic(state) else _FALLBACK_QUERIES):
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append((q, t))
    return out


def _generic_fallback(state: dict) -> list[tuple[str, str]]:
    name = state["tax"].short_name
    return [(f"{name} market size 2025", "global_size"), (f"{name} revenue 2024 annual report", "global_size"),
            (f"{name} sales by region", "regional"), (f"{name} price per unit cost", "pricing"),
            (f"{name} key companies market share", "players"),
            (f"{name} installed base demand volume" if G.INDUSTRY == "neutral" else f"{name} patients prevalence demand",
             "installed_base"),
            (f"{name} approval launch 2025", "current_events"), (f"{name} market growth forecast 2032", "global_size")]


def research_node(state: dict) -> dict:
    cfg = state["cfg"]
    if not cfg.web:
        return {"evidence": [], "facts": [], "log": state.get("log", []) + ["research: web disabled"]}
    web: Web = state["web"]
    queries = _plan(state)
    event("batch.search", queries=len(queries), plan=[f"{t}: {q}" for q, t in queries])
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda qt: (qt, web.search(qt[0])), queries))

    evidence, by_url = [], {}
    for (q, theme), hits in results:
        for h in hits:
            if h["url"] in by_url:
                continue
            item = {"id": f"W{len(evidence) + 1}", "url": h["url"], "title": h["title"], "domain": domain_of(h["url"]),
                    "query": q, "theme": theme, "text": h["snippet"], "kind": "snippet"}
            by_url[h["url"]] = item
            evidence.append(item)

    # read the pages most likely to carry numbers: sizing, pricing and installed base first
    priority = {"global_size": 0, "pricing": 1, "installed_base": 2, "players": 3, "segment": 4, "regional": 5, "current_events": 6}
    to_read = sorted(evidence, key=lambda e: priority.get(e["theme"], 9))[: cfg.pages_to_read]
    event("batch.read_pages", pages=len(to_read), urls=[e["url"] for e in to_read])
    kw = ("gear", "aftermarket", "service", "repair", "overhaul", "compressor", "turbine", "lng", "market",
          "capacity", "installed", "mtpa", "pipeline", "ammonia", "price", "cost")
    if _generic(state):
        kw = tuple(w for w in state["tax"].short_name.split() if len(w) > 3) + (
            "market", "revenue", "sales", "price", "cost", "patients", "prevalence", "units", "billion", "million",
            "growth", "share")
    with ThreadPoolExecutor(max_workers=4) as ex:
        pages = list(ex.map(lambda e: (e, web.read(e["url"])), to_read))
    for e, text in pages:
        passage = relevant_passages(text, kw) if text else ""
        if passage:
            e["text"] = (e["text"] + " || PAGE: " + passage)[:3200]
            e["kind"] = "page"

    facts = _extract(state, evidence)
    log.info("research: %d queries, %d sources, %d verified facts, %d blocked", len(queries), len(evidence), len(facts), web.blocked)
    return {"evidence": evidence, "facts": facts, "blocked_sources": web.blocked,
            "log": state.get("log", []) + [f"research: {len(queries)} queries, {len(evidence)} sources, "
                                           f"{len(facts)} verified facts, {web.blocked} publisher sources blocked"]}


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _extract(state: dict, evidence: list[dict]) -> list[dict]:
    llm = state["llm"]
    size = getattr(state["cfg"], "extract_batch", 10) if _generic(state) else 10
    batches = [evidence[i:i + size] for i in range(0, len(evidence), size)]
    by_id = {e["id"]: e for e in evidence}

    def run(batch):
        txt = "\n\n".join(f"ITEM {e['id']} (theme {e['theme']}; {e['domain']}; title: {e['title']}):\n{e['text']}" for e in batch)
        ids = [e["id"] for e in batch]
        try:
            system = G.EXTRACT.format(market=state["cfg"].market_name) if _generic(state) else EXTRACT_SYSTEM
            fs = llm.json("extract", system, txt, tier="fast", temperature=0.0).get("facts", [])
            event("batch.extract", items=f"{ids[0]}-{ids[-1]}", n_items=len(batch), raw_facts=len(fs))
            return fs
        except Exception as ex:
            log.warning("extract batch failed: %s", ex)
            event("batch.extract_failed", items=f"{ids[0]}-{ids[-1]}", error=str(ex)[:300])
            return []

    with ThreadPoolExecutor(max_workers=state["cfg"].llm_workers) as ex:
        raw = [f for fs in ex.map(run, batches) for f in fs]

    facts, n, dropped = [], 0, 0
    for f in raw:
        src = by_id.get(str(f.get("item", "")).strip())
        quote = str(f.get("quote", ""))
        reason = ("unknown source item" if not src else "empty quote" if not quote
                  else "quote has no number" if not re.search(r"\d", quote)
                  else "quote not found verbatim in source" if _squash(quote) not in _squash(src["text"]) else None)
        if reason:                                           # verbatim rule
            dropped += 1
            event("restriction.fact_dropped", item=f.get("item"), reason=reason, quote=quote[:200], claim=str(f.get("claim", ""))[:200])
            continue
        n += 1
        facts.append({"id": src["id"], "fid": f"F{n}", "claim": f.get("claim", ""), "quote": quote,
                      "geo": f.get("geo"), "year": f.get("year"), "metric": f.get("metric"),
                      "relevance": f.get("relevance"), "theme": src["theme"], "domain": src["domain"], "url": src["url"]})
    facts.sort(key=lambda f: -(f.get("relevance") or 0))
    event("batch.extract_summary", raw=len(raw), kept=len(facts), dropped=dropped)
    return facts
