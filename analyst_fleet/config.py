"""Run configuration: paths, models, evidence policy. Everything tunable lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent          # Refresh_ai/
PKG = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Domains/phrases that belong to the publisher whose human sheet we are later
# compared against. In blind mode their figures never enter the evidence store,
# so a match on the comparison is earned, not copied.
PUBLISHER_BLOCKLIST = (
    "coherentmarketinsights.com",
    "coherentmi.com",
    "giiresearch.com/report/coh",
    "marketresearch.com/coherent",
    "coherent market insights",
)


def _find_oem_dir() -> Path:
    """'oem market' if present, else the first sub-folder holding OEM workbooks (the folder
    has been renamed before), else FLEET_OEM_DIR from the environment."""
    if os.environ.get("FLEET_OEM_DIR"):
        return Path(os.environ["FLEET_OEM_DIR"])
    default = ROOT / "oem market"
    if default.is_dir():
        return default
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name not in ("outputs", "data", "analyst_fleet")):
        if any(f.name.endswith("OEM Market.xlsx") or "Gearbox OEM" in f.name for f in d.glob("*.xlsx")):
            return d
    return default


@dataclass
class Config:
    market_name: str = "Global Industrial High Speed Helical Gearbox Aftermarket Market"
    base_year: int = 2025
    forecast_end_year: int = 2032

    # LLM
    api_key: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.environ.get("ME_LLM_BASE_URL", "https://api.deepseek.com"))
    reasoning_model: str = field(default_factory=lambda: os.environ.get("FLEET_REASONING_MODEL", "deepseek-v4-pro"))
    fast_model: str = field(default_factory=lambda: os.environ.get("FLEET_FAST_MODEL", "deepseek-flash"))
    max_tokens: int = 64000                 # DeepSeek thinking-mode default; batched replies are long
    llm_workers: int = 6
    # Thinking mode (DeepSeek default: ON, effort high, reasoning tokens billed as output).
    # Fast tier = mechanical jobs (scope reading, query planning, fact extraction, plain-language editing): OFF.
    # Reasoning tier = the analysts' judgement calls: ON. Effort: "low" | "high" | "max".
    fast_thinking: bool = field(default_factory=lambda: os.environ.get("FLEET_FAST_THINKING", "0") == "1")
    reasoning_thinking: bool = True
    reasoning_effort: str = field(default_factory=lambda: os.environ.get("FLEET_REASONING_EFFORT", "high"))
    # Per-call trace (outputs/llm_trace/<date>.jsonl): tokens, cache hits, cost; full prompt + reply if trace_content
    trace_content: bool = True
    # never call DeepSeek: every answer must come from data/cache.sqlite (rebuilding saved runs)
    cache_only: bool = field(default_factory=lambda: os.environ.get("FLEET_CACHE_ONLY", "0") == "1")
    trace_dir: str = ""

    # Evidence
    blind: bool = True                       # exclude the publisher's own report
    web: bool = True
    # ddgs 9.x: "auto" rotates engines and is what works on this network; the rest are fallbacks
    search_backends: tuple[str, ...] = ("auto", "startpage", "brave", "yahoo")
    results_per_query: int = 6
    pages_to_read: int = 10

    # Agent loop
    max_passes: int = 2                      # 1 draft + up to (max_passes-1) targeted redos
    min_review_score: float = 7.0
    cagr_reconcile_tolerance: float = 0.002  # 0.2 pp: below this a parent/child CAGR gap is left alone

    # House judgement learned from past sheets (learn_opinions.py); reasoning habits, never targets
    use_opinions: bool = True
    plain_language: bool = True           # editor rewrites every cell's reasoning in plain English
    opinion_playbook: Path = ROOT / "data" / "corpus" / "opinion_playbook.json"

    # Paths
    definitions_docx: Path = ROOT / "Industrial_High_Speed_Helical_Gearbox_Aftermarket_Definitions.docx"
    oem_definitions_docx: Path = ROOT / "Industrial_High_Speed_Helical_Gearbox_OEM_Definitions_and_Scope.docx"
    oem_dir: Path = field(default_factory=lambda: _find_oem_dir())
    taxonomy_path: Path = PKG / "taxonomy.json"
    cache_db: Path = ROOT / "data" / "cache.sqlite"
    out_dir: Path = ROOT / "outputs"

    # Generic market mode (a market from a client scope sheet instead of the gearbox documents + OEM data)
    generic: bool = False
    market_class: str = ""                  # e.g. "prescription drug", set by the batch runner for the analysts
    # Batching (generic markets): several segment dimensions per call (same validators; failures fall back to
    # one-dimension calls) and more web sources per fact-extraction call.
    batch_segments: bool = field(default_factory=lambda: os.environ.get("FLEET_BATCH_SEGMENTS", "1") == "1")
    extract_batch: int = 20

    @property
    def blocklist(self) -> tuple[str, ...]:
        return PUBLISHER_BLOCKLIST if self.blind else ()
