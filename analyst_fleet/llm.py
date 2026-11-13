"""DeepSeek JSON client: content-addressed cache, retries, light schema checks.

Every call is cached by (model, messages) hash in SQLite, so a re-run with the same
evidence is free and reproducible. A `check` callable may reject a reply; the
rejection reason is sent back to the model for a corrected answer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
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
        self.usage = {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self._ulock = threading.Lock()
        self.calls: list[dict[str, Any]] = []   # audit log: role, model, key, seconds

    def json(self, role: str, system: str, user: str, *, tier: str = "reasoning",
             check: Callable[[dict], str | None] | None = None, retries: int = 3,
             temperature: float = 0.3) -> dict:
        model = self.cfg.reasoning_model if tier == "reasoning" else self.cfg.fast_model
        msgs = [{"role": "system", "content": system.strip() + "\n\nRespond with a single valid json object only."},
                {"role": "user", "content": user.strip()}]
        key = hashlib.sha256(json.dumps([model, msgs], ensure_ascii=False).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            out = json.loads(hit)
            if check is None or check(out) is None:
                with self._ulock:
                    self.usage["cached"] += 1
                event("llm.call", role=role, model=model, source="cache", key=key[:12])
                return out
            event("restriction.reject", role=role, source="cache", reason=check(out)[:300])

        last_err = ""
        for attempt in range(retries):
            t0 = time.time()
            try:
                txt, usage = self._complete(role, model, msgs, temperature)
            except LLMFatal:
                raise
            except LLMError as e:          # API unreachable after all network retries
                last_err = str(e)
                break
            dt = time.time() - t0
            with self._ulock:
                self.usage["calls"] += 1
                if usage:
                    self.usage["prompt_tokens"] += usage.prompt_tokens or 0
                    self.usage["completion_tokens"] += usage.completion_tokens or 0
                self.calls.append({"role": role, "model": model, "key": key[:12], "seconds": round(dt, 1)})
            r = type("R", (), {"usage": usage})()
            try:
                try:
                    out = json.loads(txt)
                except json.JSONDecodeError as e0:
                    if "Extra data" not in str(e0):
                        raise
                    # a complete object followed by stray text: keep the object, log the repair
                    out, end = json.JSONDecoder().raw_decode(txt.strip())
                    event("restriction.repair", role=role, model=model, attempt=attempt + 1,
                          reason=f"trailing text after json dropped ({len(txt.strip()) - end} chars)")
            except json.JSONDecodeError as e:
                last_err = f"invalid json: {e}"
                event("restriction.reject", role=role, model=model, attempt=attempt + 1, reason=last_err)
                msgs = msgs + [{"role": "assistant", "content": txt[:4000]},
                               {"role": "user", "content": f"That was not valid json ({e}). Return the corrected json object only."}]
                continue
            problem = check(out) if check else None
            if problem is None:
                self.cache.put(key, json.dumps(out, ensure_ascii=False))
                log.info("[%s] ok in %.0fs", role, dt)
                event("llm.call", role=role, model=model, source="live", key=key[:12], attempt=attempt + 1,
                      seconds=round(dt, 1), prompt_tokens=getattr(r.usage, "prompt_tokens", None),
                      completion_tokens=getattr(r.usage, "completion_tokens", None))
                return out
            last_err = problem
            log.warning("[%s] rejected: %s", role, problem[:300])
            event("restriction.reject", role=role, model=model, attempt=attempt + 1, reason=problem[:400])
            msgs = msgs + [{"role": "assistant", "content": txt[:6000]},
                           {"role": "user", "content": f"Your answer failed a check: {problem}\nFix it and return the complete corrected json object."}]
        event("llm.failed", role=role, model=model, reason=last_err[:400])
        raise LLMError(f"{role}: no valid answer after {retries} attempts ({last_err[:300]})")

    def _complete(self, role: str, model: str, msgs: list, temperature: float, net_retries: int = 6):
        """One completion, streamed (keeps long reasoning calls alive through proxies), with
        exponential backoff on network/API errors: 10, 20, 40, 80, 160 s."""
        for n in range(net_retries):
            try:
                stream = self.client.chat.completions.create(
                    model=model, messages=msgs, temperature=temperature, max_tokens=self.cfg.max_tokens,
                    response_format={"type": "json_object"}, stream=True, stream_options={"include_usage": True})
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
