"""Web evidence: search (Bing -> Google via ddgs), page reading, caching, blocklist.

Every search result and page is cached in SQLite so a run is reproducible and
re-runs are free. Blocklisted sources (the publisher we are compared against, in
blind mode) are dropped at the door and counted, never shown to an agent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from ..config import Config
from ..llm import Cache
from ..syslog import event

log = logging.getLogger("fleet.web")


class Web:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = Cache(cfg.cache_db)
        self.blocked = 0

    def _is_blocked(self, *texts: str) -> bool:
        blob = " ".join(t or "" for t in texts).lower()
        return any(b in blob for b in self.cfg.blocklist)

    def search(self, query: str, n: int | None = None) -> list[dict[str, Any]]:
        n = n or self.cfg.results_per_query
        key = "search:" + hashlib.sha256(f"{query}|{n}".encode()).hexdigest()
        hit = self.cache.get(key)
        used = "cache"
        if hit is not None:
            results = json.loads(hit)
        else:
            results = []
            from ddgs import DDGS
            for backend in self.cfg.search_backends:
                try:
                    results = DDGS().text(query, max_results=n, backend=backend) or []
                except Exception as e:
                    log.debug("search %s failed on %s: %s", query, backend, e)
                    event("search.backend_fail", query=query, backend=backend, error=str(e)[:200])
                    results = []
                if results:
                    used = backend
                    break
            if results:  # never cache an empty answer (could be a transient block)
                self.cache.put(key, json.dumps(results))
        out, blocked = [], []
        for r in results:
            url, title, body = r.get("href", ""), r.get("title", ""), r.get("body", "")
            if self._is_blocked(url, title, body):
                self.blocked += 1
                blocked.append(url)
                event("restriction.blocked", stage="search", query=query, url=url, rule=self._rule(url, title, body))
                continue
            out.append({"url": url, "title": title, "snippet": body, "query": query})
        event("search.query", query=query, source=used, results=len(results), kept=len(out), blocked=len(blocked))
        return out

    def _rule(self, *texts: str) -> str:
        blob = " ".join(t or "" for t in texts).lower()
        return next((b for b in self.cfg.blocklist if b in blob), "")

    def read(self, url: str) -> str:
        key = "page:" + hashlib.sha256(url.encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            text = hit
        else:
            try:
                import trafilatura
                raw = trafilatura.fetch_url(url)
                text = (trafilatura.extract(raw) or "") if raw else ""
            except Exception as e:
                log.debug("read %s failed: %s", url, e)
                text = ""
            self.cache.put(key, text)
        if self._is_blocked(text[:20000]):
            # a page that quotes the blocklisted publisher's figures is dropped whole
            self.blocked += 1
            event("restriction.blocked", stage="page", url=url, rule=self._rule(text[:20000]))
            return ""
        event("page.read", url=url, chars=len(text), source="cache" if hit is not None else "live")
        return text


_NUM = re.compile(r"(US\$|USD|\$|€|EUR)?\s?\d[\d,.]*\s?(million|billion|mn|bn|%|percent|units|MW|GW|mtpa|years?)", re.I)


def relevant_passages(text: str, keywords: tuple[str, ...], max_chars: int = 2200) -> str:
    """Sentences that mention a keyword AND carry a number, in document order."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    keep, total = [], 0
    for s in sents:
        low = s.lower()
        if any(k in low for k in keywords) and _NUM.search(s):
            s = s.strip()[:500]
            keep.append(s)
            total += len(s)
            if total > max_chars:
                break
    return " ".join(keep)
