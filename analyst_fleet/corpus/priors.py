"""Analog retrieval, prior bands, rule verification and leave-one-out evaluation.

Analog similarity (0-1) = 0.40 same category + 0.15 same offering + 0.10 same customer
                        + 0.35 TF-IDF cosine over name, keywords, demand drivers and segment names.
A prior band for a feature is the p10/p50/p90 of the k most similar markets that have it
(never the market's own family), falling back to its category and then the whole corpus
when fewer than MIN_N analogs carry the feature.
"""
from __future__ import annotations

import hashlib
from statistics import mean, median
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

K, MIN_N = 10, 5
# features a prior can speak to (the rest are descriptive)
PRIOR_FEATURES = ("growth.fcst_cagr", "growth.fcst_minus_hist", "growth.price_cagr",
                  "region.share.NA", "region.share.Europe", "region.share.APAC", "region.share.LATAM", "region.share.MEA",
                  "region.cagr_gap.NA", "region.cagr_gap.Europe", "region.cagr_gap.APAC", "region.cagr_gap.LATAM",
                  "region.cagr_gap.MEA", "region.cagr_range", "country.share.U.S.", "country.share.Canada",
                  "country.share.Germany", "country.share.U.K.", "country.share.France", "country.share.Italy",
                  "country.share.Spain", "country.share.Russia", "country.share.China", "country.share.Japan",
                  "country.share.India", "country.share.South Korea", "country.share.Brazil", "country.share.Mexico",
                  "country.share.Argentina", "country.share.South Africa", "country.rest_share", "country.cagr_gap_sd",
                  "seg.top_share", "seg.cagr_spread", "seg.cagr_dev", "seg.sub_cagr_dev", "seg.country_mix_sd")


def split_of(family: str) -> str:
    """Deterministic 80/20 train/test split by family (a family never straddles the split)."""
    return "test" if int(hashlib.md5(family.encode()).hexdigest(), 16) % 5 == 0 else "train"


def _doc(m: dict) -> str:
    c = m.get("class") or {}
    return " ".join([m["name"], " ".join(c.get("keywords") or []), " ".join(c.get("demand_drivers") or []),
                     " ".join(m.get("segments") or [])]).lower()


class Library:
    def __init__(self, markets: list[dict]):
        self.markets = [m for m in markets if m.get("features")]
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        self.mat = self.vec.fit_transform([_doc(m) for m in self.markets])

    def similar(self, target: dict, exclude_family: str | None = None, pool: list[dict] | None = None) -> list[tuple[float, dict]]:
        tv = self.vec.transform([_doc(target)])
        cos = cosine_similarity(tv, self.mat)[0]
        tc = target.get("class") or {}
        allowed = {id(m) for m in pool} if pool is not None else None
        out = []
        for i, m in enumerate(self.markets):
            if m["family"] == exclude_family or (allowed is not None and id(m) not in allowed):
                continue
            c = m.get("class") or {}
            s = (0.40 * (c.get("category") == tc.get("category")) + 0.15 * (c.get("offering") == tc.get("offering"))
                 + 0.10 * (c.get("customer") == tc.get("customer")) + 0.35 * float(cos[i]))
            out.append((s, m))
        out.sort(key=lambda x: -x[0])
        return out

    def band(self, target: dict, feat: str, exclude_family: str | None = None, pool: list[dict] | None = None) -> dict | None:
        ranked = [(s, m) for s, m in self.similar(target, exclude_family, pool) if feat in m["features"]]
        if not ranked:
            return None
        cat = (target.get("class") or {}).get("category")
        tiers = [("analogs", ranked[:K]),
                 ("category", [(s, m) for s, m in ranked if (m.get("class") or {}).get("category") == cat]),
                 ("all", ranked)]
        for src, rows in tiers:
            if len(rows) >= MIN_N or src == "all":
                vals = sorted(m["features"][feat] for _, m in rows)
                q = lambda p: vals[min(len(vals) - 1, max(0, round(p * (len(vals) - 1))))]
                w = q(0.9) - q(0.1)
                # dispute band: p10-p90 widened by a quarter of its width each side (81% coverage on held-out markets)
                return {"feature": feat, "source": src, "n": len(vals), "p10": q(0.1), "p25": q(0.25), "p50": median(vals),
                        "p75": q(0.75), "p90": q(0.9), "lo": q(0.1) - 0.25 * w, "hi": q(0.9) + 0.25 * w,
                        "markets": [(m["name"], round(m["features"][feat], 4), round(s, 2)) for s, m in rows[:K]]}
        return None


# --------------------------------------------------------------------------- statistics for the rule step
def describe(markets: list[dict], by: str = "category") -> dict[str, Any]:
    def dist(vals):
        v = sorted(vals)
        q = lambda p: v[min(len(v) - 1, round(p * (len(v) - 1)))]
        return [round(q(0.1), 4), round(median(v), 4), round(q(0.9), 4), len(v)]

    feats = sorted({k for m in markets for k in m["features"]})
    out: dict[str, Any] = {"overall": {}, f"by_{by}": {}, "directional": {}}
    for f in feats:
        vals = [m["features"][f] for m in markets if f in m["features"]]
        if len(vals) >= 3:
            out["overall"][f] = dist(vals)
    groups: dict[str, list[dict]] = {}
    for m in markets:
        groups.setdefault((m.get("class") or {}).get(by, "?"), []).append(m)
    for g, ms in groups.items():
        out[f"by_{by}"][g] = {"n_markets": len(ms)} | {
            f: dist([m["features"][f] for m in ms if f in m["features"]])
            for f in PRIOR_FEATURES if sum(f in m["features"] for m in ms) >= 3}
    fr = lambda cond, ms: round(mean(cond(m["features"]) for m in ms), 2) if ms else None
    glob = [m for m in markets if "region.share.NA" in m["features"]]
    out["directional"] = {
        "share of markets with forecast CAGR > historical CAGR": fr(lambda f: f.get("growth.fcst_minus_hist", 0) > 0, markets),
        "share of global markets where APAC grows fastest": fr(lambda f: f.get("region.fastest_is_apac", 0) == 1, glob),
        "share of global markets where Europe grows slowest": fr(lambda f: f.get("region.slowest_is_europe", 0) == 1, glob),
        "share of global markets where NA is the largest region": fr(
            lambda f: f["region.share.NA"] >= max(f.get(f"region.share.{r}", 0) for r in ("Europe", "APAC", "LATAM", "MEA")), glob),
        "share of global markets where NA grows slower than global": fr(lambda f: f.get("region.cagr_gap.NA", 0) < 0, glob),
        "share of markets where smaller segments grow faster (seg.small_fast < 0)": fr(lambda f: f.get("seg.small_fast", 0) < 0,
                                                                                       [m for m in markets if "seg.small_fast" in m["features"]]),
    }
    return out


# --------------------------------------------------------------------------- rules
def _side(f: dict, rhs, off: float = 0.0):
    if isinstance(rhs, (int, float)):
        return float(rhs)
    return f[rhs] + off if isinstance(rhs, str) and rhs in f else None


def rule_applies(rule: dict, m: dict) -> bool:
    sc = rule.get("scope") or {}
    c = m.get("class") or {}
    if any(c.get(k) not in vals for k, vals in sc.items()):
        return False
    f = m["features"]
    if rule["lhs"] not in f:
        return False
    if rule["op"] != "between" and _side(f, rule["rhs"]) is None:
        return False
    return True


def rule_holds(rule: dict, f: dict) -> bool:
    x = f[rule["lhs"]]
    if rule["op"] == "between":
        lo, hi = rule["rhs"]
        return lo <= x <= hi
    y = _side(f, rule["rhs"], float(rule.get("rhs_offset") or 0.0))
    return x > y if rule["op"] == ">" else x < y


def verify_rules(rules: list[dict], markets: list[dict]) -> list[dict]:
    out = []
    for r in rules:
        rec = dict(r)
        for part in ("train", "test"):
            ms = [m for m in markets if m["split"] == part and rule_applies(r, m)]
            rec[f"{part}_n"] = len(ms)
            rec[f"{part}_support"] = round(mean(rule_holds(r, m["features"]) for m in ms), 3) if ms else None
        n_all = rec["train_n"] + rec["test_n"]
        sup = [(rec[f"{p}_support"], rec[f"{p}_n"]) for p in ("train", "test") if rec[f"{p}_support"] is not None]
        rec["support"] = round(sum(s * n for s, n in sup) / n_all, 3) if n_all else None
        # kept only if it also holds on markets that were not used to write it
        rec["status"] = ("kept" if rec["test_n"] >= 3 and (rec["test_support"] or 0) >= 0.7 and (rec["train_support"] or 0) >= 0.7
                         else "weak" if n_all >= 5 and (rec["support"] or 0) >= 0.6 else "rejected")
        out.append(rec)
    return out


# --------------------------------------------------------------------------- leave-one-out evaluation
def loo_eval(lib: Library, feats=PRIOR_FEATURES) -> dict[str, Any]:
    """For every market and feature: predict from (a) the corpus median without the market's family,
    (b) the analog median. Report MAE and how often the truth falls inside the analog p10-p90 band."""
    res: dict[str, Any] = {}
    for f in feats:
        errs_all, errs_an, inside = [], [], []
        for m in lib.markets:
            if f not in m["features"]:
                continue
            others = [o["features"][f] for o in lib.markets if o["family"] != m["family"] and f in o["features"]]
            if len(others) < MIN_N:
                continue
            b = lib.band(m, f, exclude_family=m["family"])
            if not b:
                continue
            t = m["features"][f]
            errs_all.append(abs(median(others) - t))
            errs_an.append(abs(b["p50"] - t))
            inside.append(b["p10"] <= t <= b["p90"])
        if errs_all:
            res[f] = {"n": len(errs_all), "mae_corpus_median": round(mean(errs_all), 4), "mae_analogs": round(mean(errs_an), 4),
                      "gain": round(1 - mean(errs_an) / mean(errs_all), 3) if mean(errs_all) else 0.0,
                      "band_coverage": round(mean(inside), 2)}
    return res
