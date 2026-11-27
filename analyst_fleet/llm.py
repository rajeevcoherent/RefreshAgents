"""DeepSeek JSON client: content-addressed cache, retries, light schema checks, per-call tracing.

Every call is cached by (model, messages[, thinking off]) hash in SQLite, so a re-run with the same
evidence is free and reproducible. A `check` callable may reject a reply; the rejection reason is
sent back to the model for a corrected answer.

Thinking mode (DeepSeek docs, guides/thinking_mode): ON by default for every model, effort "high";
reasoning tokens are billed as output. The fast tier (extraction, planning, editing) runs with
thinking OFF unless cfg.fast_thinking; the reasoning tier keeps it ON at cfg.reasoning_effort.

Tracing: every API attempt (accepted, rejected by a check, invalid json) is logged to system.log
(event llm.call / restriction.reject with tokens) and appended to outputs/llm_trace/<date>.jsonl with
start time, seconds, input tokens split cache hit / miss, output and reasoning tokens, cost (USD, at
the peak or off-peak rate in force at the start time) and - when cfg.trace_content - the full prompt
and reply.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from .config import Config
from .syslog import event

log = logging.getLogger("fleet.llm")


class LLMError(RuntimeError):
    pass


class LLMFatal(LLMError):
    """Account-level refusal (no balance, bad key): stop instead of retrying."""


def repair_comma_slip(txt: str, err: json.JSONDecodeError, max_fixes: int = 5) -> tuple[Any, list[str]]:
    """Repair ONLY one model slip in a reply that already failed to parse: right after a text value
    ("...") the comma is missing before the next key, or a stray "]" stands where the comma belongs ("],").
    Returns (object, fixes) or (None, []) if the reply has any other defect - then it stays rejected."""
    fixes: list[str] = []
    for _ in range(max_fixes):
        if not err.msg.startswith("Expecting ',' delimiter"):
            return None, []
        pos = err.pos
        before = txt[:pos].rstrip()
        if not before.endswith('"'):                    # must follow a text value
            return None, []
        rest = txt[pos:]
        if rest.startswith('"'):                        # missing comma before the next key
            txt = before + "," + txt[len(before):]
            fixes.append(f"comma inserted at char {len(before)}")
        elif rest.startswith("]") and rest[1:].lstrip().startswith(","):   # "]," where "," belongs
            txt = txt[:pos] + txt[pos + 1:]
            fixes.append(f"stray ']' removed at char {pos}")
        else:
            return None, []
        try:
            return json.loads(txt), fixes
        except json.JSONDecodeError as e:
            err = e
    return None, []



# USD per 1M tokens at PEAK rate (api-docs.deepseek.com/quick_start/pricing, read 2026-11-23):
# (input cache hit, input cache miss, output). Off-peak = half. Peak = 01:00-04:00 and 06:00-10:00 UTC,
# Monday-Friday (Chinese public holidays are off-peak - not modelled here).
PRICES = {"deepseek-flash": (0.006, 0.30, 1.20), "deepseek-v4-pro": (0.044, 1.32, 3.96)}


def is_peak(t: datetime | None = None) -> bool:
    u = (t or datetime.now(timezone.utc)).astimezone(timezone.utc)
    h = u.hour + u.minute / 60
    return u.weekday() < 5 and (1 <= h < 4 or 6 <= h < 10)


def cost_usd(model: str, hit: int, miss: int, out: int, peak: bool) -> float:
    ph, pm, po = PRICES.get(model, PRICES["deepseek-v4-pro"])
    return (hit * ph + miss * pm + out * po) / 1e6 * (1.0 if peak else 0.5)


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, t REAL)")

    def _conn(self):
        return sqlite3.connect(self._path, timeout=30)

    def get(self, k: str) -> str | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def put(self, k: str, v: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (k, v, time.time()))


class LLM:
    def __init__(self, cfg: Config):
        if not cfg.api_key:
            raise LLMError("DEEPSEEK_API_KEY missing from .env")
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=600)
        self.cache = Cache(cfg.cache_db)
        self.usage = {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cache_hit_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0}
        self._ulock = threading.Lock()
        self.calls: list[dict[str, Any]] = []   # audit log: one record per API attempt
        self.run_label = getattr(cfg, "market_name", "")
        tdir = Path(getattr(cfg, "trace_dir", "") or (Path(cfg.cache_db).parent.parent / "outputs" / "llm_trace"))
        tdir.mkdir(parents=True, exist_ok=True)
        self._trace = tdir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        self._tlock = threading.Lock()

    def _thinking(self, tier: str) -> tuple[bool, str]:
        if tier == "reasoning":
            return bool(getattr(self.cfg, "reasoning_thinking", True)), getattr(self.cfg, "reasoning_effort", "high")
        return bool(getattr(self.cfg, "fast_thinking", False)), "high"

    def _record(self, *, role, model, key, attempt, status, started, dt, usage, think, msgs, txt, reason=""):
        u = usage
        pt = getattr(u, "prompt_tokens", 0) or 0
        hit = getattr(u, "prompt_cache_hit_tokens", None)
        if hit is None:
            hit = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        miss = getattr(u, "prompt_cache_miss_tokens", None)
        miss = pt - hit if miss is None else miss
        ct = getattr(u, "completion_tokens", 0) or 0
        rt = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        peak = is_peak(started)
        c = cost_usd(model, hit, miss, ct, peak)
        rec = {"time": started.astimezone().isoformat(timespec="seconds"), "run": self.run_label, "role": role,
               "model": model, "thinking": think, "attempt": attempt, "status": status, "seconds": round(dt, 1),
               "prompt_tokens": pt, "cache_hit_tokens": hit, "cache_miss_tokens": miss, "completion_tokens": ct,
               "reasoning_tokens": rt, "peak": peak, "cost_usd": round(c, 6), "key": key[:12], "reason": reason[:300]}
        with self._ulock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += pt
            self.usage["completion_tokens"] += ct
            self.usage["cache_hit_tokens"] += hit
            self.usage["reasoning_tokens"] += rt
            self.usage["cost_usd"] += c
            self.calls.append(rec)
        if getattr(self.cfg, "trace_content", True):
            full = {**rec, "messages": msgs, "reply": txt}
        else:
            full = rec
        with self._tlock, self._trace.open("a", encoding="utf-8") as f:
            f.write(json.dumps(full, ensure_ascii=False) + "\n")
        return rec

    def json(self, role: str, system: str, user: str, *, tier: str = "reasoning",
             check: Callable[[dict], str | None] | None = None, retries: int = 3,
             temperature: float = 0.3) -> dict:
        model = self.cfg.reasoning_model if tier == "reasoning" else self.cfg.fast_model
        think, effort = self._thinking(tier)
        msgs = [{"role": "system", "content": system.strip() + "\n\nRespond with a single valid json object only."},
                {"role": "user", "content": user.strip()}]
        # thinking ON at default effort keeps the historical key, so earlier cached answers stay valid
        mode = [] if (think and effort == "high") else [f"thinking={'on:' + effort if think else 'off'}"]
        key = hashlib.sha256(json.dumps([model, msgs] + mode, ensure_ascii=False).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            out = json.loads(hit)
            if check is None or check(out) is None:
                with self._ulock:
                    self.usage["cached"] += 1
                event("llm.call", role=role, model=model, source="cache", key=key[:12])
                return out
            event("restriction.reject", role=role, source="cache", reason=check(out)[:300])
        if getattr(self.cfg, "cache_only", False):
            event("llm.cache_only_miss", role=role, model=model, key=key[:12])
            raise LLMError(f"{role}: not in the local cache and cache-only mode is on (no DeepSeek spend allowed)")

        last_err = ""
        for attempt in range(retries):
            t0 = time.time()
            started = datetime.now(timezone.utc)
            try:
                txt, usage = self._complete(role, model, msgs, temperature, think=think, effort=effort)
            except LLMFatal:
                raise
            except LLMError as e:          # API unreachable after all network retries
                last_err = str(e)
                break
            dt = time.time() - t0
            rec_args = dict(role=role, model=model, key=key, attempt=attempt + 1, started=started, dt=dt,
                            usage=usage, think=think, msgs=msgs, txt=txt)
            try:
                try:
                    out = json.loads(txt)
                except json.JSONDecodeError as e0:
                    if "Extra data" not in str(e0):
                        # only for replies that already failed: the missing-comma / "]," slip after a text value
                        out, fixes = repair_comma_slip(txt, e0)
                        if out is None:
                            raise
                        log.warning("[%s] repaired json: %s", role, "; ".join(fixes))
                        event("restriction.repair", role=role, model=model, attempt=attempt + 1,
                              reason="comma slip after a text value fixed: " + "; ".join(fixes))
                    else:
                        # a complete object followed by stray text: keep the object, log the repair
                        out, end = json.JSONDecoder().raw_decode(txt.strip())
                        event("restriction.repair", role=role, model=model, attempt=attempt + 1,
                              reason=f"trailing text after json dropped ({len(txt.strip()) - end} chars)")
            except json.JSONDecodeError as e:
                last_err = f"invalid json: {e}"
                rec = self._record(status="invalid_json", reason=last_err, **rec_args)
                event("restriction.reject", role=role, model=model, attempt=attempt + 1, reason=last_err,
                      prompt_tokens=rec["prompt_tokens"], completion_tokens=rec["completion_tokens"], cost_usd=rec["cost_usd"])
                msgs = msgs + [{"role": "assistant", "content": txt[:4000]},
                               {"role": "user", "content": f"That was not valid json ({e}). Return the corrected json object only."}]
                continue
            problem = check(out) if check else None
            if problem is None:
                self.cache.put(key, json.dumps(out, ensure_ascii=False))
                rec = self._record(status="ok", **rec_args)
                log.info("[%s] ok in %.0fs (%s in / %s out, US$ %.4f)", role, dt, rec["prompt_tokens"],
                         rec["completion_tokens"], rec["cost_usd"])
                event("llm.call", role=role, model=model, source="live", key=key[:12], attempt=attempt + 1,
                      seconds=round(dt, 1), prompt_tokens=rec["prompt_tokens"], completion_tokens=rec["completion_tokens"],
                      cache_hit_tokens=rec["cache_hit_tokens"], reasoning_tokens=rec["reasoning_tokens"],
                      thinking=think, peak=rec["peak"], cost_usd=rec["cost_usd"])
                return out
            last_err = problem
            rec = self._record(status="rejected", reason=problem, **rec_args)
            log.warning("[%s] rejected: %s", role, problem[:300])
            event("restriction.reject", role=role, model=model, attempt=attempt + 1, reason=problem[:400],
                  prompt_tokens=rec["prompt_tokens"], completion_tokens=rec["completion_tokens"], cost_usd=rec["cost_usd"])
            msgs = msgs + [{"role": "assistant", "content": txt[:6000]},
                           {"role": "user", "content": f"Your answer failed a check: {problem}\nFix it and return the complete corrected json object."}]
        event("llm.failed", role=role, model=model, reason=last_err[:400])
        raise LLMError(f"{role}: no valid answer after {retries} attempts ({last_err[:300]})")

    def _complete(self, role: str, model: str, msgs: list, temperature: float, net_retries: int = 6,
                  think: bool = True, effort: str = "high"):
        """One completion, streamed (keeps long reasoning calls alive through proxies), with
        exponential backoff on network/API errors: 10, 20, 40, 80, 160 s."""
        for n in range(net_retries):
            try:
                stream = self.client.chat.completions.create(
                    model=model, messages=msgs, temperature=temperature, max_tokens=self.cfg.max_tokens,
                    response_format={"type": "json_object"}, stream=True, stream_options={"include_usage": True},
                    extra_body={"thinking": {"type": "enabled" if think else "disabled"}}
                    | ({"reasoning_effort": effort} if think and effort != "high" else {}))
                parts, usage = [], None
                for ch in stream:
                    if getattr(ch, "usage", None):
                        usage = ch.usage
                    if ch.choices and ch.choices[0].delta and ch.choices[0].delta.content:
                        parts.append(ch.choices[0].delta.content)
                return "".join(parts).strip(), usage
            except Exception as e:
                code = getattr(e, "status_code", None)
                if code in (401, 402, 403):          # bad key / no balance / forbidden: retrying cannot help
                    event("llm.fatal", role=role, model=model, status=code, error=str(e)[:300])
                    raise LLMFatal(f"DeepSeek API refused the request ({code}): "
                                   + ("account balance exhausted - top up and re-run" if code == 402 else str(e)[:200])) from e
                wait = 10 * 2 ** n
                log.warning("[%s] api error: %s (network try %d/%d, waiting %ds)", role, str(e)[:200], n + 1, net_retries, wait)
                event("llm.error", role=role, model=model, network_try=n + 1, wait_s=wait, error=str(e)[:300])
                if n < net_retries - 1:
                    time.sleep(wait)
        raise LLMError(f"api unreachable after {net_retries} network retries")
