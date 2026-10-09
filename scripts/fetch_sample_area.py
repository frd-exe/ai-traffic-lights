"""Fetch OSM roads + signals for a bbox from Overpass and save backend/data/sample_area.json.

    python scripts/fetch_sample_area.py --bbox WEST,SOUTH,EAST,NORTH      # order: west,south,east,north
    python scripts/fetch_sample_area.py --bbox ... --force               # ignore the cache
    python scripts/fetch_sample_area.py --offline-check                  # validate the existing file

Run LOCALLY (needs internet). Robust against the public servers' frequent 504s:
  - several endpoints tried in turn (OVERPASS_ENDPOINTS in .env, comma-separated, overrides the default list)
  - 4 tries with exponential backoff + jitter (about 5 / 15 / 45 s between tries)
  - if the whole box fails, it is split into 2x2 tiles fetched one by one and merged
  - every successful response is cached in backend/data/cache/ and reused on the next run
If everything fails, the backend keeps working with the synthetic demo city (grid_network.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_keys import load_env  # noqa: E402

CACHE = ROOT / "backend" / "data" / "cache"
OUT = ROOT / "backend" / "data" / "sample_area.json"
# The first is the main public instance. The mirrors are from memory and UNVERIFIED (docs/DATA_SOURCES.md).
DEFAULT_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
TRIES = 4
BACKOFF_S = [5, 15, 45]  # between tries; +-20% jitter
TILE_PAUSE_S = 3
REQUEST_TIMEOUT_S = 120
MAX_SIDE_DEG = 0.05  # ~5 km; keep requests small
CAR_ROADS = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
             "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link")
RETRYABLE_HTTP = {429, 500, 502, 503, 504}

# Injection points for tests.
URLOPEN = urllib.request.urlopen
SLEEP = time.sleep
JITTER = random.Random()

BBox = tuple[float, float, float, float]  # west, south, east, north


class FetchFailed(RuntimeError):
    def __init__(self, label: str, errors: list[str], retryable: bool = True):
        super().__init__(f"{label}: " + "; ".join(errors))
        self.label, self.errors, self.retryable = label, errors, retryable


def say(msg: str) -> None:
    print(msg, flush=True)


def settings() -> tuple[list[str], str]:
    env = {**load_env(ROOT / ".env"), **os.environ}
    raw = env.get("OVERPASS_ENDPOINTS", "").strip()
    endpoints = [e.strip() for e in raw.split(",") if e.strip()] or list(DEFAULT_ENDPOINTS)
    contact = env.get("OVERPASS_CONTACT", "hackathon prototype")
    return endpoints, f"ai-traffic-lights-hackathon/0.2 ({contact})"


def parse_bbox(s: str) -> BBox:
    try:
        w, so, e, n = (float(x) for x in s.split(","))
    except ValueError:
        raise SystemExit("--bbox must be WEST,SOUTH,EAST,NORTH, e.g. --bbox 2.160,41.385,2.172,41.395")
    if not (-180 <= w < e <= 180 and -90 <= so < n <= 90):
        raise SystemExit("bbox must satisfy west < east and south < north (order: west,south,east,north). "
                         "Did you pass lat,lon instead of lon,lat?")
    if e - w > MAX_SIDE_DEG or n - so > MAX_SIDE_DEG:
        raise SystemExit(f"bbox too large (max {MAX_SIDE_DEG} deg, about 5 km, per side). Use a smaller box.")
    return w, so, e, n


def query(b: BBox) -> str:
    w, s, e, n = b
    bb = f"{s},{w},{n},{e}"  # Overpass order: south,west,north,east
    return (
        "[out:json][timeout:90];\n"
        f'(way["highway"~"^({CAR_ROADS})$"]({bb});\n'
        f' node["highway"~"^(traffic_signals|mini_roundabout)$"]({bb}););\n'
        "out body;\n>;\nout skel qt;"
    )


def cache_file(q: str) -> Path:
    return CACHE / f"overpass_{hashlib.sha1(q.encode()).hexdigest()[:12]}.json"


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or url


def fetch_once(endpoint: str, q: str, user_agent: str) -> dict:
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": user_agent})
    with URLOPEN(req, timeout=REQUEST_TIMEOUT_S) as r:
        data = json.loads(r.read().decode("utf-8"))
    remark = str(data.get("remark", ""))
    if "runtime error" in remark or "timed out" in remark:
        # Overpass sometimes answers 200 with a partial result + an error remark.
        raise urllib.error.URLError(f"server-side timeout ({remark[:120]})")
    if not isinstance(data.get("elements"), list):
        raise ValueError("response has no 'elements' list")
    return data


def fetch_with_retries(q: str, label: str, endpoints: list[str], user_agent: str) -> dict:
    errors: list[str] = []
    for attempt in range(TRIES):
        ep = endpoints[attempt % len(endpoints)]
        try:
            say(f"[{label}] try {attempt + 1}/{TRIES}: {_host(ep)} ...")
            data = fetch_once(ep, q, user_agent)
            say(f"[{label}] OK from {_host(ep)}: {len(data['elements'])} elements")
            return data
        except urllib.error.HTTPError as e:
            msg = f"{_host(ep)}: HTTP {e.code} {e.reason}"
            if e.code not in RETRYABLE_HTTP:
                raise FetchFailed(label, errors + [msg + " (not retryable: the query was rejected)"], retryable=False)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            msg = f"{_host(ep)}: {getattr(e, 'reason', e)}"
        except ValueError as e:
            msg = f"{_host(ep)}: invalid response ({e})"
        errors.append(msg)
        if attempt < TRIES - 1:
            wait = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)] * JITTER.uniform(0.8, 1.2)
            say(f"[{label}] failed: {msg} -> next try in {wait:.0f} s")
            SLEEP(wait)
        else:
            say(f"[{label}] failed: {msg}")
    raise FetchFailed(label, errors)


def cached_or_fetch(b: BBox, label: str, endpoints: list[str], ua: str, force: bool) -> dict:
    q = query(b)
    cf = cache_file(q)
    if cf.exists() and not force:
        say(f"[{label}] using cache {cf.relative_to(ROOT)}")
        return json.loads(cf.read_text("utf-8"))
    data = fetch_with_retries(q, label, endpoints, ua)
    CACHE.mkdir(parents=True, exist_ok=True)
    cf.write_bytes(json.dumps(data).encode("utf-8"))
    return data


def tiles_2x2(b: BBox) -> list[BBox]:
    w, s, e, n = b
    mx, my = (w + e) / 2, (s + n) / 2
    return [(w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)]


def merge(parts: list[dict]) -> dict:
    seen: dict[tuple[str, int], dict] = {}
    for p in parts:
        for el in p.get("elements", []):
            key = (el.get("type"), el.get("id"))
            prev = seen.get(key)
            # keep the richest copy (tags beat `skel` copies of the same node)
            if prev is None or (len(el) > len(prev)):
                seen[key] = el
    elements = sorted(seen.values(), key=lambda el: (el.get("type") != "way", el.get("type"), el.get("id")))
    return {"version": parts[0].get("version", 0.6) if parts else 0.6, "generator": "merged tiles",
            "elements": elements}


NEXT_STEPS = """What to do next:
  1. Wait 10-30 minutes and run the same command again (the public Overpass servers are often overloaded;
     successful parts are cached, so a re-run only fetches what is missing).
  2. Use a smaller box (e.g. about 800 x 800 m) - the order is WEST,SOUTH,EAST,NORTH.
  3. Put other mirrors in .env: OVERPASS_ENDPOINTS=https://...,https://...
  4. Or keep using the demo city: the backend serves backend/data/grid_network.json automatically
     when sample_area.json is missing. Nothing else is blocked."""


def fetch(b: BBox, force: bool = False) -> dict:
    endpoints, ua = settings()
    say(f"bbox (west,south,east,north) = {b}; endpoints: {', '.join(_host(e) for e in endpoints)}")
    try:
        return {"tiles": 1, "osm": cached_or_fetch(b, "whole box", endpoints, ua, force)}
    except FetchFailed as e:
        if not e.retryable:
            raise
        say("The whole box failed on every try; splitting it into 2x2 tiles ...")
    parts = []
    for i, t in enumerate(tiles_2x2(b)):
        if i:
            SLEEP(TILE_PAUSE_S)
        parts.append(cached_or_fetch(t, f"tile {i + 1}/4", endpoints, ua, force))
    return {"tiles": 4, "osm": merge(parts)}


def write_sample(b: BBox, result: dict) -> dict:
    w, s, e, n = b
    endpoints, _ = settings()
    wrapped = {
        "source": "overpass",
        "endpoints": endpoints,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": {"west": w, "south": s, "east": e, "north": n},
        "tiles": result["tiles"],
        "attribution": "(c) OpenStreetMap contributors, ODbL",
        "osm": result["osm"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes((json.dumps(wrapped, indent=1) + "\n").encode("utf-8"))
    return wrapped


def summarize(osm: dict) -> dict[str, int]:
    els = osm.get("elements", [])
    return {
        "nodes": sum(1 for el in els if el.get("type") == "node"),
        "ways": sum(1 for el in els if el.get("type") == "way"),
        "signals": sum(1 for el in els if (el.get("tags") or {}).get("highway") == "traffic_signals"),
    }


def offline_check(path: Path = OUT) -> int:
    if not path.exists():
        say(f"FAIL: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} does not exist.\n"
            "Run: python scripts/fetch_sample_area.py --bbox WEST,SOUTH,EAST,NORTH\n"
            "(Without it the backend uses the synthetic demo city - that is fine.)")
        return 1
    try:
        data = json.loads(path.read_text("utf-8"))
        osm = data.get("osm", data)
        assert isinstance(osm.get("elements"), list), "no 'elements' list"
        bbox = data.get("bbox")
        assert bbox and bbox["west"] < bbox["east"] and bbox["south"] < bbox["north"], "missing/invalid bbox"
    except (ValueError, AssertionError, KeyError, TypeError) as e:
        say(f"FAIL: {path.name} is not a valid sample ({e}). Delete it and fetch again.")
        return 1
    c = summarize(osm)
    say(f"OK: {path.name}: {c['nodes']} nodes, {c['ways']} ways, {c['signals']} traffic signals; "
        f"bbox {bbox}; fetched {data.get('fetched_at', '?')} ({data.get('tiles', 1)} tile(s))")
    try:
        from backend.contract.models import BBox as ModelBBox
        from backend.roadnet.intersections import build_intersections
        from backend.roadnet.overpass import OverpassParseError, parse_overpass

        p = parse_overpass(osm, ModelBBox(**bbox))
        ixs = build_intersections(p.network, exclude=p.roundabout_nodes, signalised=p.signalised_nodes)
        say(f"OK: road network: {len(p.network.nodes)} nodes, {len(p.network.edges)} edges, "
            f"{len(p.network.entry_nodes)} entry nodes, {len(ixs)} signal candidates")
    except OverpassParseError as e:
        say(f"FAIL: the file has no usable road network ({e}).")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", help="WEST,SOUTH,EAST,NORTH (WGS84 degrees)")
    ap.add_argument("--force", action="store_true", help="ignore cached responses")
    ap.add_argument("--offline-check", action="store_true", help="validate the existing sample_area.json and exit")
    args = ap.parse_args(argv)
    if args.offline_check:
        return offline_check()
    if not args.bbox:
        ap.error("--bbox is required (or use --offline-check)")
    b = parse_bbox(args.bbox)
    try:
        result = fetch(b, force=args.force)
    except FetchFailed as e:
        say(f"\nFAILED ({e.label}). Attempts:")
        for err in e.errors:
            say(f"  - {err}")
        say("\n" + NEXT_STEPS)
        return 2
    wrapped = write_sample(b, result)
    c = summarize(wrapped["osm"])
    say(f"wrote {OUT.relative_to(ROOT)}: {c['ways']} ways, {c['nodes']} nodes, {c['signals']} signal nodes "
        f"({result['tiles']} tile(s)). Check it with: python scripts/fetch_sample_area.py --offline-check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
