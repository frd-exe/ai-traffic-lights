"""Fetch OSM roads + signals for a bbox from Overpass and save backend/data/sample_area.json.

    python scripts/fetch_sample_area.py --bbox 2.160,41.385,2.172,41.395   # west,south,east,north
    python scripts/fetch_sample_area.py --bbox ... --force                  # ignore the cache

Run LOCALLY (needs internet). Responses are cached in backend/data/cache/ by bbox hash so
repeated runs don't hit Overpass. Output is the raw Overpass JSON wrapped with metadata; the
road-network module converts it into a contract RoadNetwork.
Respect the Overpass usage policy: small boxes, few requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "backend" / "data" / "cache"
OUT = ROOT / "backend" / "data" / "sample_area.json"
ENDPOINT = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Overpass asks for an identifying User-Agent; set OVERPASS_CONTACT to an email or URL.
USER_AGENT = f"ai-traffic-lights-hackathon/0.1 ({os.environ.get('OVERPASS_CONTACT', 'hackathon prototype')})"
CAR_ROADS = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|" \
            "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
MAX_SIDE_DEG = 0.05  # ~5 km; keep requests small


def parse_bbox(s: str) -> tuple[float, float, float, float]:
    try:
        w, so, e, n = (float(x) for x in s.split(","))
    except ValueError:
        raise SystemExit("--bbox must be west,south,east,north")
    if not (w < e and so < n):
        raise SystemExit("bbox must satisfy west < east and south < north")
    if e - w > MAX_SIDE_DEG or n - so > MAX_SIDE_DEG:
        raise SystemExit(f"bbox too large (max {MAX_SIDE_DEG} deg per side)")
    return w, so, e, n


def query(w: float, s: float, e: float, n: float) -> str:
    bb = f"{s},{w},{n},{e}"  # Overpass order: south,west,north,east
    return (
        "[out:json][timeout:60];\n"
        f'(way["highway"~"^({CAR_ROADS})$"]({bb});\n'
        f' node["highway"="traffic_signals"]({bb}););\n'
        "out body;\n>;\nout skel qt;"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="west,south,east,north (WGS84 degrees)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    w, s, e, n = parse_bbox(args.bbox)
    q = query(w, s, e, n)
    key = hashlib.sha1(q.encode()).hexdigest()[:12]
    cache_file = CACHE / f"overpass_{key}.json"

    if cache_file.exists() and not args.force:
        print(f"using cache {cache_file.relative_to(ROOT)}")
        osm = json.loads(cache_file.read_text("utf-8"))
    else:
        print(f"querying {ENDPOINT} ...")
        req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode({"data": q}).encode(),
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=90) as r:
            osm = json.loads(r.read().decode("utf-8"))
        CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(json.dumps(osm).encode("utf-8"))

    wrapped = {
        "source": "overpass",
        "endpoint": ENDPOINT,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": {"west": w, "south": s, "east": e, "north": n},
        "attribution": "(c) OpenStreetMap contributors, ODbL",
        "osm": osm,
    }
    OUT.write_bytes((json.dumps(wrapped, indent=1) + "\n").encode("utf-8"))
    ways = sum(1 for el in osm.get("elements", []) if el.get("type") == "way")
    signals = sum(1 for el in osm.get("elements", []) if el.get("tags", {}).get("highway") == "traffic_signals")
    print(f"wrote {OUT.relative_to(ROOT)}: {ways} ways, {signals} signal nodes")


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        sys.exit(f"network error: {exc}")
