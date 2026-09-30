"""Route planner for the document converter (Module 2).

Formats are graph nodes and conversion steps are edges, both loaded from JSON
(resources/defaults/conversion/{formats,routes}.json) so routes can be changed
without touching code. For a (source, target, mode) request the planner returns
the cheapest chain of steps, honouring per-pair overrides, and lists what the
chain can lose: format-capability losses (worked out from the formats the chain
passes through) plus the step-specific losses declared on each edge.
"""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from itertools import count
from pathlib import Path

MODES = ("exact", "editable")
LEVELS = {"none": 0, "partial": 1, "full": 2}


class CatalogError(ValueError):
    """The formats/routes JSON is inconsistent."""


class NoRouteError(LookupError):
    """No chain of edges converts source to target in the requested mode."""


@dataclass(frozen=True)
class StepLoss:
    text: str
    if_source_has: str | None = None  # only relevant when the source format can hold this feature


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    engine: str
    costs: dict[str, float]
    final_only: bool
    summary: str
    losses: tuple[StepLoss, ...]


@dataclass(frozen=True)
class Route:
    source: str
    target: str
    mode: str
    steps: tuple[Edge, ...]
    cost: float
    override_reason: str | None = None

    @property
    def chain(self) -> list[str]:
        return [self.source] + [step.dst for step in self.steps]


@dataclass(frozen=True)
class Loss:
    feature: str
    label: str
    level: str  # "lost" or "reduced"


class Catalog:
    def __init__(self, formats_doc: dict, routes_doc: dict):
        self.features: dict[str, dict] = formats_doc["features"]
        self.formats: dict[str, dict] = formats_doc["formats"]
        self.engines: dict[str, str] = routes_doc.get("engines", {})
        self.edges: list[Edge] = []
        self.overrides: dict[tuple[str, str, str], tuple[list[str], str]] = {}
        self._validate_formats()
        self._load_edges(routes_doc["edges"])
        self._load_overrides(routes_doc.get("overrides", []))
        self._out: dict[str, list[Edge]] = {}
        for edge in self.edges:
            self._out.setdefault(edge.src, []).append(edge)

    # ---------------------------------------------------------------- loading
    @classmethod
    def from_dir(cls, directory: str | Path) -> "Catalog":
        directory = Path(directory)
        with open(directory / "formats.json", encoding="utf-8") as fh:
            formats_doc = json.load(fh)
        with open(directory / "routes.json", encoding="utf-8") as fh:
            routes_doc = json.load(fh)
        return cls(formats_doc, routes_doc)

    def _validate_formats(self) -> None:
        for fid, fmt in self.formats.items():
            for key in ("label", "roles", "caps"):
                if key not in fmt:
                    raise CatalogError(f"format '{fid}' is missing '{key}'")
            missing = set(self.features) - set(fmt["caps"])
            if missing:
                raise CatalogError(f"format '{fid}' caps missing {sorted(missing)}")
            for feat, level in {**fmt["caps"], **fmt.get("sourceCaps", {})}.items():
                if feat not in self.features:
                    raise CatalogError(f"format '{fid}' has unknown feature '{feat}'")
                if level not in LEVELS:
                    raise CatalogError(f"format '{fid}' feature '{feat}' has bad level '{level}'")
            for mode in fmt.get("targetModes", []):
                if mode not in MODES:
                    raise CatalogError(f"format '{fid}' has unknown target mode '{mode}'")
            if "target" in fmt["roles"] and not fmt.get("targetModes"):
                raise CatalogError(f"target format '{fid}' needs targetModes")

    def _load_edges(self, raw_edges: list[dict]) -> None:
        seen_ids: set[str] = set()
        for raw in raw_edges:
            eid = raw.get("id")
            if not eid or eid in seen_ids:
                raise CatalogError(f"edge id missing or duplicated: {eid!r}")
            seen_ids.add(eid)
            if "pairs" in raw:
                pairs = [tuple(p) for p in raw["pairs"]]
            else:
                pairs = [(s, d) for s in raw["from"] for d in raw["to"]]
            costs = raw.get("modes") or {}
            if not costs or any(m not in MODES for m in costs):
                raise CatalogError(f"edge '{eid}' needs modes drawn from {MODES}")
            if any(not isinstance(c, (int, float)) or c < 0 for c in costs.values()):
                raise CatalogError(f"edge '{eid}' has a negative or non-numeric cost")
            if raw.get("engine") not in self.engines:
                raise CatalogError(f"edge '{eid}' uses unknown engine {raw.get('engine')!r}")
            raw_losses = raw.get("losses", [])
            for src, dst in pairs:
                for fid in (src, dst):
                    if fid not in self.formats:
                        raise CatalogError(f"edge '{eid}' references unknown format '{fid}'")
                if src == dst:
                    raise CatalogError(f"edge '{eid}' converts '{src}' to itself")
                picked = (list(raw_losses.get("*", [])) + list(raw_losses.get(dst, []))
                          if isinstance(raw_losses, dict) else list(raw_losses))
                losses = tuple(self._step_loss(eid, item) for item in picked)
                self.edges.append(Edge(
                    id=eid, src=src, dst=dst, engine=raw["engine"], costs=dict(costs),
                    final_only=bool(raw.get("finalOnly", False)),
                    summary=raw.get("summary", ""), losses=losses,
                ))

    def _step_loss(self, eid: str, item: str | dict) -> StepLoss:
        if isinstance(item, str):
            return StepLoss(item)
        feature = item.get("ifSourceHas")
        if feature is not None and feature not in self.features:
            raise CatalogError(f"edge '{eid}' loss refers to unknown feature '{feature}'")
        return StepLoss(item["text"], feature)

    def _load_overrides(self, raw_overrides: list[dict]) -> None:
        for raw in raw_overrides:
            chain = raw["chain"]
            if chain[0] != raw["source"] or chain[-1] != raw["target"]:
                raise CatalogError(f"override {raw['source']}->{raw['target']} chain has wrong ends")
            for mode in raw.get("modes", MODES):
                self.overrides[(raw["source"], raw["target"], mode)] = (chain, raw.get("reason", ""))

    # ---------------------------------------------------------------- queries
    def sources(self) -> list[str]:
        return [f for f, d in self.formats.items() if "source" in d["roles"]]

    def targets(self) -> list[str]:
        return [f for f, d in self.formats.items() if "target" in d["roles"]]

    def effective_mode(self, target: str, mode: str) -> str:
        """Targets where 'editable' means nothing (PDF, images, TXT) run in exact mode."""
        modes = self.formats[target]["targetModes"]
        return mode if mode in modes else modes[0]

    def plan(self, source: str, target: str, mode: str) -> Route:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if source not in self.formats or "source" not in self.formats[source]["roles"]:
            raise NoRouteError(f"'{source}' is not a source format")
        if target not in self.formats or "target" not in self.formats[target]["roles"]:
            raise NoRouteError(f"'{target}' is not a target format")
        if source == target:
            raise NoRouteError("source and target are the same format")
        mode = self.effective_mode(target, mode)

        override = self.overrides.get((source, target, mode))
        if override:
            chain, reason = override
            return self._route_from_chain(chain, mode, reason)
        return self._dijkstra(source, target, mode)

    def _dijkstra(self, source: str, target: str, mode: str) -> Route:
        tie = count()
        # (cost, hops, tiebreak, node, steps)
        heap: list[tuple[float, int, int, str, tuple[Edge, ...]]] = [(0.0, 0, next(tie), source, ())]
        best: dict[str, tuple[float, int]] = {}
        while heap:
            cost, hops, _, node, steps = heapq.heappop(heap)
            if node == target:
                return Route(source, target, mode, steps, round(cost, 4))
            if node in best and best[node] <= (cost, hops):
                continue
            best[node] = (cost, hops)
            if not self._expandable(node, source):
                continue
            visited = {source, *(s.dst for s in steps)}
            for edge in self._out.get(node, []):
                if mode not in edge.costs or edge.dst in visited:
                    continue
                if edge.final_only and edge.dst != target:
                    continue
                heapq.heappush(heap, (cost + edge.costs[mode], hops + 1, next(tie), edge.dst, steps + (edge,)))
        raise NoRouteError(f"no {mode} route from '{source}' to '{target}'")

    def _expandable(self, node: str, source: str) -> bool:
        """Routes may leave a node only if it is the source, a transit format, or
        the modern form a legacy source normalises to (DOC -> DOCX -> ...)."""
        return (node == source
                or self.formats[node].get("transit", False)
                or node == self.formats[source].get("normalizeTo"))

    def _route_from_chain(self, chain: list[str], mode: str, reason: str) -> Route:
        steps: list[Edge] = []
        for src, dst in zip(chain, chain[1:]):
            options = [e for e in self._out.get(src, []) if e.dst == dst and mode in e.costs]
            if not options:
                raise CatalogError(f"override chain step {src}->{dst} has no {mode} edge")
            steps.append(min(options, key=lambda e: e.costs[mode]))
        cost = sum(s.costs[mode] for s in steps)
        return Route(chain[0], chain[-1], mode, tuple(steps), round(cost, 4), reason or "override")

    def potential_losses(self, route: Route) -> list[Loss]:
        """Features the source format can hold that some format on the chain cannot."""
        src = self.formats[route.source]
        src_caps = {**src["caps"], **src.get("sourceCaps", {})}
        losses: list[Loss] = []
        for feat, meta in self.features.items():
            have = LEVELS[src_caps[feat]]
            if have == 0:
                continue
            keep = min(LEVELS[self.formats[f]["caps"][feat]] for f in route.chain[1:])
            if keep < have:
                losses.append(Loss(feat, meta["label"], "lost" if keep == 0 else "reduced"))
        return losses

    def step_losses(self, route: Route) -> list[str]:
        src = self.formats[route.source]
        src_caps = {**src["caps"], **src.get("sourceCaps", {})}
        out: list[str] = []
        for step in route.steps:
            for loss in step.losses:
                if loss.if_source_has and src_caps[loss.if_source_has] == "none":
                    continue
                if loss.text not in out:
                    out.append(loss.text)
        return out
