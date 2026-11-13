"""OEM (new-unit) market workbooks -> per-geography evidence.

The OEM market is the flow of new high-speed helical gearboxes into the installed
base; it is the best quantitative signal available for where the aftermarket's
installed base sits and what a gearbox is worth. Values in these workbooks are
US$ thousand (labels say "Mn" on country sheets but the magnitudes are thousands:
the global 2025 total is ~451,816 = US$ 452 Mn). The Africa workbook is on a
different, inconsistent scale, so for Africa only *relative* country splits are used.
Volume rows hold units (not thousands): ~3,789 units globally in 2025.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from ..taxonomy import Taxonomy

YEARS = list(range(2020, 2033))          # columns E..Q
_YEAR_COL0 = 5


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip() if s is not None else ""


@dataclass
class OEMGeo:
    name: str
    source: str
    value_k: dict[int, float]                       # US$ thousand by year
    seg_value_k: dict[str, float] = field(default_factory=dict)   # 2025, every segment row
    seg_units: dict[str, float] = field(default_factory=dict)     # 2025 units (where present)
    seg_units_y: dict[str, dict[int, float]] = field(default_factory=dict)  # units by year
    scale_uncertain: bool = False

    def v(self, year: int) -> float:
        return self.value_k.get(year, 0.0) / 1000.0     # US$ Mn

    def cagr(self, y0: int, y1: int) -> float | None:
        a, b = self.value_k.get(y0), self.value_k.get(y1)
        if not a or not b or a <= 0:
            return None
        return (b / a) ** (1 / (y1 - y0)) - 1

    def mix(self, tax: Taxonomy, dim_title: str) -> dict[str, float]:
        """2025 shares in the deliverable's convention: top-level = share of
        dimension, children = share of their parent."""
        dim = tax.dimension(dim_title)
        tot = sum(self.seg_value_k.get(s, 0.0) for s in dim.top)
        out: dict[str, float] = {}
        if tot <= 0:
            return out
        for s in dim.top:
            out[s] = self.seg_value_k.get(s, 0.0) / tot
            kids = dim.children.get(s, ())
            ktot = sum(self.seg_value_k.get(k, 0.0) for k in kids)
            for k in kids:
                out[k] = self.seg_value_k.get(k, 0.0) / ktot if ktot > 0 else 0.0
        return out

    def asp_usd(self) -> dict[str, float]:
        out = {}
        for s, u in self.seg_units.items():
            val = self.seg_value_k.get(s)
            if u and u > 0 and val:
                out[s] = val * 1000.0 / u
        return out


def _read_sheet(ws, tax: Taxonomy, source: str) -> OEMGeo | None:
    seg_names = {_clean(s).lower(): s for d in tax.dimensions for s in d.all_segments}
    seg_names["others"] = "Others"
    rows = list(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 140), max_col=17, values_only=True))
    total = None
    seg_val: dict[str, float] = {}
    seg_units: dict[str, float] = {}
    seg_units_y: dict[str, dict[int, float]] = {}
    section = "value"
    for r in rows:
        c, d = _clean(r[2]), _clean(r[3])
        if c in ("Price",):
            section = "price"
        elif c == "Volume":
            section = "volume"
        if total is None and "Value in" in d:
            total = {y: float(r[_YEAR_COL0 - 1 + i] or 0) for i, y in enumerate(YEARS)}
            continue
        key = seg_names.get(d.lower())
        if not key:
            continue
        v25 = r[_YEAR_COL0 - 1 + YEARS.index(2025)]
        if not isinstance(v25, (int, float)):
            continue
        if section == "value" and key not in seg_val:
            seg_val[key] = float(v25)
        elif section == "volume" and key not in seg_units and v25:
            seg_units[key] = float(v25)
            seg_units_y[key] = {y: float(r[_YEAR_COL0 - 1 + i] or 0) for i, y in enumerate(YEARS)}
    if not total or total.get(2025, 0) <= 0:
        return None
    name = tax.oem_aliases.get(ws.title, ws.title.strip())
    return OEMGeo(name=name, source=source, value_k=total, seg_value_k=seg_val, seg_units=seg_units, seg_units_y=seg_units_y,
                  scale_uncertain=source.lower().startswith("africa"))


def load_oem(oem_dir: Path, tax: Taxonomy) -> dict[str, OEMGeo]:
    out: dict[str, OEMGeo] = {}
    for f in sorted(oem_dir.glob("*.xlsx")):
        if f.name.lower().startswith("dataset") or f.name.startswith("~$"):
            continue
        wb = load_workbook(f, read_only=True, data_only=True)
        for ws in wb.worksheets:
            g = _read_sheet(ws, tax, f.name)
            # first sheet wins, except a scale-consistent sheet replaces an Africa-file one
            if g and (g.name not in out or (out[g.name].scale_uncertain and not g.scale_uncertain)):
                out[g.name] = g
        wb.close()
    return out


def oem_table(oem: dict[str, OEMGeo], geos: list[str], tax: Taxonomy, with_mix: str | None = None) -> str:
    """Compact text table of OEM evidence for prompts."""
    lines = ["geo | OEM 2025 US$ Mn | OEM CAGR 2020-25 | OEM CAGR 2025-32" + (f" | OEM 2025 mix ({with_mix})" if with_mix else "")]
    for g in geos:
        o = oem.get(g)
        if not o:
            lines.append(f"{g} | n/a (no separate OEM sheet; inside a 'Rest of' or grouped OEM geography) | | ")
            continue
        h, f = o.cagr(2020, 2025), o.cagr(2025, 2032)
        val = "scale-inconsistent (use relative only): raw " + f"{o.value_k[2025]:.3g}" if o.scale_uncertain else f"{o.v(2025):.2f}"
        mix = ""
        if with_mix:
            m = o.mix(tax, with_mix)
            mix = " | " + ", ".join(f"{k}: {v:.1%}" for k, v in m.items())
        lines.append(f"{g} | {val} | {h:.1%} | {f:.1%}{mix}" if h is not None and f is not None else f"{g} | {val} | | {mix}")
    return "\n".join(lines)
