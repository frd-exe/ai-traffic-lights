"""Generate backend/data/grid_network.json: a 3x3 grid, 150 m blocks, two-way edges.

    python scripts/gen_grid_network.py

Layout (r = row, c = col; interior junctions r,c in 1..3; boundary nodes on the rim):

        n01  n02  n03
    n10 n11--n12--n13 n14
    n20 n21==n22==n23 n24   <- row 2 is the major street (primary, 2 lanes, 50 km/h)
    n30 n31--n32--n33 n34
        n41  n42  n43

Boundary nodes are both entry and exit nodes. Deterministic output (LF, sorted keys).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.contract.models import RoadNetwork  # noqa: E402

OUT = ROOT / "backend" / "data" / "grid_network.json"
CENTER_LAT, CENTER_LON = 41.39000, 2.16500  # Eixample, Barcelona (a real grid city)
BLOCK_M = 150.0
M_PER_DEG_LAT = 111_320.0


def node_latlon(r: int, c: int) -> tuple[float, float]:
    north_m = (2 - r) * BLOCK_M
    east_m = (c - 2) * BLOCK_M
    lat = CENTER_LAT + north_m / M_PER_DEG_LAT
    lon = CENTER_LON + east_m / (M_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT)))
    return round(lat, 7), round(lon, 7)


def build() -> dict:
    coords: dict[str, tuple[float, float]] = {}
    for r in range(5):
        for c in range(5):
            interior = 1 <= r <= 3 and 1 <= c <= 3
            rim = (r in (0, 4) and 1 <= c <= 3) or (c in (0, 4) and 1 <= r <= 3)
            if interior or rim:
                coords[f"n{r}{c}"] = node_latlon(r, c)

    pairs: list[tuple[str, str, bool]] = []  # (a, b, major)
    for r in range(1, 4):
        for c in range(0, 4):
            pairs.append((f"n{r}{c}", f"n{r}{c + 1}", r == 2))
    for c in range(1, 4):
        for r in range(0, 4):
            pairs.append((f"n{r}{c}", f"n{r + 1}{c}", False))

    edges = []
    for a, b, major in pairs:
        for u, v in ((a, b), (b, a)):
            (lat1, lon1), (lat2, lon2) = coords[u], coords[v]
            edges.append({
                "id": f"e_{u}_{v}",
                "from_node": u,
                "to_node": v,
                "geometry": [[lon1, lat1], [lon2, lat2]],
                "length_m": BLOCK_M,
                "speed_limit": 13.89 if major else 8.33,
                "lanes": 2 if major else 1,
                "road_class": "primary" if major else "residential",
            })

    boundary = sorted(n for n in coords if n[1] in "04" or n[2] in "04")
    lats = [p[0] for p in coords.values()]
    lons = [p[1] for p in coords.values()]
    net = {
        "nodes": [{"id": n, "lat": lat, "lon": lon} for n, (lat, lon) in sorted(coords.items())],
        "edges": edges,
        "entry_nodes": boundary,
        "exit_nodes": boundary,
        "bbox": {
            "west": round(min(lons) - 0.0005, 6), "south": round(min(lats) - 0.0005, 6),
            "east": round(max(lons) + 0.0005, 6), "north": round(max(lats) + 0.0005, 6),
        },
    }
    RoadNetwork.model_validate(net)
    return net


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes((json.dumps(build(), indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
