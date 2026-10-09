"""Structural pre-filter for signal siting (docs/SITING.md). NOT an optimiser.

score = W_DEGREE*degree + W_CLASS*road_class + W_BETWEENNESS*betweenness + W_SIGNAL*osm_signal
        - W_SPACING*spacing_penalty, clamped to [0, 1].

All features are normalised to [0, 1]. The spacing penalty is applied greedily: junctions are
picked best-first, and each candidate is penalised by its proximity to junctions already
ranked above it, so the top of the list spreads over the area. Deterministic (ties by id).
Simulation-based selection (sim_gain_s) comes later and may reorder the list.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from backend.contract.models import Intersection, RoadNetwork
from backend.roadnet.geo import distance_m
from backend.roadnet.intersections import ROAD_CLASS_RANK

W_DEGREE = 0.25
W_CLASS = 0.30
W_BETWEENNESS = 0.35
W_SIGNAL = 0.10
W_SPACING = 0.15
SPACING_RADIUS_M = 200.0  # penalty fades linearly to 0 at this distance
MAX_DEGREE = 4  # approaches; more counts as 4
RECOMMENDED_COUNT = 3
MIN_RECOMMENDED_SCORE = 0.2


@dataclass(frozen=True)
class Features:
    degree: float
    road_class: float
    betweenness: float
    osm_signal: float

    def base(self) -> float:
        return (W_DEGREE * self.degree + W_CLASS * self.road_class
                + W_BETWEENNESS * self.betweenness + W_SIGNAL * self.osm_signal)


def _graph(net: RoadNetwork) -> nx.DiGraph:
    g = nx.DiGraph()
    for e in net.edges:
        tt = e.length_m / e.speed_limit
        if g.has_edge(e.from_node, e.to_node) and g[e.from_node][e.to_node]["w"] <= tt:
            continue
        g.add_edge(e.from_node, e.to_node, w=tt)
    return g


def features(net: RoadNetwork, intersections: list[Intersection], ignore_osm_signals: bool) -> dict[str, Features]:
    edges = {e.id: e for e in net.edges}
    max_rank = max(ROAD_CLASS_RANK.values())
    # Betweenness on travel time, only between boundary nodes (where trips start and end).
    g = _graph(net)
    sources = [n for n in net.entry_nodes if n in g]
    targets = [n for n in net.exit_nodes if n in g]
    bc = nx.betweenness_centrality_subset(g, sources=sources, targets=targets, normalized=False, weight="w") \
        if sources and targets else {}
    max_bc = max((bc.get(i.node_id, 0.0) for i in intersections), default=0.0) or 1.0

    out: dict[str, Features] = {}
    for ix in intersections:
        ranks = sorted((ROAD_CLASS_RANK[edges[a.in_edge].road_class] for a in ix.approaches), reverse=True)
        # Mean of the two biggest approaches: a primary x primary crossing beats primary x residential.
        top2 = ranks[:2] if len(ranks) >= 2 else ranks
        out[ix.id] = Features(
            degree=min(len(ix.approaches), MAX_DEGREE) / MAX_DEGREE,
            road_class=(sum(top2) / len(top2)) / max_rank,
            betweenness=bc.get(ix.node_id, 0.0) / max_bc,
            osm_signal=0.0 if ignore_osm_signals else float(ix.has_signal_in_osm),
        )
    return out


def rank(
    net: RoadNetwork, intersections: list[Intersection], ignore_osm_signals: bool = False
) -> tuple[list[Intersection], list[str]]:
    """Return (intersections best-first with structural_score set, recommended_ids)."""
    feats = features(net, intersections, ignore_osm_signals)
    by_id = {i.id: i for i in intersections}
    remaining = set(by_id)
    picked: list[tuple[str, float]] = []
    while remaining:
        best_id, best_score = "", -1.0
        for iid in sorted(remaining):
            ix = by_id[iid]
            penalty = max(
                (max(0.0, 1 - distance_m(ix.lat, ix.lon, by_id[p].lat, by_id[p].lon) / SPACING_RADIUS_M)
                 for p, _ in picked),
                default=0.0,
            )
            score = min(1.0, max(0.0, feats[iid].base() - W_SPACING * penalty))
            if score > best_score + 1e-12:
                best_id, best_score = iid, score
        picked.append((best_id, round(best_score, 4)))
        remaining.remove(best_id)

    ranked = [by_id[i].model_copy(update={"structural_score": s}) for i, s in picked]
    recommended = [i.id for i in ranked[:RECOMMENDED_COUNT] if i.structural_score >= MIN_RECOMMENDED_SCORE]
    return ranked, recommended
