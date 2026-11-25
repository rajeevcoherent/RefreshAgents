"""Every DeepSeek API request recorded in system.log for one day, and a comparison with the DeepSeek dashboard.

    python deepseek_usage_report.py 2026-11-23 [--dashboard flash=368:2925882 pro=299:4328646]

Writes outputs/deepseek_requests_<day>.csv (one row per API request the fleet recorded) and prints totals per
model next to the dashboard figures you pass in (requests:tokens, copied from platform.deepseek.com/usage).

What system.log records per request (analyst_fleet/llm.py):
  llm.call source=live      a reply that passed its checks - with prompt/completion tokens
  restriction.reject        a reply that failed a check or was not valid json (then retried) - no token counts
  llm.error / llm.fatal     a request the API refused or the network dropped
  llm.call source=cache     NOT an API request (answer replayed from data/cache.sqlite) - excluded
Not recorded: requests in flight when a process is stopped or crashes (paid for, never answered).
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KV = re.compile(r"(\w+)=(.*?)(?= \w+=|$)")


def parse(day: str) -> list[dict]:
    rows = []
    for line in (ROOT / "system.log").read_text(encoding="utf-8").splitlines():
        if not line.startswith(day):
            continue
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) < 4:
            continue
        ts, run, ev, rest = parts
        kv = dict(KV.findall(rest))
        if ev == "llm.call" and kv.get("source") == "live":
            kind = "ok"
        elif ev == "restriction.reject" and kv.get("source") != "cache":
            kind = "rejected (retried)"
        elif ev in ("llm.error", "llm.fatal"):
            kind = ev.split(".")[1]
        else:
            continue
        num = lambda k: int(kv[k]) if kv.get(k, "").isdigit() else None
        rows.append({"time": ts, "run": run, "status": kind, "model": kv.get("model", ""), "role": kv.get("role", ""),
                     "attempt": kv.get("attempt", ""), "seconds": kv.get("seconds", ""),
                     "prompt_tokens": num("prompt_tokens"), "completion_tokens": num("completion_tokens"),
                     "note": (kv.get("reason") or kv.get("error") or "")[:300]})
    return rows


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    day = argv[0] if argv else __import__("datetime").date.today().isoformat()
    dash = {}
    for a in argv[1:]:
        if "=" in a and ":" in a:
            m, v = a.split("=", 1)
            r, t = v.split(":")
            dash["deepseek-flash" if m == "flash" else "deepseek-v4-pro" if m == "pro" else m] = (int(r), int(t))
    rows = parse(day)
    out = ROOT / "outputs" / f"deepseek_requests_{day}.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["time"])
        w.writeheader()
        w.writerows(rows)
    agg = defaultdict(lambda: defaultdict(int))
    for r in rows:
        a = agg[r["model"] or "(unknown)"]
        a[r["status"]] += 1
        a["requests"] += 1
        a["prompt"] += r["prompt_tokens"] or 0
        a["completion"] += r["completion_tokens"] or 0
    print(f"{len(rows)} API requests recorded on {day} -> {out}\n")
    print(f"{'model':18} {'requests':>9} {'ok':>6} {'rejected':>9} {'err/fatal':>9} {'tokens (ok only)':>17} | "
          f"{'dashboard req':>13} {'dashboard tok':>14} | {'req gap':>8} {'tok gap':>10}")
    for m, a in sorted(agg.items()):
        tok = a["prompt"] + a["completion"]
        d = dash.get(m)
        gap = f"{d[0] - a['requests']:>8} {d[1] - tok:>10,}" if d else ""
        print(f"{m:18} {a['requests']:>9} {a['ok']:>6} {a['rejected (retried)']:>9} {a['error'] + a['fatal']:>9} "
              f"{tok:>17,} | {d[0] if d else '':>13} {(f'{d[1]:,}' if d else ''):>14} | {gap}")
    by_run = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["status"] == "ok":
            by_run[(r["time"][:13], r["model"])][0] += 1
            by_run[(r["time"][:13], r["model"])][1] += (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
    print("\nBy hour (local time, successful requests): hour | model | requests | tokens")
    for (h, m), (n, t) in sorted(by_run.items()):
        print(f"  {h}:00 | {m:16} | {n:4} | {t:,}")
    trace_summary(day)
    return 0


def trace_summary(day: str) -> None:
    """Per-attempt trace written by analyst_fleet/llm.py since the tracing upgrade (cache hits, reasoning, cost)."""
    import json
    tf = ROOT / "outputs" / "llm_trace" / f"{day}.jsonl"
    if not tf.exists():
        print(f"\n(no per-call trace for {day}: {tf} - runs before the tracing upgrade only have system.log)")
        return
    recs = [json.loads(l) for l in tf.read_text(encoding="utf-8").splitlines() if l.strip()]
    agg = defaultdict(lambda: defaultdict(float))
    for r in recs:
        stage = r["role"].split(":")[0] + (" (country chunk)" if r["role"].endswith("..") else "")
        a = agg[(r.get("run", ""), stage, r["model"], r["thinking"])]
        for k in ("prompt_tokens", "cache_hit_tokens", "completion_tokens", "reasoning_tokens", "cost_usd", "seconds"):
            a[k] += r.get(k) or 0
        a["n"] += 1
        a["failed"] += r["status"] != "ok"
    print(f"\nPer-call trace {tf} ({len(recs)} API attempts): run | stage | model | thinking | calls (failed) | "
          f"input (cache hit) | output (reasoning) | seconds | US$")
    for (run, st, m, th), a in sorted(agg.items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"  {run[:34]:34} | {st:28} | {m:15} | {'on' if th else 'off':3} | {int(a['n']):4} ({int(a['failed'])}) | "
              f"{int(a['prompt_tokens']):>9,} ({int(a['cache_hit_tokens']):>9,}) | {int(a['completion_tokens']):>9,} "
              f"({int(a['reasoning_tokens']):>9,}) | {a['seconds']:7.0f} | {a['cost_usd']:.4f}")
    print(f"  TOTAL US$ {sum(r.get('cost_usd') or 0 for r in recs):.4f}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
