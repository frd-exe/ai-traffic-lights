"""Offline fixture loading/detection for examples, tests and benchmarking."""
import json
import math
from pathlib import Path

from backend.contract.helpers import approach_id, intersection_id
from backend.contract.models import Approach, Intersection, RoadNetwork


def load_network(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / "data/grid_network.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return RoadNetwork.model_validate(data.get("network", data))


def derive_intersections(network):
    """Fixture-only junction detection; production area workstream supplies models."""
    nodes = {n.id: n for n in network.nodes}
    result = []
    for node in network.nodes:
        incoming = [e for e in network.edges if e.to_node == node.id]
        outgoing = [e for e in network.edges if e.from_node == node.id]
        if len(incoming) < 2:
            continue
        iid = intersection_id(node.lat, node.lon)
        approaches = []
        for edge in sorted(incoming, key=lambda e: e.id):
            upstream = nodes[edge.from_node]
            bearing = math.degrees(math.atan2((upstream.lon - node.lon) * math.cos(math.radians(node.lat)),
                                             upstream.lat - node.lat)) % 360
            approaches.append(Approach(id=approach_id(iid, bearing), bearing=bearing, lanes=edge.lanes,
                in_edge=edge.id, out_edges=[e.id for e in outgoing if e.to_node != edge.from_node]))
        result.append(Intersection(id=iid, node_id=node.id, lat=node.lat, lon=node.lon,
            approaches=approaches, has_signal_in_osm=False, structural_score=0))
    return result
