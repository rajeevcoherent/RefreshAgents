"""Audit log -> <project root>/system.log (appended across runs).

One line per batch operation (LLM call, search query, page read, extraction batch,
analyst fan-out) and per restriction result (publisher source blocked, fact dropped by
the verbatim-quote rule, analyst reply rejected by a validator, code adjustment, flag).
Format: time | run | event | key=value ...
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger("fleet.system")
_run = {"id": "-"}


def setup(path: Path, run_id: str) -> None:
    _run["id"] = run_id
    if not any(getattr(h, "_fleet_system", False) for h in _log.handlers):
        h = logging.FileHandler(path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        h._fleet_system = True
        _log.addHandler(h)
    _log.setLevel(logging.INFO)
    _log.propagate = False          # keep the console readable; run.log gets its own lines


def event(name: str, **kw) -> None:
    parts = []
    for k, v in kw.items():
        if isinstance(v, (dict, list, tuple)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        s = str(v).replace("\n", " ").replace("|", "/")
        parts.append(f"{k}={s[:2000]}")
    _log.info("%s | %-18s | %s", _run["id"], name, " ".join(parts))
