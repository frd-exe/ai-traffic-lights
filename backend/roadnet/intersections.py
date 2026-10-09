"""RoadNetwork -> Intersections (junction detection + approaches with deterministic ids).

A junction is a non-boundary node connected to >= 3 distinct neighbour nodes. Each in-edge is an
approach; bearing = direction from the junction toward the upstream end of the in-edge
(CONTRACT.md §1). Scores are filled in later by backend/siting.
"""

from __future__ import annotations

import logging

from backend.contract.helpers import approach_id, intersection_id
from backend.contract.models import Approach, Intersection, RoadNetwork

from .geo import bearing_deg

log = logging.getLogger(__name__)

ROAD_CLASS_RANK = {
    "motorway": 8, "trunk": 7, "primary": 6, "secondary": 5, "tertiary": 4,
    "unclassified": 3, "residential": 2, "living_street": 1, "service": 0,
}


def junction_node_ids(net: RoadNetwork) -> list[str]:
    boundary = set(net.entry_nodes) | set(net.exit_nodes)
    neighbours: dict[str, set[str]] = {}
    for e in net.edges:
        neighbours.setdefault(e.from_node, set()).add(e.to_node)
        neighbours.setdefault(e.to_node, set()).add(e.from_node)
    return sorted(n for n, nb in neighbours.items() if len(nb) >= 3 and n not in boundary)


def build_intersections(
    net: RoadNetwork,
    exclude: set[str] | frozenset[str] = frozenset(),
    signalised: set[str] | frozenset[str] = frozenset(),
) -> list[Intersection]:
    """Unscored intersections (structural_score=0) for every junction not in `exclude`."""
    nodes = {n.id: n for n in net.nodes}
    out_by_node: dict[str, list] = {}
    in_by_node: dict[str, list] = {}
    for e in net.edges:
        out_by_node.setdefault(e.from_node, []).append(e)
        in_by_node.setdefault(e.to_node, []).append(e)

    result: list[Intersection] = []
    for nid in junction_node_ids(net):
        if nid in exclude:
            continue
        n = nodes[nid]
        iid = intersection_id(n.lat, n.lon)
        approaches: dict[str, Approach] = {}
        for e in sorted(in_by_node.get(nid, []), key=lambda x: x.id):
            up_lon, up_lat = e.geometry[-2]
            b = round(bearing_deg(n.lat, n.lon, up_lat, up_lon), 2) % 360
            aid = approach_id(iid, b)
            outs = sorted(o.id for o in out_by_node.get(nid, []) if o.to_node != e.from_node)
            cand = Approach(id=aid, bearing=b, lanes=e.lanes, in_edge=e.id, out_edges=outs)
            prev = approaches.get(aid)
            if prev is not None:
                # Two in-edges within the same 5-degree bucket: keep the bigger road, log the drop.
                log.info("approach id collision at %s (%s vs %s); keeping the larger road", iid, prev.in_edge, e.id)
                if (cand.lanes, cand.in_edge) <= (prev.lanes, prev.in_edge):
                    continue
            approaches[aid] = cand
        if len(approaches) < 2:
            continue
        result.append(Intersection(
            id=iid, node_id=nid, lat=n.lat, lon=n.lon,
            approaches=sorted(approaches.values(), key=lambda a: (a.bearing, a.id)),
            has_signal_in_osm=nid in signalised, structural_score=0.0, sim_gain_s=None,
        ))
    return result
