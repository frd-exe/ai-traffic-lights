"""Synthetic Overpass response for parser tests (no network). Coordinates are built in metres.

Layout (x east, y north, metres from 41.39N 2.165E; test bbox = +-300 m):
  - primary dual carriageway along y=0: two oneways at y=+6 (east) and y=-6 (west)
  - residential N-S streets at x=-150, 0, 150 and E-W streets at y=-150, 150, all out to +-400 m
  - (-150,-150) is a roundabout ring (junction=roundabout, radius 25 m)
  - (150,-150) is a mini_roundabout node
  - a traffic_signals node 10 m south of junction (0,150)
  - a service road and a footway (must be dropped)
Expected: 7 signal-candidate intersections (9 grid junctions - ring - mini roundabout),
12 boundary nodes, dual carriageway crossings consolidated into one node each.
"""

from __future__ import annotations

import math

LAT0, LON0 = 41.39, 2.165
M_LAT = 111_320.0
M_LON = 111_320.0 * math.cos(math.radians(LAT0))
BBOX = {
    "west": LON0 - 300 / M_LON, "south": LAT0 - 300 / M_LAT,
    "east": LON0 + 300 / M_LON, "north": LAT0 + 300 / M_LAT,
}


class _B:
    def __init__(self):
        self.nodes: dict[tuple[float, float], int] = {}
        self.node_tags: dict[int, dict] = {}
        self.ways: list[dict] = []
        self._next_way = 1000

    def n(self, x: float, y: float, tags: dict | None = None) -> int:
        key = (round(x, 3), round(y, 3))
        if key not in self.nodes:
            self.nodes[key] = 1 + len(self.nodes)
        nid = self.nodes[key]
        if tags:
            self.node_tags[nid] = tags
        return nid

    def way(self, pts: list[tuple[float, float]], **tags: str) -> None:
        self._next_way += 1
        self.ways.append({"type": "way", "id": self._next_way, "nodes": [self.n(x, y) for x, y in pts], "tags": tags})

    def elements(self) -> list[dict]:
        els = list(self.ways)
        for (x, y), nid in sorted(self.nodes.items(), key=lambda kv: kv[1]):
            el = {"type": "node", "id": nid, "lat": round(LAT0 + y / M_LAT, 7), "lon": round(LON0 + x / M_LON, 7)}
            if nid in self.node_tags:
                el["tags"] = self.node_tags[nid]
            els.append(el)
        return els


def build() -> dict:
    b = _B()
    xs = [-400, -150, 0, 150, 400]
    # dual carriageway (primary, oneway)
    b.way([(x, 6) for x in xs], highway="primary", oneway="yes", lanes="2", maxspeed="50", name="Main N")
    b.way([(x, -6) for x in reversed(xs)], highway="primary", oneway="yes", lanes="2", maxspeed="50", name="Main S")
    # signal node on x=0 street, 10 m south of y=150
    b.n(0, 140, {"highway": "traffic_signals"})
    b.n(150, -150, {"highway": "mini_roundabout"})
    # N-S streets
    for x in (0, 150):
        ys = [-400, -150, -6, 6, 140, 150, 400] if x == 0 else [-400, -150, -6, 6, 150, 400]
        b.way([(x, y) for y in ys], highway="residential", maxspeed="30")
    # x=-150 street is split by the ring at (-150,-150)
    b.way([(-150, y) for y in (400, 150, 6, -6, -125)], highway="residential")
    b.way([(-150, y) for y in (-175, -400)], highway="residential")
    # E-W streets
    b.way([(x, 150) for x in xs], highway="residential")
    b.way([(-400, -150), (-175, -150)], highway="residential")
    b.way([(-125, -150), (0, -150), (150, -150), (400, -150)], highway="residential")
    # roundabout ring around (-150,-150): N, E, S, W (clockwise)
    b.way([(-150, -125), (-125, -150), (-150, -175), (-175, -150), (-150, -125)],
          highway="residential", junction="roundabout")
    # to be dropped
    b.way([(150, 150), (250, 250)], highway="service")
    b.way([(0, 150), (60, 250)], highway="footway")
    return {"version": 0.6, "generator": "test-fixture", "elements": b.elements()}


def wrapped() -> dict:
    """Same shape as scripts/fetch_sample_area.py output."""
    return {"source": "overpass", "bbox": BBOX, "osm": build()}
