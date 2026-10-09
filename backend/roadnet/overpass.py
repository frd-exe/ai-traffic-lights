"""Overpass JSON -> contract RoadNetwork (+ roundabout / OSM-signal info for intersections).

Pipeline:
 1. keep drivable ways (motorway..residential incl. *_link; drop service, footway, ...)
 2. split ways at shared nodes / endpoints -> segments between graph nodes
 3. consolidate graph nodes closer than CONSOLIDATE_RADIUS_M (dual carriageways, complex
    junctions) into one node at the cluster centroid (cluster diameter capped)
 4. directed edges per oneway tags, lanes per direction, speed (m/s), length (m)
 5. clip to bbox: keep edges touching the inside; outside endpoints become boundary nodes
 6. keep the largest weakly connected component
 7. entry nodes = boundary nodes with an out-edge, exit nodes = boundary nodes with an in-edge
Deterministic for the same input.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from backend.contract.constants import CONSOLIDATE_RADIUS_M, ID_COORD_DECIMALS, SIGNAL_SNAP_M
from backend.contract.models import BBox, Edge, Node, RoadNetwork, RoadClass

from .geo import distance_m, polyline_length_m

DRIVABLE: dict[str, RoadClass] = {
    "motorway": "motorway", "motorway_link": "motorway",
    "trunk": "trunk", "trunk_link": "trunk",
    "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "tertiary", "tertiary_link": "tertiary",
    "unclassified": "unclassified", "residential": "residential",
}
DEFAULT_SPEED_KMH: dict[str, float] = {
    "motorway": 110, "trunk": 90, "primary": 50, "secondary": 50, "tertiary": 40,
    "unclassified": 40, "residential": 30,
}
DEFAULT_LANES_PER_DIR: dict[str, int] = {"motorway": 2, "trunk": 2, "primary": 2}
CLASS_RANK = {c: r for r, c in enumerate(
    ["residential", "unclassified", "tertiary", "secondary", "primary", "trunk", "motorway"])}
MAX_CLUSTER_DIAMETER_M = 3 * CONSOLIDATE_RADIUS_M
NO_ACCESS = {"no", "private"}


class OverpassParseError(ValueError):
    pass


@dataclass
class ParsedArea:
    network: RoadNetwork
    roundabout_nodes: set[str] = field(default_factory=set)  # network node ids; not signal candidates
    signalised_nodes: set[str] = field(default_factory=set)  # network node ids with an OSM signal nearby
    stats: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ tag parsing
def _first_number(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def speed_mps(tags: dict, road_class: str) -> float:
    raw = tags.get("maxspeed", "")
    v = _first_number(raw)
    if v is None or v <= 0:
        v = DEFAULT_SPEED_KMH[road_class]
    elif "mph" in raw:
        v *= 1.609344
    return round(v / 3.6, 2)


def oneway_dir(tags: dict, road_class: str) -> int:
    """1 = forward only, -1 = backward only, 0 = both."""
    ow = tags.get("oneway", "").lower()
    if ow in ("yes", "true", "1"):
        return 1
    if ow in ("-1", "reverse"):
        return -1
    if ow == "no":
        return 0
    if tags.get("junction") in ("roundabout", "circular") or road_class == "motorway":
        return 1
    return 0


def lanes_per_dir(tags: dict, road_class: str, direction: int, oneway: int) -> int:
    key = "lanes:forward" if direction == 1 else "lanes:backward"
    v = _first_number(tags.get(key))
    if v is None:
        total = _first_number(tags.get("lanes"))
        if total is not None:
            v = total if oneway else total / 2
    if v is None:
        v = DEFAULT_LANES_PER_DIR.get(road_class, 1)
    return max(1, int(v))


# ------------------------------------------------------------------ ids
def _coord(x: float) -> str:
    return f"{round(x, ID_COORD_DECIMALS):.{ID_COORD_DECIMALS}f}"


def node_id_for(lat: float, lon: float) -> str:
    return "n_" + hashlib.sha1(f"{_coord(lat)},{_coord(lon)}".encode()).hexdigest()[:8]


# ------------------------------------------------------------------ consolidation
class _Clusters:
    def __init__(self, pos: dict[int, tuple[float, float]]):
        self.pos = pos
        self.parent = {k: k for k in pos}
        self.members = {k: [k] for k in pos}

    def find(self, k: int) -> int:
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def try_union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        merged = self.members[ra] + self.members[rb]
        for i, x in enumerate(merged):
            for y in merged[i + 1:]:
                if distance_m(*self.pos[x], *self.pos[y]) > MAX_CLUSTER_DIAMETER_M:
                    return False
        keep, drop = (ra, rb) if ra < rb else (rb, ra)
        self.parent[drop] = keep
        self.members[keep] = sorted(merged)
        del self.members[drop]
        return True

    def centroid(self, root: int) -> tuple[float, float]:
        m = self.members[root]
        return (sum(self.pos[k][0] for k in m) / len(m), sum(self.pos[k][1] for k in m) / len(m))


def _close_pairs(points: dict[int, tuple[float, float]], radius_m: float) -> list[tuple[float, int, int]]:
    """All pairs within radius (grid hashing), sorted by (distance, a, b)."""
    cell = radius_m / 111_320.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for k, (lat, lon) in points.items():
        grid[(int(lat // cell), int(lon // cell))].append(k)
    pairs = []
    for (ci, cj), ks in grid.items():
        for di in (-1, 0, 1):
            for dj in range(-3, 4):  # lon cells are narrower in metres away from the equator
                for b in grid.get((ci + di, cj + dj), ()):
                    for a in ks:
                        if a < b:
                            d = distance_m(*points[a], *points[b])
                            if d < radius_m:
                                pairs.append((d, a, b))
    return sorted(set(pairs))


def consolidate(pos: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float]]:
    """Map each OSM graph node to its cluster centroid. Repeats on centroids until stable."""
    cl = _Clusters(pos)
    for _ in range(5):
        roots = {cl.find(k) for k in pos}
        cents = {r: cl.centroid(r) for r in roots}
        merged = False
        for _, a, b in _close_pairs(cents, CONSOLIDATE_RADIUS_M):
            merged |= cl.try_union(a, b)
        if not merged:
            break
    return {k: cl.centroid(cl.find(k)) for k in pos}


# ------------------------------------------------------------------ main
def parse_overpass(osm: dict, bbox: BBox | None = None) -> ParsedArea:
    elements = osm.get("elements")
    if not isinstance(elements, list):
        raise OverpassParseError("not an Overpass JSON response (no 'elements' list)")

    nodes: dict[int, tuple[float, float]] = {}
    node_tags: dict[int, dict] = {}
    ways: list[dict] = []
    for el in elements:
        t = el.get("type")
        if t == "node" and "lat" in el and "lon" in el:
            nodes[el["id"]] = (float(el["lat"]), float(el["lon"]))
            if el.get("tags"):
                node_tags[el["id"]] = el["tags"]
        elif t == "way":
            tags = el.get("tags") or {}
            rc = DRIVABLE.get(tags.get("highway", ""))
            if rc is None or tags.get("area") == "yes":
                continue
            if tags.get("access") in NO_ACCESS or tags.get("motor_vehicle") in NO_ACCESS:
                continue
            ways.append({"id": el["id"], "tags": tags, "class": rc, "nodes": el.get("nodes", [])})
    ways = [w for w in ways if sum(1 for n in w["nodes"] if n in nodes) >= 2]
    for w in ways:
        w["nodes"] = [n for n in w["nodes"] if n in nodes]
    if not ways:
        raise OverpassParseError("no drivable roads found in the data")

    # 2. graph nodes: endpoints + nodes used more than once
    use = defaultdict(int)
    for w in ways:
        for n in w["nodes"]:
            use[n] += 1
        use[w["nodes"][0]] += 1
        use[w["nodes"][-1]] += 1
    graph_nodes = {n for n, c in use.items() if c >= 2}

    segments = []  # (way, [osm node ids])
    for w in ways:
        cur = [w["nodes"][0]]
        for n in w["nodes"][1:]:
            cur.append(n)
            if n in graph_nodes:
                segments.append((w, cur))
                cur = [n]

    roundabout_osm = {n for w in ways if w["tags"].get("junction") in ("roundabout", "circular") for n in w["nodes"]}
    roundabout_osm |= {n for n, t in node_tags.items() if t.get("highway") == "mini_roundabout"}

    # 3. consolidate
    gpos = {n: nodes[n] for n in graph_nodes}
    cent = consolidate(gpos)
    nid_of = {n: node_id_for(*cent[n]) for n in graph_nodes}
    net_pos = {nid_of[n]: cent[n] for n in graph_nodes}

    # 4. directed edges
    best: dict[tuple[str, str], tuple[tuple, Edge]] = {}
    for w, seq in segments:
        u, v = nid_of[seq[0]], nid_of[seq[-1]]
        if u == v:
            continue
        coords = [[net_pos[u][1], net_pos[u][0]]] + [[nodes[n][1], nodes[n][0]] for n in seq[1:-1]] \
            + [[net_pos[v][1], net_pos[v][0]]]
        coords = [[round(x, 7), round(y, 7)] for x, y in coords]
        length = max(1.0, round(polyline_length_m(coords), 2))
        rc, tags = w["class"], w["tags"]
        ow = oneway_dir(tags, rc)
        for direction, a, b, geom in ((1, u, v, coords), (-1, v, u, coords[::-1])):
            if ow and ow != direction:
                continue
            key = f"{a}>{b}:{w['id']}:{seq[0]}:{seq[-1]}"
            e = Edge(id="e_" + hashlib.sha1(key.encode()).hexdigest()[:10], from_node=a, to_node=b,
                     geometry=geom, length_m=length, speed_limit=speed_mps(tags, rc),
                     lanes=lanes_per_dir(tags, rc, direction, ow), road_class=rc)
            rank_key = (e.lanes, CLASS_RANK[rc], -length, e.id)
            prev = best.get((a, b))
            if prev is None or rank_key > prev[0]:
                best[(a, b)] = (rank_key, e)
    edges = [e for _, e in sorted(best.values(), key=lambda x: x[1].id)]

    # 5. clip to bbox
    def inside(nid: str) -> bool:
        lat, lon = net_pos[nid]
        return bbox is None or (bbox.south <= lat <= bbox.north and bbox.west <= lon <= bbox.east)

    edges = [e for e in edges if inside(e.from_node) or inside(e.to_node)]

    # 6. largest weakly connected component
    g = nx.DiGraph()
    g.add_edges_from((e.from_node, e.to_node) for e in edges)
    if g.number_of_nodes() == 0:
        raise OverpassParseError("no drivable roads inside the bbox")
    comp = max(nx.weakly_connected_components(g), key=lambda c: (len(c), min(c)))
    edges = [e for e in edges if e.from_node in comp]

    # 7. boundary
    if bbox is not None:
        boundary = {n for n in comp if not inside(n)}
    else:
        und = g.subgraph(comp).to_undirected()
        boundary = {n for n in comp if und.degree(n) == 1}
    entry = sorted(n for n in boundary if any(e.from_node == n for e in edges))
    exit_ = sorted(n for n in boundary if any(e.to_node == n for e in edges))

    net = RoadNetwork(
        nodes=[Node(id=n, lat=round(net_pos[n][0], 7), lon=round(net_pos[n][1], 7)) for n in sorted(comp)],
        edges=edges, entry_nodes=entry, exit_nodes=exit_, bbox=bbox,
    )

    # roundabouts / signals mapped to network nodes
    roundabout_nodes = {nid_of[n] for n in roundabout_osm if n in nid_of and nid_of[n] in comp}
    deg = defaultdict(set)
    for e in edges:
        deg[e.from_node].add(e.to_node)
        deg[e.to_node].add(e.from_node)
    junctions = [n for n in comp if len(deg[n]) >= 3]
    signalised: set[str] = set()
    for n, t in node_tags.items():
        if t.get("highway") != "traffic_signals":
            continue
        lat, lon = nodes[n]
        near = min(((distance_m(lat, lon, *net_pos[j]), j) for j in junctions), default=None)
        if near and near[0] <= SIGNAL_SNAP_M:
            signalised.add(near[1])

    stats = {"osm_ways_kept": len(ways), "graph_nodes_raw": len(graph_nodes), "nodes": len(net.nodes),
             "edges": len(edges), "entry_nodes": len(entry), "osm_signals_snapped": len(signalised)}
    return ParsedArea(network=net, roundabout_nodes=roundabout_nodes, signalised_nodes=signalised, stats=stats)
