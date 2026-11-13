"""Shared analyst persona, evidence formatting and reply validators."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

PERSONA = """You are a senior, opinionated market-research analyst covering industrial
turbomachinery and power-transmission aftermarkets (high-speed single/double-helical
gearboxes for turbines, compressors, pumps, turboexpanders and blowers). You have
walked compressor stations, LNG trains, refineries, fertilizer complexes and power plants
and you know who services their gearboxes (OEMs such as Flender, Voith/BHS, RENK,
Siemens Energy, Baker Hughes/Lufkin, Allen Gears, Elecon, Philadelphia Gear, plus
independent repair shops).

How you work:
- You COMMIT to a specific number for every item you are asked for. No ranges where a
  point value is requested, no refusals, no "it depends".
- Every number is defended with concrete reasoning: installed base and its age/vintage,
  industrial activity, capacity additions (LNG, petrochemicals, fertilizers, gas
  pipelines, power), utilisation, maintenance culture, OEM vs independent service mix,
  labour cost, geopolitics (sanctions, trade restrictions, energy-security policy, local-
  content rules, currency/financing stress), regulation and decarbonisation policy.
- The aftermarket follows the INSTALLED BASE (decades of past OEM shipments) and its
  utilisation, not this year's new-build flow. Mature fleets (U.S., Europe, Japan,
  Middle East oil & gas) generate more service per new unit sold than fast-building
  markets; you say so explicitly when you move away from the OEM signal.
- Cite evidence ids (e.g. "W12", "OEM") only for evidence you were actually given and
  used. Anything else is labelled analyst judgment. Never invent sources.
- Use decimals for shares and growth rates (0.065 = 6.5%)."""

GEO_ITEM_FIELDS = """For EACH item return an object with:
  "share": number (decimal),
  "cagr": number (decimal, 2025-2032 value CAGR),
  "reasoning": 3-5 sentences explaining WHY this share and this growth rate - installed
               base, demand drivers, how it compares with the OEM signal and why it differs,
  "drivers": [short strings],
  "restraints": [short strings],
  "geopolitics": [short strings: geopolitical / regulatory / policy factors, may be empty],
  "evidence": [evidence ids used, e.g. "W3", "OEM"],
  "confidence": "High" | "Medium" | "Low\""""


def fmt_facts(facts: Iterable[dict], limit: int = 45) -> str:
    rows = []
    for f in list(facts)[:limit]:
        rows.append(f"[{f['id']}] ({f.get('theme','')}; {f.get('geo') or 'n/a'}; {f.get('year') or 'n/a'}) "
                    f"{f.get('claim','')[:260]}  <{f.get('domain','')}>")
    return "\n".join(rows) if rows else "(no web evidence available for this item - rely on analyst judgment and the OEM data)"


def select_facts(facts: list[dict], *, themes: Iterable[str] = (), geos: Iterable[str] = (),
                 limit: int = 45) -> list[dict]:
    themes, geos = set(themes), [g.lower() for g in geos]
    scored = []
    for f in facts:
        s = 0
        if f.get("theme") in themes:
            s += 2
        blob = (f.get("claim", "") + " " + (f.get("geo") or "")).lower()
        if geos and any(g in blob for g in geos):
            s += 3
        if s:
            scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower().replace("&amp;", "&"))


def canon_keys(d: Any, expected: Iterable[str]) -> Any:
    """Remap near-miss keys (case, punctuation, '&amp;', a truncated long label) onto the
    exact expected names, in place. Unmatched keys are left alone."""
    if not isinstance(d, dict):
        return d
    exp = list(expected)
    by_norm = {_key(e): e for e in exp}
    for k in list(d):
        if k in exp:
            continue
        nk = _key(k)
        target = by_norm.get(nk)
        if target is None and len(nk) >= 8:
            cands = [e for n, e in by_norm.items() if n.startswith(nk) or nk.startswith(n)]
            target = cands[0] if len(cands) == 1 else None
        if target and target not in d:
            d[target] = d.pop(k)
    return d


def check_geo_items(expected: list[str], key: str = "items", share_tol: float = 0.04,
                    cagr_range: tuple[float, float] = (-0.05, 0.25)):
    """Validator for {items: {name: {share, cagr, reasoning, ...}}} replies."""
    def check(out: dict) -> str | None:
        items = canon_keys(out.get(key), expected)
        if not isinstance(items, dict):
            return f'top-level "{key}" object keyed by name is missing'
        missing = [e for e in expected if e not in items]
        if missing:
            return f"missing items (use these exact names): {missing}"
        tot = 0.0
        for name in expected:
            it = items[name]
            s, c = num(it.get("share")), num(it.get("cagr"))
            if s is None or s < 0:
                return f"{name}: share must be a non-negative number"
            if c is None or not (cagr_range[0] <= c <= cagr_range[1]):
                return f"{name}: cagr must be a decimal between {cagr_range[0]} and {cagr_range[1]} (got {it.get('cagr')})"
            if len(str(it.get("reasoning", ""))) < 60:
                return f"{name}: reasoning is too thin - give 3-5 concrete sentences"
            tot += s
        if abs(tot - 1.0) > share_tol:
            return f"shares sum to {tot:.4f}; they must sum to 1.0"
        return None
    return check


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def domain_of(url: str) -> str:
    m = re.match(r"https?://(www\.)?([^/]+)", url or "")
    return m.group(2) if m else ""


# ------------------------------------------------------------------ house judgement (learned playbook)
_PLAYBOOKS: dict[str, Any] = {}


def house(state: dict, stage: str) -> str:
    """Stage guidance from the opinion playbook ('' when disabled or not built yet)."""
    cfg = state["cfg"]
    if not getattr(cfg, "use_opinions", False):
        return ""
    from ..corpus.opinion import guidance, load_playbook      # lazy: corpus imports this module
    key = str(cfg.opinion_playbook)
    if key not in _PLAYBOOKS:
        _PLAYBOOKS[key] = load_playbook(cfg.opinion_playbook)
    return guidance(_PLAYBOOKS[key], stage)


def dim_stage(state: dict, dim_title: str) -> str:
    """'use_case' (2B) or 'type' (2C) for a segmentation dimension, decided once by the fast model (cached)."""
    from ..corpus.opinion import classify_dims
    tax = state["tax"]
    declared = tax.strategy_of(dim_title) if hasattr(tax, "strategy_of") else None
    if declared:                                      # the market's own 2A/2B/2C definition wins over the classifier
        return "use_case" if declared == "2B" else "type"
    roles = classify_dims(state["llm"], state["cfg"].market_name,
                          {d.title: d.all_segments for d in tax.dimensions})
    return "use_case" if roles.get(dim_title) == "use_case" else "type"


def scope_block(state: dict, part: str = "all") -> str:
    """The client's binding scope rulebook (verbatim from the definition documents)."""
    t = state.get("scope_rules") or ""
    if not t:
        return ""
    if part == "allocation":
        t = t[t.find("ALLOCATION RULES:"):] if "ALLOCATION RULES:" in t else ""
    return ("\n\n" + t + "\nEvery number you give must respect these rules; when a number depends on a boundary, "
            "say which rule you applied.") if t else ""
