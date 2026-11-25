"""Geography tree and segmentation dimensions loaded from taxonomy.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path


@dataclass(frozen=True)
class Dimension:
    title: str
    priced: bool
    top: tuple[str, ...]                     # top-level segments (share of dimension)
    children: dict[str, tuple[str, ...]]     # parent -> children (share of parent)

    @property
    def rows(self) -> list[tuple[str, str | None]]:
        """(segment, parent) in sheet order: each parent followed by its children."""
        out: list[tuple[str, str | None]] = []
        for s in self.top:
            out.append((s, None))
            for c in self.children.get(s, ()):
                out.append((c, s))
        return out

    @property
    def all_segments(self) -> list[str]:
        return [s for s, _ in self.rows]

    @property
    def leaves(self) -> list[str]:
        out = []
        for s in self.top:
            out.extend(self.children.get(s, ()) or (s,))
        return out


class Taxonomy:
    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.raw = raw
        self.market_name: str = raw["market_name"]
        self.short_name: str = raw["short_name"]
        self.parent_of: dict[str, str | None] = {}
        self.children_of: dict[str, list[str]] = {}
        root_name, tree = next(iter(raw["geography"].items()))
        self.root = root_name
        self.parent_of[root_name] = None
        self._walk(root_name, tree)
        self.dimensions = tuple(
            Dimension(
                title=d["title"],
                priced=bool(d.get("priced")),
                top=tuple(s["name"] for s in d["segments"]),
                children={s["name"]: tuple(s["children"]) for s in d["segments"] if s.get("children")},
            )
            for d in raw["dimensions"]
        )
        self.oem_aliases: dict[str, str] = raw.get("oem_geo_aliases", {})
        self.strategies: dict = raw.get("strategies", {})

    def strategy_of(self, dim_title: str) -> str | None:
        """'2B' / '2C' for a segmentation dimension when the market declares it (taxonomy 'strategies')."""
        return next((k for k, v in self.strategies.items()
                     if isinstance(v, dict) and v.get("dimension") == dim_title), None)   # skips 'how_they_combine' text

    def _walk(self, name: str, sub) -> None:
        kids: list[str] = []
        if isinstance(sub, dict):
            for child, grand in sub.items():
                kids.append(child)
                self.parent_of[child] = name
                self._walk(child, grand)
        elif isinstance(sub, list):
            for child in sub:
                kids.append(child)
                self.parent_of[child] = name
                self.children_of[child] = []
        self.children_of[name] = kids

    # ------------------------------------------------------------------ geo
    @cached_property
    def leaves(self) -> list[str]:
        """Leaf geographies in sheet order (depth-first)."""
        out: list[str] = []

        def rec(n: str) -> None:
            kids = self.children_of.get(n, [])
            if not kids:
                out.append(n)
            for k in kids:
                rec(k)
        rec(self.root)
        return out

    @cached_property
    def parent_nodes(self) -> list[str]:
        """Every node that has children, breadth-first (Global first)."""
        out, queue = [], [self.root]
        while queue:
            n = queue.pop(0)
            if self.children_of.get(n):
                out.append(n)
                queue.extend(self.children_of[n])
        return out

    @cached_property
    def regions(self) -> list[str]:
        return list(self.children_of[self.root])

    def depth(self, node: str) -> int:
        d = 0
        while self.parent_of.get(node):
            node = self.parent_of[node]
            d += 1
        return d

    def path(self, node: str) -> list[str]:
        p = [node]
        while self.parent_of.get(node):
            node = self.parent_of[node]
            p.append(node)
        return list(reversed(p))

    def region_of(self, node: str) -> str:
        p = self.path(node)
        return p[1] if len(p) > 1 else node

    @cached_property
    def leaf_groups(self) -> dict[str, list[str]]:
        """Parent node -> its direct leaf children (the unit a segment analyst covers)."""
        groups: dict[str, list[str]] = {}
        for leaf in self.leaves:                 # a single-country market is its own group
            groups.setdefault(self.parent_of[leaf] or leaf, []).append(leaf)
        return groups

    def dimension(self, title: str) -> Dimension:
        return next(d for d in self.dimensions if d.title == title)

    @property
    def priced_dimension(self) -> Dimension:
        return next(d for d in self.dimensions if d.priced)
